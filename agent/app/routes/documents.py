"""Typed authenticated document upload/status/retry endpoints (§2a, W2-D9/D10)."""

from __future__ import annotations

from threading import Lock
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile

from app.ingestion.admission import (
    UploadAdmissionController,
    UploadCapacityExceeded,
    UploadQuotaExceeded,
)
from app.ingestion.service import (
    DocumentAccessError,
    DocumentOperations,
    EncounterMismatch,
    ExtractionReportNotReady,
    ExtractionReportUnavailable,
    LabTrendsUnavailable,
    RetryConflict,
)
from app.ingestion.pages import PageNotFound, RenderedPage
from app.ingestion.readback import DocumentReadbackVerification
from app.ingestion.uploads import (
    MAX_UPLOAD_BYTES,
    UploadValidationError,
    validate_upload,
)
from app.middleware.correlation import correlation_id_var
from app.routes.openapi_contract import documented_errors, documented_response
from app.schemas.documents import (
    DocumentStatus,
    FailureReason,
    RetryAccepted,
    RetryRequest,
    UploadAccepted,
    UploadRequest,
)
from app.schemas.extraction_report import DocumentExtractionReport
from app.schemas.lab_trends import LabTrendResponse
from app.session.store import (
    CrossPatientError,
    Session,
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)
from app.writeback.live_gateway import EncounterRouteMismatch, PatientRouteMismatch
from app.writeback.route_attestations import RouteAttestationUnavailable

router = APIRouter()

_UPLOAD_READ_CHUNK_BYTES = 64 * 1024
_UPLOAD_ADMISSION_INIT_LOCK = Lock()


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
    if isinstance(exc, PatientRouteMismatch):
        return _typed_http(
            FailureReason.PATIENT_MISMATCH,
            status_code=403,
            message="selected patient is not attested for the document write path",
        )
    if isinstance(exc, EncounterRouteMismatch):
        return _typed_http(
            FailureReason.ENCOUNTER_MISMATCH,
            status_code=403,
            message="encounter is not attested for the pinned patient",
        )
    if isinstance(exc, RetryConflict):
        return HTTPException(status_code=409, detail="job is not safely retryable")
    raise exc


def _route_registry_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="document route attestations unavailable",
    )


def _upload_admission(request: Request) -> UploadAdmissionController:
    """Install exactly one process-local controller for each ASGI application."""

    controller = getattr(request.app.state, "document_upload_admission", None)
    if controller is not None:
        return controller
    with _UPLOAD_ADMISSION_INIT_LOCK:
        controller = getattr(request.app.state, "document_upload_admission", None)
        if controller is None:
            controller = UploadAdmissionController()
            request.app.state.document_upload_admission = controller
    return controller


def _upload_quota_exceeded(exc: UploadQuotaExceeded) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="document upload quota exceeded",
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


def _upload_capacity_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="document upload capacity unavailable",
    )


async def _read_bounded_upload(file: UploadFile) -> bytes:
    """Read at most the configured cap plus one byte, then always close the spool."""

    data = bytearray()
    try:
        while len(data) <= MAX_UPLOAD_BYTES:
            remaining = MAX_UPLOAD_BYTES + 1 - len(data)
            chunk = await file.read(min(_UPLOAD_READ_CHUNK_BYTES, remaining))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise UploadValidationError(
                FailureReason.SIZE_OR_PAGE_CAP_EXCEEDED,
                "upload exceeds 10 MB",
            )
        return bytes(data)
    except UploadValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to a content-free 4xx
        raise UploadValidationError(
            FailureReason.UPLOAD_REJECTED,
            "upload body could not be read",
        ) from exc
    finally:
        try:
            await file.close()
        except Exception:  # noqa: BLE001 - cleanup cannot widen the serving response
            pass


