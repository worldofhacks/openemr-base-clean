"""Authority-divergence enforcement proof (AF-P1-03; W2-REQ-57/79; PDF p.6).

The declared ledger: Agent PostgreSQL is the single authority for extraction
artifacts/refs; the OpenEMR document copy is a verified byte-digest projection;
OpenEMR remains authoritative for source documents and written vitals.

These tests mutate exactly one copy in a fixture and pin that divergence is
DETECTED and the read path FAILS CLOSED — neither the authoritative copy nor
the diverged projection is silently served as verified truth.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.ingestion.artifacts import InMemoryArtifactStore
from app.ingestion.readback import BinaryReadbackVerification
from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
from app.schemas.extraction import ExtractionArtifact, LabPdfExtraction
from app.session.store import Session


class _Gateway:
    """OpenEMR projection double: serves whatever bytes the fixture placed there."""

    def __init__(self, documents: dict[str, list[tuple[str, str, bytes]]]) -> None:
        self.documents = documents
        self.contents: dict[str, bytes] = {}

    async def list_documents(self, *, patient_id: str, category_path: str):
        from app.writeback.gateway import DocumentRecord

        assert patient_id == "patient-synthetic"
        rows = []
        for remote_id, filename, content in self.documents[category_path]:
            self.contents[remote_id] = content
            rows.append(DocumentRecord(remote_id, filename))
        return rows

    async def read_document_bytes(self, *, patient_id: str, remote_id: str):
        assert patient_id == "patient-synthetic"
        return self.contents.get(remote_id)


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


def _settings():
    return type(
        "Settings",
        (),
        {
            "source_document_path": "/AI-Source-Documents",
            "artifact_document_path": "/AI-Extractions",
        },
    )()


async def _authoritative_fixture(
    *, source: bytes
) -> tuple[InMemoryDocumentRepository, InMemoryArtifactStore, ExtractionArtifact]:
    """One admitted document plus its Postgres-authoritative extraction artifact."""

    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash=hashlib.sha256(source).hexdigest(),
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
        content_hash=record.content_hash,
        correlation_id=record.correlation_id,
        doc_type="lab_pdf",
        extraction=LabPdfExtraction(results=[], source_document_id=record.document_id),
        grounding_summary={"fields_grounded": 0, "fields_unsupported": 0},
        created_ts=record.created_ts,
        agent_version="test",
    )
    artifacts = InMemoryArtifactStore()
    await artifacts.persist(artifact)
    return repository, artifacts, artifact


def _facade(repository, artifacts, gateway):
    from app.ingestion.runtime import _DocumentOperationsFacade

    facade = object.__new__(_DocumentOperationsFacade)
    facade._repository = repository
    facade._artifacts = artifacts
    facade._gateways = _Gateways(gateway)
    facade._settings = _settings()
    return facade


@pytest.mark.asyncio
async def test_mutated_openemr_artifact_projection_is_detected_and_fails_closed():
    """Mutate the OpenEMR artifact copy only; the digest attestation must refuse it.

    The expected hash is derived exclusively from the Postgres-authoritative
    artifact; the diverged projection can never verify, and neither copy's
    content crosses the verification surface.
    """

    source = b"%PDF-1.7 synthetic source"
    repository, artifacts, artifact = await _authoritative_fixture(source=source)
    record = await repository.get(artifact.document_id)

    authoritative_bytes = artifact.model_dump_json(warnings=False).encode("utf-8")
    tampered = artifact.model_copy(update={"agent_version": "tampered"})
    tampered_bytes = tampered.model_dump_json(warnings=False).encode("utf-8")
    assert tampered_bytes != authoritative_bytes  # the fixture truly diverged

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
                    tampered_bytes,
                )
            ],
        }
    )

    result = await _facade(repository, artifacts, gateway).verify_readback(
        _session(), record.document_id
    )

    # Source copy untouched: still verifies against the durable admitted hash.
    assert result.source.verified is True
    # Diverged artifact projection: detected, fails closed, never reported verified.
    assert result.artifact is not None
    assert result.artifact.verified is False
    assert result.artifact.expected_hash == hashlib.sha256(
        authoritative_bytes
    ).hexdigest()
    assert result.artifact.observed_hash == hashlib.sha256(
        tampered_bytes
    ).hexdigest()
    assert result.artifact.observed_hash != result.artifact.expected_hash
    # The verification surface serves digests only — no silent serving of either copy.
    assert set(BinaryReadbackVerification.model_fields) == {
        "algorithm",
        "expected_hash",
        "observed_hash",
        "verified",
    }


@pytest.mark.asyncio
async def test_mutated_openemr_source_copy_is_detected_and_fails_closed():
    """Mutate the OpenEMR source Binary; attestation against the durable record hash fails."""

    source = b"%PDF-1.7 synthetic source"
    repository, artifacts, artifact = await _authoritative_fixture(source=source)
    record = await repository.get(artifact.document_id)
    mutated_source = source + b" tampered"

    gateway = _Gateway(
        {
            "/AI-Source-Documents": [
                (
                    "source-remote",
                    f"document:{record.document_id}:source:v1-synthetic.pdf",
                    mutated_source,
                )
            ],
            "/AI-Extractions": [
                (
                    "artifact-remote",
                    f"document:{record.document_id}:artifact:v1-extraction.json",
                    artifact.model_dump_json(warnings=False).encode("utf-8"),
                )
            ],
        }
    )

    result = await _facade(repository, artifacts, gateway).verify_readback(
        _session(), record.document_id
    )

    assert result.source.verified is False
    assert result.source.expected_hash == record.content_hash
    assert result.source.observed_hash == hashlib.sha256(mutated_source).hexdigest()
    assert result.artifact is not None and result.artifact.verified is True


@pytest.mark.asyncio
async def test_diverged_postgres_artifact_copy_is_never_served_by_the_report_path():
    """Mutate the Postgres artifact copy against the durable dedup record.

    The extraction-report read path must refuse to serve the diverged artifact
    (fail closed with the sanitized unavailable error) instead of silently
    serving either version. A matching control proves the same path serves when
    the copies agree.
    """

    from app.ingestion.service import ExtractionReportUnavailable

    source = b"%PDF-1.7 synthetic source"
    repository, artifacts, artifact = await _authoritative_fixture(source=source)
    record = await repository.get(artifact.document_id)
    await repository.set_state(record.document_id, state="complete")
    gateway = _Gateway({"/AI-Source-Documents": [], "/AI-Extractions": []})

    # Control: agreeing copies serve the redacted report.
    control = await _facade(repository, artifacts, gateway).extraction_report(
        _session(), record.document_id
    )
    assert control.document_id == record.document_id

    # Mutation: the resolved artifact diverges from the admitted content hash.
    diverged = artifact.model_copy(
        update={"content_hash": hashlib.sha256(b"other bytes").hexdigest()}
    )
    diverged_store = InMemoryArtifactStore()
    await diverged_store.persist(diverged)

    with pytest.raises(ExtractionReportUnavailable):
        await _facade(repository, diverged_store, gateway).extraction_report(
            _session(), record.document_id
        )
