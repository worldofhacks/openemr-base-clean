"""E5 live smoke — ONE real Anthropic call proving the loop runs end-to-end and a
tool is actually invoked, plus a trace-level proof that the cached prefix earns a
cache read on a repeat call (R1). This is the right live check; loop-control and
fallback logic are covered by the mocked unit tests (test_orchestrator_loop.py).

Opt-in (kept out of the fast suite): RUN_LIVE=1 and ANTHROPIC_API_KEY set. Bounded by
a $1 daily cap so a misconfiguration can't run up spend. No PHI — synthetic packet.
"""

from __future__ import annotations

import json
import os

import pytest

from app.evidence.packet import build_evidence_packet
from app.llm.cost import DailyCostCap
from app.llm.provider import AnthropicLLMProvider
from app.orchestrator.loop import (
    Orchestrator,
    ToolRegistry,
    ToolSpec,
    build_initial_user_content,
    build_system_blocks,
)
from app.tools.contracts import ConditionRecord, ToolResult, ToolStatus

pytestmark = pytest.mark.live

_skip = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="live LLM smoke: set RUN_LIVE=1 and ANTHROPIC_API_KEY",
)

MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")


def _provider():
    return AnthropicLLMProvider(api_key=os.environ["ANTHROPIC_API_KEY"], model=MODEL, max_tokens=1024)


@_skip
async def test_live_single_call_runs_loop_and_invokes_a_tool(capsys):
    invoked: list[dict] = []

    async def handler(tool_input):
        invoked.append(tool_input)
        return json.dumps({"status": "ok", "records": [{"display": "Type 2 diabetes mellitus"}]})

    reg = ToolRegistry([ToolSpec(
        name="get_conditions",
        description="Return this patient's active problem list. Call this before summarizing.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler)])

    packet = build_evidence_packet("pat-live", {"get_conditions": ToolResult(
        tool="get_conditions", status=ToolStatus.NO_RECORDS, records=[])})
    orch = Orchestrator(_provider(), cost_cap=DailyCostCap(cap_usd=1.0))

    res = await orch.run_previsit_brief(
        packet,
        "Use the get_conditions tool to fetch this patient's problem list, then give a one-line pre-visit brief.",
        tools=reg,
    )

    assert res.source == "llm", f"expected a live LLM answer, got {res.source} ({res.fallback_reason})"
    assert invoked, "the tool was never invoked by the live loop"
    with capsys.disabled():
        print(f"\n[E5 LIVE] loop ran: source={res.source} iterations={res.iterations} "
              f"tool_calls={res.tool_calls}")
        print(f"[E5 LIVE] usage in={res.usage.input_tokens} out={res.usage.output_tokens} "
              f"cache_write={res.usage.cache_creation_input_tokens} "
              f"cache_read={res.usage.cache_read_input_tokens}")


@_skip
async def test_live_repeated_stable_prefix_earns_a_cache_read(capsys):
    prov = _provider()
    # A large stable packet so the cached prefix clears Sonnet's ~2048-token minimum.
    recs = [ConditionRecord(resource_id=f"c{i}", display=f"Chronic condition number {i} with clinical detail")
            for i in range(250)]
    packet = build_evidence_packet("pat-cache", {"get_conditions": ToolResult(
        tool="get_conditions", status=ToolStatus.OK, records=recs)})
    system = build_system_blocks()
    messages = [{"role": "user", "content": build_initial_user_content(packet, "One-line summary, please.")}]

    r1 = await prov.complete(system=system, messages=messages, tools=[])  # writes the cache
    r2 = await prov.complete(system=system, messages=messages, tools=[])  # identical prefix → reads it
    with capsys.disabled():
        print(f"\n[E5 LIVE cache] r1 write={r1.usage.cache_creation_input_tokens} "
              f"read={r1.usage.cache_read_input_tokens}")
        print(f"[E5 LIVE cache] r2 write={r2.usage.cache_creation_input_tokens} "
              f"read={r2.usage.cache_read_input_tokens}")
    assert r2.usage.cache_read_input_tokens > 0, "identical prefix earned no cache read — caching not wired"
