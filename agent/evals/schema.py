"""EvalCase / EvalResult schema + the case runner (ARCHITECTURE.md §8, PRD eval requirements).

Every case names the failure mode it guards (`guards` = the F-#/D#/§ anchor) and its category
so the suite is auditable and happy-path-only is impossible to pass off as coverage. `run`
produces an outcome (sync or async); `check` is the pass-criteria over that outcome. An
exception inside a case is a FAIL (a case that errors is not a pass), never an uncaught crash.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvalCategory(str, Enum):
    BOUNDARY = "boundary"        # a designed edge/failure behavior
    INVARIANT = "invariant"      # a safety guarantee that must always hold
    REGRESSION = "regression"    # a past bug that must not return
    ADVERSARIAL = "adversarial"  # a hostile input that must be contained


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: EvalCategory
    guards: str                                    # the F-#/D#/§ anchor this case guards
    description: str                               # the failure mode guarded against
    expected: str                                  # human-readable expected outcome
    run: Callable[[], Any | Awaitable[Any]]        # produce the outcome
    check: Callable[[Any], bool]                   # pass-criteria over the outcome


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    category: str
    guards: str
    passed: bool
    detail: str


async def run_case(case: EvalCase) -> EvalResult:
    try:
        outcome = case.run()
        if inspect.isawaitable(outcome):
            outcome = await outcome
        passed = bool(case.check(outcome))
        detail = case.expected if passed else f"expected: {case.expected}"
    except Exception as exc:  # a case that errors is a FAIL, never a silent pass
        passed = False
        detail = f"ERROR {type(exc).__name__}: {exc}"
    return EvalResult(case_id=case.id, category=case.category.value, guards=case.guards,
                      passed=passed, detail=detail)
