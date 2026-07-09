"""Daily LLM cost cap (D4 cost control; E5 is the first real LLM spend).

A guard, not a meter: when the day's spend reaches the cap, `guard()` raises and the
orchestrator degrades the turn to the deterministic D13 fallback — bounded spend and a
grounded answer, never an uncapped bill and never a hard error. Spend is bucketed by
UTC day and resets on rollover.

Pricing is per-model (D4); cache reads are billed at ~0.1x input (R1: the 90%-off cache)
and cache writes at ~1.25x input. Demo posture: the counter is in-process. Production
with multiple replicas needs a shared counter (e.g. Redis/DB) — flagged, not silently
assumed away.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from app.llm.provider import Usage

# $ per 1,000,000 tokens: (input, output). Sonnet 4.6 primary + Haiku 4.5 utility (D4).
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-8": (5.0, 25.0),
}
_CACHE_READ_MULT = 0.1    # cache read ≈ 0.1x the input rate (R1)
_CACHE_WRITE_MULT = 1.25  # 5-minute ephemeral cache write ≈ 1.25x the input rate


class CostCapExceeded(RuntimeError):
    """The daily USD cap has been reached. The orchestrator degrades to the D13 fallback."""


def estimate_cost(usage: Usage, model: str) -> float:
    """USD cost of one usage record under the model's D4 pricing. Cache reads ≈ 0.1x input
    (R1), cache writes ≈ 1.25x input. KeyError on an unpriced model — never silently free.
    Shared by the cost cap and the observability tracer so pricing lives in one place."""
    in_rate, out_rate = _MODEL_PRICES[model]
    per = 1_000_000
    return (
        usage.input_tokens / per * in_rate
        + usage.output_tokens / per * out_rate
        + usage.cache_read_input_tokens / per * in_rate * _CACHE_READ_MULT
        + usage.cache_creation_input_tokens / per * in_rate * _CACHE_WRITE_MULT
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DailyCostCap:
    def __init__(self, *, cap_usd: float, now: Callable[[], datetime] = _utcnow):
        self.cap_usd = cap_usd
        self._now = now
        self._day: str | None = None
        self._spent: float = 0.0

    def cost_of(self, usage: Usage, model: str) -> float:
        return estimate_cost(usage, model)  # KeyError on an unpriced model — never free

    def _roll(self) -> None:
        today = self._now().strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._spent = 0.0

    def record(self, usage: Usage, model: str) -> None:
        self._roll()
        self._spent += self.cost_of(usage, model)

    def spent_today(self) -> float:
        self._roll()
        return self._spent

    def guard(self) -> None:
        if self.spent_today() >= self.cap_usd:
            raise CostCapExceeded(
                f"daily LLM cost cap reached: ${self.spent_today():.4f} >= ${self.cap_usd:.2f}")
