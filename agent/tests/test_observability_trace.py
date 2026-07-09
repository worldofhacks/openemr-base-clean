"""E7.1 — the request trace as HIPAA system-of-record (ARCHITECTURE.md §7, D5-rev, F-C.1).

OpenEMR's `api_log` cannot attribute a request to the Co-Pilot OAuth client or the
scopes it exercised (F-C.1), so the Langfuse trace is the accountability system-of-record.
Every request must therefore carry `{correlation_id, client_id, exercised_scopes, user,
patient, request_url, utc_timestamp}` plus tokens/cost and the degradation class — and it
must be PHI-minimized (D5: hashes, not raw identifiers). These tests pin that contract on
the trace value object directly (deterministic; no network, no Langfuse).
"""

from __future__ import annotations

from app.llm.provider import Usage
from app.observability.langfuse import InMemoryTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext, hash_identifier

CORR = "req-abc123"


def _acct(**over):
    base = dict(
        correlation_id=CORR,
        client_id="copilot-client-42",
        exercised_scopes=("openid", "user/Condition.read", "user/MedicationRequest.read"),
        request_url="https://agent.example/chat",
        user_id="clinician-7",
        patient_id="a234b786-539a-4f9a-96a0-432293226f02",
        utc_timestamp="2026-07-09T12:00:00+00:00",
    )
    base.update(over)
    return AccountabilityContext(**base)


def _trace(**finish_over):
    sink = InMemoryTraceSink()
    b = RequestTracer(sink).begin(_acct())
    b.record_usage(Usage(input_tokens=100, output_tokens=40, cache_read_input_tokens=80))
    kw = dict(model="claude-sonnet-4-6", fallback_kind=None, degraded=False, source="llm")
    kw.update(finish_over)
    b.finish(**kw)
    return sink.traces[0]


# --- the named F-C.1 accountability invariant --------------------------------

def test_trace_has_client_id_and_scopes():  # invariant, F-C.1 / D5 system-of-record
    t = _trace()
    assert t.client_id == "copilot-client-42"
    assert "user/Condition.read" in t.exercised_scopes and "openid" in t.exercised_scopes
    assert t.exercised_scopes  # never empty — the whole point of D5 over api_log


def test_trace_carries_correlation_url_and_timestamp():  # §3.1
    t = _trace()
    assert t.correlation_id == CORR
    assert t.request_url == "https://agent.example/chat"
    assert t.utc_timestamp == "2026-07-09T12:00:00+00:00"


# --- PHI minimization (D5: hashes, not identifiers) --------------------------

def test_patient_and_user_are_hashed_never_raw():  # invariant, D5 PHI-min
    raw_patient = "a234b786-539a-4f9a-96a0-432293226f02"
    t = _trace()
    blob = repr(t)
    assert raw_patient not in blob                 # raw MRN/patient id never in the trace
    assert "clinician-7" not in blob               # raw user id never in the trace
    assert t.patient_hash and t.patient_hash != raw_patient
    assert t.user_hash and t.user_hash != "clinician-7"


def test_hash_identifier_is_deterministic_and_distinct():
    assert hash_identifier("patient-1") == hash_identifier("patient-1")
    assert hash_identifier("patient-1") != hash_identifier("patient-2")
    assert hash_identifier("") == "" and hash_identifier(None) == ""


# --- cost/tokens + the E5 degradation taxonomy (alertable) -------------------

def test_trace_carries_tokens_and_cost():  # §7 dashboard: token cost per request
    t = _trace()
    assert t.input_tokens == 100 and t.output_tokens == 40 and t.cache_read_tokens == 80
    assert t.cost_usd > 0  # priced from the model (D4), not left at zero


def test_trace_surfaces_fallback_kind_for_alerting():  # user ask: fallback-rate alertable
    ok = _trace(fallback_kind=None, degraded=False, source="llm")
    assert ok.fallback_kind is None and ok.degraded is False and ok.source == "llm"
    degraded = _trace(fallback_kind="transient", degraded=True, source="deterministic_fallback")
    assert degraded.fallback_kind == "transient" and degraded.degraded is True


def test_trace_records_ordered_steps_with_latency():  # §7: steps + order + per-step latency
    sink = InMemoryTraceSink()
    b = RequestTracer(sink).begin(_acct())
    b.step("llm.complete", latency_ms=120.0, input_tokens=100, stop_reason="tool_use")
    b.step("tool.get_conditions", latency_ms=35.0, status="ok")
    b.step("llm.complete", latency_ms=90.0, stop_reason="end_turn")
    b.finish(model="claude-sonnet-4-6", fallback_kind=None, degraded=False, source="llm")
    steps = sink.traces[0].steps
    assert [s.name for s in steps] == ["llm.complete", "tool.get_conditions", "llm.complete"]
    assert [s.order for s in steps] == [0, 1, 2]           # order preserved
    assert all(s.latency_ms >= 0 for s in steps)
    assert steps[1].detail["status"] == "ok"              # per-step detail retained
