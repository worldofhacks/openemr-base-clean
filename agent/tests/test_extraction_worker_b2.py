"""Canonical B2 extractor-worker boundary tests (W2-D2/D3; §2)."""

from __future__ import annotations

import pytest

from app.schemas.workers import WorkerInput


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def extract_document(self, document_ref, *, patient_ref, correlation_id):
        self.calls.append((document_ref, patient_ref, correlation_id))
        from app.orchestrator.workers.intake_extractor import PersistedExtraction

        return PersistedExtraction(
            artifact_ref=f"artifact:{document_ref}",
            citation_refs=(f"citation:{document_ref}",),
        )


@pytest.mark.asyncio
async def test_real_extractor_callable_uses_only_canonical_worker_models():
    from app.orchestrator.workers.intake_extractor import run_extraction_worker

    worker_input = WorkerInput(
        correlation_id="corr-synthetic-1",
        turn=1,
        patient_ref="patient-ref:synthetic",
        document_refs=["document-ref:one"],
        evidence_refs=[],
        request_kind="extract",
    )
    pipeline = FakePipeline()

    output = await run_extraction_worker(worker_input, pipeline=pipeline)

    assert output.correlation_id == worker_input.correlation_id
    assert output.worker == "intake_extractor"
    assert output.status == "complete"
    assert output.artifact_refs == ["artifact:document-ref:one"]
    assert output.citation_refs == ["citation:document-ref:one"]
    assert pipeline.calls == [
        ("document-ref:one", "patient-ref:synthetic", "corr-synthetic-1")
    ]
