"""W2 B4 eval harness/scorer contract tests (W2-D5/D7/D8, §7/§7a).

These tests intentionally exercise a known failure for every boolean scorer.  The
failure probes guard against the graded gate becoming permanently green.
"""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.canary import known_leak_self_test, scan_generated_surfaces
from evals.golden_loader import load_golden_cases
from evals.harness import aggregate_scores, render_report, run_harness
from evals.scorers import (
    citation_present,
    factually_consistent,
    no_phi_in_logs,
    safe_refusal,
    schema_valid,
)
from evals.w2_models import (
    CaseObservation,
    CaseRubricResult,
    EvalCategoryV2,
    GeneratedSurfaces,
    GoldenCase,
    RefusalObservation,
    Rubric,
)


_MANIFEST = Path(__file__).parent / "golden" / "cases.json"


def _citation(*, value: str = "92") -> dict[str, object]:
    return {
        "source_type": "uploaded_document",
        "source_id": "fixture:unit-case",
        "page_or_section": "page 1",
        "field_or_chunk_id": "results[0].value",
        "quote_or_value": value,
    }


def _case(*, maps_to: Rubric = Rubric.FACTUALLY_CONSISTENT) -> GoldenCase:
    return GoldenCase(
        case_id="unit-case",
        category=EvalCategoryV2.BOUNDARY,
        doc_type="lab_pdf",
        fixture_path="agent/evals/fixtures/golden/unit-case.pdf",
        expected_fields={
            "results": [
                {
                    "test_name": "Glucose",
                    "value": "92",
                    "unit": "mg/dL",
                    "reference_range": "70-99",
                    "collection_date": "2026-06-01",
                    "abnormal_flag": "N",
                }
            ],
            "source_document_id": "fixture:unit-case",
        },
        expected_citations=[_citation()],
        expected_verdict="extract",
        guards="guards: W2-D5 / §7a permanently-green gate",
        pass_criteria=["The structured result remains grounded."],
        maps_to=maps_to,
    )


def _observation(case: GoldenCase | None = None) -> CaseObservation:
    case = case or _case()
    return CaseObservation(
        case_id=case.case_id,
        fields=deepcopy(case.expected_fields),
        citations=deepcopy(case.expected_citations),
        verdict=case.expected_verdict,
    )


def _refusal_case() -> GoldenCase:
    return _case(maps_to=Rubric.SAFE_REFUSAL).model_copy(
        update={"expected_verdict": "confirm_with_patient"}
    )


def _refusal_observation(case: GoldenCase | None = None) -> CaseObservation:
    case = case or _refusal_case()
    return _observation(case).model_copy(
        update={
            "refusal": RefusalObservation(
                code="confirm_with_patient",
                blocked=True,
                message="No allergy record returned — confirm with patient.",
            )
        }
    )


def test_loader_runs_from_manifest_length_not_a_hardcoded_count(tmp_path: Path):
    # guards: W2-D5 / §7 — newly appended cases must automatically enter the gate.
    raw = json.loads(_MANIFEST.read_text())
    loaded = load_golden_cases(_MANIFEST)
    assert [case.case_id for case in loaded] == [entry["case_id"] for entry in raw]

    dynamic = tmp_path / "dynamic.json"
    dynamic.write_text(json.dumps([raw[0], raw[-1], raw[1]]))
    assert [case.case_id for case in load_golden_cases(dynamic)] == [
        raw[0]["case_id"],
        raw[-1]["case_id"],
        raw[1]["case_id"],
    ]


def test_loader_rejects_duplicate_case_ids(tmp_path: Path):
    # guards: W2-D5 — duplicate IDs must not overwrite a manifest entry silently.
    entry = json.loads(_MANIFEST.read_text())[0]
    manifest = tmp_path / "duplicates.json"
    manifest.write_text(json.dumps([entry, entry]))
    with pytest.raises(ValueError, match="duplicate case_id"):
        load_golden_cases(manifest)


