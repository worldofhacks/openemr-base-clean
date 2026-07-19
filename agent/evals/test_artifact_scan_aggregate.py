"""Focused contracts for the explicit closed eval-result scanner boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evals.artifact_scan import (
    ArtifactScanError,
    main,
    scan_eval_result_paths,
    scan_paths,
)
from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from evals.retrieval_adapters import retrieval_provenance
from evals.w2_models import Rubric


_RECORDINGS = Path(__file__).parent / "recordings" / "index.json"
_SOURCE_SHA = "9ce1559e816f97590ca24c68efa353c0e2892099"
_RUBRIC_ORDER = tuple(rubric.value for rubric in Rubric)
_UNIVERSAL = {
    Rubric.SCHEMA_VALID.value,
    Rubric.CITATION_PRESENT.value,
    Rubric.NO_PHI_IN_LOGS.value,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _applicable(case) -> set[str]:
    return _UNIVERSAL | {case.maps_to.value}


def _category(
    rubric: str,
    values: list[bool | None],
    *,
    baseline_score: float | None,
) -> dict[str, object]:
    numerator = sum(value is True for value in values)
    denominator = sum(value is not None for value in values)
    inconclusive = sum(value is None for value in values)
    current = numerator / denominator if denominator else 0.0
    delta = (current - baseline_score) * 100.0 if baseline_score is not None else None
    threshold = 0.9 if rubric == Rubric.FACTUALLY_CONSISTENT.value else 1.0
    if rubric == Rubric.FACTUALLY_CONSISTENT.value:
        passed = (
            denominator > 0
            and inconclusive == 0
            and current >= 0.9
            and (delta is None or -delta <= 5.0)
        )
        trigger = "met >=90% threshold and <=5 percentage-point regression"
    else:
        passed = denominator > 0 and inconclusive == 0 and numerator == denominator
        trigger = "met 100% invariant" if passed else "failed 100% invariant"
    return {
        "rubric": rubric,
        "numerator": numerator,
        "denominator": denominator,
        "inconclusive": inconclusive,
        "current_score": current,
        "baseline_score": baseline_score,
        "percentage_point_delta": delta,
        "threshold": threshold,
        "passed": passed,
        "trigger": trigger,
    }


def _case_rows(*, citation_passes: int | None = None, count: int = 50) -> list[dict]:
    rows: list[dict] = []
    for index, case in enumerate(load_golden_cases()[:count]):
        rubrics = {rubric: True for rubric in _applicable(case)}
        if citation_passes is not None and index >= citation_passes:
            rubrics[Rubric.CITATION_PRESENT.value] = False
        rows.append(
            {
                "case_id": case.case_id,
                "status": (
                    "FAIL"
                    if any(value is False for value in rubrics.values())
                    else "PASS"
                ),
                "rubrics": rubrics,
            }
        )
    return rows


def _categories(
    rows: list[dict], *, baseline_score: float | None
) -> list[dict[str, object]]:
    return [
        _category(
            rubric,
            [row["rubrics"][rubric] for row in rows if rubric in row["rubrics"]],
            baseline_score=baseline_score,
        )
        for rubric in _RUBRIC_ORDER
        if any(rubric in row["rubrics"] for row in rows)
    ]


def _metrics(case_count: int, *, retrieval_hit_count: int = 31) -> dict[str, object]:
    return {
        "elapsed_seconds": 899.0,
        "p50_ms": 100.0,
        "p95_ms": 200.0,
        "input_tokens": 1000,
        "output_tokens": 500,
        "cost_usd": 3.0,
        "retries": 0,
        "retrieval_hit_count": min(retrieval_hit_count, case_count * 5),
        "extraction_grounding_rate": 0.95,
    }


def _failed_live_result() -> dict[str, object]:
    # This is the retained shape of the real regression: citation 31/50. The 31 True
    # and 19 False case booleans intentionally agree with the category arithmetic.
    rows = _case_rows(citation_passes=31)
    return {
        "schema_version": 1,
        "status": "FAIL",
        "tier": "live",
        "source_sha": _SOURCE_SHA,
        "manifest_sha256": _sha256(DEFAULT_MANIFEST),
        "recordings_sha256": None,
        "retrieval": retrieval_provenance(),
        "case_count": 50,
        "executor_call_count": 50,
        "inconclusive_reason": None,
        "limits": {"max_cost_usd": 10.0, "max_seconds": 1800.0},
        "categories": _categories(rows, baseline_score=1.0),
        "cases": rows,
        "metrics": _metrics(50),
    }


def _recorded_result() -> dict[str, object]:
    rows = _case_rows()
    return {
        "schema_version": 1,
        "status": "PASS",
        "tier": "recorded",
        "source_sha": "local-uncommitted",
        "manifest_sha256": _sha256(DEFAULT_MANIFEST),
        "recordings_sha256": _sha256(_RECORDINGS),
        "retrieval": retrieval_provenance(),
        "case_count": 50,
        "executor_call_count": 50,
        "inconclusive_reason": None,
        "limits": None,
        # The recorded (PR) tier now always loads the committed recorded baseline,
        # so its categories must carry baseline/delta arithmetic (R02 point 7).
        "categories": _categories(rows, baseline_score=1.0),
        "cases": [],
        "metrics": _metrics(50, retrieval_hit_count=202),
    }


def _live_subset_result(count: int) -> dict[str, object]:
    rows = _case_rows(count=count)
    return {
        "schema_version": 1,
        "status": "PASS",
        "tier": "live_subset",
        "source_sha": _SOURCE_SHA,
        "manifest_sha256": _sha256(DEFAULT_MANIFEST),
        "recordings_sha256": None,
        "retrieval": retrieval_provenance(),
        "case_count": count,
        "executor_call_count": count,
        "inconclusive_reason": None,
        "limits": {"max_cost_usd": 1.0, "max_seconds": 600.0},
        "categories": _categories(rows, baseline_score=None),
        "cases": rows,
        "metrics": _metrics(count, retrieval_hit_count=count),
    }


def _runner_error_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "FAIL",
        "tier": "recorded",
        "source_sha": "local-uncommitted",
        "error_type": "RecordingIntegrityError",
    }


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def test_explicit_eval_result_exempts_validated_31_of_50_numeric_collision_only(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "results-tier2.json"
    _write(artifact, _failed_live_result())

    # Generic scanning remains literal, so the clinical numeric signature still collides.
    assert scan_paths([artifact]) == (False, 1, 1)
    # The explicit boundary validates all booleans/arithmetic before removing numbers.
    assert scan_eval_result_paths([artifact]) == (True, 1, 0)


def test_cli_requires_explicit_eval_result_boundary_for_numeric_exemption(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write(first, _failed_live_result())
    _write(second, _recorded_result())

    assert main([str(first)]) == 1
    assert (
        main(
            [
                "--eval-result",
                str(first),
                "--eval-result",
                str(second),
            ]
        )
        == 0
    )


def test_eval_result_flag_consumes_one_path_and_preserves_generic_paths(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    generic_dir = tmp_path / "generic"
    generic_dir.mkdir()
    generic = generic_dir / "report.txt"
    _write(result, _recorded_result())
    generic.write_text("operational report: clean", encoding="utf-8")

    assert main(["--eval-result", str(result), str(generic_dir)]) == 0


def test_closed_runner_error_aggregate_is_retained_and_scanned(tmp_path: Path) -> None:
    artifact = tmp_path / "runner-error.json"
    _write(artifact, _runner_error_result())

    assert scan_eval_result_paths([artifact]) == (True, 1, 0)

    leaked = _runner_error_result()
    leaked["error_type"] = "31"
    _write(artifact, leaked)
    with pytest.raises(ArtifactScanError, match="closed-schema"):
        scan_eval_result_paths([artifact])


def test_runner_error_aggregate_rejects_noncanonical_shape(tmp_path: Path) -> None:
    mutations = []

    extra = _runner_error_result()
    extra["details"] = "safe-looking detail"
    mutations.append(extra)

    status = _runner_error_result()
    status["status"] = "PASS"
    mutations.append(status)

    tier = _runner_error_result()
    tier["tier"] = "diagnostic"
    mutations.append(tier)

    exception_text = _runner_error_result()
    exception_text["error_type"] = "raw exception message"
    mutations.append(exception_text)

    for index, result in enumerate(mutations):
        artifact = tmp_path / f"runner-error-invalid-{index}.json"
        _write(artifact, result)
        with pytest.raises(ArtifactScanError, match="closed-schema"):
            scan_eval_result_paths([artifact])


@pytest.mark.parametrize(
    "string_value",
    [
        "31",
        "ZZPHI-lab-missing-unit",
        "ANTHROPIC_API_KEY=test-only",
    ],
)
def test_explicit_eval_result_still_scans_every_string(
    string_value: str, tmp_path: Path
) -> None:
    result = _failed_live_result()
    categories = result["categories"]
    assert isinstance(categories, list) and isinstance(categories[0], dict)
    categories[0]["trigger"] = string_value
    artifact = tmp_path / "string-leak.json"
    _write(artifact, result)

    assert scan_eval_result_paths([artifact]) == (False, 1, 1)


def test_generic_json_never_receives_eval_numeric_sanitization(tmp_path: Path) -> None:
    artifact = tmp_path / "generic.json"
    _write(artifact, {"operational_looking_number": 31})

    assert scan_paths([artifact]) == (False, 1, 1)
    with pytest.raises(ArtifactScanError, match="closed-schema"):
        scan_eval_result_paths([artifact])


@pytest.mark.parametrize("count", [1, 20])
def test_live_subset_accepts_only_bounded_canonical_case_sets(
    count: int, tmp_path: Path
) -> None:
    artifact = tmp_path / f"subset-{count}.json"
    _write(artifact, _live_subset_result(count))

    assert scan_eval_result_paths([artifact]) == (True, 1, 0)


def test_full_live_and_recorded_results_require_exactly_50_cases(
    tmp_path: Path,
) -> None:
    for index, result in enumerate((_failed_live_result(), _recorded_result())):
        valid = tmp_path / f"valid-{index}.json"
        _write(valid, result)
        assert scan_eval_result_paths([valid]) == (True, 1, 0)

        result["case_count"] = 49
        invalid = tmp_path / f"invalid-{index}.json"
        _write(invalid, result)
        with pytest.raises(ArtifactScanError, match="closed-schema"):
            scan_eval_result_paths([invalid])


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe{}",
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b'{"schema_version":-Infinity}',
        b'{"schema_version":1} trailing',
    ],
    ids=("utf8", "duplicate-key", "nan", "infinity", "negative-infinity", "trailing"),
)
def test_explicit_eval_result_rejects_non_strict_json(
    raw: bytes, tmp_path: Path
) -> None:
    artifact = tmp_path / "malformed.json"
    artifact.write_bytes(raw)

    with pytest.raises(ArtifactScanError):
        scan_eval_result_paths([artifact])
    assert main(["--eval-result", str(artifact)]) == 2


def test_closed_result_schema_and_arithmetic_fail_closed(tmp_path: Path) -> None:
    mutations: list[dict[str, object]] = []

    extra = _failed_live_result()
    extra["unexpected"] = "field"
    mutations.append(extra)

    manifest = _failed_live_result()
    manifest["manifest_sha256"] = "0" * 64
    mutations.append(manifest)

    unknown_case = _failed_live_result()
    unknown_case["cases"][0]["case_id"] = "not-a-canonical-case"
    mutations.append(unknown_case)

    unknown_rubric = _failed_live_result()
    unknown_rubric["categories"][0]["rubric"] = "made_up"
    mutations.append(unknown_rubric)

    unknown_status = _failed_live_result()
    unknown_status["cases"][0]["status"] = "MAYBE"
    mutations.append(unknown_status)

    wrong_arithmetic = _failed_live_result()
    citation = next(
        category
        for category in wrong_arithmetic["categories"]
        if category["rubric"] == Rubric.CITATION_PRESENT.value
    )
    citation["numerator"] = 30
    mutations.append(wrong_arithmetic)

    mismatched_boolean = _failed_live_result()
    mismatched_boolean["cases"][0]["rubrics"][Rubric.CITATION_PRESENT.value] = False
    mutations.append(mismatched_boolean)

    bool_as_number = _failed_live_result()
    bool_as_number["case_count"] = True
    mutations.append(bool_as_number)

    unbounded_metric = _failed_live_result()
    unbounded_metric["metrics"]["retries"] = 10_001
    mutations.append(unbounded_metric)

    unbounded_limit = _failed_live_result()
    unbounded_limit["limits"]["max_seconds"] = 100_000.0
    mutations.append(unbounded_limit)

    subset_too_large = _live_subset_result(20)
    subset_too_large["case_count"] = 21
    mutations.append(subset_too_large)

    subset_out_of_order = _live_subset_result(2)
    subset_out_of_order["cases"].reverse()
    mutations.append(subset_out_of_order)

    for index, result in enumerate(mutations):
        artifact = tmp_path / f"invalid-{index}.json"
        _write(artifact, result)
        with pytest.raises(ArtifactScanError):
            scan_eval_result_paths([artifact])
