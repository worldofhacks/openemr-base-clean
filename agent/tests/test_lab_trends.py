"""Artifact-backed patient-pinned lab-trend regressions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.ingestion.artifacts import InMemoryArtifactStore
from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
from app.schemas.citations import CitationV2
from app.schemas.extraction import (
    ExtractionArtifact,
    GroundedField,
    LabPdfExtraction,
    LabResult,
    NormBBox,
)
from app.session.store import Session


def _field(value, document_id: str, path: str, *, page: int = 1):
    return GroundedField(
        value=value,
        page=page,
        bbox=NormBBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4),
        grounded=True,
        citation=CitationV2(
            source_type="uploaded_document",
            source_id=document_id,
            page_or_section=str(page),
            field_or_chunk_id=path,
            quote_or_value=str(value),
        ),
    )


async def _lab_artifact(
    repository: InMemoryDocumentRepository,
    artifacts: InMemoryArtifactStore,
    *,
    patient_id: str,
    content_hash: str,
    name: str,
    value: str,
    unit: str,
    collected: date,
):
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id=patient_id,
            content_hash=content_hash,
            doc_type="lab_pdf",
            filename="synthetic.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id=f"corr-{content_hash[0]}",
            credential_ref="credential-synthetic",
        )
    )
    result = LabResult(
        test_name=_field(name, record.document_id, "results[0].test_name"),
        value=_field(value, record.document_id, "results[0].value"),
        unit=_field(unit, record.document_id, "results[0].unit"),
        reference_range=_field("reference", record.document_id, "results[0].reference_range"),
        abnormal_flag=_field("N", record.document_id, "results[0].abnormal_flag"),
        collection_date=_field(
            collected, record.document_id, "results[0].collection_date"
        ),
    )
    artifact = ExtractionArtifact(
        artifact_version=1,
        document_id=record.document_id,
        content_hash=record.content_hash,
        correlation_id=record.correlation_id,
        doc_type="lab_pdf",
        extraction=LabPdfExtraction(
            results=[result], source_document_id=record.document_id
        ),
        grounding_summary={"fields_grounded": 6, "fields_unsupported": 0},
        created_ts=record.created_ts,
        agent_version="test",
    )
    await artifacts.persist(artifact)
    await repository.set_state(
        record.document_id,
        state="complete",
        fields_grounded=6,
        fields_unsupported=0,
    )
    return record


@pytest.mark.asyncio
async def test_trends_preserve_decimals_split_units_and_sort_deterministically() -> None:
    from app.ingestion.lab_trends import project_lab_trends

    repository = InMemoryDocumentRepository()
    artifacts = InMemoryArtifactStore()
    await _lab_artifact(
        repository,
        artifacts,
        patient_id="patient-synthetic",
        content_hash="a" * 64,
        name="HbA1c",
        value="6.5",
        unit="%",
        collected=date(2026, 7, 2),
    )
    await _lab_artifact(
        repository,
        artifacts,
        patient_id="patient-synthetic",
        content_hash="b" * 64,
        name="  hba1c  ",
        value="65",
        unit="%",
        collected=date(2026, 7, 1),
    )
    await _lab_artifact(
        repository,
        artifacts,
        patient_id="patient-synthetic",
        content_hash="c" * 64,
        name="HBA1C",
        value="0.065",
        unit="fraction",
        collected=date(2026, 7, 3),
    )
    await _lab_artifact(
        repository,
        artifacts,
        patient_id="patient-other",
        content_hash="d" * 64,
        name="HbA1c",
        value="99",
        unit="%",
        collected=date(2026, 7, 4),
    )

    response = await project_lab_trends(
        repository=repository,
        artifact_store=artifacts,
        patient_id="patient-synthetic",
    )

    assert [item.unit for item in response.series] == ["%", "fraction"]
    percentage = response.series[0]
    assert [point.display_value for point in percentage.points] == ["65", "6.5"]
    assert [str(point.value) for point in percentage.points] == ["65", "6.5"]
    assert percentage.points[0].value != percentage.points[1].value
    assert all(point.citation.source_id == point.document_id for point in percentage.points)
    assert all(point.page == 1 and point.bbox.x0 == 0.1 for point in percentage.points)


@pytest.mark.asyncio
async def test_completed_lab_without_a_verified_artifact_fails_closed() -> None:
    from app.ingestion.lab_trends import LabTrendIntegrityError, project_lab_trends

    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash="e" * 64,
            doc_type="lab_pdf",
            filename="synthetic.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id="corr-synthetic",
            credential_ref="credential-synthetic",
        )
    )
    await repository.set_state(record.document_id, state="complete")

    with pytest.raises(LabTrendIntegrityError):
        await project_lab_trends(
            repository=repository,
            artifact_store=InMemoryArtifactStore(),
            patient_id="patient-synthetic",
        )


def _session() -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id="session-synthetic",
        clinician_sub="clinician-synthetic",
        patient_id="patient-synthetic",
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
    )


def test_lab_trends_route_accepts_only_the_opaque_session_pin() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routes.documents import router
    from app.schemas.lab_trends import LabTrendResponse

    class Documents:
        def __init__(self) -> None:
            self.patient_ids: list[str] = []

        async def lab_trends(self, session: Session) -> LabTrendResponse:
            self.patient_ids.append(session.patient_id)
            return LabTrendResponse()

    class Services:
        def __init__(self) -> None:
            self.documents = Documents()

        async def resolve_session(self, session_id: str) -> Session:
            assert session_id == "session-synthetic"
            return _session()

    services = Services()
    app = FastAPI()
    app.state.services = services
    app.include_router(router)
    response = TestClient(app).get(
        "/documents/lab-trends", params={"session_id": "session-synthetic"}
    )

    assert response.status_code == 200
    assert response.json() == {"series": []}
    assert services.documents.patient_ids == ["patient-synthetic"]
    operation = app.openapi()["paths"]["/documents/lab-trends"]["get"]
    assert [parameter["name"] for parameter in operation["parameters"]] == [
        "session_id"
    ]