def test_golden_case_is_typed_and_rejects_unknown_fields():
    # guards: W2-D5 — malformed case definitions cannot enter scoring as loose dicts.
    raw = _case().model_dump(mode="json")
    raw["surprise"] = "ignored by an untyped loader"
    with pytest.raises(ValidationError):
        GoldenCase.model_validate(raw)


def test_schema_valid_known_fail():
    # guards: W2-D5/§7a — a malformed output must make schema_valid False.
    case = _case()
    bad = _observation(case).model_copy(update={"fields": {"results": "not-a-list"}})
    assert schema_valid(case, bad) is False


def test_citation_present_known_fail():
    # guards: W2-D5/W2-D6/§7a — an incomplete CitationV2 must fail.
    case = _case()
    bad_citation = _citation()
    del bad_citation["quote_or_value"]
    bad = _observation(case).model_copy(update={"citations": [bad_citation]})
    assert citation_present(case, bad) is False


def test_factually_consistent_known_fail():
    # guards: W2-D5/§7a — a structured value drift must fail deterministically.
    case = _case()
    fields = deepcopy(case.expected_fields)
    fields["results"][0]["value"] = "920"
    bad = _observation(case).model_copy(update={"fields": fields})
    assert factually_consistent(case, bad) is False


def test_safe_refusal_known_fail():
    # guards: W2-D5/§7a — unsafe NKDA phrasing must fail the canonical refusal match.
    case = _refusal_case()
    bad = _refusal_observation(case).model_copy(
        update={
            "refusal": RefusalObservation(
                code="confirm_with_patient",
                blocked=True,
                message="NKDA",
            )
        }
    )
    assert safe_refusal(case, bad) is False


def test_no_phi_in_logs_known_fail():
    # guards: W2-D7/§7a — a generated canary leak must make no_phi_in_logs False.
    case = _case()
    leaked = _observation(case).model_copy(
        update={"generated": GeneratedSurfaces(logs=[f"ZZPHI-{case.case_id}"])}
    )
    assert no_phi_in_logs(case, leaked) is False


@pytest.mark.parametrize("channel", ["observation", "logs", "traces", "results"])
def test_phi_scanner_checks_each_generated_surface(channel: str):
    # guards: W2-D7 — excluding fixture inputs must not make the scanner vacuous.
    case = _case()
    token = f"ZZPHI-{case.case_id}"
    observation = _observation(case)
    if channel == "observation":
        observation = observation.model_copy(update={"output": token})
    else:
        observation = observation.model_copy(
            update={"generated": GeneratedSurfaces(**{channel: [token]})}
        )

    scan = scan_generated_surfaces(case, observation)
    assert scan.clean is False
    assert channel in scan.leak_channels


def test_phi_scanner_excludes_canonical_fixture_inputs(tmp_path: Path):
    # guards: W2-D7 — deliberate canaries in canonical inputs are not scan targets.
    fixture = tmp_path / "canonical-input.txt"
    fixture.write_text("ZZPHI-unit-case")
    case = _case().model_copy(update={"fixture_path": str(fixture)})

    scan = scan_generated_surfaces(case, _observation(case))
    assert scan.clean is True
    assert scan.leak_channels == []


def test_generated_known_leak_self_test_trips_the_real_scanner():
    # guards: W2-D7/§7 — prove the canonical-input exclusion is not a blind spot.
    assert known_leak_self_test(_case()) is True


def _rubric_result(rubric: Rubric, passed: bool) -> CaseRubricResult:
    return CaseRubricResult(
        case_id=f"{rubric.value}-{passed}",
        rubric=rubric,
        applicable=True,
        passed=passed,
        detail="known threshold input",
    )


