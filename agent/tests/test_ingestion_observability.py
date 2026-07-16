"""Focused PHI-safe observability tests for the durable document path."""

from __future__ import annotations

import hashlib

import pytest

from app.ingestion.artifacts import InMemoryArtifactStore
from app.ingestion.pipeline import DocumentExtractionPipeline
from app.ingestion.processor import DocumentProcessor
from app.ingestion.reader import PageWords, WordsBoxes
from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
from app.ingestion.service import DocumentCoordinator
from app.ingestion.telemetry import DocumentTelemetry
from app.ingestion.uploads import ValidatedUpload
from app.llm.provider import Usage
from app.observability.events import (
    EventComponent,
    EventEmitter,
    EventType,
    InMemoryEventSink,
)
from app.schemas.extraction import LabPdfExtraction
from app.writeback.intents import ExactlyOnceWriter, InMemoryIntentRepository


class _SourceLoader:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def fetch(self, _record) -> bytes:
        return self._content


class _Vlm:
    async def extract(
        self, *, doc_type, source, words_boxes, source_document_id
    ) -> LabPdfExtraction:
        assert doc_type == "lab_pdf"
        assert source and len(words_boxes.pages) == 1
        return LabPdfExtraction(results=[], source_document_id=source_document_id)


class _Transport:
    async def discover(self, _intent):
        return []

    async def post(self, intent, _payload):
        return f"remote-{intent.field_id}"

    async def verify(self, _intent, _match, _payload_hash):
        return True


class _Session:
    patient_id = "patient-synthetic"

    def authorize_patient(self, patient_id: str) -> None:
        assert patient_id == self.patient_id


def _words(_record, _source: bytes) -> WordsBoxes:
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="ocr",
                render_dpi=200,
                page_pixel_dims=(100, 100),
                words=[],
            )
        ]
    )


class _InstrumentedPipeline:
    """Mirror the production per-job wrapper while keeping this test fully local."""

    def __init__(self, repository, pipeline, events) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._events = events

    async def extract_document(
        self,
        document_ref,
        *,
        patient_ref,
        correlation_id,
        on_stage=None,
    ):
        record = await self._repository.get(document_ref)
        telemetry = DocumentTelemetry(
            self._events,
            correlation_id=record.correlation_id,
            job_id=record.job_id,
        )
        telemetry.record_usage(
            Usage(input_tokens=11, output_tokens=7), "claude-sonnet-4-6"
        )
        return await self._pipeline.extract_document(
            document_ref,
            patient_ref=patient_ref,
            correlation_id=correlation_id,
            on_stage=on_stage,
            telemetry=telemetry,
        )


async def _run_worker(events: EventEmitter):
    content = b"synthetic-observability-document"
    repository = InMemoryDocumentRepository()
    record, created = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash=hashlib.sha256(content).hexdigest(),
            doc_type="lab_pdf",
            filename="synthetic.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id="persisted-correlation",
            credential_ref="credential:synthetic",
        )
    )
    assert created
    await repository.set_state(record.document_id, state="queued")
    concrete = DocumentExtractionPipeline(
        repository=repository,
        source_loader=_SourceLoader(content),
        vlm_extractor=_Vlm(),
        artifact_writer=ExactlyOnceWriter(
            InMemoryIntentRepository(), _Transport()
        ),
        vital_writer=None,
        artifact_store=InMemoryArtifactStore(),
        words_reader=_words,
        agent_version="test-observability",
    )
    processor = DocumentProcessor(
        repository=repository,
        pipeline=_InstrumentedPipeline(repository, concrete, events),
        worker_id="worker-synthetic",
        events=events,
    )
    return await processor.process_once()


