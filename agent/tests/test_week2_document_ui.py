"""Week 2 document-write UI contract (W2-D3/D6/D9/D10; §2a/§5).

The Week 1 pre-visit application remains at ``/app``.  A distinct SMART launch state
must land on the Week 2 upload/extraction/readback/citation workbench, and every report
read stays pinned to the launched patient.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.documents import DocumentStatus
from app.schemas.extraction import ExtractionArtifact, LabPdfExtraction
from app.schemas.extraction_report import DocumentExtractionReport
from app.session.store import Session


def _session(patient_id: str = "patient-synthetic") -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id="session-synthetic",
        clinician_sub="Practitioner/clinician-synthetic",
        patient_id=patient_id,
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
    )


class _LaunchServices:
    def __init__(self) -> None:
        self.destinations: list[str] = []

    def begin_launch(self, *, launch=None, destination="week1") -> str:
        self.destinations.append(destination)
        state = "w2.synthetic-state" if destination == "week2" else "synthetic-state"
        return f"https://openemr.test/oauth2/default/authorize?state={state}"

    async def complete_callback(self, *, code: str, state: str) -> Session:
        assert code == "synthetic-code"
        return _session()

    async def complete_callback_with_destination(self, *, code: str, state: str):
        session = await self.complete_callback(code=code, state=state)
        destination = "week2" if state == "w2.synthetic-state" else "week1"
        return session, destination

    async def resolve_session(self, session_id: str) -> Session:
        assert session_id == "session-synthetic"
        return _session()


def _app_client(complete_env, services: object) -> TestClient:
    from app.config import get_settings
    from app.main import create_app

    return TestClient(
        create_app(
            settings=get_settings(), services=services, readiness_checks=[]
        )
    )


def test_week1_and_week2_smart_launches_have_distinct_callback_destinations(
    complete_env,
):
    services = _LaunchServices()
    with _app_client(complete_env, services) as client:
        week1_launch = client.get("/launch", follow_redirects=False)
        week2_launch = client.get("/week2/launch", follow_redirects=False)
        week1_callback = client.get(
            "/callback",
            params={"code": "synthetic-code", "state": "synthetic-state"},
            follow_redirects=False,
        )
        week2_callback = client.get(
            "/callback",
            params={"code": "synthetic-code", "state": "w2.synthetic-state"},
            follow_redirects=False,
        )

    assert week1_launch.status_code == 302
    assert week2_launch.status_code == 302
    assert services.destinations == ["week1", "week2"]
    assert week1_callback.headers["location"] == "/app?sid=session-synthetic"
    assert week2_callback.headers["location"] == "/week2?sid=session-synthetic"


def test_week2_page_is_separate_and_embeds_only_server_pinned_context(complete_env):
    services = _LaunchServices()
    with _app_client(complete_env, services) as client:
        response = client.get("/week2", params={"sid": "session-synthetic"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert "Week 2 · Document Write" in response.text
    assert "Upload clinical document" in response.text
    assert "OpenEMR readback" in response.text
    assert "pending clinical review" in response.text
    assert "Pre-visit brief" not in response.text
    assert '"patient_id":"patient-synthetic"' in response.text
    assert 'name="patient_id"' not in response.text
    assert "innerHTML" not in response.text


def test_week2_page_disables_document_write_for_unattested_selected_patient():
    from app.routes.week2_ui import router

    class Services:
        settings = SimpleNamespace(
            w2_document_runtime_enabled=True,
            openemr_legacy_patient_uuid="patient-attested",
            openemr_legacy_encounter_uuid="encounter-attested",
        )

        async def resolve_session(self, session_id: str) -> Session:
            assert session_id == "session-synthetic"
            return _session(patient_id="patient-selected")

    app = FastAPI()
    app.state.services = Services()
    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/week2", params={"sid": "session-synthetic"})

    assert response.status_code == 200
    assert '"write_path_attested":false' in response.text
    assert "Document write is unavailable for this selected chart" in response.text
    assert 'byId("upload").disabled = true' in response.text
    assert "patient-attested" not in response.text
    assert "encounter-attested" not in response.text


class _Documents:
    def __init__(self) -> None:
        self.patient_seen: str | None = None

    async def status(self, session: Session, document_id: str) -> DocumentStatus:
        self.patient_seen = session.patient_id
        return DocumentStatus(
            document_id=document_id,
            state="complete",
            reason=None,
            correlation_id="corr-synthetic",
            updated_ts="2026-07-15T12:00:00+00:00",
            fields_grounded=0,
            fields_unsupported=0,
            attempt_count=1,
            next_retry_at=None,
        )

    async def extraction_report(
        self, session: Session, document_id: str
    ) -> DocumentExtractionReport:
        self.patient_seen = session.patient_id
        return DocumentExtractionReport(
            document_id=document_id,
            doc_type="lab_pdf",
            state="complete",
            fields_grounded=0,
            fields_unsupported=0,
            fields=[],
        )


class _DocumentServices:
    def __init__(self) -> None:
        self.documents = _Documents()

    async def resolve_session(self, session_id: str) -> Session:
        assert session_id == "session-synthetic"
        return _session()


def test_typed_extraction_report_route_is_patient_pinned():
    from app.routes.documents import router

    services = _DocumentServices()
    app = FastAPI()
    app.state.services = services
    app.include_router(router)

    with TestClient(app) as client:
        response = client.get(
            "/documents/document-synthetic/extraction-report",
            params={"session_id": "session-synthetic"},
        )

    assert response.status_code == 200
    report = DocumentExtractionReport.model_validate(response.json())
    assert report.document_id == "document-synthetic"
    assert report.doc_type == "lab_pdf"
    assert services.documents.patient_seen == "patient-synthetic"


def test_week2_page_contains_the_closed_document_workflow_and_overlay_math(complete_env):
    services = _LaunchServices()
    with _app_client(complete_env, services) as client:
        response = client.get("/week2", params={"sid": "session-synthetic"})

    page = response.text
    assert "/documents" in page
    assert "/status" in page
    assert "/extraction-report" in page
    assert "/readback-verification" in page
    assert "/pages/" in page
    assert "/chat" in page
    assert "text/event-stream" in page
    assert "uploaded_document" in page
    assert "patient_record" in page
    assert "guideline" in page
    assert "bbox.x0 * width" in page
    assert "bbox.y0 * height" in page
    assert "UNSUPPORTED" in page