def test_deterministic_threshold_requires_100_percent():
    # guards: W2-D5 — one deterministic applicable failure must turn the gate red.
    rows = [_rubric_result(Rubric.SCHEMA_VALID, True) for _ in range(9)]
    rows.append(_rubric_result(Rubric.SCHEMA_VALID, False))
    summary = aggregate_scores(rows)[Rubric.SCHEMA_VALID]
    assert (summary.numerator, summary.denominator, summary.score) == (9, 10, 0.9)
    assert summary.passed is False
    assert "100% invariant" in summary.trigger


def test_factual_threshold_is_inclusive_at_90_percent():
    # guards: W2-D5 — 9/10 passes, while falling below 90% is red.
    at_threshold = [_rubric_result(Rubric.FACTUALLY_CONSISTENT, True) for _ in range(9)]
    at_threshold.append(_rubric_result(Rubric.FACTUALLY_CONSISTENT, False))
    passing = aggregate_scores(at_threshold)[Rubric.FACTUALLY_CONSISTENT]
    assert passing.score == 0.9 and passing.passed is True

    below = at_threshold.copy()
    below[0] = _rubric_result(Rubric.FACTUALLY_CONSISTENT, False)
    failing = aggregate_scores(below)[Rubric.FACTUALLY_CONSISTENT]
    assert failing.score == 0.8 and failing.passed is False
    assert ">=90% threshold" in failing.trigger


def test_executor_is_required_and_has_no_golden_observation_default():
    # guards: W2-D8 — the harness cannot pass by replaying expected_fields as output.
    parameter = inspect.signature(run_harness).parameters["executor"]
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_harness_executes_every_loaded_entry_and_applies_subset_rubrics(
    tmp_path: Path,
):
    # guards: W2-D5/D8 — executor seam supports offline recordings or live CI unchanged.
    factual_case = _case()
    refusal_case = _refusal_case().model_copy(update={"case_id": "refusal-case"})
    manifest = tmp_path / "cases.json"
    manifest.write_text(
        json.dumps(
            [factual_case.model_dump(mode="json"), refusal_case.model_dump(mode="json")]
        )
    )
    called: list[str] = []

    async def executor(case: GoldenCase) -> CaseObservation:
        called.append(case.case_id)
        if case.maps_to is Rubric.SAFE_REFUSAL:
            return _refusal_observation(case)
        return _observation(case)

    report = await run_harness(executor=executor, manifest_path=manifest)
    assert called == [factual_case.case_id, refusal_case.case_id]
    assert report.passed is True

    by_case = {
        row.case_id: {score.rubric for score in row.scores} for row in report.cases
    }
    universal = {Rubric.SCHEMA_VALID, Rubric.CITATION_PRESENT, Rubric.NO_PHI_IN_LOGS}
    assert by_case[factual_case.case_id] == universal | {Rubric.FACTUALLY_CONSISTENT}
    assert by_case[refusal_case.case_id] == universal | {Rubric.SAFE_REFUSAL}


@pytest.mark.asyncio
async def test_executor_error_is_a_failure_never_an_auto_pass(tmp_path: Path):
    # guards: W2-D8 — missing recording/provider output cannot silently green the gate.
    case = _case()
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([case.model_dump(mode="json")]))

    def executor(_case: GoldenCase) -> CaseObservation:
        raise RuntimeError("recording unavailable")

    report = await run_harness(executor=executor, manifest_path=manifest)
    assert report.passed is False
    assert all(score.passed is False for score in report.cases[0].scores)
    assert "executor error" in report.cases[0].scores[0].detail.lower()


@pytest.mark.asyncio
async def test_report_emits_required_threshold_arithmetic(tmp_path: Path):
    # guards: W2-D5 — denominator and triggering rule remain auditable in every run.
    case = _case()
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([case.model_dump(mode="json")]))

    report = await run_harness(
        executor=lambda loaded: _observation(loaded), manifest_path=manifest
    )
    rendered = render_report(report)
    for summary in report.categories:
        assert summary.denominator > 0
        assert summary.score is not None
        assert summary.trigger
        assert f"{summary.numerator}/{summary.denominator}" in rendered
        assert summary.rubric.value in rendered
