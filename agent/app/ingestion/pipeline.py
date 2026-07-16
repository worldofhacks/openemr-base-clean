"""Concrete persisted-source extraction pipeline (W2-D1/D3/D9/D10; §2/§3/§5)."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable, Mapping
from typing import AsyncIterator, Protocol, cast

from pydantic import BaseModel, ValidationError

from app.grounding.verifier import GroundingOutcome, GroundingSummary, GroundingVerifier
from app.ingestion.artifacts import ArtifactStore
from app.ingestion.image_reader import read_image_words_and_boxes
from app.ingestion.reader import WordsBoxes, read_pdf_bytes_words_and_boxes
from app.ingestion.repository import DocumentRecord, DocumentRepository, DocumentType
from app.ingestion.telemetry import DocumentTelemetry, StageSpan
from app.observability.events import IngestionStageCode
from app.orchestrator.workers.intake_extractor import PersistedExtraction
from app.schemas.documents import FailureReason
from app.schemas.extraction import (
    ExtractionArtifact,
    GroundedField,
    IntakeFormExtraction,
    LabPdfExtraction,
    MedicationListExtraction,
)
from app.schemas.writeback import WriteLeg, WriteState
from app.writeback.intents import ExactlyOnceWriter, IntentSpec
from app.writeback.ranges import build_vital_writes
from app.writeback.transports import DocumentWritePayload, VitalWritePayload

Extraction = LabPdfExtraction | IntakeFormExtraction | MedicationListExtraction
StageCallback = Callable[[str], Awaitable[None] | None]
WordsReader = Callable[[DocumentRecord, bytes], WordsBoxes]


class SourceLoader(Protocol):
    async def fetch(self, record: DocumentRecord) -> bytes: ...


class StrictVlmExtractor(Protocol):
    async def extract(
        self,
        *,
        doc_type: DocumentType,
        source: bytes,
        words_boxes: WordsBoxes,
        source_document_id: str,
    ) -> Extraction | Mapping[str, object]: ...


class PipelineFailure(RuntimeError):
    def __init__(self, reason: FailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class DocumentExtractionPipeline:
    """Load, strictly extract, locally reground, persist, then reconcile writes.

    The injected VLM may only propose values.  Every ``GroundedField`` is rebuilt by the
    local verifier before the canonical artifact is persisted.  A retry reads that
    persisted artifact and resumes exactly-once write legs without another VLM call.
    """

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        source_loader: SourceLoader,
        vlm_extractor: StrictVlmExtractor,
        artifact_writer: ExactlyOnceWriter,
        vital_writer: ExactlyOnceWriter | None,
        artifact_store: ArtifactStore,
        words_reader: WordsReader | None = None,
        agent_version: str,
    ) -> None:
        self._repository = repository
        self._source_loader = source_loader
        self._vlm = vlm_extractor
        self._artifact_writer = artifact_writer
        self._vital_writer = vital_writer
        self._artifact_store = artifact_store
        self._words_reader = words_reader or _read_words
        self._agent_version = agent_version
        self._verifier = GroundingVerifier()

    async def extract_document(
        self,
        document_ref: str,
        *,
        patient_ref: str,
        correlation_id: str,
        on_stage: StageCallback | None = None,
        telemetry: DocumentTelemetry | None = None,
    ) -> PersistedExtraction:
        success = False
        try:
            record = await self._repository.get(document_ref)
            if _patient_id(patient_ref) != record.patient_id:
                raise PipelineFailure(FailureReason.PATIENT_MISMATCH)

            refs = await self._artifact_store.refs_for_document(record.document_id)
            artifact: ExtractionArtifact
            if refs is None:
                artifact = await self._extract(
                    record,
                    correlation_id=correlation_id,
                    on_stage=on_stage,
                    telemetry=telemetry,
                )
                refs = await self._artifact_store.persist(artifact)
            else:
                resolved = self._artifact_store.resolve(refs.artifact_ref)
                if not isinstance(resolved, ExtractionArtifact):
                    raise PipelineFailure(FailureReason.SCHEMA_VIOLATION)
                artifact = resolved
                summary = artifact.grounding_summary
                if telemetry is not None:
                    telemetry.record_grounding(
                        fields_grounded=int(summary.get("fields_grounded", 0)),
                        fields_unsupported=int(summary.get("fields_unsupported", 0)),
                    )

            await _stage(on_stage, "writing")
            await self._write_artifact(record, artifact, telemetry=telemetry)
            await self._write_vitals(record, artifact, telemetry=telemetry)
            summary = artifact.grounding_summary
            success = True
            return PersistedExtraction(
                artifact_ref=refs.artifact_ref,
                citation_refs=refs.citation_refs,
                fields_grounded=int(summary.get("fields_grounded", 0)),
                fields_unsupported=int(summary.get("fields_unsupported", 0)),
            )
        finally:
            if telemetry is not None:
                telemetry.finish(success=success)

    async def _extract(
        self,
        record: DocumentRecord,
        *,
        correlation_id: str,
        on_stage: StageCallback | None,
        telemetry: DocumentTelemetry | None,
    ) -> ExtractionArtifact:
        try:
            async with _observed_stage(telemetry, "source_readback"):
                source = await self._source_loader.fetch(record)
        except Exception as exc:
            raise PipelineFailure(FailureReason.STORAGE_WRITE_FAILED) from exc
        if not hashlib.sha256(source).hexdigest() == record.content_hash:
            raise PipelineFailure(FailureReason.BINARY_READBACK_UNSAFE)

        try:
            async with _observed_stage(telemetry, "ocr"):
                words_boxes = self._words_reader(record, source)
        except Exception as exc:
            raise PipelineFailure(FailureReason.OCR_FAILED) from exc
        try:
            async with _observed_stage(telemetry, "vlm"):
                proposed = await self._vlm.extract(
                    doc_type=record.doc_type,
                    source=source,
                    words_boxes=words_boxes,
                    source_document_id=record.document_id,
                )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise PipelineFailure(FailureReason.VLM_TIMEOUT) from exc
        except PipelineFailure:
            raise
        except Exception as exc:
            raise PipelineFailure(FailureReason.VLM_UNAVAILABLE) from exc

        try:
            async with _observed_stage(telemetry, "schema_parse"):
                extraction = _strict_extraction(record, proposed)
        except ValidationError as exc:
            raise PipelineFailure(FailureReason.SCHEMA_VIOLATION) from exc
        except (TypeError, ValueError) as exc:
            raise PipelineFailure(FailureReason.DOC_TYPE_MISMATCH) from exc

        await _stage(on_stage, "grounding")
        async with _observed_stage(telemetry, "grounding"):
            grounded, outcomes = _reground(
                extraction,
                words_boxes=words_boxes,
                document_id=record.document_id,
                verifier=self._verifier,
            )
        summary = GroundingSummary.from_outcomes(outcomes)
        if telemetry is not None:
            telemetry.record_grounding(
                fields_grounded=summary.fields_grounded,
                fields_unsupported=summary.fields_unsupported,
            )
        return ExtractionArtifact(
            artifact_version=(2 if record.doc_type == "medication_list" else 1),
            document_id=record.document_id,
            content_hash=record.content_hash,
            correlation_id=record.correlation_id or correlation_id,
            doc_type=record.doc_type,
            extraction=cast(Extraction, grounded),
            grounding_summary={
                "fields_grounded": summary.fields_grounded,
                "fields_unsupported": summary.fields_unsupported,
            },
            # Stable across retries so the permanent payload fingerprint cannot drift.
            created_ts=record.created_ts,
            agent_version=self._agent_version,
        )

    async def _write_artifact(
        self,
        record: DocumentRecord,
        artifact: ExtractionArtifact,
        *,
        telemetry: DocumentTelemetry | None,
    ) -> None:
        content = artifact.model_dump_json(warnings=False).encode("utf-8")
        marker = f"document:{record.document_id}:artifact:v{artifact.artifact_version}"
        spec = IntentSpec(
            patient_id=record.patient_id,
            document_id_or_content_hash=record.document_id,
            leg=WriteLeg.EXTRACTION_ARTIFACT,
            version=artifact.artifact_version,
            field_id="artifact",
            correlation_marker=marker,
            payload_hash=hashlib.sha256(content).hexdigest(),
        )
        try:
            async with _observed_stage(telemetry, "artifact_write") as span:
                result = await self._artifact_writer.execute(
                    spec,
                    payload=DocumentWritePayload(
                        filename=f"{marker}-extraction.json",
                        content_type="application/json",
                        content=content,
                    ),
                )
        except Exception as exc:
            raise PipelineFailure(FailureReason.WRITEBACK_FAILED) from exc
        if telemetry is not None:
            telemetry.record_write_result(
                WriteLeg.EXTRACTION_ARTIFACT, result, latency_ms=span.latency_ms
            )
        _require_verified(result.state, result.verified, result.failure_reason)

    async def _write_vitals(
        self,
        record: DocumentRecord,
        artifact: ExtractionArtifact,
        *,
        telemetry: DocumentTelemetry | None,
    ) -> None:
        extraction = artifact.extraction
        if not isinstance(extraction, IntakeFormExtraction):
            return
        mapping = build_vital_writes(
            extraction.vitals,
            encounter_id=record.encounter_id,
            correlation_marker=record.correlation_id,
        )
        if mapping.writes and self._vital_writer is None:
            raise PipelineFailure(FailureReason.WRITEBACK_FAILED)
        for candidate in mapping.writes:
            assert record.encounter_id is not None
            marker = record.correlation_id
            clinical = candidate.payload.model_dump(
                mode="json", exclude={"note"}, exclude_none=True
            )
            payload_hash = hashlib.sha256(
                json.dumps(clinical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            values = candidate.payload.model_dump(mode="json", exclude_none=True)
            values["note"] = f"copilot-intent:{marker};payload:{payload_hash[:12]}"
            spec = IntentSpec(
                patient_id=record.patient_id,
                document_id_or_content_hash=record.document_id,
                leg=WriteLeg.VITAL,
                version=artifact.artifact_version,
                field_id=candidate.field_id,
                correlation_marker=marker,
                payload_hash=payload_hash,
            )
            try:
                assert self._vital_writer is not None
                async with _observed_stage(telemetry, "vital_write") as span:
                    result = await self._vital_writer.execute(
                        spec,
                        payload=VitalWritePayload(record.encounter_id, values),
                    )
            except Exception as exc:
                raise PipelineFailure(FailureReason.WRITEBACK_FAILED) from exc
            if telemetry is not None:
                telemetry.record_write_result(
                    WriteLeg.VITAL, result, latency_ms=span.latency_ms
                )
            _require_verified(result.state, result.verified, result.failure_reason)


def _read_words(record: DocumentRecord, source: bytes) -> WordsBoxes:
    if record.content_type == "application/pdf":
        return read_pdf_bytes_words_and_boxes(source)
    return read_image_words_and_boxes(source)


def _patient_id(patient_ref: str) -> str:
    prefix = "patient:"
    return patient_ref[len(prefix) :] if patient_ref.startswith(prefix) else patient_ref


def _strict_extraction(
    record: DocumentRecord, proposed: Extraction | Mapping[str, object]
) -> Extraction:
    expected: (
        type[LabPdfExtraction]
        | type[IntakeFormExtraction]
        | type[MedicationListExtraction]
    )
    if record.doc_type == "lab_pdf":
        expected = LabPdfExtraction
    elif record.doc_type == "intake_form":
        expected = IntakeFormExtraction
    else:
        expected = MedicationListExtraction
    if isinstance(proposed, BaseModel):
        data = proposed.model_dump(round_trip=True)
    elif isinstance(proposed, Mapping):
        data = dict(proposed)
    else:
        raise TypeError("VLM extractor returned a non-schema value")
    extraction = expected.model_validate(data, strict=True)
    if extraction.source_document_id != record.document_id:
        raise ValueError("VLM source document does not match the claimed job")
    return extraction


def _reground(
    extraction: Extraction,
    *,
    words_boxes: WordsBoxes,
    document_id: str,
    verifier: GroundingVerifier,
) -> tuple[Extraction, list[GroundingOutcome[object]]]:
    outcomes: list[GroundingOutcome[object]] = []

    def visit(value: object, path: str) -> object:
        if isinstance(value, GroundedField):
            outcome = verifier.reground_candidate(
                value,
                words_boxes=words_boxes,
                source_document_id=document_id,
                field_id=path,
            )
            outcomes.append(cast(GroundingOutcome[object], outcome))
            return outcome.field
        if isinstance(value, BaseModel):
            updates = {
                name: visit(getattr(value, name), f"{path}.{name}" if path else name)
                for name in type(value).model_fields
            }
            return value.model_copy(update=updates)
        if isinstance(value, list):
            return [visit(item, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    return cast(Extraction, visit(extraction, "")), outcomes


async def _stage(callback: StageCallback | None, state: str) -> None:
    if callback is None:
        return
    result = callback(state)
    if inspect.isawaitable(result):
        await result


@asynccontextmanager
async def _observed_stage(
    telemetry: DocumentTelemetry | None, stage: IngestionStageCode
) -> AsyncIterator[StageSpan]:
    """Use the same timing seam with or without an installed event sink."""

    if telemetry is not None:
        async with telemetry.stage(stage) as span:
            yield span
        return
    span = StageSpan(stage)
    started = time.perf_counter()
    try:
        yield span
    finally:
        span.latency_ms = max((time.perf_counter() - started) * 1000, 0.0)


def _require_verified(
    state: WriteState, verified: bool, reason: FailureReason | None
) -> None:
    if state is WriteState.COMPLETE and verified:
        return
    raise PipelineFailure(reason or FailureReason.WRITEBACK_VERIFY_FAILED)
