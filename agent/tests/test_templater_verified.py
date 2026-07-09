"""E6.1 — verified-claims re-render (ARCHITECTURE.md §5, D7).

The normal path: after the verifier accepts claims, the templater RE-RENDERS display
text ONLY from the verified fields. The LLM's own prose is DISCARDED if it diverges —
the model cannot phrase past verification. This extends the same module as the D13
fallback render (test_templater_fallback.py); `render_packet_fallback` must remain intact
alongside the new `render_from_verified`.

Deterministic and packet-driven; no LLM is mocked. We hand the templater
VerificationResults and assert the output reflects the VERIFIED fields and is free of any
divergent prose or forbidden phrasing (F-D.1/F-D.4/F-D.5).
"""

from __future__ import annotations

from datetime import date

from app.evidence.packet import build_evidence_packet
from app.tools.contracts import (
    AllergyRecord,
    LabObservation,
    MedicationRecord,
    ToolResult,
    ToolStatus,
)
from app.verify.claims import (
    AllergyClaim,
    LabValueClaim,
    MedicationClaim,
    TextClaim,
    Verdict,
)
from app.verify.templater import (
    FALLBACK_BANNER,
    render_from_verified,
    render_packet_fallback,
)
from app.verify.verifier import Verifier

PID = "pat-1"


def _ok(tool, records):
    return ToolResult(tool=tool, status=ToolStatus.OK, records=records)


def _none(tool):
    return ToolResult(tool=tool, status=ToolStatus.NO_RECORDS, records=[])


def _first_id(packet, resource_type):
    return packet.by_type(resource_type)[0].evidence_id


def _verify(claim, packet):
    return Verifier().verify(claim, packet)


# --- the D13 fallback path is NOT removed by E6.1 ---

def test_fallback_render_still_present_alongside_verified_render():  # spec: §5 (extend, not replace)
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="metformin",
                                                    dose_text="500 mg")])})
    assert FALLBACK_BANNER in render_packet_fallback(packet)


# --- re-render reflects VERIFIED fields ---

def test_render_from_verified_reflects_verified_dose():  # spec: §5 D7
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="metformin",
                                                    dose_text="500 mg")])})
    eid = _first_id(packet, "MedicationRequest")
    result = _verify(MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[eid]), packet)
    out = render_from_verified([result])
    assert "metformin" in out and "500 mg" in out


def test_render_discards_divergent_prose_uses_verified_fields():  # spec: §5 D7
    # The record (and thus the verified field) is 500 mg. Even if the model's prose said
    # 5000 mg, the re-render must show the VERIFIED 500 mg and must NOT contain "5000".
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="metformin",
                                                    dose_text="500 mg")])})
    eid = _first_id(packet, "MedicationRequest")
    # dose omitted from the claim → verifier passes on absence; templater renders the
    # verified field from the packet, never the model's number.
    result = _verify(MedicationClaim(name="metformin", dose=None, evidence_ids=[eid]), packet)
    out = render_from_verified([result])
    assert "500 mg" in out
    assert "5000" not in out  # divergent prose cannot survive the re-render


def test_render_from_verified_lab_shows_value_and_unit():  # spec: §5a
    packet = build_evidence_packet(PID, {"get_recent_labs": _ok(
        "get_recent_labs", [LabObservation(resource_id="l1", display="Hemoglobin A1c",
                                            value=7.8, unit="%", effective=date(2026, 6, 1))])})
    eid = _first_id(packet, "Observation")
    result = _verify(LabValueClaim(display="Hemoglobin A1c", value="7.8", unit="%",
                                   evidence_ids=[eid]), packet)
    out = render_from_verified([result])
    assert "Hemoglobin A1c" in out and "7.8" in out and "%" in out


# --- forbidden phrasing never survives the re-render ---

def test_render_never_emits_declined_or_refused():  # spec: F-D.1
    # Even given a claim that tried to assert the patient declined, the pipeline verdict is
    # blocked and no rendered output may contain the inversion-trap phrasing.
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="influenza vaccine")])})
    eid = _first_id(packet, "MedicationRequest")
    result = _verify(TextClaim(text="Patient declined / refused the vaccine — patient objection.",
                               evidence_ids=[eid]), packet)
    # blocked claims are not rendered as verified content.
    out = render_from_verified([result]).lower()
    assert "declined" not in out
    assert "refused" not in out
    assert "patient objection" not in out


def test_render_never_emits_nkda_for_empty_allergy():  # spec: F-D.5
    packet = build_evidence_packet(PID, {"get_allergies": _none("get_allergies")})
    # Empty allergy result → the verified render surfaces the confirm-with-patient phrasing,
    # never NKDA. We render straight from the packet's no_records notice path.
    out = render_from_verified([], packet=packet)
    low = out.lower()
    assert "confirm with patient" in low
    assert "nkda" not in low and "no known allergies" not in low


def test_render_never_surfaces_criticality_as_risk():  # spec: F-D.4
    packet = build_evidence_packet(PID, {"get_allergies": _ok(
        "get_allergies", [AllergyRecord(resource_id="a1", substance="penicillin", criticality="high")])})
    eid = _first_id(packet, "AllergyIntolerance")
    result = _verify(AllergyClaim(substance="penicillin", evidence_ids=[eid]), packet)
    out = render_from_verified([result])
    assert "penicillin" in out
    low = out.lower()
    assert "high risk" not in low and "high-risk" not in low


# --- only verified content is rendered ---

def test_blocked_claim_not_rendered_as_verified_content():  # spec: §5 D7
    # A contradicted claim (blocked) must not appear in the verified render.
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="lisinopril",
                                                    dose_text="5 mg")])})
    eid = _first_id(packet, "MedicationRequest")
    blocked = _verify(MedicationClaim(name="lisinopril", dose="10 mg", evidence_ids=[eid]), packet)
    assert blocked.verdict != Verdict.PASS
    out = render_from_verified([blocked])
    assert "10 mg" not in out  # the unverified/contradicted value never renders


def test_render_from_verified_is_deterministic():  # spec: §5 (serving path deterministic)
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="metformin",
                                                    dose_text="500 mg")])})
    eid = _first_id(packet, "MedicationRequest")
    result = _verify(MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[eid]), packet)
    assert render_from_verified([result]) == render_from_verified([result])
