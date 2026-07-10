"""E6a — §5 verify-then-flush wired into the serving loop (ARCHITECTURE.md §5, D7, D12, §3 UC1 step 3).

Today the orchestrator serves the LLM's RAW text. This freezes the behavioral invariant of
the change: the model answers by calling a `submit_claims` tool with TYPED claims; the
orchestrator VERIFIES each claim against the cited EvidencePacket record, records every
verdict to the trace, and serves ONLY the re-rendered VERIFIED (PASS/FLAGGED) content —
BLOCKED/REFUSED content is DROPPED and can never reach `BriefResult.text`. A D12 deceased
pre-flight refuses BEFORE the LLM is ever consulted.

These are invariant tests, not implementation tests. They assert BEHAVIOR only — served
text, per-claim verdicts, `source`, and whether the LLM was consulted — never a mock's
phrasing quality. A lazy implementation that echoes the model's prose (or skips the
verifier) cannot pass: a `submit_claims` tool call carries NO assistant text, so the served
answer only contains a value if it was re-rendered from a VERIFIED field.

Mirrors the FakeProvider + AccountabilityContext + InMemoryTraceSink style of
test_orchestrator_trace.py, but the provider here returns a `submit_claims` ToolUseBlock.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, TextBlock, ToolUseBlock, Usage
from app.observability.langfuse import InMemoryTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator.loop import Orchestrator, ToolRegistry, ToolSpec
from app.tools.contracts import (
    ConditionRecord,
    MedicationRecord,
    PatientRecord,
    ToolResult,
    ToolStatus,
)

PID = "a234b786-539a-4f9a-96a0-432293226f02"


# --- evidence packet helpers -----------------------------------------------

def _ok(tool, records):
    return ToolResult(tool=tool, status=ToolStatus.OK, records=records)


def _med_packet(**kw):
    """A single-medication packet (metformin 500 mg by default)."""
    kw.setdefault("name", "metformin")
    kw.setdefault("dose_text", "500 mg")
    return build_evidence_packet(
        PID,
        {"get_active_medications": _ok(
            "get_active_medications", [MedicationRecord(resource_id="m1", **kw)])},
    )


def _condition_packet(display="Type 2 diabetes", **kw):
    return build_evidence_packet(
        PID,
        {"get_conditions": _ok(
            "get_conditions", [ConditionRecord(resource_id="c1", display=display, **kw)])},
    )


def _deceased_packet():
    """A packet whose Patient record is flagged deceased (D12 pre-flight hard-stop)."""
    return build_evidence_packet(
        PID,
        {"get_patient_summary": _ok("get_patient_summary", [PatientRecord(
            resource_id="p1", name="Jane Doe",
            deceased_datetime=datetime(2025, 1, 1, tzinfo=timezone.utc))])},
    )


def _med_evidence_id(packet):
    return packet.by_type("MedicationRequest")[0].evidence_id


# --- fake provider that answers via a submit_claims tool call --------------

def _submit_claims_response(claims: list[dict]) -> LLMResponse:
    """One assistant turn: a `submit_claims` tool_use carrying typed claim dicts.

    Note: a tool call carries NO assistant TEXT (`.text()` == ""), so any value that appears
    in the served answer must have been re-rendered from a VERIFIED field — the model's own
    number cannot leak through raw."""
    return LLMResponse(
        content=[ToolUseBlock(id="toolu_submit_1", name="submit_claims",
                              input={"claims": claims})],
        stop_reason="tool_use",
        usage=Usage(input_tokens=12, output_tokens=6),
        model="claude-sonnet-4-6",
    )


class SubmitClaimsProvider:
    """Returns a scripted `submit_claims` tool call, then (if consulted again) a plain
    end_turn text turn so the loop can terminate. Records whether `complete()` ran at all —
    that flag is the D12 "LLM never consulted" probe."""

    def __init__(self, claims: list[dict], model="claude-sonnet-4-6"):
        self.model = model
        self.completed = False
        self.call_count = 0
        self._claims = claims

    async def complete(self, *, system, messages, tools):
        self.completed = True
        self.call_count += 1
        if self.call_count == 1:
            return _submit_claims_response(self._claims)
        # If the loop feeds the tool result back and asks again, end cleanly with no new text.
        return LLMResponse(content=[TextBlock(text="")], stop_reason="end_turn",
                           usage=Usage(input_tokens=4, output_tokens=1),
                           model=self.model)


class NeverCalledProvider:
    """A sentinel that MUST NOT be consulted (D12 deceased pre-flight). Any call to
    `complete()` records the violation and returns a benign non-tool turn so a wrong
    implementation fails on the `completed` assertion, not on a raised error it might catch."""

    def __init__(self, model="claude-sonnet-4-6"):
        self.model = model
        self.completed = False

    async def complete(self, *, system, messages, tools):
        self.completed = True
        return LLMResponse(content=[TextBlock(text="brief")], stop_reason="end_turn",
                           usage=Usage(input_tokens=1, output_tokens=1), model=self.model)


class _StubTool:
    async def __call__(self, tool_input):
        return "{}"


def _registry():
    """A registry WITHOUT `submit_claims` — the orchestrator intercepts submit_claims itself
    (verify-then-flush), it is not a generic dispatched tool. get_conditions is present only
    so the tool list is non-empty, matching the real serving loop."""
    return ToolRegistry([ToolSpec("get_conditions", "problem list",
                                  {"type": "object", "properties": {}}, _StubTool())])


def _acct():
    return AccountabilityContext(
        correlation_id="req-verify-1", client_id="copilot-42",
        exercised_scopes=("openid", "user/MedicationRequest.read"),
        request_url="https://agent/chat", user_id="clinician-7", patient_id=PID,
        utc_timestamp="2026-07-09T12:00:00+00:00")


def _run(provider, packet, *, sink):
    tracer = RequestTracer(sink)
    return Orchestrator(provider).run_previsit_brief(
        packet, "Summarize.", tools=_registry(), tracer=tracer, accountability=_acct())


# ============================================================================
# 1. HEADLINE — an unsupported claim is NOT served, and both verdicts are traced.
# ============================================================================

async def test_unsupported_claim_never_served_and_verdicts_traced():  # spec: §5 verify-then-flush / D7 / D12
    # Packet: metformin 500 mg. The model submits TWO medication claims citing that same
    # evidence id — one dose "500 mg" (supported → PASS) and one dose "5000 mg" (a genuine
    # contradiction → BLOCKED). The supported one must be re-rendered and served; the
    # contradicted one must be DROPPED (it cannot phrase past verification).
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _med_evidence_id(packet)
    claims = [
        {"type": "medication", "name": "metformin", "dose": "500 mg", "evidence_ids": [eid]},
        {"type": "medication", "name": "metformin", "dose": "5000 mg", "evidence_ids": [eid]},
    ]
    prov = SubmitClaimsProvider(claims)
    sink = InMemoryTraceSink()
    res = await _run(prov, packet, sink=sink)

    # The supported claim IS served (re-rendered from the verified field).
    assert "500 mg" in res.text
    # The contradicted claim is NOT served — a BLOCKED value can never reach the answer.
    assert "5000" not in res.text
    # A verified answer is still sourced from the LLM turn (source unchanged).
    assert res.source == "llm"

    # Per-claim verdicts are carried on the result AND recorded to the emitted trace.
    verdicts = [str(v).lower() for v in res.verdicts]
    assert any("pass" in v for v in verdicts)
    assert any("blocked" in v for v in verdicts)

    assert len(sink.traces) == 1
    traced = [str(v).lower() for v in sink.traces[0].verdicts]
    assert traced, "the trace must carry the per-claim verification verdicts (non-empty)"
    assert any("pass" in v for v in traced)
    assert any("blocked" in v for v in traced)


# ============================================================================
# 2. A fabricated citation (evidence id not in the packet) is never served.
# ============================================================================

async def test_fabricated_citation_not_served_and_verdict_non_pass():  # spec: §5 verify-then-flush / D7
    # The model cites an evidence id that is NOT in the packet — fabricated provenance. Its
    # content ("warfarin") must not appear in the served answer, and its verdict is non-PASS.
    packet = _med_packet(name="metformin", dose_text="500 mg")
    claims = [
        {"type": "medication", "name": "warfarin", "dose": "1 mg",
         "evidence_ids": ["MedicationRequest:ghost:00000000"]},
    ]
    prov = SubmitClaimsProvider(claims)
    sink = InMemoryTraceSink()
    res = await _run(prov, packet, sink=sink)

    # Fabricated content never reaches the served text. (T-E6b change 2: this single-claim,
    # all-blocked turn now serves the honest D13 grounded render — the metformin packet — so
    # "warfarin" is doubly absent: neither served verbatim nor present in the real records.)
    assert "warfarin" not in res.text.lower()

    # Its verdict is recorded and is NOT a pass. INVARIANT the D13-supersede must preserve:
    # the per-claim verdicts are carried through on the served result AND the trace even when
    # the all-blocked turn falls back to the grounded render.
    verdicts = [str(v).lower() for v in res.verdicts]
    assert verdicts, "the fabricated claim's verdict must be recorded"
    assert not any(v == "pass" for v in verdicts)

    assert len(sink.traces) == 1
    traced = [str(v).lower() for v in sink.traces[0].verdicts]
    assert traced and not any(v == "pass" for v in traced)


# ============================================================================
# 3. D12 deceased pre-flight refuses BEFORE the LLM is consulted.
# ============================================================================

async def test_deceased_preflight_refuses_before_calling_llm():  # spec: §5 verify-then-flush / D12
    # A deceased patient must trigger a deterministic refusal that does NOT consult the LLM.
    packet = _deceased_packet()
    prov = NeverCalledProvider()
    sink = InMemoryTraceSink()
    res = await _run(prov, packet, sink=sink)

    # The LLM was NEVER consulted for a deceased patient.
    assert prov.completed is False, "the LLM must not be called on the D12 deceased pre-flight"

    # The result is a refusal, not an LLM brief: its source is a non-'llm' refusal marker.
    assert res.source != "llm"

    # The text reads as a review-the-chart-manually refusal, not a synthesized brief.
    low = res.text.lower()
    assert "chart" in low and "manual" in low

    # Exactly one accountable trace is still emitted for the refused request.
    assert len(sink.traces) == 1


# ============================================================================
# 4. Fully-supported claims ARE served (the happy path still flushes real content).
# ============================================================================

async def test_fully_supported_claims_are_served():  # spec: §5 verify-then-flush / D7
    # A claim set entirely supported by the evidence → the verified content is served. Proves
    # the pipeline flushes real, verified content — it does not blank-out everything.
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _med_evidence_id(packet)
    claims = [
        {"type": "medication", "name": "metformin", "dose": "500 mg", "evidence_ids": [eid]},
    ]
    prov = SubmitClaimsProvider(claims)
    sink = InMemoryTraceSink()
    res = await _run(prov, packet, sink=sink)

    assert "metformin" in res.text.lower()
    assert "500 mg" in res.text
    assert res.source == "llm"

    verdicts = [str(v).lower() for v in res.verdicts]
    assert any("pass" in v for v in verdicts)

    assert len(sink.traces) == 1
    traced = [str(v).lower() for v in sink.traces[0].verdicts]
    assert any("pass" in v for v in traced)


# ============================================================================
# 5. Finding #1 / §6 fail-closed parse — malformed submit_claims payloads must
#    NOT crash the serving turn (parse-don't-validate boundary).
# ============================================================================

async def test_non_dict_claim_item_does_not_crash_turn():  # spec: finding-1 / §6 fail-closed parse
    """A non-dict item mixed into the claims list must never raise; the valid claim is served."""
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _med_evidence_id(packet)
    # Mix a raw string (non-dict) with a fully-valid supported claim.
    claims = [
        "a raw string",
        {"type": "medication", "name": "metformin", "dose": "500 mg", "evidence_ids": [eid]},
    ]
    prov = SubmitClaimsProvider(claims)
    sink = InMemoryTraceSink()
    # Must NOT raise — if it does the test fails via unhandled exception.
    res = await _run(prov, packet, sink=sink)

    # The run returned a BriefResult (not an exception).
    assert res.source == "llm"

    # The valid medication claim IS rendered and served in the output text.
    assert "500 mg" in res.text

    # The raw-string item must NOT have produced a PASS verdict (it has no evidence).
    verdicts = [str(v).lower() for v in res.verdicts]
    # At most one PASS (for the good medication claim); the malformed item is not a PASS.
    # We assert on count: only the valid claim may be PASS, the raw string may not add one.
    pass_count = sum(1 for v in verdicts if v == "pass")
    # The only PASS-eligible claim is the well-formed medication; the raw string cannot PASS.
    assert pass_count <= 1, "malformed non-dict item must not produce an extra PASS verdict"


async def test_non_list_evidence_ids_does_not_crash_turn():  # spec: finding-1 / §6 fail-closed parse
    """A claim whose evidence_ids is a scalar string must not crash the serving turn."""
    packet = _med_packet(name="metformin", dose_text="500 mg")
    claims = [
        {"type": "medication", "name": "metformin", "dose": "500 mg",
         "evidence_ids": "not-a-list"},
    ]
    prov = SubmitClaimsProvider(claims)
    sink = InMemoryTraceSink()
    # Must NOT raise — a ValidationError escaping to the caller is the bug being frozen.
    res = await _run(prov, packet, sink=sink)

    # The load-bearing invariant: the run RETURNED (did not raise). RECONCILED (T-E6b change 2):
    # the single malformed claim is BLOCKED → all claims blocked → the served answer is the
    # honest D13 grounded render (source="deterministic_fallback"), never an empty source="llm".
    assert res.source == "deterministic_fallback"
    assert res.text.strip() != ""            # grounded, non-empty — the run completed

    # The malformed claim cannot be served as PASS (its evidence lookup is broken/empty). The
    # per-claim verdict is still carried through the D13-supersede path.
    verdicts = [str(v).lower() for v in res.verdicts]
    assert not any(v == "pass" for v in verdicts), (
        "a claim with non-list evidence_ids must not receive a PASS verdict"
    )


def test_parse_claims_never_raises_on_malformed_items():  # spec: finding-1 / §6 fail-closed parse
    """Unit: parse_claims is safe against every malformed item shape the model might emit."""
    from app.verify.claims import Claim, parse_claims

    malformed = [
        "x",                                             # non-dict string
        None,                                            # None
        42,                                              # integer
        {"type": "medication", "evidence_ids": "bad"},  # non-list evidence_ids
    ]
    # Must NOT raise AttributeError, ValidationError, or anything else.
    result = parse_claims(malformed)

    # Returns a list of Claim instances — every malformed item degraded, none dropped silently.
    assert isinstance(result, list)
    assert len(result) == len(malformed)
    for claim in result:
        assert isinstance(claim, Claim), f"expected Claim, got {type(claim)!r}: {claim!r}"
