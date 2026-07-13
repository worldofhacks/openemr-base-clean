"""E5.2 — deterministic D13 fallback render (ARCHITECTURE.md §6, D13).

When the LLM hard-fails, the physician still gets something GROUNDED: the
EvidencePacket rendered directly (grouped, values+dates, state-aware) with an
explicit "generated without LLM assistance" banner — never "LLM failed, no answer."
This renderer is packet-only and pure; the E6 verified-claims path extends the same
templater. Safety phrasings that ride on the packet's notices are honored here:
an empty allergy result is "confirm with patient," never NKDA (F-D.5); a missing dose
is "confirm before dosing" (rule 6); criticality is never surfaced as risk (F-D.4).
"""

from __future__ import annotations

from datetime import date

from app.evidence.packet import build_evidence_packet
from app.tools.contracts import (
    AllergyRecord,
    ConditionRecord,
    EncounterRecord,
    LabObservation,
    MedicationRecord,
    ToolResult,
    ToolStatus,
)
from app.verify.templater import FALLBACK_BANNER, render_packet_fallback

PID = "pat-1"


def _ok(tool, records):
    return ToolResult(tool=tool, status=ToolStatus.OK, records=records)


def _none(tool):
    return ToolResult(tool=tool, status=ToolStatus.NO_RECORDS, records=[])


def _failed(tool, reason):
    return ToolResult(tool=tool, status=ToolStatus.FAILED, missing_reason=reason)


def test_fallback_carries_the_no_llm_banner():
    packet = build_evidence_packet(PID, {"get_conditions": _ok(
        "get_conditions", [ConditionRecord(resource_id="c1", display="Asthma")])})
    out = render_packet_fallback(packet)
    assert FALLBACK_BANNER in out
    assert "without llm assistance" in out.lower()


def test_fallback_renders_condition_display_and_date():
    r = ConditionRecord(resource_id="c1", display="Type 2 diabetes",
                        onset=date(2019, 3, 1), clinical_status="active")
    out = render_packet_fallback(build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [r])}))
    assert "Type 2 diabetes" in out and "2019-03-01" in out


def test_fallback_med_with_dose_shows_the_dose():
    r = MedicationRecord(resource_id="m1", name="metformin", dose_text="500 mg BID")
    out = render_packet_fallback(build_evidence_packet(
        PID, {"get_active_medications": _ok("get_active_medications", [r])}))
    assert "metformin" in out and "500 mg BID" in out


def test_fallback_med_without_dose_says_confirm_before_dosing():
    r = MedicationRecord(resource_id="m1", name="lisinopril", dose_text=None)
    out = render_packet_fallback(build_evidence_packet(
        PID, {"get_active_medications": _ok("get_active_medications", [r])}))
    assert "lisinopril" in out
    assert "confirm before dosing" in out.lower()  # rule 6 — never invent a dose


def test_fallback_lab_shows_value_unit_and_date():
    r = LabObservation(resource_id="l1", display="Hemoglobin A1c",
                       value=7.8, unit="%", effective=date(2026, 6, 1))
    out = render_packet_fallback(build_evidence_packet(
        PID, {"get_recent_labs": _ok("get_recent_labs", [r])}))
    assert "Hemoglobin A1c" in out and "7.8" in out and "%" in out and "2026-06-01" in out


def test_fallback_empty_allergy_says_confirm_with_patient_not_nkda():
    out = render_packet_fallback(build_evidence_packet(PID, {"get_allergies": _none("get_allergies")}))
    low = out.lower()
    assert "confirm with patient" in low
    assert "nkda" not in low and "no known" not in low  # F-D.5: empty ≠ no allergies


def test_fallback_names_failed_tool_as_missing_data():
    out = render_packet_fallback(build_evidence_packet(
        PID, {"get_active_medications": _failed("get_active_medications", "medications unavailable: HTTP 503")}))
    low = out.lower()
    assert "unavailable" in low and "medication" in low  # F3 partial answer, never silent


def test_fallback_never_surfaces_criticality_as_risk():
    r = AllergyRecord(resource_id="a1", substance="penicillin", criticality="high")
    out = render_packet_fallback(build_evidence_packet(PID, {"get_allergies": _ok("get_allergies", [r])}))
    assert "penicillin" in out
    low = out.lower()
    assert "high risk" not in low and "high-risk" not in low  # F-D.4: criticality is null/untrusted


def test_fallback_is_deterministic():
    r = ConditionRecord(resource_id="c1", display="COPD", onset=date(2018, 1, 1))
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [r])})
    assert render_packet_fallback(packet) == render_packet_fallback(packet)


def test_general_fallback_has_hard_record_and_character_caps():
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [
        ConditionRecord(resource_id=f"c-{i}", display=f"Condition {i}", clinical_status="active")
        for i in range(30)
    ])})

    out = render_packet_fallback(packet)

    assert sum(line.startswith("- ") for line in out.splitlines()) <= 8
    assert len(out) <= 2_500
    assert "additional" in out.lower() and "omitted" in out.lower()


def test_resolution_followup_fallback_is_scoped_bounded_and_honest():
    conditions = [
        ConditionRecord(
            resource_id=f"inactive-{i}",
            display=f"Inactive condition {i:02d}",
            clinical_status="inactive" if i % 2 == 0 else "resolved",
        )
        for i in range(30)
    ]
    conditions.append(ConditionRecord(
        resource_id="active-sentinel",
        display="ACTIVE CONDITION MUST NOT RENDER",
        clinical_status="active",
    ))
    packet = build_evidence_packet(PID, {
        "get_conditions": _ok("get_conditions", conditions),
        "get_active_medications": _ok("get_active_medications", [MedicationRecord(
            resource_id="med-sentinel", name="MEDICATION MUST NOT RENDER", dose_text="5 mg")]),
        "get_recent_labs": _ok("get_recent_labs", [LabObservation(
            resource_id="lab-sentinel", display="LAB MUST NOT RENDER", value=99)]),
        "get_encounters": _ok("get_encounters", [EncounterRecord(
            resource_id="enc-sentinel", type_display="ENCOUNTER MUST NOT RENDER")]),
    })

    out = render_packet_fallback(
        packet,
        question="What has been cured, and what does the patient no longer have?",
    )
    low = out.lower()

    assert "inactive condition 00" in low
    assert "inactive" in low or "resolved" in low
    assert "cured" in low and ("can't verify" in low or "cannot verify" in low)
    assert "confirm with the chart" in low
    assert "active condition must not render" not in low
    assert "medication must not render" not in low
    assert "lab must not render" not in low
    assert "encounter must not render" not in low
    assert "## medications" not in low
    assert "## labs / observations" not in low
    assert "## encounters" not in low
    assert sum(line.startswith("- ") for line in out.splitlines()) <= 8
    assert len(out) <= 2_500
    assert "additional" in low and "omitted" in low


def test_resolution_followup_with_no_inactive_records_returns_short_honest_message():
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [
        ConditionRecord(resource_id="active", display="Asthma", clinical_status="active"),
    ])})

    out = render_packet_fallback(packet, question="Which problems are resolved?")
    low = out.lower()

    assert "asthma" not in low
    assert "no conditions are marked inactive or resolved" in low
    assert "can't verify" in low or "cannot verify" in low
    assert len(out) <= 2_500
