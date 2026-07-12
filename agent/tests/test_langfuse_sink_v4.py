"""LangfuseSink emits against the INSTALLED Langfuse SDK without error — regression guard.

The sink was written against the v3 API (`start_as_current_span` / `update_trace`); the
installed SDK is v4 (`start_observation` / `propagate_attributes`), where the old calls raise.
Because tracing is a soft dependency, the tracer would SWALLOW that and emit nothing — a silent
"observability configured but zero traces". This test builds a representative trace and asserts
emit() runs clean (dropped == 0), and that the generation helpers carry real usage + cost so the
native Langfuse cost/latency widgets have data (metadata alone cannot power them).
"""

from __future__ import annotations

from app.observability.langfuse import (
    LangfuseSink,
    RequestTracer,
    _cost_details,
    _usage_details,
)
from app.observability.trace import RequestTrace, TraceStep


def _trace() -> RequestTrace:
    steps = (
        TraceStep(order=0, name="fhir.get_patient_summary", latency_ms=12.0,
                  detail={"status": "ok", "records": 1, "missing_reason": ""}),
        TraceStep(order=1, name="fhir.get_allergies", latency_ms=8.0,
                  detail={"status": "failed", "records": 0, "missing_reason": "allergies unavailable"}),
        TraceStep(order=2, name="llm.complete", latency_ms=34000.0,
                  detail={"input_tokens": 5000, "output_tokens": 800,
                          "cache_read_tokens": 4000, "stop_reason": "tool_use"}),
        TraceStep(order=3, name="verify", latency_ms=1.0,
                  detail={"verdict": "pass", "claim_type": "ConditionClaim"}),
    )
    return RequestTrace(
        correlation_id="req-live-1", client_id="copilot-42",
        exercised_scopes=("openid", "user/Condition.read"), request_url="https://agent/chat",
        user_hash="uhash", patient_hash="phash", utc_timestamp="2026-07-12T12:00:00+00:00",
        steps=steps, model="claude-sonnet-4-6", input_tokens=5000, output_tokens=800,
        cache_read_tokens=4000, cache_creation_tokens=0, cost_usd=0.03,
        verdicts=("pass",), source="llm", degraded=False)


def test_langfuse_sink_emits_against_installed_sdk_without_error():
    # A dummy-keyed client never connects (no flush is called); this only asserts the SDK API
    # is used correctly — v3 calls (start_as_current_span/update_trace) would raise here.
    sink = LangfuseSink(host=None, public_key="pk-lf-11111111", secret_key="sk-lf-11111111")
    tracer = RequestTracer(sink)
    tracer._emit(_trace())
    assert tracer.dropped == 0  # emit ran clean → the v4 API is correct


def test_generation_helpers_carry_usage_and_cost():
    detail = {"input_tokens": 5000, "output_tokens": 800, "cache_read_tokens": 4000}
    assert _usage_details(detail) == {"input": 5000, "output": 800, "cache_read_input": 4000}
    cost = _cost_details(detail, "claude-sonnet-4-6")
    assert cost is not None and cost["total"] > 0  # real cost → native cost widget has data
    assert _cost_details(detail, "no-such-model") is None  # unpriced → None, never a silent zero
