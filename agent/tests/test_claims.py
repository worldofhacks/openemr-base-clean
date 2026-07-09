"""E6.1 — typed claim value objects + verdict/refusal enums (ARCHITECTURE.md §5, D7).

The LLM answers ONLY in typed claims; each claim carries `evidence_ids` back into
the packet so the verifier can resolve every assertion against a cited record. These
tests freeze the claim shapes and the verdict/refusal vocabulary the whole §5 pipeline
speaks — they are the contract the verifier and templater build against.

We test the DATA CONTRACT here (construction, defaults, cited-id plumbing), not the
verifier rules; the accept/reject decisions live in test_verifier.py. No LLM is mocked
anywhere — claim objects are the deterministic hand-off boundary between the model and
the verifier.
"""

from __future__ import annotations

from enum import Enum

import pytest

from app.verify.claims import (
    AllergyClaim,
    Claim,
    ConditionClaim,
    ImmunizationClaim,
    LabValueClaim,
    MedicationClaim,
    RefusalKind,
    TextClaim,
    Verdict,
)


# --- verdict + refusal vocabulary (§5: pass | flagged | blocked | refused(kind)) ---

def test_verdict_is_string_enum_with_the_four_serving_verdicts():  # spec: §5 D7
    assert issubclass(Verdict, Enum)
    names = {v.name for v in Verdict}
    assert {"PASS", "FLAGGED", "BLOCKED", "REFUSED"} <= names
    # str-backed so it can be logged/serialized per response (E6.1 accept).
    assert isinstance(Verdict.PASS, str)


def test_refusal_kind_covers_the_named_hard_stops():  # spec: §5 D12 / §6
    assert issubclass(RefusalKind, Enum)
    names = {k.name for k in RefusalKind}
    # D12 canonical refusals: deceased, treatment-advice, wrong-patient, ambiguous, expired session.
    assert {
        "DECEASED",
        "TREATMENT_ADVICE",
        "WRONG_PATIENT",
        "AMBIGUOUS",
        "EXPIRED_SESSION",
    } <= names


# --- every claim carries evidence_ids into the packet (D7) ---

def test_medication_claim_carries_name_dose_and_cited_ids():  # spec: §5 D7
    c = MedicationClaim(name="metformin", dose="500 mg", evidence_ids=["MedicationRequest:m1:aaaaaaaa"])
    assert c.name == "metformin"
    assert c.dose == "500 mg"
    assert c.evidence_ids == ["MedicationRequest:m1:aaaaaaaa"]


def test_medication_claim_dose_is_optional_and_defaults_none():  # spec: §5 rule 6 / F-D.2
    c = MedicationClaim(name="lisinopril", evidence_ids=["MedicationRequest:m2:bbbbbbbb"])
    assert c.dose is None  # silence, not an invented dose


def test_lab_value_claim_shape():  # spec: §5a
    c = LabValueClaim(display="Hemoglobin A1c", value="7.8", unit="%", evidence_ids=["Observation:l1:cccccccc"])
    assert c.display == "Hemoglobin A1c"
    assert c.value == "7.8"
    assert c.unit == "%"


def test_condition_claim_present_defaults_true_and_encodes_negation():  # spec: §5 rule 4 / F-D.6
    positive = ConditionClaim(display="Diabetes", evidence_ids=["Condition:c1:dddddddd"])
    assert positive.present is True
    # present=False is the "no history of X" negation the F-D.6 rule must be able to reject.
    negative = ConditionClaim(display="Diabetes", present=False, evidence_ids=["Condition:c1:dddddddd"])
    assert negative.present is False


def test_allergy_claim_risk_is_optional_and_marks_a_criticality_claim():  # spec: §5 rule 2 / F-D.4
    plain = AllergyClaim(substance="penicillin", evidence_ids=["AllergyIntolerance:a1:eeeeeeee"])
    assert plain.substance == "penicillin"
    assert plain.risk is None  # a substance-only claim is fine
    risky = AllergyClaim(substance="penicillin", risk="high", evidence_ids=["AllergyIntolerance:a1:eeeeeeee"])
    assert risky.risk == "high"  # any non-None risk = a criticality-derived claim the verifier rejects


def test_immunization_claim_declined_defaults_false():  # spec: §5 rule 1 / F-D.1
    c = ImmunizationClaim(vaccine="influenza", evidence_ids=["Immunization:i1:ffffffff"])
    assert c.vaccine == "influenza"
    assert c.declined is False
    declined = ImmunizationClaim(vaccine="influenza", declined=True, evidence_ids=["Immunization:i1:ffffffff"])
    assert declined.declined is True  # the inverted-status trap the verifier must block


def test_text_claim_is_free_prose_for_the_phrasing_screens():  # spec: §5
    c = TextClaim(text="Patient has a history of asthma.", evidence_ids=["Condition:c1:dddddddd"])
    assert "asthma" in c.text


# --- the base contract: evidence_ids is intrinsic to every claim ---

def test_all_claim_types_are_claims_and_expose_evidence_ids():  # spec: §5 D7
    claims = [
        MedicationClaim(name="x", evidence_ids=["MedicationRequest:m:11111111"]),
        LabValueClaim(display="x", evidence_ids=["Observation:l:22222222"]),
        ConditionClaim(display="x", evidence_ids=["Condition:c:33333333"]),
        AllergyClaim(substance="x", evidence_ids=["AllergyIntolerance:a:44444444"]),
        ImmunizationClaim(vaccine="x", evidence_ids=["Immunization:i:55555555"]),
        TextClaim(text="x", evidence_ids=["Condition:c:33333333"]),
    ]
    for c in claims:
        assert isinstance(c, Claim)
        assert isinstance(c.evidence_ids, list)


def test_claim_with_no_evidence_ids_defaults_to_empty_list():  # spec: §5 D7 (uncited = empty)
    # An uncited claim is representable (the verifier BLOCKS it — see test_verifier);
    # construction must not silently fabricate a citation.
    c = TextClaim(text="unsourced assertion")
    assert c.evidence_ids == []


@pytest.mark.parametrize(
    "kind",
    ["DECEASED", "TREATMENT_ADVICE", "WRONG_PATIENT", "AMBIGUOUS", "EXPIRED_SESSION"],
)
def test_refusal_kinds_are_addressable_by_name(kind):  # spec: §6
    assert RefusalKind[kind].name == kind
