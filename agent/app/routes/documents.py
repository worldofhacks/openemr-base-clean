"""Typed authenticated document upload/status/retry endpoints (§2a, W2-D9/D10)."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile

from app.ingestion.service import (
    DocumentAccessError,
    DocumentOperations,
    EncounterMismatch,
    ExtractionReportNotReady,
    ExtractionReportUnavailable,
    RetryConflict,
)
from app.ingestion.pages import PageNotFound, RenderedPage
from app.ingestion.readback import DocumentReadbackVerification
from app.ingestion.uploads import UploadValidationError, validate_upload
from app.middleware.correlation import correlation_id_var
from app.schemas.documents import (
    DocumentStatus,
    FailureReason,
    RetryAccepted,
    RetryRequest,
    UploadAccepted,
    UploadRequest,
)
from app.schemas.extraction_report import DocumentExtractionReport
from app.session.store import (
    CrossPatientError,
    Session,
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)

router = APIRouter()


class DocumentRouteServices(Protocol):
    documents: DocumentOperations

    async def resolve_session(self, session_id: str) -> Session: ...


def _typed_http(reason: FailureReason, *, status_code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"reason": reason.value, "message": message},
    )


async def _session(services: DocumentRouteServices, session_id: str) -> Session:
    try:
        return await services.resolve_session(session_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found")
    except SessionExpiredError:
        raise HTTPException(status_code=401, detail="session expired")
    except SessionStoreUnavailable:
        raise HTTPException(status_code=503, detail="session store unavailable")


def _map_operation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DocumentAccessError):
        return _typed_http(
            FailureReason.PATIENT_MISMATCH,
            status_code=403,
            message="document does not belong to the pinned patient",
        )
    if isinstance(exc, EncounterMismatch):
        return _typed_http(
            FailureReason.ENCOUNTER_MISMATCH,
            status_code=403,
            message="encounter does not belong to the pinned patient",
        )
    if isinstance(exc, RetryConflict):
        return HTTPException(status_code=409, detail="job is not safely retryable")
    raise exc


@router.post("/documents", response_model=UploadAccepted, status_code=202)
async def upload_document(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    session_id: Annotated[str, Form(min_length=1)],
    patient_id: Annotated[str, Form(min_length=1)],
    doc_type: Annotated[Literal["lab_pdf", "intake_form"], Form()],
    encounter_id: Annotated[str | None, Form()] = None,
    content_hash: Annotated[str | None, Form()] = None,
) -> UploadAccepted:
    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    try:
        session.authorize_patient(patient_id)
    except CrossPatientError:
        raise _typed_http(
            FailureReason.PATIENT_MISMATCH,
            status_code=403,
            message="upload patient differs from the pinned patient",
        )

    try:
        data = await file.read()
    except Exception as exc:  # noqa: BLE001 - typed upload rejection at boundary
        raise _typed_http(
            FailureReason.UPLOAD_REJECTED,
            status_code=422,
            message="upload body could not be read",
        ) from exc
    try:
        upload = validate_upload(
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=data,
            doc_type=doc_type,
            claimed_content_hash=content_hash,
        )
        # Validate the frozen request metadata shape; the file remains out-of-band.
        UploadRequest(
            patient_id=patient_id,
            doc_type=doc_type,
            filename=upload.filename,
            content_hash=upload.content_hash,
        )
    except UploadValidationError as exc:
        status_code = (
            415 if exc.reason is FailureReason.UNSUPPORTED_MEDIA_TYPE else 422
        )
        raise _typed_http(exc.reason, status_code=status_code, message=str(exc))

    try:
        submission = await services.documents.submit(
            session,
            upload,
            encounter_id=encounter_id,
            correlation_id=correlation_id_var.get(),
        )
    except (DocumentAccessError, EncounterMismatch) as exc:
        raise _map_operation_error(exc)
    if submission.duplicate:
        response.status_code = 200
    return submission.accepted


@router.get("/documents/{document_id}/status", response_model=DocumentStatus)
async def document_status(
    document_id: str,
    request: Request,
    session_id: Annotated[str, Query(min_length=1)],
) -> DocumentStatus:
    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    try:
        return await services.documents.status(session, document_id)
    except DocumentAccessError as exc:
        raise _map_operation_error(exc)


@router.get(
    "/documents/{document_id}/extraction-report",
    response_model=DocumentExtractionReport,
)
async def document_extraction_report(
    document_id: str,
    request: Request,
    session_id: Annotated[str, Query(min_length=1)],
) -> DocumentExtractionReport:
    """Render only persisted grounded facts; unsupported VLM proposals stay redacted."""

    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    try:
        return await services.documents.extraction_report(session, document_id)
    except DocumentAccessError as exc:
        raise _map_operation_error(exc)
    except ExtractionReportNotReady:
        raise HTTPException(status_code=409, detail="extraction report is not complete")
    except ExtractionReportUnavailable:
        raise HTTPException(status_code=503, detail="extraction report is unavailable")


@router.post(
    "/documents/{document_id}/retry",
    response_model=RetryAccepted,
    status_code=202,
)
async def retry_document(
    document_id: str,
    retry: RetryRequest,
    request: Request,
    session_id: Annotated[str, Query(min_length=1)],
) -> RetryAccepted:
    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    try:
        return await services.documents.retry(
            session,
            document_id,
            retry,
            correlation_id=correlation_id_var.get(),
        )
    except (DocumentAccessError, RetryConflict) as exc:
        raise _map_operation_error(exc)


@router.get("/documents/{document_id}/pages/{page_number}")
async def document_page(
    document_id: str,
    page_number: int,
    request: Request,
    session_id: Annotated[str, Query(min_length=1)],
) -> Response:
    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    try:
        rendered = await services.documents.page_png(
            session, document_id, page_number
        )
    except DocumentAccessError as exc:
        raise _map_operation_error(exc)
    except PageNotFound:
        raise HTTPException(status_code=404, detail="page not found")
    if not isinstance(rendered, RenderedPage):
        raise HTTPException(status_code=503, detail="page renderer unavailable")
    return Response(
        content=rendered.content,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(
    "/documents/{document_id}/readback-verification",
    response_model=DocumentReadbackVerification,
)
async def document_readback_verification(
    document_id: str,
    request: Request,
    session_id: Annotated[str, Query(min_length=1)],
) -> DocumentReadbackVerification:
    """Return content-free digests from fresh, patient-pinned FHIR Binary reads."""

    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    try:
        return await services.documents.verify_readback(session, document_id)
    except DocumentAccessError as exc:
        raise _map_operation_error(exc)
