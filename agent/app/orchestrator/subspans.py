"""Per-hop worker sub-call capture (R03/AF-P1-02; W2-REQ-74; W2_ARCHITECTURE.md §6).

The PDF (p.7) requires extraction and retrieval sub-calls to be traceable WITHIN their
worker spans. The graph installs a :class:`SubSpanRecorder` around each worker
invocation via :func:`recording`; worker adapters mark their sub-calls with
:func:`sub_span`. Recorded spans become child observations of the worker's Langfuse
span when the turn's trace is emitted.

Design constraints:
- **Soft seam.** With no recorder installed, :func:`sub_span` is a strict no-op — the
  worker adapters stay usable outside the graph (direct calls, background jobs).
- **PHI posture (W1 D16, unchanged).** Sub-span names are fixed operational labels and
  metadata values are operational scalars (counts, closed codes, booleans) — never
  query text, clinical values, filenames, or identifiers.
- **Context propagation.** The recorder rides a ``ContextVar``, so it follows the
  worker coroutine and survives ``run_in_threadpool`` (anyio copies the context into
  the thread); the shared recorder object collects spans from either side.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerSubSpan:
    """One recorded worker sub-call: fixed name, real boundaries, PHI-free metadata."""

    name: str
    started_ns: int
    ended_ns: int
    metadata: Mapping[str, object]


class SubSpanRecorder:
    """Collects the sub-calls of exactly one worker hop, in completion order."""

    def __init__(self) -> None:
        self.spans: list[WorkerSubSpan] = []

    def record(
        self, name: str, started_ns: int, ended_ns: int, **metadata: object
    ) -> None:
        self.spans.append(
            WorkerSubSpan(
                name=name,
                started_ns=started_ns,
                ended_ns=ended_ns,
                metadata=dict(metadata),
            )
        )

    @contextmanager
    def span(self, name: str, **metadata: object) -> Iterator[dict[str, object]]:
        """Time one sub-call; the yielded dict lets the caller add outcome metadata.

        The span is recorded in a ``finally`` so a FAILED sub-call remains traceable —
        exactly the case an incident reader needs the nesting for.
        """

        extra: dict[str, object] = {}
        started_ns = time.time_ns()
        try:
            yield extra
        finally:
            self.record(name, started_ns, time.time_ns(), **{**metadata, **extra})


_RECORDER: ContextVar[SubSpanRecorder | None] = ContextVar(
    "w2_worker_subspan_recorder", default=None
)


def current_recorder() -> SubSpanRecorder | None:
    """The recorder installed by the graph for the currently executing worker hop."""

    return _RECORDER.get()


@contextmanager
def recording(recorder: SubSpanRecorder) -> Iterator[SubSpanRecorder]:
    """Install ``recorder`` for the duration of one worker invocation."""

    token = _RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        _RECORDER.reset(token)


@contextmanager
def sub_span(name: str, **metadata: object) -> Iterator[dict[str, object]]:
    """Mark one worker sub-call. A strict no-op when no recorder is installed."""

    recorder = _RECORDER.get()
    if recorder is None:
        yield {}
        return
    with recorder.span(name, **metadata) as extra:
        yield extra
