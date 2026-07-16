"""Document-ingestion service contracts and coordinator (W2-D9/D10; §3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

from app.ingestion.telemetry import DocumentTelemetry
from app.ingestion.uploads import ValidatedUpload
from app.ingestion.repository import (
    DocumentNotRetryable,
    DocumentPatientMismatch,
    DocumentRepository,
    NewDocument,
)
from app.schemas.documents import (
    DocumentStatus,
    FailureReason,
    RetryAccepted,
    RetryRequest,
    UploadAccepted,
)
from app.ingestion.readback import DocumentReadbackVerification
from app.schemas.extraction_report import DocumentExtractionReport
from app.schemas.lab_trends import LabTrendResponse
from app.observability.events import EventComponent, EventEmitter, EventType
from app.session.store import Session
from app.schemas.writeback import WriteLeg, WriteState
from app.writeback.intents import (
    ExactlyOnceWriter,
    IntentSpec,
    ReconciliationConflict,
    ReconciliationRequired,
)
from app.writeback.transports import DocumentWritePayload


class DocumentAccessError(Exception):
    reason = FailureReason.PATIENT_MISMATCH


class EncounterMismatch(Exception):
    reason = FailureReason.ENCOUNTER_MISMATCH


class RetryConflict(Exception):
    """The logical job is not failed or has an unresolved unknown intent."""


class ExtractionReportNotReady(Exception):
    """The logical job has not completed its verified write path yet."""


class ExtractionReportUnavailable(Exception):
    """A complete job's persisted artifact failed an integrity check."""


class LabTrendsUnavailable(Exception):
    """Completed lab artifacts could not be projected without weakening integrity."""


_SOURCE_STORAGE_LEASE_SECONDS = 300
_SOURCE_EXTENSION_BY_MEDIA_TYPE = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


@dataclass(frozen=True)
class DocumentSubmission:
    accepted: UploadAccepted
    duplicate: bool


@dataclass(frozen=True)
class DocumentAdmissionSnapshot:
    """Content-free repository facts used by pre-write HTTP admission."""

    duplicate: bool
    outstanding_jobs: int


class DocumentOperations(Protocol):
    async def admission_snapshot(
        self, session: Session, content_hash: str
    ) -> DocumentAdmissionSnapshot: ...

    async def submit(
        self,
        session: Session,
        upload: ValidatedUpload,
        *,
        encounter_id: str | None,
        correlation_id: str,
    ) -> DocumentSubmission: ...

    async def status(self, session: Session, document_id: str) -> DocumentStatus: ...

    async def retry(
        self,
        session: Session,
        document_id: str,
        request: RetryRequest,
        *,
        correlation_id: str,
    ) -> RetryAccepted: ...

    async def page_png(
        self, session: Session, document_id: str, page_number: int
    ) -> object: ...

    async def verify_readback(
        self, session: Session, document_id: str
    ) -> DocumentReadbackVerification: ...

    async def extraction_report(
        self, session: Session, document_id: str
    ) -> DocumentExtractionReport: ...

    async def lab_trends(self, session: Session) -> LabTrendResponse: ...


class PageRenderer(Protocol):
    async def page_png(
        self, session: Session, document_id: str, page_number: int
    ) -> object: ...


