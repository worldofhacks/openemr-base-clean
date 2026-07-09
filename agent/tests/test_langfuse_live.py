"""E7.1 live smoke — emit ONE real trace to Langfuse Cloud (D5-rev, §7).

Opt-in (kept out of the fast suite): RUN_LIVE=1 and LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY set
(the E7.0 provisioning step). Proves the LangfuseSink maps a RequestTrace to a real Langfuse
trace without dropping it. No PHI — synthetic accountability with hashed ids.
"""

from __future__ import annotations

import os

import pytest

from app.llm.provider import Usage
from app.observability.langfuse import LangfuseSink, RequestTracer
from app.observability.trace import AccountabilityContext

pytestmark = pytest.mark.live

_skip = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1"
    or not os.environ.get("LANGFUSE_PUBLIC_KEY")
    or not os.environ.get("LANGFUSE_SECRET_KEY"),
    reason="live Langfuse: set RUN_LIVE=1 + LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY",
)


@_skip
def test_live_emits_one_trace_without_dropping(capsys):
    sink = LangfuseSink(
        host=os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com"),
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    )
    tracer = RequestTracer(sink)
    b = tracer.begin(AccountabilityContext(
        correlation_id="live-smoke-e7", client_id="copilot-live",
        exercised_scopes=("openid", "user/Condition.read"),
        request_url="https://agent/chat", user_id="clinician-live", patient_id="patient-live",
        utc_timestamp="2026-07-09T12:00:00+00:00"))
    b.step("llm.complete", latency_ms=120.0, input_tokens=100, stop_reason="end_turn")
    b.record_usage(Usage(input_tokens=100, output_tokens=40, cache_read_input_tokens=80))
    b.finish(model="claude-sonnet-4-6", fallback_kind="transient", degraded=True,
             source="deterministic_fallback")
    sink.flush()
    with capsys.disabled():
        print(f"\n[E7 LIVE] emitted trace, dropped={tracer.dropped}")
    assert tracer.dropped == 0, "the real Langfuse export was dropped"
