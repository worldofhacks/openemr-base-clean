"""E5 — daily LLM cost cap (D4 cost control; E5 is the first real LLM spend).

The cap is a guard, not a meter for its own sake: a trip must degrade the turn to
the deterministic D13 fallback (tested in test_orchestrator_loop.py), never an
uncapped spend and never a hard error. Here we prove the accounting: per-model
pricing, cache reads billed cheaply, day-bucketed spend that resets on a new UTC
day, and guard() raising exactly at the cap.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.llm.cost import CostCapExceeded, DailyCostCap
from app.llm.provider import Usage

DAY1 = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
DAY2 = datetime(2026, 7, 10, 0, 30, tzinfo=timezone.utc)


def _fixed(dt):
    return lambda: dt


def test_cost_of_uses_per_model_prices():
    cap = DailyCostCap(cap_usd=100.0, now=_fixed(DAY1))
    # Sonnet 4.6 = $3/1M in, $15/1M out (D4).
    cost = cap.cost_of(Usage(input_tokens=1_000_000, output_tokens=1_000_000), "claude-sonnet-4-6")
    assert cost == pytest.approx(18.0)


def test_cache_read_is_far_cheaper_than_fresh_input():
    cap = DailyCostCap(cap_usd=100.0, now=_fixed(DAY1))
    fresh = cap.cost_of(Usage(input_tokens=1_000_000), "claude-sonnet-4-6")
    cached = cap.cost_of(Usage(cache_read_input_tokens=1_000_000), "claude-sonnet-4-6")
    assert cached == pytest.approx(0.30)  # ~0.1x input (R1: the 90%-off cache)
    assert cached < fresh


def test_guard_trips_when_daily_cap_exceeded():
    cap = DailyCostCap(cap_usd=1.0, now=_fixed(DAY1))
    cap.record(Usage(input_tokens=1_000_000), "claude-sonnet-4-6")  # $3 > $1 cap
    with pytest.raises(CostCapExceeded):
        cap.guard()


def test_guard_passes_below_cap():
    cap = DailyCostCap(cap_usd=10.0, now=_fixed(DAY1))
    cap.record(Usage(output_tokens=100_000), "claude-sonnet-4-6")  # $1.50 < $10
    cap.guard()  # must not raise
    assert cap.spent_today() == pytest.approx(1.5)


def test_spend_resets_on_new_utc_day():
    clock = {"t": DAY1}
    cap = DailyCostCap(cap_usd=100.0, now=lambda: clock["t"])
    cap.record(Usage(input_tokens=1_000_000), "claude-sonnet-4-6")
    assert cap.spent_today() == pytest.approx(3.0)
    clock["t"] = DAY2
    assert cap.spent_today() == 0.0  # new day → fresh bucket


def test_unknown_model_does_not_silently_zero_cost():
    cap = DailyCostCap(cap_usd=100.0, now=_fixed(DAY1))
    # An unpriced model must not be treated as free (that would defeat the cap).
    with pytest.raises(KeyError):
        cap.cost_of(Usage(input_tokens=1_000), "made-up-model")
