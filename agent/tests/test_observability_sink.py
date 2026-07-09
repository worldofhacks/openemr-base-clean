"""E7.1 — the trace sink is a SOFT dependency (ARCHITECTURE.md §6/§7, D5-rev).

Observability must never affect serving: if Langfuse is down or misconfigured, the export
is dropped (with a counter) and the request still completes. These tests prove the tracer
swallows sink failures and counts drops, and that the in-memory sink captures for tests.
The LangfuseSink itself is lazy/defensive — a construction/emit failure degrades, never raises.
"""

from __future__ import annotations

from app.llm.provider import Usage
from app.observability.langfuse import (
    InMemoryTraceSink,
    LangfuseSink,
    RequestTracer,
)
from app.observability.trace import AccountabilityContext


def _acct():
    return AccountabilityContext(
        correlation_id="c1", client_id="client", exercised_scopes=("openid",),
        request_url="https://a/chat", user_id="u", patient_id="p",
        utc_timestamp="2026-07-09T12:00:00+00:00")


class _RaisingSink:
    def emit(self, trace):
        raise RuntimeError("langfuse unreachable")


def test_inmemory_sink_captures_the_trace():
    sink = InMemoryTraceSink()
    b = RequestTracer(sink).begin(_acct())
    b.record_usage(Usage(input_tokens=10, output_tokens=5))
    t = b.finish(model="claude-sonnet-4-6", fallback_kind=None, degraded=False, source="llm")
    assert sink.traces == [t]


def test_langfuse_down_serving_continues():  # boundary — §6 soft dependency
    tracer = RequestTracer(_RaisingSink())
    b = tracer.begin(_acct())
    b.record_usage(Usage(input_tokens=10, output_tokens=5))
    # finish must NOT raise even though the sink throws; the trace is still returned to the caller.
    t = b.finish(model="claude-sonnet-4-6", fallback_kind=None, degraded=False, source="llm")
    assert t is not None
    assert tracer.dropped == 1  # the failed export is counted, not silently lost


def test_dropped_counter_accumulates_across_failures():
    tracer = RequestTracer(_RaisingSink())
    for _ in range(3):
        tracer.begin(_acct()).finish(model="claude-sonnet-4-6", fallback_kind=None,
                                     degraded=False, source="llm")
    assert tracer.dropped == 3


def test_langfuse_sink_construction_never_raises_without_keys():
    # An unconfigured/misconfigured sink must be constructible and degrade on emit, not crash
    # at import/build time (the live client is lazy). Emitting through the tracer is swallowed.
    sink = LangfuseSink(host=None, public_key=None, secret_key=None)
    tracer = RequestTracer(sink)
    t = tracer.begin(_acct()).finish(model="claude-sonnet-4-6", fallback_kind=None,
                                     degraded=False, source="llm")
    assert t is not None
    assert tracer.dropped == 1  # no credentials → export degraded, serving unaffected
