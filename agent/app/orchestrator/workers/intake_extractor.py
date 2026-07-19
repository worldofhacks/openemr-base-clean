"""Production B2 intake-extractor boundary for the LangGraph worker.

The worker accepts and returns only the frozen refs-only models. Clinical values stay
inside the injected ingestion pipeline and persisted extraction artifacts; no raw PHI
crosses the supervisor boundary (W2_ARCHITECTURE §2, W2-D2/D3).

Sub-call tracing (R03; W2-REQ-74): every per-document ``extract_document`` sub-call is
marked with ``sub_span`` so it nests inside this worker's span in the graph trace.
When the bound pipeline exposes the concrete ``DocumentExtractionPipeline`` telemetry
seam (an optional ``telemetry`` parameter), the worker additionally hands it a
stage-recording telemetry so the pipeline's own OCR/VLM/schema-parse/write stage
boundaries become nested sub-spans with their REAL timings. Pipelines without that
seam (the frozen minimal protocol; the runtime's dynamic pipeline, which emits its
stage record to the structured event lane itself) are called exactly as before.
"""

from __future__ import annotations

import inspect
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from app.orchestrator.subspans import sub_span
from app.schemas.workers import WorkerInput, WorkerOutput

WORKER_NAME = "intake_extractor"


@dataclass(frozen=True)
class PersistedExtraction:
    artifact_ref: str
    citation_refs: tuple[str, ...] = ()
    fields_grounded: int = 0
    fields_unsupported: int = 0


class ExtractionPipeline(Protocol):
    async def extract_document(
        self,
        document_ref: str,
        *,
        patient_ref: str,
        correlation_id: str,
    ) -> PersistedExtraction: ...


class _StageSpanHandle:
    """Duck-typed ``StageSpan``: the pipeline reads ``latency_ms`` after the stage."""

    def __init__(self) -> None:
        self.latency_ms: float = 0.0


class _StageSubSpanTelemetry:
    """Records pipeline stage boundaries as worker sub-spans; everything else no-ops.

    This duck-types the surface ``DocumentExtractionPipeline`` uses on an injected
    telemetry (``stage``/``record_usage``/``record_grounding``/``record_write_result``/
    ``record_write_transition``/``record_readback``/``finish``). Only the stage
    boundaries are captured — event emission remains the runtime pipeline's own
    responsibility, and no clinical value enters a span name or metadata.
    """

    @asynccontextmanager
    async def stage(self, stage: str) -> AsyncIterator[_StageSpanHandle]:
        handle = _StageSpanHandle()
        started = time.perf_counter()
        with sub_span(f"intake.{stage}"):
            try:
                yield handle
            finally:
                handle.latency_ms = max((time.perf_counter() - started) * 1000, 0.0)

    def record_usage(self, usage: object, model: str) -> None:
        del usage, model

    def record_grounding(
        self, *, fields_grounded: int, fields_unsupported: int
    ) -> None:
        del fields_grounded, fields_unsupported

    def record_write_result(
        self, leg: object, result: object, *, latency_ms: float
    ) -> None:
        del leg, result, latency_ms

    def record_write_transition(
        self, leg: object, *, state: object, verified: bool
    ) -> None:
        del leg, state, verified

    def record_readback(
        self, leg: object, *, verified: bool, latency_ms: float
    ) -> None:
        del leg, verified, latency_ms

    def finish(self, *, success: bool) -> None:
        del success


def _supports_telemetry(pipeline: ExtractionPipeline) -> bool:
    """True only when the bound pipeline's ``extract_document`` accepts ``telemetry``."""

    try:
        parameters = inspect.signature(pipeline.extract_document).parameters
    except (TypeError, ValueError):
        return False
    return "telemetry" in parameters


async def run_extraction_worker(
    worker_input: WorkerInput,
    *,
    pipeline: ExtractionPipeline,
) -> WorkerOutput:
    """Extract each reference through the real B2 ingestion pipeline interface."""

    supports_telemetry = _supports_telemetry(pipeline)
    artifacts: list[str] = []
    citations: list[str] = []
    for index, document_ref in enumerate(worker_input.document_refs):
        with sub_span("intake.extract_document", document_index=index) as span_meta:
            stage_kwargs: dict[str, object] = {}
            if supports_telemetry:
                stage_kwargs["telemetry"] = _StageSubSpanTelemetry()
            persisted = await pipeline.extract_document(
                document_ref,
                patient_ref=worker_input.patient_ref,
                correlation_id=worker_input.correlation_id,
                **stage_kwargs,
            )
            span_meta["fields_grounded"] = persisted.fields_grounded
            span_meta["fields_unsupported"] = persisted.fields_unsupported
        artifacts.append(persisted.artifact_ref)
        for citation_ref in persisted.citation_refs:
            if citation_ref not in citations:
                citations.append(citation_ref)
    return WorkerOutput(
        correlation_id=worker_input.correlation_id,
        worker=WORKER_NAME,
        status="complete",
        artifact_refs=artifacts,
        citation_refs=citations,
        reason_code=None,
    )


# Compatibility name for the B3 skeleton's existing node vocabulary. New wiring should
# import ``run_extraction_worker`` explicitly.
run_intake_extractor = run_extraction_worker
