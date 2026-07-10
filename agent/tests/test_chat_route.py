"""E9 — POST /chat serves the verify-then-flush UC1 brief over HTTP (§3 UC1, §5, §5a).

Proves the HTTP path returns ONLY verified content (an unsupported claim is dropped end-to-end
through the real orchestrator + verifier, not just in unit tests), carries the correlation id,
and maps the session lifecycle errors correctly (404/401/503-fail-closed/403-cross-patient).
Services are injected on `app.state.services`, so no live OpenEMR / Anthropic is needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, ToolUseBlock, Usage
from app.orchestrator.loop import BriefResult, Orchestrator, ToolRegistry
from app.session.store import (
    CrossPatientError,
    Session,
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)
from app.tools.contracts import MedicationRecord, ToolResult, ToolStatus

PID = "a234b786-539a-4f9a-96a0-432293226f02"


def _session(patient_id=PID) -> Session:
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    return Session(session_id="sess-1", clinician_sub="clin-1", patient_id=patient_id,
                   created_at=now, last_activity_at=now, token_expires_at=now + timedelta(hours=1),
                   idle_timeout_s=1800, turn_cap=20)


class _SubmitClaimsProvider:
    model = "claude-sonnet-4-6"

    def __init__(self, claims):
        self._claims = claims

    async def complete(self, *, system, messages, tools):
        return LLMResponse(content=[ToolUseBlock(id="tu1", name="submit_claims",
                                                 input={"claims": self._claims})],
                           stop_reason="tool_use", usage=Usage(input_tokens=5, output_tokens=2),
                           model=self.model)


class _FakeServices:
    """Minimal ChatService: a resolvable session + a run_brief backed by the REAL orchestrator."""

    def __init__(self, *, session=None, resolve_error=None):
        self._session = session
        self._resolve_error = resolve_error

    async def resolve_session(self, session_id):
        if self._resolve_error is not None:
            raise self._resolve_error
        return self._session

    async def run_brief(self, session, message, *, request_url):
        # Real serving tail: a metformin-500 packet; the model submits a supported (500 mg) and
        # an unsupported (5000 mg) claim → verify-then-flush must drop the unsupported one.
        packet = build_evidence_packet(session.patient_id, {"get_active_medications": ToolResult(
            tool="get_active_medications", status=ToolStatus.OK,
            records=[MedicationRecord(resource_id="m1", name="metformin", dose_text="500 mg")])})
        eid = packet.by_type("MedicationRequest")[0].evidence_id
        prov = _SubmitClaimsProvider([
            {"type": "medication", "name": "metformin", "dose": "500 mg", "evidence_ids": [eid]},
            {"type": "medication", "name": "metformin", "dose": "5000 mg", "evidence_ids": [eid]},
        ])
        return await Orchestrator(prov).run_previsit_brief(packet, message, tools=ToolRegistry([]))


def _client(services, complete_env):
    from app.main import create_app
    return TestClient(create_app(services=services, readiness_checks=[]))


def test_chat_serves_only_verified_content_over_http(complete_env):
    client = _client(_FakeServices(session=_session()), complete_env)
    resp = client.post("/chat", json={"session_id": "sess-1", "message": "brief"},
                       headers={"X-Copilot-Request-Id": "corr-xyz"})
    assert resp.status_code == 200
    body = resp.json()
    assert "500 mg" in body["brief"]          # the supported claim is served
    assert "5000" not in body["brief"]        # the unsupported claim was dropped (verify-then-flush)
    assert body["source"] == "llm"
    assert "blocked" in [v.lower() for v in body["verdicts"]]  # the drop is recorded as a verdict
    assert body["correlation_id"] == "corr-xyz"                # correlation id carried through


def test_chat_session_not_found_is_404(complete_env):
    client = _client(_FakeServices(resolve_error=SessionNotFound("sess-x")), complete_env)
    assert client.post("/chat", json={"session_id": "sess-x"}).status_code == 404


def test_chat_expired_session_is_401(complete_env):
    client = _client(_FakeServices(resolve_error=SessionExpiredError("sess-1")), complete_env)
    assert client.post("/chat", json={"session_id": "sess-1"}).status_code == 401


def test_chat_session_store_down_fails_closed_503(complete_env):
    client = _client(_FakeServices(resolve_error=SessionStoreUnavailable("down")), complete_env)
    assert client.post("/chat", json={"session_id": "sess-1"}).status_code == 503


def test_chat_cross_patient_request_refused_403(complete_env):
    client = _client(_FakeServices(session=_session()), complete_env)
    resp = client.post("/chat", json={"session_id": "sess-1", "patient_id": "some-other-patient"})
    assert resp.status_code == 403  # the session pin refuses a different patient (F-S.2)
