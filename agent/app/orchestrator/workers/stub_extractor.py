"""Placeholder intake-extractor worker for the B3 topology skeleton.

No real extraction happens here — the stub exists so the supervisor has a genuine
worker node to route to, exercising the handoff contract (HandoffRecord emission,
span nesting) end to end. It returns only a trace-addressable artifact ref (§2:
refs, never raw values, cross the handoff boundary).

``run_intake_extractor_stub`` is the intentionally explicit swap seam. It must not call
the real extraction interface until ``W2_B3_B4_HANDOFF.md`` lands.

Traceability: W2-D2; W2_ARCHITECTURE.md §2.
"""

from __future__ import annotations

WORKER_NAME = "intake_extractor_stub"


async def run_intake_extractor_stub(
    *, correlation_id: str, turn: int, input_ref: str
) -> str:
    """Pretend to extract; return the trace-addressable ref of the (empty) artifact."""
    # input_ref is acknowledged but unused — the real W2-M9 worker reads the referenced
    # document artifact; the stub produces nothing beyond its addressable output slot.
    del input_ref
    return f"trace:{correlation_id}/hop-{turn}/{WORKER_NAME}/output"


# Preserve the original M3 stub seam for callers outside the graph while making the B3
# node call the clearly named function above. Remove only during the handoff-driven swap.
run = run_intake_extractor_stub
