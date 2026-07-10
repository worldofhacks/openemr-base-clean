"""The eval gate as pytest (ARCHITECTURE.md §8). One test per case (so a failure names the
exact guard) + a meta-test proving the harness detects both pass and fail (E8.1). Run with
`pytest evals` (the CI gate also runs `python -m evals.runner`, which shares this dataset)."""

from __future__ import annotations

import pytest

from evals.cases import EVAL_CASES
from evals.schema import EvalCase, EvalCategory, run_case


@pytest.mark.parametrize("case", EVAL_CASES, ids=[c.id for c in EVAL_CASES])
async def test_eval_case(case: EvalCase):
    result = await run_case(case)
    assert result.passed, (
        f"[{case.category.value}] {case.id} (guards {case.guards}) failed — {result.detail}")


def test_every_category_is_represented():
    # Production-grade: happy-path-only is not coverage. Boundary + invariant + regression +
    # adversarial must each have at least one case.
    present = {c.category for c in EVAL_CASES}
    assert present == set(EvalCategory), f"missing eval categories: {set(EvalCategory) - present}"


async def test_harness_detects_pass_and_fail():
    # Meta-test: the runner must distinguish a passing case from a failing one, and treat a
    # case that raises as a FAIL (not a silent pass).
    ok = EvalCase(id="meta-ok", category=EvalCategory.INVARIANT, guards="meta",
                  description="trivial pass", expected="1==1", run=lambda: 1, check=lambda o: o == 1)
    bad = EvalCase(id="meta-bad", category=EvalCategory.INVARIANT, guards="meta",
                   description="trivial fail", expected="1==2", run=lambda: 1, check=lambda o: o == 2)
    boom = EvalCase(id="meta-boom", category=EvalCategory.INVARIANT, guards="meta",
                    description="raises", expected="no error", run=lambda: 1 / 0, check=lambda o: True)
    assert (await run_case(ok)).passed is True
    assert (await run_case(bad)).passed is False
    boom_result = await run_case(boom)
    assert boom_result.passed is False and boom_result.detail.startswith("ERROR")
