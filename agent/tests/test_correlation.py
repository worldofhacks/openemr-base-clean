"""E1.3 — structured JSON logging + correlation-ID middleware (§3.1, §7, D10-rev).

Every request carries a correlation id (inbound X-Copilot-Request-Id is honored,
else one is minted), it is echoed on the response, propagated to log lines as JSON,
and made available for outbound FHIR calls as the X-Copilot-Request-Id header
(D10-rev: the id joins the agent's own Langfuse trace; there is no hard api_log
join). Logs must be JSON and must not carry PHI in the message field.
"""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

HEADER = "X-Copilot-Request-Id"


def test_response_carries_a_generated_correlation_id(complete_env):
    from app.main import create_app

    with TestClient(create_app(readiness_checks=[])) as client:
        resp = client.get("/")
    assert resp.headers.get(HEADER)  # a non-empty id was minted
    assert len(resp.headers[HEADER]) >= 8


def test_inbound_correlation_id_is_propagated_not_regenerated(complete_env):
    from app.main import create_app

    with TestClient(create_app(readiness_checks=[])) as client:
        resp = client.get("/", headers={HEADER: "caller-supplied-123"})
    assert resp.headers[HEADER] == "caller-supplied-123"


def test_request_emits_json_log_with_correlation_id(complete_env):
    from app.logging import configure_logging
    from app.main import create_app

    stream = io.StringIO()
    configure_logging(stream=stream, level=logging.INFO)

    with TestClient(create_app(readiness_checks=[])) as client:
        resp = client.get("/", headers={HEADER: "log-corr-999"})

    lines = [ln for ln in stream.getvalue().splitlines() if ln.strip()]
    records = [json.loads(ln) for ln in lines]
    req_logs = [r for r in records if r.get("correlation_id") == "log-corr-999"]
    assert req_logs, "expected a JSON log line tagged with the request correlation id"
    # JSON structured — each line parses; message field is a plain string, no PHI.
    for r in req_logs:
        assert isinstance(r["message"], str)
        assert "level" in r and "correlation_id" in r


def test_logging_does_not_emit_phi_in_message(complete_env):
    from app.logging import configure_logging, get_logger

    stream = io.StringIO()
    configure_logging(stream=stream, level=logging.INFO)
    log = get_logger("test")
    # PHI-like values belong in structured context we choose NOT to log at INFO,
    # never interpolated into the message (PSR-3-style discipline, CLAUDE.md).
    log.info("fhir_read_complete", extra={"resource": "Condition", "count": 19})
    record = json.loads(stream.getvalue().splitlines()[-1])
    assert record["message"] == "fhir_read_complete"
    assert "Condition" not in record["message"]  # structured, not interpolated


def test_outbound_headers_carry_correlation_id(complete_env):
    from app.middleware.correlation import correlation_id_var, outbound_headers

    token = correlation_id_var.set("outbound-corr-42")
    try:
        headers = outbound_headers()
        assert headers[HEADER] == "outbound-corr-42"
    finally:
        correlation_id_var.reset(token)
