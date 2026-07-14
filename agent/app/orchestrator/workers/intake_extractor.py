"""Production B2 intake-extractor boundary for the LangGraph worker.

The worker accepts and returns only the frozen refs-only models. Clinical values stay
inside the injected ingestion pipeline and persisted extraction artifacts; no raw PHI
crosses the supervisor boundary (W2_ARCHITECTURE §2, W2-D2/D3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.schemas.workers import WorkerInput, WorkerOutput

WORKER_NAME = "intake_extractor"


@dataclass(frozen=True)
class PersistedExtraction:
    artifact_ref: str
    citation_refs: tuple[str, ...] = ()


class ExtractionPipeline(Protocol):
    async def extract_document(
        self,
        document_ref: str,
        *,
        patient_ref: str,
        correlation_id: str,
    ) -> PersistedExtraction: ...


async def run_extraction_worker(
    worker_input: WorkerInput,
    *,
    pipeline: ExtractionPipeline,
) -> WorkerOutput:
    """Extract each reference through the real B2 ingestion pipeline interface."""

    artifacts: list[str] = []
    citations: list[str] = []
    for document_ref in worker_input.document_refs:
        persisted = await pipeline.extract_document(
            document_ref,
            patient_ref=worker_input.patient_ref,
            correlation_id=worker_input.correlation_id,
        )
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