class DocumentCoordinator:
    """Web-facing source-document coordinator; workers handle later pipeline legs."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        source_writer: ExactlyOnceWriter,
        encounter_belongs_to_patient: Callable[[str, str], Awaitable[bool]],
        credential_ref_for_session: Callable[[Session], Awaitable[str]],
        page_renderer: PageRenderer | None = None,
        events: EventEmitter | None = None,
    ) -> None:
        self._repository = repository
        self._source_writer = source_writer
        self._encounter_belongs = encounter_belongs_to_patient
        self._credential_ref = credential_ref_for_session
        self._page_renderer = page_renderer
        self._events = events

    @staticmethod
    def _queue_age_ms(record) -> float:
        try:
            created = datetime.fromisoformat(record.created_ts)
            if created.tzinfo is None:
                return 0.0
            return max(
                (datetime.now(timezone.utc) - created).total_seconds() * 1000,
                0.0,
            )
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _emit_queue(self, record) -> None:
        if self._events is None:
            return
        self._events.emit(
            EventType.QUEUE_STATE,
            {
                "state": record.state,
                "attempt_count": record.attempt_count,
                "queue_age_ms": self._queue_age_ms(record),
            },
            component=EventComponent.API,
            job_id=record.job_id,
            correlation_id=record.correlation_id,
        )

    async def admission_snapshot(
        self, session: Session, content_hash: str
    ) -> DocumentAdmissionSnapshot:
        """Read dedup/workload authority without constructing a remote client."""

        session.authorize_patient(session.patient_id)
        existing = await self._repository.find_by_patient_hash(
            session.patient_id, content_hash
        )
        outstanding_jobs = await self._repository.count_outstanding()
        return DocumentAdmissionSnapshot(
            duplicate=existing is not None,
            outstanding_jobs=outstanding_jobs,
        )

    async def submit(
        self,
        session: Session,
        upload: ValidatedUpload,
        *,
        encounter_id: str | None,
        correlation_id: str,
    ) -> DocumentSubmission:
        session.authorize_patient(session.patient_id)
        if encounter_id is not None and not await self._encounter_belongs(
            session.patient_id, encounter_id
        ):
            raise EncounterMismatch(encounter_id)
        credential_ref = await self._credential_ref(session)
        record, created = await self._repository.get_or_create(
            NewDocument(
                patient_id=session.patient_id,
                content_hash=upload.content_hash,
                doc_type=upload.doc_type,
                filename=upload.filename,
                content_type=upload.content_type,
                encounter_id=encounter_id,
                correlation_id=correlation_id,
                credential_ref=credential_ref,
            )
        )
        self._emit_queue(record)
        telemetry = DocumentTelemetry(
            self._events,
            correlation_id=record.correlation_id,
            job_id=record.job_id,
        )
        source_owner = f"source:{correlation_id}"
        claimed = await self._repository.claim_source_storage(
            record.document_id,
            owner=source_owner,
            lease_seconds=_SOURCE_STORAGE_LEASE_SECONDS,
        )
        if claimed is not None:
            record = claimed
            marker = f"document:{record.document_id}:source:v1"
            spec = IntentSpec(
                patient_id=session.patient_id,
                document_id_or_content_hash=upload.content_hash,
                leg=WriteLeg.SOURCE_DOCUMENT,
                version=1,
                field_id="source",
                correlation_marker=marker,
                payload_hash=upload.content_hash,
            )
            try:
                async with telemetry.stage("source_write") as span:
                    result = await self._source_writer.execute(
                        spec,
                        payload=DocumentWritePayload(
                            # The remote title is a server-generated marker only. The
                            # caller's original filename is metadata, never executable
                            # content in OpenEMR's document UI.
                            filename=(
                                marker
                                + _SOURCE_EXTENSION_BY_MEDIA_TYPE[upload.content_type]
                            ),
                            content_type=upload.content_type,
                            content=upload.data,
                        ),
                    )
            except (ReconciliationConflict, ReconciliationRequired):
                telemetry.record_write_transition(
                    WriteLeg.SOURCE_DOCUMENT,
                    state="unknown",
                    verified=False,
                )
                record = await self._repository.finish_source_storage(
                    record.document_id,
                    owner=source_owner,
                    state="reconciling",
                )
            else:
                telemetry.record_write_result(
                    WriteLeg.SOURCE_DOCUMENT,
                    result,
                    latency_ms=span.latency_ms,
                )
                if result.state is WriteState.COMPLETE:
                    record = await self._repository.finish_source_storage(
                        record.document_id,
                        owner=source_owner,
                        state="queued",
                    )
                elif result.state is WriteState.UNKNOWN:
                    record = await self._repository.finish_source_storage(
                        record.document_id,
                        owner=source_owner,
                        state="reconciling",
                    )
                else:
                    record = await self._repository.finish_source_storage(
                        record.document_id,
                        owner=source_owner,
                        state="failed",
                        reason=FailureReason.STORAGE_WRITE_FAILED,
                    )
        else:
            record = await self._repository.get(record.document_id)
        self._emit_queue(record)
        return DocumentSubmission(self._accepted(record), duplicate=not created)

    async def status(self, session: Session, document_id: str) -> DocumentStatus:
        record = await self._repository.get(document_id)
        if record.patient_id != session.patient_id:
            raise DocumentAccessError(document_id)
        return record.to_status()

    async def retry(
        self,
        session: Session,
        document_id: str,
        request: RetryRequest,
        *,
        correlation_id: str,
    ) -> RetryAccepted:
        if request.expected_state != "failed":  # defensive; frozen schema already pins it
            raise RetryConflict(document_id)
        try:
            record = await self._repository.requeue_failed(
                document_id, patient_id=session.patient_id
            )
        except DocumentPatientMismatch as exc:
            raise DocumentAccessError(document_id) from exc
        except DocumentNotRetryable as exc:
            raise RetryConflict(document_id) from exc
        self._emit_queue(record)
        return RetryAccepted(
            job_id=record.job_id,
            document_id=record.document_id,
            state=record.state,
            status_url=f"/documents/{record.document_id}/status",
            correlation_id=correlation_id,
        )

    async def page_png(
        self, session: Session, document_id: str, page_number: int
    ) -> object:
        if self._page_renderer is None:
            from app.ingestion.pages import PageNotFound

            raise PageNotFound(page_number)
        return await self._page_renderer.page_png(session, document_id, page_number)

    @staticmethod
    def _accepted(record) -> UploadAccepted:
        return UploadAccepted(
            job_id=record.job_id,
            document_id=record.document_id,
            state=record.state,
            status_url=f"/documents/{record.document_id}/status",
            correlation_id=record.correlation_id,
        )