@pytest.mark.asyncio
async def test_source_upload_uses_persisted_correlation_for_events_and_duplicates() -> None:
    content = b"synthetic-upload-content"
    upload = ValidatedUpload(
        filename="synthetic.pdf",
        content_type="application/pdf",
        data=content,
        doc_type="lab_pdf",
        content_hash=hashlib.sha256(content).hexdigest(),
        page_count=1,
    )
    sink = InMemoryEventSink()
    emitter = EventEmitter(sink)
    repository = InMemoryDocumentRepository()
    coordinator = DocumentCoordinator(
        repository=repository,
        source_writer=ExactlyOnceWriter(InMemoryIntentRepository(), _Transport()),
        encounter_belongs_to_patient=lambda _patient, _encounter: _return(True),
        credential_ref_for_session=lambda _session: _return("credential:synthetic"),
        events=emitter,
    )

    first = await coordinator.submit(
        _Session(), upload, encounter_id=None, correlation_id="persisted-correlation"
    )
    first_event_count = len(sink.events)
    duplicate = await coordinator.submit(
        _Session(), upload, encounter_id=None, correlation_id="new-request-correlation"
    )

    assert first.accepted.state == "queued"
    assert duplicate.duplicate is True
    assert duplicate.accepted.correlation_id == "persisted-correlation"
    assert {
        event.attributes["stage"]
        for event in sink.events
        if event.event_type is EventType.INGESTION_STAGE
        and event.attributes["state"] == "completed"
    } == {"source_write"}
    assert EventType.WRITE_INTENT_TRANSITION in {
        event.event_type for event in sink.events[:first_event_count]
    }
    assert EventType.READBACK_COMPLETED in {
        event.event_type for event in sink.events[:first_event_count]
    }
    assert all(
        event.correlation_id == "persisted-correlation" for event in sink.events
    )
    assert "new-request-correlation" not in repr(sink.events)


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_document_worker_emits_complete_content_free_event_chain() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sink)

    result = await _run_worker(emitter)

    assert result is not None and result.state == "complete"
    types = [event.event_type for event in sink.events]
    assert EventType.JOB_CLAIMED in types
    assert EventType.QUEUE_STATE in types
    assert EventType.GROUNDING_COMPLETED in types
    assert EventType.WRITE_INTENT_TRANSITION in types
    assert EventType.READBACK_COMPLETED in types
    assert types[-1] is EventType.QUEUE_STATE

    stages = [
        event.attributes
        for event in sink.events
        if event.event_type is EventType.INGESTION_STAGE
    ]
    completed = {
        attributes["stage"]
        for attributes in stages
        if attributes["state"] == "completed"
    }
    assert completed == {
        "source_readback",
        "ocr",
        "vlm",
        "schema_parse",
        "grounding",
        "artifact_write",
    }

    summaries = [
        event
        for event in sink.events
        if event.event_type is EventType.ENCOUNTER_SUMMARY
    ]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.correlation_id == "persisted-correlation"
    assert summary.attributes["ordered_steps"] == [
        "source_readback",
        "ocr",
        "vlm",
        "schema_parse",
        "grounding",
        "artifact_write",
    ]
    assert len(summary.attributes["step_latencies_ms"]) == 6
    assert summary.attributes["input_tokens"] == 11
    assert summary.attributes["output_tokens"] == 7
    assert summary.attributes["cost_usd"] > 0
    assert summary.attributes["retrieval_hit_count"] == 0
    assert summary.attributes["extraction_grounding_rate"] == 0.0
    assert summary.attributes["verification_outcomes"] == ["complete"]
    assert all(
        event.correlation_id == "persisted-correlation" for event in sink.events
    )
    assert "patient-synthetic" not in repr(sink.events)
    assert "synthetic-observability-document" not in repr(sink.events)


@pytest.mark.asyncio
async def test_sink_outage_never_changes_document_completion() -> None:
    class _RaisingSink:
        def emit(self, _event) -> None:
            raise RuntimeError("synthetic telemetry outage")

    emitter = EventEmitter(_RaisingSink())
    result = await _run_worker(emitter)

    assert result is not None and result.state == "complete"
    assert emitter.dropped > 0


@pytest.mark.parametrize(
    ("field", "clinical_looking_value"),
    [
        ("worker", "metformin"),
        ("reason_code", "patient-12345"),
    ],
)
def test_closed_operational_vocabulary_rejects_clinical_looking_codes(
    field: str, clinical_looking_value: str
) -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sink)
    attributes = {
        "turn": 1,
        "decision": "review_critic",
        "reason_code": "critic_review_requested",
        "worker": "critic",
        "latency_ms": 1.0,
    }
    attributes[field] = clinical_looking_value

    emitted = emitter.emit(
        EventType.HANDOFF_COMPLETED,
        attributes,
        component=EventComponent.ORCHESTRATOR,
        correlation_id="correlation-synthetic",
    )

    assert emitted is None
    assert emitter.dropped == 1
    assert sink.events == []
