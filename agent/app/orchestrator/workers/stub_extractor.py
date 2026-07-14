"""Placeholder intake-extractor worker (W2-M3 skeleton; replaced by W2-M9).

No real extraction happens here — the stub exists so the supervisor has a genuine
worker node to route to, exercising the handoff contract (HandoffRecord emission,
span nesting) end to end. It returns only a trace-addressable artifact ref (§2:
refs, never raw values, cross the handoff boundary).
"""

from __future__ import annotations

WORKER_NAME = "stub_extractor"


async def run(*, correlation_id: str, turn: int, input_ref: str) -> str:
    """Pretend to extract; return the trace-addressable ref of the (empty) artifact."""
    # input_ref is acknowledged but unused — the real W2-M9 worker reads the referenced
    # document artifact; the stub produces nothing beyond its addressable output slot.
    del input_ref
    return f"trace:{correlation_id}/hop-{turn}/{WORKER_NAME}/output"
