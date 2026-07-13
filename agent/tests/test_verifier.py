"""E6.1/E6.2 — the §5 verifier: field-level match + the audit's concrete rules (D7).

This is the load-bearing trust layer. Given a typed claim and the EvidencePacket it
cites, the verifier decides pass | flagged | blocked | refused(kind) by matching the
claim's fields against the CITED record — rejecting on CONTRADICTION, never on absence.
Plus the deceased pre-flight hard-stop (D12) and the audit's six concrete rules.

All checks here are DETERMINISTIC and packet-driven. No LLM is mocked: we feed a claim
(the model's hand-off) + real records and assert the verdict. Mirrors the _ok/_none/_failed
ToolResult helpers from test_evidence_packet / test_templater_fallback.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.evidence.packet import build_evidence_packet
from app.tools.contracts import (
    AllergyRecord,
    ConditionRecord,
    MedicationRecord,
    PatientRecord,
    ToolResult,
    ToolStatus,
)
from app.verify.claims import (
    AllergyClaim,
    ConditionClaim,
    ImmunizationClaim,
    MedicationClaim,
    RefusalKind,
    TextClaim,
    Verdict,
)
from app.verify.verifier import Verifier

PID = "pat-1"


def _ok(tool, records):
    return ToolResult(tool=tool, status=ToolStatus.OK, records=records)


def _none(tool):
    return ToolResult(tool=tool, status=ToolStatus.NO_RECORDS, records=[])


def _failed(tool, reason):
    return ToolResult(tool=tool, status=ToolStatus.FAILED, missing_reason=reason)


def _med_packet(**kw):
    return build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", **kw)])})


def _first_id(packet, resource_type):
    return packet.by_type(resource_type)[0].evidence_id


def _not_pass(verdict):
    return verdict in (Verdict.BLOCKED, Verdict.FLAGGED, Verdict.REFUSED)


# ============================================================================
# CORE PIPELINE (§5, D7)
# ============================================================================

def test_reject_on_contradiction_dose_mismatch():  # spec: §5 D7
    # Claim says 10 mg; the cited record says 5 mg → a genuine contradiction, NOT pass.
    packet = _med_packet(name="lisinopril", dose_text="5 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="lisinopril", dose="10 mg", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert _not_pass(result.verdict)
    assert result.verdict != Verdict.PASS
    # the reason must NAME the contradiction (dose values), not be a generic failure.
    reason = (result.reason or "").lower()
    assert "10 mg" in reason and "5 mg" in reason


def test_pass_on_absence_dose_silent_in_both():  # spec: §5 D7
    # Dose silent in the claim AND silent in the evidence → absence, not contradiction → PASS.
    packet = _med_packet(name="lisinopril", dose_text=None)
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="lisinopril", dose=None, evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS


def test_pass_when_dose_matches_cited_record():  # spec: §5 D7
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[eid])
    assert Verifier().verify(claim, packet).verdict == Verdict.PASS


def test_empty_citation_is_never_pass():  # spec: §5 D7 (every claim must cite)
    packet = _med_packet(name="metformin", dose_text="500 mg")
    claim = MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[])
    result = Verifier().verify(claim, packet)
    assert _not_pass(result.verdict)
    assert result.verdict != Verdict.PASS


def test_unresolvable_citation_is_never_pass():  # spec: §5 D7 (id not in packet)
    packet = _med_packet(name="metformin", dose_text="500 mg")
    claim = MedicationClaim(name="metformin", dose="500 mg",
                            evidence_ids=["MedicationRequest:ghost:00000000"])
    result = Verifier().verify(claim, packet)
    assert _not_pass(result.verdict)
    assert result.verdict != Verdict.PASS
    assert result.matched_evidence_ids == []  # nothing resolved


def test_matched_evidence_ids_reported_on_pass():  # spec: §5 D7
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert eid in result.matched_evidence_ids  # provenance recorded for the templater


def test_name_mismatch_is_a_contradiction():  # spec: §5 D7
    # The claim cites a metformin record but asserts a different drug → contradiction.
    packet = _med_packet(name="metformin", dose_text="500 mg")
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="warfarin", dose="500 mg", evidence_ids=[eid])
    assert _not_pass(Verifier().verify(claim, packet).verdict)


# ============================================================================
# D12 DECEASED HARD-STOP (pre-flight, before any summarization)
# ============================================================================

def test_preflight_refuses_deceased_datetime():  # spec: §5 D12 / F-S.7
    packet = build_evidence_packet(PID, {"get_patient_summary": _ok(
        "get_patient_summary",
        [PatientRecord(resource_id="p1", name="Jane Doe",
                       deceased_datetime=datetime(2025, 1, 1, tzinfo=timezone.utc))])})
    assert Verifier().preflight(packet) is RefusalKind.DECEASED


def test_preflight_refuses_deceased_boolean_true():  # spec: §5 D12 / F-S.7
    packet = build_evidence_packet(PID, {"get_patient_summary": _ok(
        "get_patient_summary",
        [PatientRecord(resource_id="p1", name="Jane Doe", deceased_boolean=True)])})
    assert Verifier().preflight(packet) is RefusalKind.DECEASED


def test_preflight_passes_live_patient():  # spec: §5 D12
    packet = build_evidence_packet(PID, {"get_patient_summary": _ok(
        "get_patient_summary",
        [PatientRecord(resource_id="p1", name="Jane Doe", birth_date=date(1970, 1, 1))])})
    assert Verifier().preflight(packet) is None


def test_preflight_ignores_deceased_boolean_false():  # spec: §5 D12
    packet = build_evidence_packet(PID, {"get_patient_summary": _ok(
        "get_patient_summary",
        [PatientRecord(resource_id="p1", name="Jane Doe", deceased_boolean=False)])})
    assert Verifier().preflight(packet) is None


# ============================================================================
# F-D.1 — FHIR status never rendered verbatim (immunization inversion)
# ============================================================================

def test_immunization_declined_claim_blocked():  # spec: F-D.1
    # The stock mapper reports completed vaccines as not-done/"patient objection".
    # A claim asserting the patient declined must be BLOCKED — status is never trusted.
    packet = build_evidence_packet(PID, {"get_conditions": _ok(
        "get_conditions", [ConditionRecord(resource_id="c1", display="anything")])})
    claim = ImmunizationClaim(vaccine="influenza", declined=True,
                              evidence_ids=[_first_id(packet, "Condition")])
    result = Verifier().verify(claim, packet)
    assert result.verdict in (Verdict.BLOCKED, Verdict.REFUSED)


def test_text_claim_asserting_patient_declined_is_blocked():  # spec: F-D.1 (§5 rule 1)
    packet = build_evidence_packet(PID, {"get_conditions": _ok(
        "get_conditions", [ConditionRecord(resource_id="c1", display="influenza vaccine")])})
    claim = TextClaim(text="Patient refused the influenza vaccine (patient objection).",
                      evidence_ids=[_first_id(packet, "Condition")])
    result = Verifier().verify(claim, packet)
    assert result.verdict in (Verdict.BLOCKED, Verdict.REFUSED)
    # even if some verdict slipped through, the rendered text must be free of the trap phrasing.
    low = (result.rendered_text or "").lower()
    assert "declined" not in low and "refused" not in low and "patient objection" not in low


# ============================================================================
# F-D.4 — reject any criticality-based allergy claim; never rank risk
# ============================================================================

def test_allergy_risk_claim_blocked():  # spec: F-D.4
    packet = build_evidence_packet(PID, {"get_allergies": _ok(
        "get_allergies", [AllergyRecord(resource_id="a1", substance="penicillin", criticality="high")])})
    claim = AllergyClaim(substance="penicillin", risk="high",
                         evidence_ids=[_first_id(packet, "AllergyIntolerance")])
    result = Verifier().verify(claim, packet)
    assert result.verdict in (Verdict.BLOCKED, Verdict.REFUSED)


def test_plain_allergy_substance_claim_passes():  # spec: F-D.4 (substance is fine)
    packet = build_evidence_packet(PID, {"get_allergies": _ok(
        "get_allergies", [AllergyRecord(resource_id="a1", substance="penicillin")])})
    claim = AllergyClaim(substance="penicillin",
                         evidence_ids=[_first_id(packet, "AllergyIntolerance")])
    assert Verifier().verify(claim, packet).verdict == Verdict.PASS


# ============================================================================
# F-D.5 — empty allergy result is NOT NKDA
# ============================================================================

def test_nkda_claim_on_empty_allergy_result_is_rejected():  # spec: F-D.5
    # Allergy tool returned NO_RECORDS. A claim of "no known allergies" must NOT pass —
    # absence of records is not evidence of no allergies.
    packet = build_evidence_packet(PID, {"get_allergies": _none("get_allergies")})
    claim = AllergyClaim(substance="no known allergies", evidence_ids=[])
    result = Verifier().verify(claim, packet)
    assert _not_pass(result.verdict)
    assert result.verdict != Verdict.PASS
    low = (result.rendered_text or "").lower()
    assert "nkda" not in low and "no known allergies" not in low


# ============================================================================
# F-D.6 — consume ALL conditions; reject "no history of X" when an inactive match exists
# ============================================================================

def test_no_history_claim_blocked_when_inactive_match_exists():  # spec: F-D.6
    # A resolved/inactive Condition for "Diabetes" exists → "no history of Diabetes" is FALSE.
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [
        ConditionRecord(resource_id="c1", display="Diabetes", clinical_status="resolved")])})
    claim = ConditionClaim(display="Diabetes", present=False,
                           evidence_ids=[_first_id(packet, "Condition")])
    result = Verifier().verify(claim, packet)
    assert result.verdict in (Verdict.BLOCKED, Verdict.REFUSED)
    assert result.verdict != Verdict.PASS


def test_pipeline_consumes_inactive_conditions():  # spec: F-D.6 (nothing filters them out)
    # The packet the verifier reads must include inactive/resolved conditions — no active-only filter.
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [
        ConditionRecord(resource_id="c1", display="Asthma", clinical_status="active"),
        ConditionRecord(resource_id="c2", display="Diabetes", clinical_status="resolved"),
        ConditionRecord(resource_id="c3", display="Hypertension", clinical_status="inactive"),
    ])})
    statuses = {r.fields.get("clinical_status") for r in packet.by_type("Condition")}
    assert statuses == {"active", "resolved", "inactive"}  # all consumed, none dropped


def test_condition_present_claim_matching_active_record_passes():  # spec: F-D.6
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [
        ConditionRecord(resource_id="c1", display="Asthma", clinical_status="active")])})
    claim = ConditionClaim(display="Asthma", present=True,
                           evidence_ids=[_first_id(packet, "Condition")])
    assert Verifier().verify(claim, packet).verdict == Verdict.PASS


def test_inactive_condition_claim_passes_with_record_sourced_status():  # spec: F-D.6 / D7
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [
        ConditionRecord(resource_id="c1", display="Pneumonia", clinical_status="resolved")])})
    claim = ConditionClaim(display="Pneumonia", present=True,
                           evidence_ids=[_first_id(packet, "Condition")])

    result = Verifier().verify(claim, packet)

    assert result.verdict == Verdict.PASS
    assert result.verified == {"display": "Pneumonia", "clinical_status": "resolved"}


# ============================================================================
# F-D.2 — empty dose phrasing (verifier does not invent) + order/plan de-dup
# ============================================================================

def test_empty_dose_claim_passes_but_never_invents_a_dose():  # spec: F-D.2 rule 6
    # Dose absent in both claim and record → PASS (absence), and the verifier must not
    # fabricate a dose into the verified fields.
    packet = _med_packet(name="lisinopril", dose_text=None)
    eid = _first_id(packet, "MedicationRequest")
    claim = MedicationClaim(name="lisinopril", dose=None, evidence_ids=[eid])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS
    # no invented dose smuggled into the rendered/verified output.
    rendered = (result.rendered_text or "").lower()
    assert "mg" not in rendered or "confirm" in rendered


# ============================================================================
# Treatment-verb blocklist → REFUSED(TREATMENT_ADVICE)
# ============================================================================

def test_treatment_verb_text_claim_refused():  # spec: §5 treatment-verb blocklist / D12
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="lisinopril")])})
    claim = TextClaim(text="Start lisinopril 10mg daily.",
                      evidence_ids=[_first_id(packet, "MedicationRequest")])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.REFUSED
    assert result.refusal_kind is RefusalKind.TREATMENT_ADVICE


def test_text_claim_unresolvable_citation_is_blocked():  # spec: finding-2 / §5 D7 fail-closed citation
    # A descriptive TextClaim (no treatment verb, no forbidden phrasing) whose evidence_ids
    # do NOT resolve in the packet must be BLOCKED — fabricated provenance is fail-closed.
    # We use plainly descriptive text so the ONLY possible rejection reason is the citation.
    packet = build_evidence_packet(PID, {"get_conditions": _ok(
        "get_conditions", [ConditionRecord(resource_id="c1", display="Asthma")])})
    # "Condition:ghost:00000000" is NOT in the packet — it is fabricated provenance.
    claim = TextClaim(
        text="Patient has a documented history of asthma.",
        evidence_ids=["Condition:ghost:00000000"],
    )
    result = Verifier().verify(claim, packet)
    # Fail-closed: an unresolvable citation must be BLOCKED, not merely FLAGGED.
    assert result.verdict == Verdict.BLOCKED, (
        f"Expected BLOCKED for unresolvable citation, got {result.verdict} "
        f"(reason: {result.reason!r})"
    )


def test_descriptive_text_claim_not_refused_as_treatment():  # spec: §5 (read-only descriptive)
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="lisinopril",
                                                    dose_text="10 mg")])})
    claim = TextClaim(text="Patient is currently taking lisinopril 10 mg.",
                      evidence_ids=[_first_id(packet, "MedicationRequest")])
    result = Verifier().verify(claim, packet)
    assert result.verdict != Verdict.REFUSED
    assert result.refusal_kind is None
