"""E3.1 — Pydantic tool contracts are the source of truth (ARCHITECTURE.md §5a, PRD).

Freezes the typed inputs/outputs for the six read tools: a tri-state `ToolResult`
envelope (ok / no_records / failed — the allergy tri-state and partial-failure path
live on this), and evidence-record shapes carrying exactly the fields the §5 rules
touch (medication dose optional → rule 6; allergy criticality optional/untrusted →
rule 2; condition clinical_status present → rule 4; encounter status present but
non-asserted → rule 1; patient deceased → D12). Malformed output is a validation
error, never a silent pass.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.tools.contracts import (
    AllergyRecord,
    ConditionRecord,
    EncounterRecord,
    LabObservation,
    MedicationRecord,
    PatientRecord,
    RecentLabsInput,
    ToolResult,
    ToolStatus,
)


def test_tool_status_is_tri_state():
    assert {s.value for s in ToolStatus} == {"ok", "no_records", "failed"}


def test_recent_labs_input_defaults_category_to_laboratory():
    # F-P.2: the labs tool passes an explicit category to prune the 10-way fan-out.
    inp = RecentLabsInput(patient_id="a234b786-539a-4f9a-96a0-432293226f02")
    assert inp.category == "laboratory"


def test_patient_id_rejects_empty():
    with pytest.raises(ValidationError):
        RecentLabsInput(patient_id="")


def test_lab_observation_rejects_wrong_typed_value():
    LabObservation(resource_id="obs-1", loinc="4548-4", value=6.1, unit="%", category="laboratory")
    with pytest.raises(ValidationError):
        LabObservation(resource_id="obs-1", value="not-a-number", category="laboratory")  # type: ignore[arg-type]


def test_medication_dose_is_optional():
    # Rule 6: an empty dose is allowed here; the templater renders "confirm before dosing".
    m = MedicationRecord(resource_id="rx-1", name="metformin", dose_text=None, intent="order")
    assert m.dose_text is None


def test_allergy_criticality_is_optional_and_untrusted():
    # Rule 2: criticality is null dataset-wide (F-D.4); the contract must allow None.
    a = AllergyRecord(resource_id="al-1", substance="penicillin", criticality=None)
    assert a.criticality is None


def test_condition_keeps_clinical_status_for_rule4():
    c = ConditionRecord(resource_id="c-1", display="Diabetes", clinical_status="inactive")
    assert c.clinical_status == "inactive"


def test_encounter_and_patient_deceased_fields_present():
    e = EncounterRecord(resource_id="e-1", status="finished")
    assert e.status == "finished"
    p = PatientRecord(resource_id="p-1", deceased_datetime=None, deceased_boolean=False)
    assert p.deceased_boolean is False


def test_tool_result_ok_carries_records():
    r = ToolResult[AllergyRecord](
        tool="get_allergies", status=ToolStatus.OK,
        records=[AllergyRecord(resource_id="al-1", substance="penicillin")],
    )
    assert r.status is ToolStatus.OK and len(r.records) == 1


def test_tool_result_no_records_is_empty_and_valid():
    r = ToolResult[AllergyRecord](tool="get_allergies", status=ToolStatus.NO_RECORDS, records=[])
    assert r.status is ToolStatus.NO_RECORDS and r.records == []


def test_tool_result_failed_requires_missing_reason():
    # Partial-failure path: a failed tool must NAME what's missing (never silent).
    with pytest.raises(ValidationError):
        ToolResult[AllergyRecord](tool="get_allergies", status=ToolStatus.FAILED)
    ok = ToolResult[AllergyRecord](tool="get_allergies", status=ToolStatus.FAILED,
                                   missing_reason="allergies unavailable (timeout)")
    assert ok.missing_reason
