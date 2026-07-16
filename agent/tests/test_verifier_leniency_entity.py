"""T-E6b adversarial — entity leniency: token-adjacent DISTINCT entities must BLOCK.

spec: T-E6b adversarial — entity leniency

Adversarial-review finding: `_labels_match` currently returns True when two labels share ANY
significant token (line 122 of verifier.py: ``return bool(_significant_tokens(a) & _significant_tokens(b))``).
This over-collapses genuinely DIFFERENT clinical entities that happen to share one word:

  - "insulin glargine" vs "insulin lispro"        → share "insulin"    → wrongly PASS today
  - "metoprolol tartrate" vs "metoprolol succinate" → share "metoprolol" → wrongly PASS today
  - "total cholesterol" vs "hdl cholesterol"       → share "cholesterol" → wrongly PASS today
  - "vitamin d" vs "vitamin b12"                  → share "vitamin"    → wrongly PASS today

The INTENDED behavior (must NOT regress):
  - Exact match → PASS
  - One label CONTAINS the other (at word boundary) → PASS
  - Shorter label's significant tokens are a SUBSET of the longer's (reordered paraphrase) → PASS
  - Mere single-token overlap between OTHERWISE-DISTINCT multi-token labels → BLOCK

The tests in groups 1–3 below are FROZEN INVARIANT against the corrected impl.

Group 1 tests are expected to FAIL against the CURRENT impl (they catch the bug).
Group 2–3 tests are expected to PASS against both old and new impl (must-still-pass).
"""

from __future__ import annotations

from app.evidence.packet import build_evidence_packet
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


# --- helpers (mirrors test_verifier_leniency.py) ----------------------------

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
# 1. TOKEN-ADJACENT DISTINCT ENTITIES MUST BLOCK
#    These tests expose the current bug: shared-one-token wrongly → PASS.
#    They FAIL against the current impl and PASS once the fix lands.
#    Doses/values are IDENTICAL so the ONLY possible blocker is the entity gate.
# ============================================================================

def test_insulin_glargine_vs_insulin_lispro_blocks():  # spec: T-E6b adversarial — entity leniency
    """MedicationClaim 'insulin glargine' citing a record 'insulin lispro' must BLOCK.

    They share the token 'insulin' — that single shared token must NOT be enough to
    declare them the same clinical entity (these are distinct insulins with different
    pharmacokinetics). The fix must require the SHORTER label's tokens to be a full
    SUBSET of the longer's, not merely an intersection.
    """
    packet = _med_packet(name="insulin lispro", dose_text="10 units")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="insulin glargine", dose="10 units", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict != Verdict.PASS, (
        f"CURRENT BUG: 'insulin glargine' wrongly matched 'insulin lispro' via shared token 'insulin'. "
        f"verdict={result.verdict}, reason={result.reason}"
    )
    assert result.verdict == Verdict.BLOCKED, (result.verdict, result.reason)


def test_metoprolol_tartrate_vs_metoprolol_succinate_blocks():  # spec: T-E6b adversarial — entity leniency
    """MedicationClaim 'metoprolol tartrate' citing record 'metoprolol succinate' must BLOCK.

    These are distinct salt forms of metoprolol with different pharmacokinetic profiles
    (immediate-release vs extended-release). Sharing 'metoprolol' alone must not pass.
    """
    packet = _med_packet(name="metoprolol succinate", dose_text="25 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="metoprolol tartrate", dose="25 mg", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict != Verdict.PASS, (
        f"CURRENT BUG: 'metoprolol tartrate' wrongly matched 'metoprolol succinate' via shared "
        f"token 'metoprolol'. verdict={result.verdict}, reason={result.reason}"
    )
    assert result.verdict == Verdict.BLOCKED, (result.verdict, result.reason)


def test_total_cholesterol_vs_hdl_cholesterol_blocks():  # spec: T-E6b adversarial — entity leniency
    """LabValueClaim 'total cholesterol' citing record 'HDL cholesterol' must BLOCK.

    These are clinically distinct lab analytes — confusing them would produce a dangerously
    wrong clinical picture. Sharing 'cholesterol' alone must not pass.
    Value is IDENTICAL (200 mg/dL) so only the label gate can block here.
    """
    packet = _lab_packet(display="HDL cholesterol", value=200.0, unit="mg/dL")
    eid = _first_id(packet, "Observation")
    claim = LabValueClaim(display="total cholesterol", value="200", unit="mg/dL", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict != Verdict.PASS, (
        f"CURRENT BUG: 'total cholesterol' wrongly matched 'HDL cholesterol' via shared token "
        f"'cholesterol'. verdict={result.verdict}, reason={result.reason}"
    )
    assert result.verdict == Verdict.BLOCKED, (result.verdict, result.reason)


# ============================================================================
# 2. REORDERED-TOKEN PARAPHRASE MUST STILL PASS
#    The fix must not over-tighten: a genuine paraphrase (all tokens match, just
#    reordered) where the SHORTER label's tokens are a SUBSET of the LONGER's
#    still names the same entity and must PASS.
# ============================================================================

def test_reordered_paraphrase_type2_diabetes_passes():  # spec: T-E6b adversarial — entity leniency
    """ConditionClaim 'Type 2 diabetes' citing record 'Diabetes mellitus type 2' must PASS.

    Same clinical condition, just reordered wording from the LLM. Under the corrected rule
    the shorter label's significant tokens {'type', 'diabetes'} (after filtering stopwords)
    must be a SUBSET of the longer label's tokens {'diabetes', 'mellitus', 'type'} → PASS.
    """
    packet = _condition_packet(display="Diabetes mellitus type 2")
    eid = _first_id(packet, "Condition")
    claim = ConditionClaim(display="Type 2 diabetes", present=True, evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS, (
        f"Reordered paraphrase 'Type 2 diabetes' / 'Diabetes mellitus type 2' must PASS "
        f"(same entity, different word order). verdict={result.verdict}, reason={result.reason}"
    )


# ============================================================================
# 3. CONTAINMENT STILL PASSES
#    The existing containment path must be preserved: a claim whose label is a
#    word-boundary substring of the record label names the same entity → PASS.
# ============================================================================

def test_containment_metformin_vs_full_tablet_name_passes():  # spec: T-E6b adversarial — entity leniency
    """MedicationClaim 'metformin' citing record 'Metformin 500 MG Oral Tablet' must PASS.

    The claim label 'metformin' appears at a word boundary inside the record label →
    containment match → same drug → PASS (dose matches too).
    This mirrors the existing test in test_verifier_leniency.py but is included here
    as a regression guard for the fix introduced by T-E6b adversarial.
    """
    packet = _med_packet(name="Metformin 500 MG Oral Tablet", dose_text="500 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS, (
        f"Containment: 'metformin' ⊂ 'Metformin 500 MG Oral Tablet' must PASS. "
        f"verdict={result.verdict}, reason={result.reason}"
    )
