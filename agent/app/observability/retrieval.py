"""Emit the registered ``retrieval.completed`` event at true retrieval completion.

R05 / AF-P1-04: ``RetrievalAttributes`` was registered in the closed event registry
(``events.py``) but never emitted anywhere. The composition root wraps its retrieval
worker callables with :func:`observe_retrieval_worker`, so every retrieval completion —
healthy, degraded, or failed — produces exactly one PHI-free ``retrieval.completed``
event carrying hit count, latency, degradation, and the closed reranker mode. The
retrieval-latency alert (``agent/ops/w2_alerts.json`` → ``retrieval-p95``) consumes
this event from the structured log lane.

Only refs-shaped ``WorkerInput``/``WorkerOutput`` payloads cross this wrapper; query
text, snippets, and exception bodies never enter the event lane.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping

from app.observability.events import (
    EventComponent,
    EventEmitter,
    EventSeverity,
    EventType,
    RerankerModeCode,
)
from app.schemas.workers import WorkerInput, WorkerOutput

RetrievalWorker = Callable[[WorkerInput], Awaitable[WorkerOutput]]

_MAX_REPORTED_HITS = 20


def resolve_reranker_mode(environ: Mapping[str, str]) -> RerankerModeCode:
    """Mirror the production ``RERANKER`` seam (corpus/retrieval.py) as a closed code."""

    mode = environ.get("RERANKER", "local").strip().casefold()
    if mode == "cohere":
        return "cohere"
    return "local"


def observe_retrieval_worker(
    worker: RetrievalWorker,
    *,
    events: EventEmitter,
    reranker_mode: RerankerModeCode,
    clock: Callable[[], float] = time.perf_counter,
) -> RetrievalWorker:
    """Wrap a retrieval worker so completion emits ``retrieval.completed``.

    Event emission is the existing soft boundary (``EventEmitter`` drops invalid or
    failing exports and counts them); the worker's result and exceptions are always
    passed through unchanged.
    """

    def _emit(
        *, correlation_id: str, latency_ms: float, hit_count: int, degraded: bool
    ) -> None:
        events.emit(
            EventType.RETRIEVAL_COMPLETED,
            {
                "hit_count": min(max(hit_count, 0), _MAX_REPORTED_HITS),
                "latency_ms": max(latency_ms, 0.0),
                "degraded": degraded,
                "reranker_mode": reranker_mode,
            },
            component=EventComponent.RETRIEVAL,
            severity=EventSeverity.WARNING if degraded else EventSeverity.INFO,
            correlation_id=correlation_id,
        )

    async def run(payload: WorkerInput) -> WorkerOutput:
        started = clock()
        try:
            output = await worker(payload)
        except Exception:
            # Retrieval ended without a usable outcome: record the completion as
            # degraded with zero hits, then let the graph's own failure handling own
            # the exception. No exception text enters the event lane.
            _emit(
                correlation_id=payload.correlation_id,
                latency_ms=(clock() - started) * 1000.0,
                hit_count=0,
                degraded=True,
            )
            raise
        degraded = output.status != "complete"
        _emit(
            correlation_id=payload.correlation_id,
            latency_ms=(clock() - started) * 1000.0,
            hit_count=len(output.citation_refs),
            degraded=degraded,
        )
        return output

    return run
