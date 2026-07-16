"""Executable Week 2 recorded/live graded gate.

Examples (from ``agent/``):

    python -m evals.w2_runner run --tier recorded
    SOURCE_SHA=<40-hex-sha> python -m evals.w2_runner run --tier live
    python -m evals.w2_runner baseline --results evals/results-tier2.json

Live execution is bounded by cost and wall-clock ceilings.  A local live run may
produce a candidate result without a baseline, but CI and ``main`` require the
canonical, reviewed baseline before the gate can pass.  Baseline generation is an
explicit local command and is disabled in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, cast

from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from evals.harness import EvalInconclusiveError, render_report, run_harness
from evals.live_executor import LiveExecutor, make_live_executor
from evals.recorded_executor import (
    DEFAULT_RECORDINGS,
    RecordedExecutor,
    RecordingIntegrityError,
    make_recorded_executor,
)
from evals.w2_models import (
    BaselineCategory,
    EvalBaseline,
    HarnessReport,
    Rubric,
    RunStatus,
)


DEFAULT_BASELINE = Path(__file__).parent / "w2_baseline.json"
MIN_GRADED_CASES = 50
DEFAULT_LIVE_MAX_COST_USD = 10.0
DEFAULT_LIVE_MAX_SECONDS = 1_800.0
_BUDGET_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class LiveGateLimits:
    """Closed live-spend bounds; exhaustion is INCONCLUSIVE, never a case failure."""

    max_cost_usd: float = DEFAULT_LIVE_MAX_COST_USD
    max_seconds: float = DEFAULT_LIVE_MAX_SECONDS

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.max_cost_usd)
            or self.max_cost_usd <= 0
            or not math.isfinite(self.max_seconds)
            or self.max_seconds <= 0
        ):
            raise ValueError("live gate cost/time ceilings must be finite and positive")


class _LiveGateInconclusive(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_sha() -> str:
    return next(
        (
            value
            for name in ("SOURCE_SHA", "GITHUB_SHA", "CI_COMMIT_SHA")
            if (value := os.environ.get(name))
        ),
        "local-uncommitted",
    )


def _percentile(values: Sequence[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, (len(ordered) * percentile + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def _load_baseline(path: Path) -> EvalBaseline | None:
    if not path.is_file():
        return None
    return EvalBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def _canonical_result_sha256(result: dict[str, object]) -> str:
    payload = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_reviewed_baseline(
    baseline: EvalBaseline,
    *,
    manifest_path: Path,
    case_count: int,
) -> None:
    if baseline.case_count != case_count:
        raise ValueError("reviewed live baseline case count does not match the manifest")
    if baseline.manifest_sha256 != _sha256(manifest_path):
        raise ValueError("reviewed live baseline is stale for the manifest")
    if not re.fullmatch(r"[0-9a-f]{40}", baseline.source_sha):
        raise ValueError("reviewed live baseline is not bound to an exact source SHA")
    _validate_baseline_categories(baseline)


def _validate_baseline_categories(baseline: EvalBaseline) -> None:
    categories = {item.rubric: item for item in baseline.categories}
    if set(categories) != set(Rubric) or len(categories) != len(baseline.categories):
        raise ValueError("reviewed live baseline does not contain every rubric exactly once")
    for rubric, category in categories.items():
        arithmetic_score = category.numerator / category.denominator
        if not math.isclose(category.score, arithmetic_score, abs_tol=1e-12):
            raise ValueError("reviewed live baseline category arithmetic is inconsistent")
        if rubric is Rubric.FACTUALLY_CONSISTENT:
            if arithmetic_score < 0.90:
                raise ValueError("reviewed live baseline factual score is below threshold")
        elif category.numerator != category.denominator or arithmetic_score != 1.0:
            raise ValueError("reviewed live baseline deterministic category is not green")


def _is_ci() -> bool:
    truthy = {"1", "true", "yes"}
    return any(
        str(os.environ.get(name, "")).casefold() in truthy
        for name in ("CI", "GITHUB_ACTIONS")
    )


def _ci_or_main_requires_baseline() -> bool:
    if _is_ci():
        return True
    github_main = os.environ.get("GITHUB_REF") == "refs/heads/main"
    gitlab_branch = os.environ.get("CI_COMMIT_BRANCH")
    gitlab_main = gitlab_branch == "main" or (
        bool(gitlab_branch)
        and gitlab_branch == os.environ.get("CI_DEFAULT_BRANCH")
    )
    return github_main or gitlab_main


def _aggregate_result(
    *,
    tier: str,
    report: HarnessReport,
    manifest_path: Path,
    case_count: int,
    executor: RecordedExecutor | LiveExecutor | None,
    elapsed_seconds: float,
    live_limits: LiveGateLimits | None = None,
    inconclusive_reason: str | None = None,
) -> dict[str, object]:
    latencies = list(getattr(executor, "latencies_ms", [])) if executor else []
    usage = getattr(executor, "usage", None)
    categories = [
        {
            "rubric": item.rubric.value,
            "numerator": item.numerator,
            "denominator": item.denominator,
            "inconclusive": item.inconclusive,
            "current_score": item.current_score,
            "baseline_score": item.baseline_score,
            "percentage_point_delta": item.percentage_point_delta,
            "threshold": item.threshold,
            "passed": item.passed,
            "trigger": item.trigger,
        }
        for item in report.categories
    ]
    # Tier 1 is the ubiquitous no-secret gate, so its artifact is category-level only.
    # Tier 2 retains per-case booleans (still no clinical content) because a reviewed baseline
    # must prove every live case completed green before it can be generated.
    cases = (
        [
            {
                "case_id": item.case_id,
                "status": item.status.value,
                "rubrics": {
                    score.rubric.value: score.passed
                    for score in item.scores
                    if score.applicable
                },
            }
            for item in report.cases
        ]
        if tier == "live"
        else []
    )
    recordings_sha = _sha256(DEFAULT_RECORDINGS) if tier == "recorded" else None
    return {
        "schema_version": 1,
        "status": report.status.value,
        "tier": tier,
        "source_sha": _source_sha(),
        "manifest_sha256": _sha256(manifest_path),
        "recordings_sha256": recordings_sha,
        "case_count": case_count,
        "executor_call_count": int(getattr(executor, "call_count", 0)),
        "inconclusive_reason": inconclusive_reason,
        "limits": (
            {
                "max_cost_usd": live_limits.max_cost_usd,
                "max_seconds": live_limits.max_seconds,
            }
            if live_limits is not None
            else None
        ),
        "categories": categories,
        "cases": cases,
        "metrics": {
            "elapsed_seconds": round(elapsed_seconds, 6),
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "input_tokens": int(getattr(usage, "input_tokens", 0)),
            "output_tokens": int(getattr(usage, "output_tokens", 0)),
            "cost_usd": round(float(getattr(executor, "cost_usd", 0.0)), 8),
            "retries": int(getattr(executor, "retries", 0)),
            "retrieval_hit_count": int(
                getattr(executor, "retrieval_hit_count", 0)
            ),
            "extraction_grounding_rate": (
                sum(getattr(executor, "grounding_rates", []))
                / len(getattr(executor, "grounding_rates", []))
                if executor and getattr(executor, "grounding_rates", [])
                else None
            ),
        },
    }


def _inconclusive_report() -> HarnessReport:
    return HarnessReport(
        status=RunStatus.INCONCLUSIVE,
        passed=False,
        cases=[],
        categories=[],
    )


async def _run_live_harness_bounded(
    *,
    executor: LiveExecutor,
    manifest_path: Path,
    baseline: EvalBaseline | None,
    limits: LiveGateLimits,
) -> HarnessReport:
    """Run the harness while independently enforcing wall-clock and spend ceilings."""

    started = time.perf_counter()
    task = asyncio.create_task(
        run_harness(
            executor=executor,
            manifest_path=manifest_path,
            baseline=baseline,
            required_min_cases=MIN_GRADED_CASES,
        )
    )
    try:
        while not task.done():
            elapsed = time.perf_counter() - started
            if elapsed >= limits.max_seconds:
                raise _LiveGateInconclusive("time_ceiling")
            cost_usd = float(executor.cost_usd)
            if not math.isfinite(cost_usd) or cost_usd < 0:
                raise _LiveGateInconclusive("cost_evidence_invalid")
            if cost_usd > limits.max_cost_usd:
                raise _LiveGateInconclusive("cost_ceiling")
            await asyncio.wait(
                {task},
                timeout=min(_BUDGET_POLL_SECONDS, limits.max_seconds - elapsed),
            )
        report = task.result()
        cost_usd = float(executor.cost_usd)
        if not math.isfinite(cost_usd) or cost_usd < 0:
            raise _LiveGateInconclusive("cost_evidence_invalid")
        if cost_usd > limits.max_cost_usd:
            raise _LiveGateInconclusive("cost_ceiling")
        if time.perf_counter() - started > limits.max_seconds:
            raise _LiveGateInconclusive("time_ceiling")
        return report
    except _LiveGateInconclusive:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


async def run_gate(
    *,
    tier: str,
    manifest_path: Path,
    recordings_path: Path,
    baseline_path: Path,
    require_reviewed_baseline: bool = False,
    live_limits: LiveGateLimits | None = None,
) -> tuple[HarnessReport, dict[str, object]]:
    cases = load_golden_cases(manifest_path)
    if len(cases) < MIN_GRADED_CASES:
        raise ValueError(
            f"graded manifest requires at least {MIN_GRADED_CASES} cases; loaded {len(cases)}"
        )
    executor: RecordedExecutor | LiveExecutor | None = None
    limits = live_limits or LiveGateLimits()
    inconclusive_reason: str | None = None
    started = time.perf_counter()
    try:
        if tier == "recorded":
            executor = make_recorded_executor(recordings_path=recordings_path)
            if executor.recording_case_ids != frozenset(case.case_id for case in cases):
                raise RecordingIntegrityError(
                    "recording index does not exactly match the loaded manifest"
                )
            baseline = None
        elif tier == "live":
            baseline = _load_baseline(baseline_path)
            if baseline is None and require_reviewed_baseline:
                raise _LiveGateInconclusive("reviewed_baseline_required")
            if baseline is not None:
                _validate_reviewed_baseline(
                    baseline,
                    manifest_path=manifest_path,
                    case_count=len(cases),
                )
            executor = make_live_executor()
        else:
            raise ValueError("tier must be recorded or live")
        if tier == "live":
            report = await _run_live_harness_bounded(
                executor=cast(LiveExecutor, executor),
                manifest_path=manifest_path,
                baseline=baseline,
                limits=limits,
            )
        else:
            report = await run_harness(
                executor=executor,
                manifest_path=manifest_path,
                baseline=baseline,
                required_min_cases=MIN_GRADED_CASES,
            )
    except EvalInconclusiveError:
        report = _inconclusive_report()
        inconclusive_reason = "provider_or_parse_exhaustion"
    except _LiveGateInconclusive as exc:
        report = _inconclusive_report()
        inconclusive_reason = exc.reason
    if report.status is RunStatus.INCONCLUSIVE and inconclusive_reason is None:
        inconclusive_reason = "case_infrastructure_exhaustion"
    elapsed = time.perf_counter() - started
    result = _aggregate_result(
        tier=tier,
        report=report,
        manifest_path=manifest_path,
        case_count=len(cases),
        executor=executor,
        elapsed_seconds=elapsed,
        live_limits=limits if tier == "live" else None,
        inconclusive_reason=inconclusive_reason,
    )
    return report, result


def write_aggregate(result: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def baseline_from_result(result: dict[str, object]) -> EvalBaseline:
    raw_case_count = result.get("case_count")
    if (
        result.get("status") != RunStatus.PASS.value
        or result.get("tier") != "live"
        or not isinstance(raw_case_count, int)
        or raw_case_count < MIN_GRADED_CASES
        or result.get("executor_call_count") != raw_case_count
        or result.get("inconclusive_reason") is not None
    ):
        raise ValueError("baseline requires a complete green live 50-case result")
    source_sha = result.get("source_sha")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("baseline requires a result bound to an exact reviewed SHA")
    raw_cases = result.get("cases")
    if (
        not isinstance(raw_cases, list)
        or len(raw_cases) != raw_case_count
        or len({case.get("case_id") for case in raw_cases if isinstance(case, dict)})
        != raw_case_count
        or any(
            not isinstance(case, dict)
            or case.get("status") != RunStatus.PASS.value
            or not isinstance(case.get("rubrics"), dict)
            or not case["rubrics"]
            or any(value is not True for value in case["rubrics"].values())
            for case in raw_cases
        )
    ):
        raise ValueError("baseline requires 50 unique complete green case summaries")
    limits = result.get("limits")
    metrics = result.get("metrics")
    if not isinstance(limits, dict) or not isinstance(metrics, dict):
        raise ValueError("baseline requires aggregate live cost/time evidence")
    try:
        cost = float(metrics["cost_usd"])
        elapsed = float(metrics["elapsed_seconds"])
        max_cost = float(limits["max_cost_usd"])
        max_seconds = float(limits["max_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("baseline live cost/time evidence is invalid") from exc
    if any(
        not math.isfinite(value) or value < 0
        for value in (cost, elapsed, max_cost, max_seconds)
    ) or max_cost == 0 or max_seconds == 0:
        raise ValueError("baseline live cost/time evidence is invalid")
    if cost > max_cost or elapsed > max_seconds:
        raise ValueError("baseline result exceeded its live cost/time ceiling")
    raw_categories = result.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ValueError("baseline result has no category arithmetic")
    categories: list[BaselineCategory] = []
    seen_rubrics: set[Rubric] = set()
    try:
        for raw in raw_categories:
            if not isinstance(raw, dict) or raw.get("passed") is not True:
                raise ValueError("baseline result contains a failing category")
            rubric = Rubric(str(raw["rubric"]))
            if rubric in seen_rubrics:
                raise ValueError("baseline result repeats a category")
            seen_rubrics.add(rubric)
            categories.append(
                BaselineCategory(
                    rubric=rubric,
                    numerator=int(raw["numerator"]),
                    denominator=int(raw["denominator"]),
                    score=float(raw["current_score"]),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("baseline result"):
            raise
        raise ValueError("baseline result category arithmetic is invalid") from exc
    if seen_rubrics != set(Rubric):
        raise ValueError("baseline result does not contain every rubric")
    candidate = EvalBaseline(
        case_count=raw_case_count,
        manifest_sha256=str(result["manifest_sha256"]),
        source_sha=source_sha,
        generated_from_result_sha256=_canonical_result_sha256(result),
        categories=categories,
    )
    _validate_baseline_categories(candidate)
    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.w2_runner")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--tier", required=True, choices=("recorded", "live"))
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    run.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    run.add_argument(
        "--require-reviewed-baseline",
        action="store_true",
        help="require the canonical reviewed baseline (automatic in CI and on main)",
    )
    run.add_argument(
        "--max-cost-usd",
        type=float,
        default=DEFAULT_LIVE_MAX_COST_USD,
        help="live-tier spend ceiling; exhaustion is INCONCLUSIVE",
    )
    run.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_LIVE_MAX_SECONDS,
        help="live-tier wall-clock ceiling; exhaustion is INCONCLUSIVE",
    )
    run.add_argument("--output", type=Path)
    baseline = commands.add_parser("baseline")
    baseline.add_argument("--results", type=Path, required=True)
    baseline.add_argument("--output", type=Path, default=DEFAULT_BASELINE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "baseline":
        if _is_ci():
            print("baseline=REFUSED reason=ci_compare_only", file=sys.stderr)
            return 1
        try:
            result = json.loads(args.results.read_text(encoding="utf-8"))
            baseline = baseline_from_result(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                baseline.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            print(f"baseline=FAIL error={type(exc).__name__}", file=sys.stderr)
            return 1
        return 0

    output = args.output or Path(
        "evals/results-tier1.json" if args.tier == "recorded" else "evals/results-tier2.json"
    )
    try:
        require_reviewed_baseline = (
            args.require_reviewed_baseline or _ci_or_main_requires_baseline()
        )
        if (
            args.tier == "live"
            and require_reviewed_baseline
            and args.baseline.resolve() != DEFAULT_BASELINE.resolve()
        ):
            raise ValueError("CI/main live gates require the canonical reviewed baseline")
        report, result = asyncio.run(
            run_gate(
                tier=args.tier,
                manifest_path=args.manifest,
                recordings_path=args.recordings,
                baseline_path=args.baseline,
                require_reviewed_baseline=require_reviewed_baseline,
                live_limits=LiveGateLimits(
                    max_cost_usd=args.max_cost_usd,
                    max_seconds=args.max_seconds,
                ),
            )
        )
    except Exception as exc:
        # Configuration/recording integrity is a hard FAIL, not inconclusive. The
        # aggregate intentionally records only the exception class, never its text.
        failure = {
            "schema_version": 1,
            "status": RunStatus.FAIL.value,
            "tier": args.tier,
            "source_sha": _source_sha(),
            "error_type": type(exc).__name__,
        }
        write_aggregate(failure, output)
        print(f"gate=FAIL error={type(exc).__name__}", file=sys.stderr)
        return 1
    write_aggregate(result, output)
    print(render_report(report))
    if report.status is RunStatus.PASS:
        return 0
    return 2 if report.status is RunStatus.INCONCLUSIVE else 1


if __name__ == "__main__":
    raise SystemExit(main())
