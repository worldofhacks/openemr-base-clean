"""Documents-boundary tests (W2-D9/D10; §2a)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from app.schemas.documents import (
    DocumentStatus,
    FailureReason,
    RetryAccepted,
    UploadAccepted,
)
from app.session.store import Session


def test_wrong_media_type_has_specific_reason():
    from app.ingestion.uploads import UploadValidationError, validate_upload

    with pytest.raises(UploadValidationError) as caught:
        validate_upload(
            filename="synthetic.txt",
            content_type="text/plain",
            data=b"synthetic",
            doc_type="lab_pdf",
        )
    assert caught.value.reason is FailureReason.UNSUPPORTED_MEDIA_TYPE


def test_size_cap_has_specific_reason():
    from app.ingestion.uploads import MAX_UPLOAD_BYTES, UploadValidationError, validate_upload

    with pytest.raises(UploadValidationError) as caught:
        validate_upload(
            filename="synthetic.pdf",
            content_type="application/pdf",
            data=b"%PDF-" + b"x" * MAX_UPLOAD_BYTES,
            doc_type="lab_pdf",
        )
    assert caught.value.reason is FailureReason.SIZE_OR_PAGE_CAP_EXCEEDED


def test_image_decode_resource_cap_has_specific_reason():
    from app.ingestion.uploads import UploadValidationError, validate_upload

    image = Image.new("1", (5_001, 5_001))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    with pytest.raises(UploadValidationError) as caught:
        validate_upload(
            filename="synthetic.png",
            content_type="image/png",
            data=buffer.getvalue(),
            doc_type="intake_form",
        )
    assert caught.value.reason is FailureReason.SIZE_OR_PAGE_CAP_EXCEEDED


def test_pdf_render_resource_cap_has_specific_reason():
    from app.ingestion.uploads import UploadValidationError, validate_upload

    writer = PdfWriter()
    writer.add_blank_page(width=4_000, height=4_000)
    buffer = BytesIO()
    writer.write(buffer)

    with pytest.raises(UploadValidationError) as caught:
        validate_upload(
            filename="synthetic.pdf",
            content_type="application/pdf",
            data=buffer.getvalue(),
            doc_type="lab_pdf",
        )
    assert caught.value.reason is FailureReason.SIZE_OR_PAGE_CAP_EXCEEDED


@pytest.mark.asyncio
async def test_route_upload_reader_stops_at_cap_and_closes_spooled_file():
    from starlette.datastructures import UploadFile

    from app.ingestion.uploads import MAX_UPLOAD_BYTES, UploadValidationError
    from app.routes.documents import _read_bounded_upload

    spool = BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1))
    upload = UploadFile(filename="synthetic.pdf", file=spool)
    with pytest.raises(UploadValidationError) as caught:
        await _read_bounded_upload(upload)

    assert caught.value.reason is FailureReason.SIZE_OR_PAGE_CAP_EXCEEDED
    assert spool.closed


@pytest.mark.parametrize("filename", ["../synthetic.pdf", "", "synthetic\x00.pdf"])
def test_unsafe_filename_has_upload_rejected_reason(filename: str):
    from app.ingestion.uploads import UploadValidationError, validate_upload

    with pytest.raises(UploadValidationError) as caught:
        validate_upload(
            filename=filename,
            content_type="application/pdf",
            data=b"%PDF-synthetic",
            doc_type="lab_pdf",
        )
    assert caught.value.reason is FailureReason.UPLOAD_REJECTED


def test_category_preflight_requires_exact_path_id_and_writable_acl():
    from app.writeback.preflight import (
        CategoryExpectation,
        CategoryMismatch,
        CategoryResolution,
        verify_category_path,
    )

    expected = CategoryExpectation(path="/AI-Source-Documents", category_id="17")
    resolution = CategoryResolution(
        path="/AI-Source-Documents", category_id="17", writable=True
    )
    assert verify_category_path(expected, resolution) == "/AI-Source-Documents"

    with pytest.raises(CategoryMismatch):
        verify_category_path(
            expected,
            CategoryResolution(
                path="/AI-Source-Documents", category_id="18", writable=True
            ),
        )


def test_transport_strips_caller_attribution_before_vital_post():
    from app.writeback.rest_client import strip_caller_attribution

    cleaned = strip_caller_attribution(
        {"bps": "120", "user": "spoof", "group": "spoof", "author": "spoof"}
    )
    assert cleaned == {"bps": "120"}


class _FakeDocumentOperations:
    def __init__(self) -> None:
        self.submissions = []
        self.retries = 0
        self.admission_duplicate = False
        self.outstanding_jobs = 0
        self.submission_duplicate = False
        self.page_error: Exception | None = None
        self.page_result: object = _DEFAULT_PAGE_RESULT

    async def admission_snapshot(self, session, content_hash):
        from app.ingestion.service import DocumentAdmissionSnapshot

        return DocumentAdmissionSnapshot(
            duplicate=self.admission_duplicate,
            outstanding_jobs=self.outstanding_jobs,
        )

    async def submit(self, session, upload, *, encounter_id, correlation_id):
        from app.ingestion.service import DocumentSubmission

        self.submissions.append((session, upload, encounter_id, correlation_id))
        return DocumentSubmission(
            accepted=UploadAccepted(
                job_id="job-synthetic-1",
                document_id="doc-synthetic-1",
                state="queued",
                status_url="/documents/doc-synthetic-1/status",
                correlation_id=correlation_id,
            ),
            duplicate=self.submission_duplicate,
        )

    async def status(self, session, document_id):
        return DocumentStatus(
            document_id=document_id,
            state="queued",
            reason=None,
            correlation_id="corr-1",
            updated_ts="2026-07-14T12:00:00+00:00",
            fields_grounded=0,
            fields_unsupported=0,
            attempt_count=0,
            next_retry_at=None,
        )

    async def retry(self, session, document_id, request, *, correlation_id):
        self.retries += 1
        return RetryAccepted(
            job_id="job-synthetic-1",
            document_id=document_id,
            state="queued",
            status_url=f"/documents/{document_id}/status",
            correlation_id=correlation_id,
        )

    async def page_png(self, session, document_id, page_number):
        from app.ingestion.pages import RenderedPage

        if session.patient_id != "patient-synthetic-a":
            from app.ingestion.service import DocumentAccessError

            raise DocumentAccessError(document_id)
        if self.page_error is not None:
            raise self.page_error
        assert page_number == 1
        if self.page_result is not _DEFAULT_PAGE_RESULT:
            return self.page_result
        return RenderedPage(content=b"\x89PNG\r\n\x1a\nsynthetic")

    async def verify_readback(self, session, document_id):
        from app.ingestion.readback import (
            BinaryReadbackVerification,
            DocumentReadbackVerification,
        )
        from app.ingestion.service import DocumentAccessError

        if session.patient_id != "patient-synthetic-a":
            raise DocumentAccessError(document_id)
        return DocumentReadbackVerification(
            document_id=document_id,
            source=BinaryReadbackVerification(
                expected_hash="a" * 64,
                observed_hash="a" * 64,
                verified=True,
            ),
            artifact=BinaryReadbackVerification(
                expected_hash="b" * 64,
                observed_hash="b" * 64,
                verified=True,
            ),
        )


_DEFAULT_PAGE_RESULT = object()


class _PatientRouteMismatchOperations(_FakeDocumentOperations):
    async def submit(self, session, upload, *, encounter_id, correlation_id):
        from app.writeback.live_gateway import PatientRouteMismatch

        raise PatientRouteMismatch("synthetic patient route is not attested")


class _EncounterRouteMismatchOperations(_FakeDocumentOperations):
    async def submit(self, session, upload, *, encounter_id, correlation_id):
        from app.writeback.live_gateway import EncounterRouteMismatch

        raise EncounterRouteMismatch("synthetic encounter route is not attested")


class _RouteRegistryUnavailableOperations(_FakeDocumentOperations):
    async def submit(self, session, upload, *, encounter_id, correlation_id):
        from app.writeback.route_attestations import RouteAttestationUnavailable

        raise RouteAttestationUnavailable("route registry unavailable")


class _FakeServices:
    def __init__(self, patient_id: str = "patient-synthetic-a") -> None:
        now = datetime.now(timezone.utc)
        self.session = Session(
            session_id="session-synthetic",
            clinician_sub="clinician-synthetic",
            patient_id=patient_id,
            created_at=now,
            last_activity_at=now,
            token_expires_at=now + timedelta(hours=1),
            idle_timeout_s=1800,
            turn_cap=20,
        )
        self.documents = _FakeDocumentOperations()
        self.resolve_error: Exception | None = None

    async def resolve_session(self, session_id):
        assert session_id == "session-synthetic"
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.session


def _documents_client(
    services: _FakeServices, *, admission: object | None = None
) -> TestClient:
    from app.routes.documents import router

    app = FastAPI()
    app.state.services = services
    if admission is not None:
        app.state.document_upload_admission = admission
    app.include_router(router)
    return TestClient(app)


def test_documents_route_enforces_patient_pin_before_submission():
    services = _FakeServices()
    response = _documents_client(services).post(
        "/documents",
        data={
            "session_id": "session-synthetic",
            "patient_id": "patient-other",
            "doc_type": "intake_form",
        },
        files={"file": ("synthetic.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "patient_mismatch"
    assert services.documents.submissions == []


def _synthetic_png() -> bytes:
    image = Image.new("RGB", (32, 24), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_intake_png_multipart_reaches_typed_submission_contract():
    services = _FakeServices()
    response = _documents_client(services).post(
        "/documents",
        data={
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "doc_type": "intake_form",
        },
        files={"file": ("synthetic-intake.png", _synthetic_png(), "image/png")},
    )

    assert response.status_code == 202
    assert response.json()["state"] == "queued"
    assert len(services.documents.submissions) == 1
    submitted = services.documents.submissions[0][1]
    assert submitted.content_type == "image/png"
    assert submitted.filename == "synthetic-intake.png"


def _tight_upload_admission(*, daily_count: int = 1, outstanding_cap: int = 1):
    from app.ingestion.admission import (
        UploadAdmissionController,
        UploadAdmissionLimits,
    )

    return UploadAdmissionController(
        limits=UploadAdmissionLimits(
            session_daily_count=daily_count,
            session_daily_bytes=10 * 1024 * 1024,
            clinician_daily_count=daily_count,
            clinician_daily_bytes=10 * 1024 * 1024,
            per_session_concurrent=1,
            global_concurrent=1,
            global_outstanding_jobs=outstanding_cap,
            max_daily_meter_keys=10,
        ),
        hash_key=b"synthetic-admission-key-for-tests",
    )


def test_upload_daily_quota_is_content_free_429_before_submission():
    services = _FakeServices()
    client = _documents_client(services, admission=_tight_upload_admission())
    request = {
        "data": {
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "doc_type": "intake_form",
        },
        "files": {
            "file": ("synthetic-intake.png", _synthetic_png(), "image/png")
        },
    }

    assert client.post("/documents", **request).status_code == 202
    rejected = client.post("/documents", **request)

    assert rejected.status_code == 429
    assert rejected.json() == {"detail": "document upload quota exceeded"}
    assert int(rejected.headers["retry-after"]) >= 1
    assert len(services.documents.submissions) == 1


def test_upload_global_job_cap_is_content_free_503_before_submission():
    services = _FakeServices()
    services.documents.outstanding_jobs = 1
    response = _documents_client(
        services, admission=_tight_upload_admission(outstanding_cap=1)
    ).post(
        "/documents",
        data={
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "doc_type": "intake_form",
        },
        files={
            "file": ("synthetic-intake.png", _synthetic_png(), "image/png")
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "document upload capacity unavailable"}
    assert services.documents.submissions == []


def test_known_duplicate_bypasses_new_work_quota_and_job_cap():
    services = _FakeServices()
    services.documents.admission_duplicate = True
    services.documents.submission_duplicate = True
    services.documents.outstanding_jobs = 100
    client = _documents_client(
        services, admission=_tight_upload_admission(outstanding_cap=1)
    )
    request = {
        "data": {
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "doc_type": "intake_form",
        },
        "files": {
            "file": ("synthetic-intake.png", _synthetic_png(), "image/png")
        },
    }

    assert client.post("/documents", **request).status_code == 200
    assert client.post("/documents", **request).status_code == 200
    assert len(services.documents.submissions) == 2


def test_dedup_race_refunds_reserved_daily_quota():
    services = _FakeServices()
    services.documents.admission_duplicate = False
    services.documents.submission_duplicate = True
    client = _documents_client(services, admission=_tight_upload_admission())
    request = {
        "data": {
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "doc_type": "intake_form",
        },
        "files": {
            "file": ("synthetic-intake.png", _synthetic_png(), "image/png")
        },
    }

    assert client.post("/documents", **request).status_code == 200
    assert client.post("/documents", **request).status_code == 200
    assert len(services.documents.submissions) == 2


def test_unattested_live_patient_route_is_typed_403_not_http_500():
    services = _FakeServices()
    services.documents = _PatientRouteMismatchOperations()
    response = _documents_client(services).post(
        "/documents",
        data={
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "doc_type": "intake_form",
        },
        files={"file": ("synthetic-intake.png", _synthetic_png(), "image/png")},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "reason": "patient_mismatch",
        "message": "selected patient is not attested for the document write path",
    }


def test_unattested_live_encounter_route_is_typed_403_before_enqueue():
    services = _FakeServices()
    services.documents = _EncounterRouteMismatchOperations()
    response = _documents_client(services).post(
        "/documents",
        data={
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "encounter_id": "encounter-synthetic-a",
            "doc_type": "intake_form",
        },
        files={"file": ("synthetic-intake.png", _synthetic_png(), "image/png")},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "reason": "encounter_mismatch",
        "message": "encounter is not attested for the pinned patient",
    }


def test_route_registry_outage_is_typed_503_before_enqueue():
    services = _FakeServices()
    services.documents = _RouteRegistryUnavailableOperations()
    response = _documents_client(services).post(
        "/documents",
        data={
            "session_id": "session-synthetic",
            "patient_id": "patient-synthetic-a",
            "doc_type": "intake_form",
        },
        files={"file": ("synthetic.png", _synthetic_png(), "image/png")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "document route attestations unavailable"


def test_documents_status_and_retry_use_typed_frozen_models():
    services = _FakeServices()
    client = _documents_client(services)

    status = client.get(
        "/documents/doc-synthetic-1/status",
        params={"session_id": "session-synthetic"},
    )
    assert status.status_code == 200
    assert status.json()["state"] == "queued"

    retry = client.post(
        "/documents/doc-synthetic-1/retry",
        params={"session_id": "session-synthetic"},
        json={"expected_state": "failed"},
    )
    assert retry.status_code == 202
    assert RetryAccepted.model_validate(retry.json()).state == "queued"
    assert services.documents.retries == 1


def test_document_page_endpoint_is_patient_pinned_png_only():
    services = _FakeServices()
    response = _documents_client(services).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"\x89PNG")

    other = _FakeServices(patient_id="patient-other")
    denied = _documents_client(other).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["reason"] == "patient_mismatch"


def test_document_page_endpoint_integrates_real_pinned_image_renderer():
    import asyncio
    import hashlib

    from app.ingestion.pages import EphemeralPageRenderer
    from app.ingestion.repository import InMemoryDocumentRepository, NewDocument

    source = _synthetic_png()
    repository = InMemoryDocumentRepository()
    record, _created = asyncio.run(
        repository.get_or_create(
            NewDocument(
                patient_id="patient-synthetic-a",
                content_hash=hashlib.sha256(source).hexdigest(),
                doc_type="intake_form",
                filename="synthetic-intake.png",
                content_type="image/png",
                encounter_id=None,
                correlation_id="corr-page-preview",
                credential_ref="credential-page-preview",
            )
        )
    )

    async def fetch_source(_record):
        return source

    services = _FakeServices()
    services.documents = EphemeralPageRenderer(
        repository,
        fetch_source=fetch_source,
    )
    response = _documents_client(services).get(
        f"/documents/{record.document_id}/pages/1",
        params={"session_id": "session-synthetic"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_document_page_endpoint_maps_expired_missing_and_unavailable_failures():
    from app.ingestion.pages import PageNotFound
    from app.session.store import SessionExpiredError
    from app.writeback.live_gateway import EncounterRouteMismatch, PatientRouteMismatch
    from app.writeback.source_loader import SourceDocumentUnavailable

    expired = _FakeServices()
    expired.resolve_error = SessionExpiredError("session-synthetic")
    expired_response = _documents_client(expired).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert expired_response.status_code == 401
    assert expired_response.json() == {"detail": "session expired"}

    missing = _FakeServices()
    missing.documents.page_error = PageNotFound(1)
    missing_response = _documents_client(missing).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert missing_response.status_code == 404
    assert missing_response.json() == {"detail": "page not found"}

    patient_mismatch = _FakeServices()
    patient_mismatch.documents.page_error = PatientRouteMismatch("patient-synthetic-a")
    patient_mismatch_response = _documents_client(patient_mismatch).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert patient_mismatch_response.status_code == 403
    assert patient_mismatch_response.json()["detail"] == {
        "reason": "patient_mismatch",
        "message": "selected patient is not attested for the document write path",
    }

    encounter_mismatch = _FakeServices()
    encounter_mismatch.documents.page_error = EncounterRouteMismatch(
        "encounter-synthetic-a"
    )
    encounter_mismatch_response = _documents_client(encounter_mismatch).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert encounter_mismatch_response.status_code == 403
    assert encounter_mismatch_response.json()["detail"] == {
        "reason": "encounter_mismatch",
        "message": "encounter is not attested for the pinned patient",
    }

    unavailable = _FakeServices()
    unavailable.documents.page_error = SourceDocumentUnavailable("doc-synthetic-1")
    unavailable_response = _documents_client(unavailable).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert unavailable_response.status_code == 503
    assert unavailable_response.json() == {
        "detail": "source document is unavailable for rendering"
    }

    invalid_renderer = _FakeServices()
    invalid_renderer.documents.page_result = object()
    invalid_response = _documents_client(invalid_renderer).get(
        "/documents/doc-synthetic-1/pages/1",
        params={"session_id": "session-synthetic"},
    )
    assert invalid_response.status_code == 503
    assert invalid_response.json() == {"detail": "page renderer unavailable"}


def test_document_readback_verification_is_patient_pinned_and_digest_only():
    services = _FakeServices()
    response = _documents_client(services).get(
        "/documents/doc-synthetic-1/readback-verification",
        params={"session_id": "session-synthetic"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "document_id": "doc-synthetic-1",
        "source": {
            "algorithm": "sha256",
            "expected_hash": "a" * 64,
            "observed_hash": "a" * 64,
            "verified": True,
        },
        "artifact": {
            "algorithm": "sha256",
            "expected_hash": "b" * 64,
            "observed_hash": "b" * 64,
            "verified": True,
        },
    }
    serialized = response.text.casefold()
    assert "content" not in serialized
    assert "token" not in serialized

    denied = _documents_client(_FakeServices(patient_id="patient-other")).get(
        "/documents/doc-synthetic-1/readback-verification",
        params={"session_id": "session-synthetic"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["reason"] == "patient_mismatch"


@pytest.mark.asyncio
async def test_document_dedup_is_patient_scoped_and_retry_reuses_logical_job():
    from app.ingestion.repository import InMemoryDocumentRepository, NewDocument

    repo = InMemoryDocumentRepository()
    request = NewDocument(
        patient_id="patient-synthetic-a",
        content_hash="hash-synthetic",
        doc_type="intake_form",
        filename="synthetic.png",
        content_type="image/png",
        encounter_id=None,
        correlation_id="corr-1",
        credential_ref="credential-ref-1",
    )
    first, created = await repo.get_or_create(request)
    duplicate, duplicate_created = await repo.get_or_create(request)
    other, other_created = await repo.get_or_create(
        NewDocument(**{**request.__dict__, "patient_id": "patient-synthetic-b"})
    )
    assert created is True
    assert duplicate_created is False
    assert first.document_id == duplicate.document_id
    assert other_created is True
    assert other.document_id != first.document_id
    assert (
        await repo.find_by_patient_hash("patient-synthetic-a", "hash-synthetic")
    ) == first
    assert await repo.find_by_patient_hash("patient-synthetic-a", "missing") is None
    assert await repo.count_outstanding() == 2

    failed = await repo.set_state(
        first.document_id, state="failed", reason=FailureReason.STORAGE_WRITE_FAILED
    )
    assert await repo.count_outstanding() == 1
    retried = await repo.requeue_failed(
        failed.document_id, patient_id="patient-synthetic-a"
    )
    assert retried.job_id == first.job_id
    assert retried.state == "queued"
    assert await repo.count_outstanding() == 2


def test_intake_image_reader_emits_canonical_ocr_boxes():
    from app.ingestion.image_reader import read_image_words_and_boxes

    image = Image.new("RGB", (100, 50), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    def fake_ocr(_image):
        return {
            "text": ["synthetic"],
            "left": [10],
            "top": [5],
            "width": [20],
            "height": [10],
        }

    result = read_image_words_and_boxes(buffer.getvalue(), ocr_runner=fake_ocr)
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.source == "ocr"
    assert page.render_dpi == 200
    assert page.page_pixel_dims == (100, 50)
    assert page.words[0].bbox.model_dump() == {
        "x0": 0.1,
        "y0": 0.1,
        "x1": 0.3,
        "y1": 0.3,
    }
