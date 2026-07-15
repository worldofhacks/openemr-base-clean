"""Documents-boundary tests (W2-D9/D10; §2a)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

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
            duplicate=False,
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
        assert page_number == 1
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

    async def resolve_session(self, session_id):
        assert session_id == "session-synthetic"
        return self.session


def _documents_client(services: _FakeServices) -> TestClient:
    from app.routes.documents import router

    app = FastAPI()
    app.state.services = services
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

    failed = await repo.set_state(
        first.document_id, state="failed", reason=FailureReason.STORAGE_WRITE_FAILED
    )
    retried = await repo.requeue_failed(
        failed.document_id, patient_id="patient-synthetic-a"
    )
    assert retried.job_id == first.job_id
    assert retried.state == "queued"


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
