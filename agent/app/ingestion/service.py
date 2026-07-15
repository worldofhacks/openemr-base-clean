"""Document-ingestion service contracts and coordinator (W2-D9/D10; §3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

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


@dataclass(frozen=True)
class DocumentSubmission:
    accepted: UploadAccepted
    duplicate: bool


class DocumentOperations(Protocol):
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
    ) -> None:
        self._repository = repository
        self._source_writer = source_writer
        self._encounter_belongs = encounter_belongs_to_patient
        self._credential_ref = credential_ref_for_session
        self._page_renderer = page_renderer

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
        if created:
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
                result = await self._source_writer.execute(
                    spec,
                    payload=DocumentWritePayload(
                        filename=f"{marker}-{upload.filename}",
                        content_type=upload.content_type,
                        content=upload.data,
                    ),
                )
            except (ReconciliationConflict, ReconciliationRequired):
                record = await self._repository.set_state(
                    record.document_id, state="reconciling"
                )
            else:
                if result.state is WriteState.COMPLETE:
                    record = await self._repository.set_state(
                        record.document_id, state="queued"
                    )
                elif result.state is WriteState.UNKNOWN:
                    record = await self._repository.set_state(
                        record.document_id, state="reconciling"
                    )
                else:
                    record = await self._repository.set_state(
                        record.document_id,
                        state="failed",
                        reason=FailureReason.STORAGE_WRITE_FAILED,
                    )
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
