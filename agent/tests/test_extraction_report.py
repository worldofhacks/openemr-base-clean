"""Extraction-report render gate regressions (W2-D3/D6; §2/§5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.schemas.citations import CitationV2
from app.schemas.extraction import (
    ExtractionArtifact,
    GroundedField,
    LabPdfExtraction,
    LabResult,
    NormBBox,
)
from app.session.store import Session


def _citation(path: str, value: str) -> CitationV2:
    return CitationV2(
        source_type="uploaded_document",
        source_id="document:synthetic",
        page_or_section="1",
        field_or_chunk_id=path,
        quote_or_value=value,
    )


def _grounded(value, path: str) -> GroundedField:
    return GroundedField(
        value=value,
        page=1,
        bbox=NormBBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4),
        grounded=True,
        citation=_citation(path, str(value)),
    )


def _artifact(*, summary: dict[str, int] | None = None) -> ExtractionArtifact:
    result = LabResult(
        test_name=_grounded("HbA1c", "results.0.test_name"),
        value=GroundedField(
            value="65",  # raw unsupported proposal remains persisted, never rendered
            page=1,
            bbox=NormBBox(x0=0.31, y0=0.2, x1=0.4, y1=0.4),
            grounded=False,
            citation=None,
        ),
        unit=_grounded("%", "results.0.unit"),
        reference_range=_grounded("4.0-5.6", "results.0.reference_range"),
        abnormal_flag=_grounded("H", "results.0.abnormal_flag"),
        collection_date=_grounded(
            date(2026, 7, 15), "results.0.collection_date"
        ),
    )
    return ExtractionArtifact(
        artifact_version=1,
        document_id="document-synthetic",
        content_hash="a" * 64,
        correlation_id="corr-synthetic",
        doc_type="lab_pdf",
        extraction=LabPdfExtraction(
            results=[result], source_document_id="document-synthetic"
        ),
        grounding_summary=summary
        or {"fields_grounded": 5, "fields_unsupported": 1},
        created_ts="2026-07-15T12:00:00+00:00",
        agent_version="test",
    )


def _session(patient_id: str = "patient-synthetic") -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id="session-synthetic",
        clinician_sub="clinician-synthetic",
        patient_id=patient_id,
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
    )


def test_projection_preserves_grounded_citations_and_redacts_unsupported_value():
    from app.ingestion.reports import project_extraction_report

    report = project_extraction_report(_artifact())

    grounded = next(field for field in report.fields if field.field_path.endswith("unit"))
    unsupported = next(field for field in report.fields if field.field_path.endswith("value"))
    assert grounded.display_value == "%"
    assert grounded.citation is not None
    assert grounded.bbox is not None
    assert unsupported.verdict == "unsupported"
    assert unsupported.display_value is None
    assert unsupported.citation is None
    assert unsupported.bbox is not None  # review region, never a verified highlight
    assert "65" not in report.model_dump_json()


def test_projection_fails_closed_when_persisted_counts_disagree():
    from app.ingestion.reports import (
        ExtractionReportIntegrityError,
        project_extraction_report,
    )

    with pytest.raises(ExtractionReportIntegrityError):
        project_extraction_report(
            _artifact(summary={"fields_grounded": 6, "fields_unsupported": 0})
        )


@pytest.mark.asyncio
async def test_runtime_checks_patient_before_touching_artifact_store():
    from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
    from app.ingestion.runtime import _DocumentOperationsFacade
    from app.ingestion.service import DocumentAccessError

    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash="a" * 64,
            doc_type="lab_pdf",
            filename="synthetic.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id="corr-synthetic",
            credential_ref="credential-synthetic",
        )
    )

    class _Artifacts:
        async def refs_for_document(self, _document_id: str):
            raise AssertionError("artifact store touched before patient authorization")

    facade = object.__new__(_DocumentOperationsFacade)
    facade._repository = repository
    facade._artifacts = _Artifacts()

    with pytest.raises(DocumentAccessError):
        await facade.extraction_report(_session("patient-other"), record.document_id)
