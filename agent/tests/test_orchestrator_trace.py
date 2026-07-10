"""E7.1 — the orchestrator emits one accountable trace per request (§7, §3.1, D5-rev).

The tool-use loop records a span per model call and per tool dispatch, then emits a single
trace carrying the accountability fields + tokens/cost + the E5 degradation class. Tracing is
a soft dependency: a failing sink must not break the brief. Mocked LLM (loop control), no
output-quality assertions — consistent with E5's discipline.
"""

from __future__ import annotations

import json

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, LLMUnavailable, TextBlock, ToolUseBlock, Usage
from app.observability.langfuse import InMemoryTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator.loop import Orchestrator, ToolRegistry, ToolSpec
from app.tools.contracts import ConditionRecord, ToolResult, ToolStatus

PID = "a234b786-539a-4f9a-96a0-432293226f02"


def _packet(display="Type 2 diabetes"):
    return build_evidence_packet(PID, {"get_conditions": ToolResult(
        tool="get_conditions", status=ToolStatus.OK,
        records=[ConditionRecord(resource_id="c1", display=display)])})


def _text(text, stop="end_turn"):
    return LLMResponse(content=[TextBlock(text=text)], stop_reason=stop,
                       usage=Usage(input_tokens=10, output_tokens=5), model="claude-sonnet-4-6")


def _tool(tool_use_id, name):
    return LLMResponse(content=[ToolUseBlock(id=tool_use_id, name=name, input={})],
                       stop_reason="tool_use",
                       usage=Usage(input_tokens=12, output_tokens=6), model="claude-sonnet-4-6")


class FakeProvider:
    def __init__(self, scripted, model="claude-sonnet-4-6"):
        self._scripted = list(scripted)
        self.model = model

    async def complete(self, *, system, messages, tools):
        nxt = self._scripted.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _StubTool:
    async def __call__(self, tool_input):
        return json.dumps({"status": "ok", "records": []})


def _registry():
    return ToolRegistry([ToolSpec("get_conditions", "problem list",
                                  {"type": "object", "properties": {}}, _StubTool())])


def _acct():
    return AccountabilityContext(
        correlation_id="req-1", client_id="copilot-42",
        exercised_scopes=("openid", "user/Condition.read"),
        request_url="https://agent/chat", user_id="clinician-7", patient_id=PID,
        utc_timestamp="2026-07-09T12:00:00+00:00")


class _RaisingSink:
    def emit(self, trace):
        raise RuntimeError("langfuse down")


async def test_orchestrator_emits_one_trace_with_accountability_and_steps():
    sink = InMemoryTraceSink()
    tracer = RequestTracer(sink)
    prov = FakeProvider([_tool("toolu_1", "get_conditions"), _text("brief")])
    res = await Orchestrator(prov).run_previsit_brief(
        _packet(), "Summarize.", tools=_registry(), tracer=tracer, accountability=_acct())
    # RECONCILED (T-E6b change 2): the terminal turn is UNCITED prose → BLOCKED → all-blocked/
    # empty → the served answer is the D13 grounded render (source="deterministic_fallback").
    # The load-bearing invariant here is the TRACE (one trace, accountability, spans, accumulated
    # tokens/cost), which is recorded per model-call/tool-dispatch and is unchanged.
    assert res.source == "deterministic_fallback"
    assert len(sink.traces) == 1                 # exactly one trace per request
    t = sink.traces[0]
    assert t.client_id == "copilot-42"
    assert "user/Condition.read" in t.exercised_scopes
    assert t.correlation_id == "req-1"
    # a span for each model call and the tool dispatch, in order
    names = [s.name for s in t.steps]
    assert "tool.get_conditions" in names
    assert names.count("llm.complete") == 2
    # accumulated usage + priced cost land on the trace
    assert t.input_tokens == 22 and t.output_tokens == 11 and t.cost_usd > 0


async def test_trace_records_fallback_kind_on_degradation():
    sink = InMemoryTraceSink()
    tracer = RequestTracer(sink)
    prov = FakeProvider([LLMUnavailable("retries exhausted")])
    res = await Orchestrator(prov).run_previsit_brief(
        _packet(), "Summarize.", tools=_registry(), tracer=tracer, accountability=_acct())
    assert res.degraded and res.source == "deterministic_fallback"
    t = sink.traces[0]
    assert t.fallback_kind == "transient" and t.degraded is True   # fallback-rate alertable
    assert t.source == "deterministic_fallback"


async def test_tracing_failure_never_breaks_the_brief():  # boundary — §6 soft dep
    tracer = RequestTracer(_RaisingSink())
    prov = FakeProvider([_text("brief")])
    res = await Orchestrator(prov).run_previsit_brief(
        _packet(), "Summarize.", tools=_registry(), tracer=tracer, accountability=_acct())
    # Under §5 verify-then-flush, an uncited prose turn is wrapped as a TextClaim, BLOCKED by
    # the verifier, so nothing verifies. RECONCILED (T-E6b change 2): an all-blocked/empty
    # turn now serves the honest D13 grounded render (source="deterministic_fallback"), never
    # an empty source="llm". The soft-dependency invariant is unchanged: the REQUEST COMPLETED
    # and returned a grounded brief; the failed sink export was absorbed and counted.
    assert res.source == "deterministic_fallback"  # all-blocked → grounded D13, request completed
    assert "Type 2 diabetes" in res.text           # grounded in the real record, not empty
    assert tracer.dropped == 1  # the dropped export was counted


async def test_no_tracer_is_a_noop():
    prov = FakeProvider([_text("brief")])
    res = await Orchestrator(prov).run_previsit_brief(_packet(), "Summarize.", tools=_registry())
    # Tracing is optional — a no-tracer run must still complete and return a grounded brief.
    # RECONCILED (T-E6b change 2): the uncited prose turn is BLOCKED → all-blocked/empty → the
    # served answer is the D13 grounded render (source="deterministic_fallback"), not empty llm.
    assert res.source == "deterministic_fallback"
    assert "Type 2 diabetes" in res.text  # grounded, request completed without a tracer
