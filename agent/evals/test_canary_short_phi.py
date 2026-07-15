"""Regression: the generated-output PHI scanner must catch SHORT PHI, not just long phrases.

Final-adversarial-review finding (HIGH, W2-D7/§7): the leak scanner only matched the
ZZPHI canary, emails, and >=12-char multi-word phrases, so it MISSED the short-PHI classes
the no_phi_in_logs invariant names — DOB, MRN, extracted clinical values, and short names
("John Smith" is 10 chars). A generated-output leak of those passed the 100%-required gate.

These cases pin: (1) a leak of a case's own DOB / MRN / clinical value / name into any
generated channel trips the scanner; (2) benign telemetry (a "92ms" duration, a "1987"
token) does NOT false-fire — the gate must stay stable at 100%.
"""

from __future__ import annotations

from evals.canary import scan_generated_surfaces
from evals.w2_models import CaseObservation, GeneratedSurfaces, GoldenCase
from app.schemas.citations import CitationV2


def _case() -> GoldenCase:
    citation = CitationV2(
        source_type="uploaded_document",
        source_id="fixture:phi-probe",
        page_or_section="1",
        field_or_chunk_id="f",
        quote_or_value="x",
    )
    return GoldenCase(
        case_id="phi-scanner-regression",
        category="adversarial",
        doc_type="intake_form",
        fixture_path="agent/evals/fixtures/golden/phi-probe.pdf",
        expected_fields={
            "demographics": {"name": "John Smith", "dob": "1987-04-12", "mrn": "MRN-99887"},
            "glucose": "92",
        },
        expected_citations=[citation],
        expected_verdict="extract",
        guards="Short-PHI (dob/mrn/value/name) leaking into generated output past the scanner.",
        pass_criteria=["No expected PHI value appears in any generated channel."],
        maps_to="no_phi_in_logs",
    )


def test_short_phi_leak_in_generated_output_is_caught() -> None:
    # spec(W2-D7): a leak of the case's own DOB/MRN/value/name into generated telemetry trips.
    case = _case()
    leak = CaseObservation(
        case_id=case.case_id,
        fields={},
        citations=[],
        verdict="v",
        output="patient John Smith seen today",  # short name leak
        generated=GeneratedSurfaces(
            logs=["dob=1987-04-12 recorded"],       # DOB leak
            traces=["credential for MRN-99887"],    # MRN leak
            results=["glucose 92 mg/dL flagged"],   # clinical value leak
        ),
    )
    result = scan_generated_surfaces(case, leak)
    assert result.clean is False
    for channel in ("observation", "logs", "traces", "results"):
        assert channel in result.leak_channels, f"{channel} leak missed"


def test_benign_telemetry_does_not_false_fire() -> None:
    # spec(W2-D7): the 100% gate must stay stable — counters/timestamps are not PHI.
    case = _case()
    benign = CaseObservation(
        case_id=case.case_id,
        fields={},
        citations=[],
        verdict="v",
        output=None,
        generated=GeneratedSurfaces(
            logs=["request took 92ms status 200", "duration 9200us"],
            traces=["span 1987 events", "retry 5 of 5"],
        ),
    )
    result = scan_generated_surfaces(case, benign)
    assert result.clean is True, f"false-positive leak channels: {result.leak_channels}"


def test_canary_self_test_still_non_vacuous() -> None:
    # spec(W2-D7): the known-leak self-test still trips on the canary in every channel.
    from evals.canary import known_leak_self_test

    assert known_leak_self_test(_case()) is True