@router.post(
    "/documents",
    response_model=UploadAccepted,
    status_code=202,
    responses={
        200: documented_response(
            "Permanent patient-scoped duplicate; no second write was made.",
            model=UploadAccepted,
        ),
        202: documented_response(
            "New source accepted for bounded asynchronous processing."
        ),
        **documented_errors(401, 403, 404, 413, 415, 422, 429, 503),
    },
)
async def upload_document(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    session_id: Annotated[str, Form(min_length=1)],
    doc_type: Annotated[
        Literal["lab_pdf", "intake_form", "medication_list"], Form()
    ],
    patient_id: Annotated[str | None, Form(min_length=1)] = None,
    encounter_id: Annotated[str | None, Form()] = None,
    content_hash: Annotated[str | None, Form()] = None,
) -> UploadAccepted:
    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    if patient_id is not None:
        try:
            session.authorize_patient(patient_id)
        except CrossPatientError:
            raise _typed_http(
                FailureReason.PATIENT_MISMATCH,
                status_code=403,
                message="upload patient differs from the pinned patient",
            )
    pinned_patient_id = session.patient_id

    try:
        data = await _read_bounded_upload(file)
        upload = validate_upload(
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=data,
            doc_type=doc_type,
            claimed_content_hash=content_hash,
        )
        # Validate the frozen request metadata shape; the file remains out-of-band.
        UploadRequest(
            patient_id=pinned_patient_id,
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
        snapshot = await services.documents.admission_snapshot(
            session, upload.content_hash
        )
    except Exception:  # noqa: BLE001 - repository admission fails closed/content-free
        raise _upload_capacity_unavailable() from None

    try:
        async with _upload_admission(request).admit(
            session_id=session.session_id,
            clinician_sub=session.clinician_sub,
            byte_count=len(upload.data),
            duplicate=snapshot.duplicate,
            outstanding_jobs=snapshot.outstanding_jobs,
        ) as lease:
            try:
                submission = await services.documents.submit(
                    session,
                    upload,
                    encounter_id=encounter_id,
                    correlation_id=correlation_id_var.get(),
                )
            except RouteAttestationUnavailable:
                raise _route_registry_unavailable() from None
            except (
                DocumentAccessError,
                EncounterMismatch,
                PatientRouteMismatch,
                EncounterRouteMismatch,
            ) as exc:
                raise _map_operation_error(exc)
            if submission.duplicate and not snapshot.duplicate:
                await lease.refund_quota()
    except UploadQuotaExceeded as exc:
        raise _upload_quota_exceeded(exc) from None
    except UploadCapacityExceeded:
        raise _upload_capacity_unavailable() from None
    if submission.duplicate:
        response.status_code = 200
    return submission.accepted


@router.get(
    "/documents/lab-trends",
    response_model=LabTrendResponse,
    responses={
        200: documented_response("Neutral exact-unit lab series, possibly empty."),
        **documented_errors(401, 404, 422, 503),
    },
)
async def document_lab_trends(
    request: Request,
    session_id: Annotated[str, Query(min_length=1)],
) -> LabTrendResponse:
    """Derive neutral lab series from the session-pinned patient's verified artifacts."""

    services: DocumentRouteServices = request.app.state.services
    session = await _session(services, session_id)
    try:
        return await services.documents.lab_trends(session)
    except LabTrendsUnavailable:
        raise HTTPException(status_code=503, detail="lab trends are unavailable") from None


@router.get(
    "/documents/{document_id}/status",
    response_model=DocumentStatus,
    responses={
        200: documented_response(
            "Current durable state for a patient-pinned document."
        ),
        **documented_errors(401, 403, 404, 422, 503),
    },
)
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
    except (
        RouteAttestationUnavailable,
        PatientRouteMismatch,
        EncounterRouteMismatch,
    ):
        raise _route_registry_unavailable() from None


@router.get(
    "/documents/{document_id}/extraction-report",
    response_model=DocumentExtractionReport,
    responses={
        200: documented_response(
            "Grounded facts and structurally redacted unsupported proposals."
        ),
        **documented_errors(401, 403, 404, 409, 422, 503),
    },
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
    responses={
        202: documented_response("Failed job safely requeued."),
        **documented_errors(401, 403, 404, 409, 413, 422, 503),
    },
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


@router.get(
    "/documents/{document_id}/pages/{page_number}",
    response_class=Response,
    responses={
        200: documented_response(
            "Ephemeral patient-pinned page render.",
            private_no_store=True,
            content={
                "image/png": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        ),
        **documented_errors(401, 403, 404, 422, 503),
    },
)
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
    except (
        RouteAttestationUnavailable,
        PatientRouteMismatch,
        EncounterRouteMismatch,
    ):
        raise _route_registry_unavailable() from None
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
    responses={
        200: documented_response(
            "Content-free SHA-256 results from fresh Binary reads."
        ),
        **documented_errors(401, 403, 404, 422, 503),
    },
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
    except (
        RouteAttestationUnavailable,
        PatientRouteMismatch,
        EncounterRouteMismatch,
    ):
        raise _route_registry_unavailable() from None
