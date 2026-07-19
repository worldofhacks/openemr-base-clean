"""One place builds the fused per-encounter summary record (R05 / AF-P1-04).

The audit found two ``encounter.summary`` emitters zero-filling each other's halves:
the ingestion job lane (``app/ingestion/telemetry.py``) hard-coded ``retrieval_hit_count``
and the serving-turn lane (``app/observability/langfuse.py``) hard-coded both retrieval
hits and grounding rate. Both lanes now delegate the record's shape to this module, so
one definition carries the PDF p.5 Core Req 7 fields — tool sequence, latency by step,
token usage, cost estimate, retrieval hits, and extraction confidence — and a lane can
only omit a half it genuinely does not have (recorded values default to zero, they are
never structurally pinned to zero).
"""

from __future__ import annotations

from collections.abc import Sequence

_MAX_STEPS = 64
_MAX_OUTCOMES = 64
_MAX_RETRIEVAL_HITS = 20


def encounter_summary_attributes(
    *,
    steps: Sequence[tuple[str, float]],
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    retrieval_hit_count: int,
    extraction_grounding_rate: float,
    verification_outcomes: Sequence[str],
) -> dict[str, object]:
    """Return the fused ``EncounterSummaryAttributes`` payload, bounded and clamped.

    The result still passes the closed event registry (``EncounterSummaryAttributes``),
    which remains the enforcement boundary; this helper only guarantees both lanes
    build the same shape from their recorded halves.
    """

    bounded_steps = list(steps)[:_MAX_STEPS]
    return {
        "ordered_steps": [name for name, _latency in bounded_steps],
        "step_latencies_ms": [
            max(float(latency), 0.0) for _name, latency in bounded_steps
        ],
        "input_tokens": max(int(input_tokens), 0),
        "output_tokens": max(int(output_tokens), 0),
        "cost_usd": max(float(cost_usd), 0.0),
        "retrieval_hit_count": min(max(int(retrieval_hit_count), 0), _MAX_RETRIEVAL_HITS),
        "extraction_grounding_rate": min(max(float(extraction_grounding_rate), 0.0), 1.0),
        "verification_outcomes": list(verification_outcomes)[:_MAX_OUTCOMES],
    }
