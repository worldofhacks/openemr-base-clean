"""Regression pins for a DOCUMENTED §5 known-limitation (ARCHITECTURE §5, DECISIONS D7
addendum 2026-07-09): the verifier's label-fallback on absence.

These tests do NOT assert a desirable invariant — they PIN the CURRENT behavior so a change
is a conscious decision, not a silent regression. When a claim cites a real, resolved record
whose LABEL field is empty (medication with no `name`, observation with no `display`), the
field-level check finds no CONTRADICTION (absence, not mismatch), the claim passes on that
field, and the templater renders the claim's own label. This is bounded: the SENSITIVE fields
(medication dose, lab value) are always record-sourced and are also pinned here, proving the
fallback never invents a dose or a number (F-D.2 holds). Hardening is deferred to E6-verifier v2.
"""

from __future__ import annotations

from app.evidence.packet import build_evidence_packet
from app.tools.contracts import LabObservation, MedicationRecord, ToolResult, ToolStatus
from app.verify.claims import LabValueClaim, MedicationClaim, Verdict
from app.verify.templater import render_from_verified
from app.verify.verifier import Verifier

PID = "pat-1"


def _ok(tool, records):
    return ToolResult(tool=tool, status=ToolStatus.OK, records=records)


def _eid(packet, rt):
    return packet.by_type(rt)[0].evidence_id


# --- PINNED: a claim's LABEL falls back when the cited record's label is empty --------

def test_medication_label_falls_back_to_claim_when_record_name_empty():  # KNOWN LIMITATION (pinned)
    # Real, resolved MedicationRequest with NO name but a real dose. A named claim citing it
    # currently PASSES (absence ≠ contradiction) and the claim's name renders.
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name=None, dose_text="500 mg")])})
    claim = MedicationClaim(name="metformin", dose="500 mg", evidence_ids=[_eid(packet, "MedicationRequest")])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS               # PINS: absence-of-label is not a contradiction
    assert "metformin" in render_from_verified([result])  # PINS: the claim's own label renders


def test_lab_label_falls_back_to_claim_when_record_display_empty():  # KNOWN LIMITATION (pinned)
    packet = build_evidence_packet(PID, {"get_recent_labs": _ok(
        "get_recent_labs", [LabObservation(resource_id="l1", display=None, value=7.8, unit="%")])})
    claim = LabValueClaim(display="Hemoglobin A1c", value="7.8", unit="%",
                          evidence_ids=[_eid(packet, "Observation")])
    result = Verifier().verify(claim, packet)
    assert result.verdict == Verdict.PASS
    assert "Hemoglobin A1c" in render_from_verified([result])  # claim's display renders


# --- PINNED (the bound): SENSITIVE fields never fall back to the claim -----------------

def test_medication_dose_is_never_taken_from_the_claim_on_contradiction():  # the limit's boundary
    # A contradicting dose is still BLOCKED — dose is record-sourced, never the claim's (F-D.2).
    packet = build_evidence_packet(PID, {"get_active_medications": _ok(
        "get_active_medications", [MedicationRecord(resource_id="m1", name="metformin", dose_text="500 mg")])})
    claim = MedicationClaim(name="metformin", dose="10 mg", evidence_ids=[_eid(packet, "MedicationRequest")])
    result = Verifier().verify(claim, packet)
    assert result.verdict != Verdict.PASS               # dose contradiction still rejected
    assert "10 mg" not in render_from_verified([result])  # the claim's dose never renders
