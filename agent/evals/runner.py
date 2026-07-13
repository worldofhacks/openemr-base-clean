"""Eval runner + results export (ARCHITECTURE.md §8, §11 deliverable).

`python -m evals.runner` runs every case, writes `evals/results.json`, prints a per-case
table, and EXITS NON-ZERO if any case fails — that non-zero exit is the CI eval deploy-gate
(E8.4: the eval suite must be green before E9 deploys).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from evals.cases import EVAL_CASES
from evals.langfuse_publish import publish_if_configured
from evals.schema import EvalResult, run_case


async def run_all() -> list[EvalResult]:
    return [await run_case(case) for case in EVAL_CASES]


def summarize(results: list[EvalResult]) -> dict:
    by_cat: dict[str, dict[str, int]] = {}
    for r in results:
        c = by_cat.setdefault(r.category, {"passed": 0, "failed": 0})
        c["passed" if r.passed else "failed"] += 1
    return {
        "total": len(results),
        "passed": sum(r.passed for r in results),
        "failed": sum(not r.passed for r in results),
        "by_category": by_cat,
        "cases": [asdict(r) for r in results],
    }


def export(summary: dict, path: str | Path = "evals/results.json") -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    return out


def main() -> int:
    results = asyncio.run(run_all())
    summary = summarize(results)
    out = export(summary)
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.category:11s} {r.case_id:34s} guards {r.guards:16s} — {r.detail}")
    cats = Counter(r.category for r in results)
    print(f"\ncategories: {dict(cats)}")
    print(f"{summary['passed']}/{summary['total']} eval cases passed → {out}")
    # D16 / §8: Langfuse is an additive soft sink. The offline summary and exit code above
    # remain canonical even if the SDK itself fails unexpectedly outside the publisher seam.
    try:
        publish_if_configured(EVAL_CASES, results)
    except Exception:
        pass
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
