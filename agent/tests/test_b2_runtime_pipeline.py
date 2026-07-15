"""B2 runtime pipeline/queue contracts (W2-D1/D3/D9/D10, §2/§3/§5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image

from app.ingestion.reader import NormBBox, PageWords, Word, WordsBoxes
from app.schemas.extraction import (
    Demographics,
    GroundedField,
    IntakeFormExtraction,
    IntakeVitals,
    VitalCandidate,
)
from app.schemas.workers import WorkerInput
from app.session.store import Session


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _new_document(*, patient_id: str = "patient-synthetic"):
    from app.ingestion.repository import NewDocument

    return NewDocument(
        patient_id=patient_id,
        content_hash="a" * 64,
        doc_type="intake_form",
        filename="synthetic.png",
        content_type="image/png",
        encounter_id="encounter-synthetic",
        correlation_id="corr-synthetic",
        credential_ref="credential:synthetic",
    )


@pytest.mark.asyncio
async def test_queue_claim_heartbeat_backoff_stale_recovery_and_patient_listing():
    from app.ingestion.repository import InMemoryDocumentRepository

    clock = _Clock()
    repository = InMemoryDocumentRepository(now=clock)
    record, _ = await repository.get_or_create(_new_document())
    await repository.set_state(record.document_id, state="queued")

    claimed = await repository.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None
    assert claimed.document_id == record.document_id
    assert claimed.state == "extracting"
    assert claimed.claim_owner == "worker-a"
    assert claimed.attempt_count == 1
    assert await repository.claim_next("worker-b", lease_seconds=30) is None

    clock.advance(20)
    heartbeat = await repository.heartbeat(
        record.document_id, worker_id="worker-a", lease_seconds=30
    )
    assert heartbeat.lease_expires_at == (clock() + timedelta(seconds=30)).isoformat()
    await repository.transition_claimed(
        record.document_id, worker_id="worker-a", state="grounding"
    )

    clock.advance(31)
    assert await repository.recover_stale() == 1
    recovered = await repository.get(record.document_id)
    assert recovered.state == "queued"
    assert recovered.claim_owner is None

    claimed_again = await repository.claim_next("worker-b", lease_seconds=10)
    assert claimed_again is not None and claimed_again.attempt_count == 2
    retry_at = clock() + timedelta(seconds=15)
    await repository.reschedule_claimed(
        record.document_id,
        worker_id="worker-b",
        reason="vlm_unavailable",
        next_retry_at=retry_at,
    )
    assert await repository.claim_next("worker-c", lease_seconds=10) is None
    clock.advance(16)
    claimed_third = await repository.claim_next("worker-c", lease_seconds=10)
    assert claimed_third is not None
    await repository.complete_claimed(
        record.document_id,
        worker_id="worker-c",
        fields_grounded=3,
        fields_unsupported=6,
    )

    ready = await repository.list_for_patient("patient-synthetic", state="complete")
    assert [item.document_id for item in ready] == [record.document_id]
    assert ready[0].fields_grounded == 3


@pytest.mark.asyncio
async def test_postgres_claim_query_is_atomic_skip_locked():
    from app.ingestion.repository import PostgresDocumentRepository

    class _Connection:
        def __init__(self) -> None:
            self.query = ""

        async def fetchrow(self, query, *_args):
            self.query = query
            return None

        async def close(self):
            return None

    connection = _Connection()
    repository = PostgresDocumentRepository(lambda: _return(connection))

    assert await repository.claim_next("worker-a", lease_seconds=30) is None
    assert "FOR UPDATE SKIP LOCKED" in connection.query
    assert "state='queued'" in connection.query.replace(" ", "")


@pytest.mark.asyncio
async def test_postgres_heartbeat_casts_lease_interval_parameter() -> None:
    from app.ingestion.repository import DocumentLeaseLost, PostgresDocumentRepository

    class _Connection:
        query = ""

        async def fetchrow(self, query, *_args):
            self.query = query
            return None

        async def close(self):
            return None

    connection = _Connection()
    repository = PostgresDocumentRepository(lambda: _return(connection))

    with pytest.raises(DocumentLeaseLost):
        await repository.heartbeat(
            "document-synthetic",
            worker_id="worker-synthetic",
            lease_seconds=60,
        )

    normalized = " ".join(connection.query.split())
    assert "heartbeat_at=$3::timestamptz" in normalized
    assert "lease_expires_at=$3::timestamptz +" in normalized
    assert "$4::double precision * interval '1 second'" in normalized


@pytest.mark.asyncio
async def test_postgres_document_creation_binds_typed_utc_timestamps() -> None:
    from app.ingestion.repository import PostgresDocumentRepository

    class _StopAfterInsert(Exception):
        pass

    class _Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Connection:
        timestamp: object = None

        def transaction(self):
            return _Transaction()

        async def fetchrow(self, query, *args):
            assert "INSERT INTO agent_document_dedup" in query
            self.timestamp = args[-1]
            raise _StopAfterInsert()

        async def close(self):
            return None

    connection = _Connection()
    repository = PostgresDocumentRepository(lambda: _return(connection))

    with pytest.raises(_StopAfterInsert):
        await repository.get_or_create(_new_document())

    assert isinstance(connection.timestamp, datetime)
    assert connection.timestamp.tzinfo is timezone.utc


@pytest.mark.asyncio
async def test_source_storage_lease_serializes_and_recovers_stale_owner() -> None:
    from app.ingestion.repository import (
        DocumentLeaseLost,
        InMemoryDocumentRepository,
    )

    clock = _Clock()
    repository = InMemoryDocumentRepository(now=clock)
    record, _ = await repository.get_or_create(_new_document())

    first = await repository.claim_source_storage(
        record.document_id,
        owner="request-a",
        lease_seconds=300,
    )
    assert first is not None and first.claim_owner == "request-a"
    assert (
        await repository.claim_source_storage(
            record.document_id,
            owner="request-b",
            lease_seconds=300,
        )
        is None
    )

    clock.advance(301)
    recovered = await repository.claim_source_storage(
        record.document_id,
        owner="request-b",
        lease_seconds=300,
    )
    assert recovered is not None and recovered.claim_owner == "request-b"
    queued = await repository.finish_source_storage(
        record.document_id,
        owner="request-b",
        state="queued",
    )
    assert queued.state == "queued"
    assert queued.claim_owner is None
    with pytest.raises(DocumentLeaseLost):
        await repository.finish_source_storage(
            record.document_id,
            owner="request-a",
            state="queued",
        )


@pytest.mark.asyncio
async def test_postgres_source_storage_claim_and_finish_are_owner_guarded() -> None:
    from app.ingestion.repository import DocumentLeaseLost, PostgresDocumentRepository

    class _Connection:
        queries: list[str]

        def __init__(self) -> None:
            self.queries = []

        async def fetchrow(self, query, *_args):
            self.queries.append(query)
            return None

        async def close(self):
            return None

    connection = _Connection()
    repository = PostgresDocumentRepository(lambda: _return(connection))

    assert (
        await repository.claim_source_storage(
            "document-synthetic",
            owner="request-a",
            lease_seconds=300,
        )
        is None
    )
    claim_query = " ".join(connection.queries[-1].split())
    assert "state IN ('storing','reconciling')" in claim_query
    assert "claim_owner IS NULL OR lease_expires_at <= $3" in claim_query

    with pytest.raises(DocumentLeaseLost):
        await repository.finish_source_storage(
            "document-synthetic",
            owner="request-a",
            state="queued",
        )
    finish_query = " ".join(connection.queries[-1].split())
    assert "document_id=$1 AND claim_owner=$2" in finish_query


async def _return(value):
    return value


class _Transport:
    def __init__(self, remote_prefix: str) -> None:
        self.remote_prefix = remote_prefix
        self.posts: list[object] = []

    async def discover(self, _intent):
        return []

    async def post(self, intent, payload):
        self.posts.append(payload)
        return f"{self.remote_prefix}-{intent.field_id}"

    async def verify(self, _intent, _match, _payload_hash):
        return True


@pytest.mark.asyncio
async def test_duplicate_storing_upload_reconciles_under_source_lease() -> None:
    from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
    from app.ingestion.service import DocumentCoordinator
    from app.ingestion.uploads import validate_upload
    from app.writeback.intents import ExactlyOnceWriter, InMemoryIntentRepository

    content = _png()
    upload = validate_upload(
        filename="synthetic.png",
        content_type="image/png",
        data=content,
        doc_type="intake_form",
    )
    repository = InMemoryDocumentRepository()
    stale, created = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash=upload.content_hash,
            doc_type="intake_form",
            filename=upload.filename,
            content_type=upload.content_type,
            encounter_id="encounter-synthetic",
            correlation_id="old-request",
            credential_ref="credential:synthetic",
        )
    )
    assert created and stale.state == "storing"

    transport = _Transport("source")
    coordinator = DocumentCoordinator(
        repository=repository,
        source_writer=ExactlyOnceWriter(InMemoryIntentRepository(), transport),
        encounter_belongs_to_patient=lambda _patient, _encounter: _return(True),
        credential_ref_for_session=lambda _session: _return("credential:synthetic"),
    )

    submission = await coordinator.submit(
        _session(),
        upload,
        encounter_id="encounter-synthetic",
        correlation_id="recovery-request",
    )

    assert submission.duplicate is True
    assert submission.accepted.document_id == stale.document_id
    assert submission.accepted.state == "queued"
    assert len(transport.posts) == 1


class _SourceLoader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0

    async def fetch(self, _record) -> bytes:
        self.calls += 1
        return self.content


def _unsupported(value=None, *, page: int | None = None):
    return GroundedField(value=value, page=page, grounded=False, citation=None)


class _StrictVlm:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, *, doc_type, source, words_boxes, source_document_id):
        self.calls += 1
        assert doc_type == "intake_form"
        assert source and words_boxes.pages
        measured = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        return IntakeFormExtraction(
            demographics=Demographics(
                name=_unsupported(),
                dob=_unsupported(),
                sex=_unsupported(),
                contact=_unsupported(),
            ),
            chief_concern=_unsupported(),
            current_medications=[],
            allergies=[],
            family_history=_unsupported(),
            vitals=IntakeVitals(
                weight=VitalCandidate(
                    value=_unsupported(Decimal("180.5"), page=1),
                    unit=_unsupported("lb", page=1),
                    measurement_date=_unsupported(measured, page=1),
                )
            ),
            source_document_id=source_document_id,
        )


def _words_reader(_record, _source: bytes) -> WordsBoxes:
    tokens = ("180.5", "lb", "2026-07-14T12:00:00+00:00")
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="ocr",
                render_dpi=200,
                page_pixel_dims=(1000, 1000),
                words=[
                    Word(
                        text=token,
                        bbox=NormBBox(
                            x0=0.1 + index * 0.2,
                            y0=0.1,
                            x1=0.2 + index * 0.2,
                            y1=0.2,
                        ),
                    )
                    for index, token in enumerate(tokens)
                ],
                unreadable=False,
            )
        ]
    )


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, format="PNG")
    return output.getvalue()


def _session() -> Session:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
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


@pytest.mark.asyncio
async def test_upload_queues_then_processor_grounds_persists_writes_and_resolves_refs():
    from app.ingestion.artifacts import InMemoryArtifactStore
    from app.ingestion.pipeline import DocumentExtractionPipeline
    from app.ingestion.processor import DocumentProcessor
    from app.ingestion.repository import InMemoryDocumentRepository
    from app.ingestion.service import DocumentCoordinator
    from app.ingestion.uploads import validate_upload
    from app.orchestrator.workers.intake_extractor import run_extraction_worker
    from app.schemas.extraction import ExtractionArtifact
    from app.writeback.intents import ExactlyOnceWriter, InMemoryIntentRepository

    content = _png()
    upload = validate_upload(
        filename="synthetic.png",
        content_type="image/png",
        data=content,
        doc_type="intake_form",
    )
    clock = _Clock()
    documents = InMemoryDocumentRepository(now=clock)
    source_transport = _Transport("source")
    coordinator = DocumentCoordinator(
        repository=documents,
        source_writer=ExactlyOnceWriter(InMemoryIntentRepository(), source_transport),
        encounter_belongs_to_patient=lambda _patient, _encounter: _return(True),
        credential_ref_for_session=lambda _session: _return("credential:synthetic"),
    )
    vlm = _StrictVlm()

    submission = await coordinator.submit(
        _session(),
        upload,
        encounter_id="encounter-synthetic",
        correlation_id="corr-synthetic",
    )
    assert submission.accepted.state == "queued"
    assert vlm.calls == 0  # web upload never executes the clinical pipeline

    artifact_transport = _Transport("artifact")
    vital_transport = _Transport("vital")
    artifacts = InMemoryArtifactStore()
    pipeline = DocumentExtractionPipeline(
        repository=documents,
        source_loader=_SourceLoader(content),
        vlm_extractor=vlm,
        artifact_writer=ExactlyOnceWriter(
            InMemoryIntentRepository(), artifact_transport
        ),
        vital_writer=ExactlyOnceWriter(InMemoryIntentRepository(), vital_transport),
        artifact_store=artifacts,
        words_reader=_words_reader,
        agent_version="test-runtime",
    )
    processor = DocumentProcessor(
        repository=documents,
        pipeline=pipeline,
        worker_id="worker-synthetic",
        lease_seconds=30,
        max_attempts=3,
        base_backoff_seconds=5,
        now=clock,
    )

    processed = await processor.process_once()
    assert processed is not None
    status = await documents.get(submission.accepted.document_id)
    assert status.state == "complete"
    assert (status.fields_grounded, status.fields_unsupported) == (3, 6)
    assert vlm.calls == 1
    assert len(source_transport.posts) == 1
    assert len(artifact_transport.posts) == 1
    assert len(vital_transport.posts) == 1

    refs = await artifacts.refs_for_document(status.document_id)
    assert refs is not None
    artifact = artifacts.resolve(refs.artifact_ref)
    assert isinstance(artifact, ExtractionArtifact)
    assert artifact.grounding_summary == {
        "fields_grounded": 3,
        "fields_unsupported": 6,
    }
    assert len(refs.citation_refs) == 3
    assert all(artifacts.resolve(ref) is not None for ref in refs.citation_refs)

    # The canonical graph worker seam reuses the persisted artifact and does not rerun VLM.
    output = await run_extraction_worker(
        WorkerInput(
            correlation_id="corr-graph",
            turn=0,
            patient_ref="patient:patient-synthetic",
            document_refs=[status.document_id],
            evidence_refs=[],
            request_kind="clinical_question",
        ),
        pipeline=pipeline,
    )
    assert output.artifact_refs == [refs.artifact_ref]
    assert output.citation_refs == list(refs.citation_refs)
    assert vlm.calls == 1


@pytest.mark.asyncio
async def test_processor_reschedules_then_terminally_fails_with_bounded_backoff():
    from app.ingestion.pipeline import PipelineFailure
    from app.ingestion.processor import DocumentProcessor
    from app.ingestion.repository import InMemoryDocumentRepository
    from app.schemas.documents import FailureReason

    class _FailingPipeline:
        async def extract_document(self, *_args, **_kwargs):
            raise PipelineFailure(FailureReason.VLM_UNAVAILABLE)

    clock = _Clock()
    repository = InMemoryDocumentRepository(now=clock)
    record, _ = await repository.get_or_create(_new_document())
    await repository.set_state(record.document_id, state="queued")
    processor = DocumentProcessor(
        repository=repository,
        pipeline=_FailingPipeline(),
        worker_id="worker-fail",
        lease_seconds=30,
        max_attempts=2,
        base_backoff_seconds=10,
        now=clock,
    )

    first = await processor.process_once()
    assert first is not None and first.state == "queued"
    assert first.next_retry_at == (clock() + timedelta(seconds=10)).isoformat()
    clock.advance(11)
    second = await processor.process_once()
    assert second is not None and second.state == "failed"
    assert second.reason is FailureReason.VLM_UNAVAILABLE
