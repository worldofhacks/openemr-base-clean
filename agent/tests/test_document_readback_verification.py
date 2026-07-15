"""Independent FHIR Binary digest attestation (W2-D1/D9/D10; §3/§5)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
from app.schemas.extraction import ExtractionArtifact, LabPdfExtraction
from app.session.store import Session


class _Artifacts:
    def __init__(self, artifact: ExtractionArtifact) -> None:
        self.artifact = artifact

    async def refs_for_document(self, document_id: str):
        from app.ingestion.artifacts import ArtifactRefs

        assert document_id == self.artifact.document_id
        return ArtifactRefs("artifact-ref", ())

    def resolve(self, ref: str):
        assert ref == "artifact-ref"
        return self.artifact


class _Gateway:
    def __init__(self, documents: dict[str, list[tuple[str, str, bytes]]]) -> None:
        self.documents = documents
        self.names: dict[str, bytes] = {}

    async def list_documents(self, *, patient_id: str, category_path: str):
        from app.writeback.gateway import DocumentRecord

        assert patient_id == "patient-synthetic"
        rows = []
        for remote_id, filename, content in self.documents[category_path]:
            self.names[remote_id] = content
            rows.append(DocumentRecord(remote_id, filename))
        return rows

    async def read_document_bytes(self, *, patient_id: str, remote_id: str):
        assert patient_id == "patient-synthetic"
        return self.names.get(remote_id)


class _Gateways:
    def __init__(self, gateway: _Gateway) -> None:
        self.gateway = gateway

    async def for_record(self, record):
        assert record.patient_id == "patient-synthetic"
        return self.gateway


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


@pytest.mark.asyncio
async def test_runtime_rereads_source_and_artifact_binary_bytes_for_digest_attestation():
    from app.ingestion.runtime import _DocumentOperationsFacade

    source = b"%PDF-1.7 synthetic source"
    source_hash = hashlib.sha256(source).hexdigest()
    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash=source_hash,
            doc_type="lab_pdf",
            filename="synthetic.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id="corr-synthetic",
            credential_ref="credential-synthetic",
        )
    )
    artifact = ExtractionArtifact(
        artifact_version=1,
        document_id=record.document_id,
        content_hash=source_hash,
        correlation_id=record.correlation_id,
        doc_type="lab_pdf",
        extraction=LabPdfExtraction(
            results=[], source_document_id=record.document_id
        ),
        grounding_summary={"fields_grounded": 0, "fields_unsupported": 0},
        created_ts=record.created_ts,
        agent_version="test",
    )
    artifact_bytes = artifact.model_dump_json(warnings=False).encode("utf-8")
    gateway = _Gateway(
        {
            "/AI-Source-Documents": [
                (
                    "source-remote",
                    f"document:{record.document_id}:source:v1-synthetic.pdf",
                    source,
                )
            ],
            "/AI-Extractions": [
                (
                    "artifact-remote",
                    f"document:{record.document_id}:artifact:v1-extraction.json",
                    artifact_bytes,
                )
            ],
        }
    )

    facade = object.__new__(_DocumentOperationsFacade)
    facade._repository = repository
    facade._artifacts = _Artifacts(artifact)
    facade._gateways = _Gateways(gateway)
    facade._settings = type(
        "Settings",
        (),
        {
            "source_document_path": "/AI-Source-Documents",
            "artifact_document_path": "/AI-Extractions",
        },
    )()

    result = await facade.verify_readback(_session(), record.document_id)

    assert result.source.verified is True
    assert result.source.expected_hash == source_hash
    assert result.source.observed_hash == source_hash
    expected_artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    assert result.artifact is not None
    assert result.artifact.verified is True
    assert result.artifact.expected_hash == expected_artifact_hash
    assert result.artifact.observed_hash == expected_artifact_hash


@pytest.mark.asyncio
async def test_runtime_readback_attestation_fails_closed_on_ambiguity_and_patient_mismatch():
    from app.ingestion.runtime import _DocumentOperationsFacade
    from app.ingestion.service import DocumentAccessError

    source = b"%PDF-1.7 synthetic source"
    source_hash = hashlib.sha256(source).hexdigest()
    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash=source_hash,
            doc_type="lab_pdf",
            filename="synthetic.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id="corr-synthetic",
            credential_ref="credential-synthetic",
        )
    )
    gateway = _Gateway(
        {
            "/AI-Source-Documents": [
                ("one", f"document:{record.document_id}:source:v1-a.pdf", source),
                ("two", f"document:{record.document_id}:source:v1-b.pdf", source),
            ],
            "/AI-Extractions": [],
        }
    )
    facade = object.__new__(_DocumentOperationsFacade)
    facade._repository = repository
    facade._artifacts = type(
        "Artifacts", (), {"refs_for_document": lambda *_: _none()}
    )()
    facade._gateways = _Gateways(gateway)
    facade._settings = type(
        "Settings",
        (),
        {
            "source_document_path": "/AI-Source-Documents",
            "artifact_document_path": "/AI-Extractions",
        },
    )()

    ambiguous = await facade.verify_readback(_session(), record.document_id)
    assert ambiguous.source.verified is False
    assert ambiguous.source.observed_hash is None

    with pytest.raises(DocumentAccessError):
        await facade.verify_readback(_session("patient-other"), record.document_id)


async def _none():
    return None
