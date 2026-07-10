"""T-E6b — verifier LABEL leniency (strict dose/value) + all-blocked→D13 + clinician-from-token + F-D.2 dedup.

These are FROZEN INVARIANT tests for the E6b verifier-leniency ticket. They assert BEHAVIOR
only — the served verdict/text/source, the parsed clinician sub, the de-duplicated packet —
never a mock's phrasing. The four changes they pin:

  1. LABEL identity is LENIENT (a paraphrasing LLM's "Obesity" == record "…obesity (finding)")
     while DOSE / LAB VALUE stay STRICT contradiction checks, and a genuinely different entity
     (warfarin vs metformin) still BLOCKS.
  2. When every claim BLOCKS/REFUSES → the served answer is the honest D13 grounded render
     (real records, "couldn't verify — confirm manually"), source="deterministic_fallback",
     never an empty source="llm".
  3. The clinician identity comes from the token's id_token (fhirUser preferred, else sub),
     not a hardcoded string.
  4. F-D.2 order/plan de-dup: one evidence record per drug (order preferred over plan).

Mirrors the _ok/_first_id verifier helpers and test_verify_in_loop's SubmitClaimsProvider.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, TextBlock, ToolUseBlock, Usage
from app.observability.langfuse import InMemoryTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator.loop import Orchestrator, ToolRegistry
from app.tools.contracts import (
    ConditionRecord,
    LabObservation,
    MedicationRecord,
    ToolResult,
    ToolStatus,
)
from app.verify.claims import (
    ConditionClaim,
    LabValueClaim,
    MedicationClaim,
    Verdict,
)
from app.verify.verifier import Verifier

PID = "a234b786-539a-4f9a-96a0-432293226f02"


# --- helpers (mirror test_verifier / test_verify_in_loop) ------------------

def _ok(tool, records):
    return ToolResult(tool=tool, status=ToolStatus.OK, records=records)


def _first_id(packet, resource_type):
    return packet.by_type(resource_type)[0].evidence_id


def _med_packet(**kw):
    return build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", **kw)])})


def _lab_packet(**kw):
    return build_evidence_packet(PID, {"get_recent_labs": _ok(
        "get_recent_labs", [LabObservation(resource_id="l1", **kw)])})


def _condition_packet(**kw):
    return build_evidence_packet(PID, {"get_conditions": _ok(
        "get_conditions", [ConditionRecord(resource_id="c1", **kw)])})


# ============================================================================
# 1. LENIENT LABEL — a paraphrasing LLM label still PASSES against the record.
# ============================================================================

def test_lenient_medication_label_substring_passes():  # spec: T-E6b (1) lenient label
    # Claim name "metformin" cites a record named "Metformin 500 MG Oral Tablet". The record
    # label CONTAINS the claim label → same entity → PASS (dose matches too).
    packet = _med_packet(name="Metformin 500 MG Oral Tablet", dose_text="500 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS, (result.verdict, result.reason)


def test_lenient_condition_label_shared_token_passes():  # spec: T-E6b (1) lenient label
    # ConditionClaim display "Obesity" cites record "Body mass index 30+ - obesity (finding)".
    # They share the significant token "obesity" → same entity → PASS.
    packet = _condition_packet(display="Body mass index 30+ - obesity (finding)")
    eid = _first_id(packet, "Condition")
    claim = ConditionClaim(display="Obesity", present=True, evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS, (result.verdict, result.reason)


def test_lenient_lab_label_substring_passes_with_matching_value():  # spec: T-E6b (1) lenient label
    # LabValueClaim display "A1c" (value+unit matching) cites record "Hemoglobin A1c".
    # The record label CONTAINS "a1c" → same entity → PASS.
    packet = _lab_packet(display="Hemoglobin A1c", value=7.8, unit="%")
    eid = _first_id(packet, "Observation")
    claim = LabValueClaim(display="A1c", value="7.8", unit="%", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS, (result.verdict, result.reason)


# ============================================================================
# 2. STRICT DOSE / VALUE — a wrong number STILL BLOCKS, even with a matching label.
# ============================================================================

def test_strict_dose_still_blocks_even_with_lenient_label():  # spec: T-E6b (1) strict dose
    # Label matches leniently ("metformin" ⊂ "Metformin 500 MG Oral Tablet") BUT the dose is
    # wrong (10 mg claimed vs 5 mg recorded) → BLOCKED. Dose stays a strict contradiction check.
    packet = _med_packet(name="Metformin 500 MG Oral Tablet", dose_text="5 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="metformin", dose="10 mg", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.BLOCKED, (result.verdict, result.reason)
    reason = (result.reason or "").lower()
    assert "10 mg" in reason and "5 mg" in reason  # the reason names the dose contradiction


def test_strict_lab_value_still_blocks_even_with_lenient_label():  # spec: T-E6b (1) strict value
    # Label matches leniently ("A1c" ⊂ "Hemoglobin A1c") BUT the value is wrong (9.9 vs 7.8)
    # → BLOCKED. Lab value stays a strict contradiction check.
    packet = _lab_packet(display="Hemoglobin A1c", value=7.8, unit="%")
    eid = _first_id(packet, "Observation")
    claim = LabValueClaim(display="A1c", value="9.9", unit="%", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.BLOCKED, (result.verdict, result.reason)


# ============================================================================
# 3. DIFFERENT ENTITY STILL BLOCKS — leniency must not collapse distinct drugs.
# ============================================================================

def test_different_drug_still_blocks():  # spec: T-E6b (1) different entity blocks
    # A metformin record cited by a warfarin claim → genuinely different entity → BLOCKED.
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="warfarin", dose="500 mg", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.BLOCKED, (result.verdict, result.reason)


def test_short_substring_does_not_falsely_match():  # spec: T-E6b (1) guard short substrings
    # "in" is a substring of "insulin" but must NOT be treated as the same entity — a short
    # substring is a false positive the lenient check must guard against → BLOCKED.
    packet = _med_packet(name="insulin", dose_text="10 units")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="in", dose="10 units", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.BLOCKED, (result.verdict, result.reason)


# ============================================================================
# 4. ALL-CLAIMS-BLOCKED → honest NON-EMPTY D13 output (never empty source="llm").
# ============================================================================

class SubmitClaimsProvider:
    """Returns a scripted submit_claims tool call, then (if consulted again) an empty end_turn.
    Mirrors test_verify_in_loop.SubmitClaimsProvider."""

    def __init__(self, claims, model="claude-sonnet-4-6"):
        self.model = model
        self.completed = False
        self.call_count = 0
        self._claims = claims

    async def complete(self, *, system, messages, tools):
        self.completed = True
        self.call_count += 1
        if self.call_count == 1:
            return LLMResponse(
                content=[ToolUseBlock(id="toolu_submit_1", name="submit_claims",
                                      input={"claims": self._claims})],
                stop_reason="tool_use",
                usage=Usage(input_tokens=12, output_tokens=6), model=self.model)
        return LLMResponse(content=[TextBlock(text="")], stop_reason="end_turn",
                           usage=Usage(input_tokens=4, output_tokens=1), model=self.model)


def _acct():
    return AccountabilityContext(
        correlation_id="req-allblocked-1", client_id="copilot-42",
        exercised_scopes=("openid", "user/MedicationRequest.read"),
        request_url="https://agent/chat", user_id="clinician-7", patient_id=PID,
        utc_timestamp="2026-07-09T12:00:00+00:00")


async def test_all_claims_blocked_serves_grounded_d13_not_empty_llm():  # spec: T-E6b (2)
    # A metformin-500 packet. The model submits ONLY claims that block: a contradicted dose
    # (5000 mg vs 500 mg) and a fabricated citation (warfarin citing a ghost id). Nothing
    # verifies → the served answer must be the honest D13 grounded render, NOT an empty
    # source="llm".
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _first_id(packet, "MedicationRequest")
    claims = [
        {"type": "medication", "name": "metformin", "dose": "5000 mg", "evidence_ids": [eid]},
        {"type": "medication", "name": "warfarin", "dose": "1 mg",
         "evidence_ids": ["MedicationRequest:ghost:00000000"]},
    ]
    prov = SubmitClaimsProvider(claims)
    sink = InMemoryTraceSink()
    tracer = RequestTracer(sink)
    res = await Orchestrator(prov).run_previsit_brief(
        packet, "Summarize.", tools=ToolRegistry([]), tracer=tracer, accountability=_acct())

    # Non-empty AND grounded: it carries a REAL record value (metformin from the packet).
    assert res.text.strip() != ""
    assert "metformin" in res.text.lower()
    # It is NOT served as the LLM turn — the honest grounded fallback superseded it.
    assert res.source != "llm", (res.source, res.text)
    assert res.source == "deterministic_fallback"

    # The contradicted / fabricated numbers never leak into the served text.
    assert "5000" not in res.text
    assert "warfarin" not in res.text.lower()


# ============================================================================
# 5. CLINICIAN IDENTITY FROM THE TOKEN — decode id_token, prefer fhirUser else sub.
# ============================================================================

def _b64url(obj: dict) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt(payload: dict) -> str:
    header = _b64url({"alg": "none", "typ": "JWT"})
    body = _b64url(payload)
    return f"{header}.{body}.sig"  # signature ignored — it is our own freshly-exchanged token


def _token_client(token_json: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=token_json)

    from app.auth.smart_client import SmartClient
    return SmartClient(
        client_id="cid-123", client_secret="sek-456",
        authorize_endpoint="https://openemr.test/oauth2/default/authorize",
        token_endpoint="https://openemr.test/oauth2/default/token",
        fhir_base_url="https://openemr.test/apis/default/fhir",
        redirect_uri="https://openemr.test/callback",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_clinician_sub_prefers_fhir_user_from_id_token():  # spec: T-E6b (3)
    id_token = _jwt({"fhirUser": "Practitioner/abc", "sub": "prac-1"})
    tok = await _token_client({
        "access_token": "AT", "token_type": "Bearer", "expires_in": 3600,
        "scope": "openid user/Patient.read", "id_token": id_token,
    }).exchange_code(code="c", code_verifier="v")
    assert tok.clinician_sub == "Practitioner/abc"


@pytest.mark.asyncio
async def test_clinician_sub_falls_back_to_sub_when_no_fhir_user():  # spec: T-E6b (3)
    id_token = _jwt({"sub": "prac-1"})
    tok = await _token_client({
        "access_token": "AT", "token_type": "Bearer", "expires_in": 3600,
        "scope": "openid", "id_token": id_token,
    }).exchange_code(code="c", code_verifier="v")
    assert tok.clinician_sub == "prac-1"


@pytest.mark.asyncio
async def test_clinician_sub_is_none_without_id_token():  # spec: T-E6b (3)
    tok = await _token_client({
        "access_token": "AT", "token_type": "Bearer", "expires_in": 3600,
        "scope": "openid",
    }).exchange_code(code="c", code_verifier="v")
    assert tok.clinician_sub is None


# ============================================================================
# 6. F-D.2 — order/plan medication de-dup: one evidence record per drug.
# ============================================================================

def test_order_and_plan_for_same_drug_dedups_to_one_order():  # spec: T-E6b (4) F-D.2 dedup
    # Two MedicationRecords for the same drug (same name), one intent="order" one intent="plan".
    # The packet the LLM+verifier see must carry exactly ONE Medication evidence record — the
    # order, preferred over the plan.
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [
            MedicationRecord(resource_id="plan-1", name="metformin", dose_text="500 mg", intent="plan"),
            MedicationRecord(resource_id="order-1", name="metformin", dose_text="500 mg", intent="order"),
        ])})
    meds = packet.by_type("MedicationRequest")
    assert len(meds) == 1, [m.fields for m in meds]
    assert meds[0].fields.get("intent") == "order"  # order preferred over plan


def test_order_and_plan_dedup_keys_on_rxnorm_when_present():  # spec: T-E6b (4) F-D.2 dedup
    # De-dup keys on rxnorm when present (else normalized name). Same rxnorm, different display
    # text → still ONE record, the order kept.
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [
            MedicationRecord(resource_id="plan-1", name="Metformin (plan phrasing)",
                             rxnorm="860975", intent="plan"),
            MedicationRecord(resource_id="order-1", name="Metformin 500 MG Oral Tablet",
                             rxnorm="860975", intent="order"),
        ])})
    meds = packet.by_type("MedicationRequest")
    assert len(meds) == 1, [m.fields for m in meds]
    assert meds[0].fields.get("intent") == "order"


def test_distinct_drugs_are_not_deduped():  # spec: T-E6b (4) F-D.2 dedup (boundary)
    # Two genuinely different drugs must BOTH survive — de-dup is per-drug, not a blanket collapse.
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [
            MedicationRecord(resource_id="o1", name="metformin", intent="order"),
            MedicationRecord(resource_id="o2", name="lisinopril", intent="order"),
        ])})
    assert len(packet.by_type("MedicationRequest")) == 2
