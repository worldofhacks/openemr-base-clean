"""Placeholder evidence-retriever worker for the B3 topology skeleton.

No real retrieval happens here — the stub exists so the supervisor has a genuine
worker node to route to, exercising the handoff contract (HandoffRecord emission,
span nesting) end to end. It returns only a trace-addressable artifact ref (§2:
refs, never raw values, cross the handoff boundary).

``run_evidence_retriever_stub`` is the intentionally explicit swap seam. The wave0 base
does not contain ``routes/evidence.py``; after the B3/B4 handoff this function can be
replaced by the typed evidence-search adapter without changing graph topology.

Traceability: W2-D2; W2-D4; W2_ARCHITECTURE.md §2.
"""

from __future__ import annotations

WORKER_NAME = "evidence_retriever_stub"


async def run_evidence_retriever_stub(
    *, correlation_id: str, turn: int, input_ref: str
) -> str:
    """Pretend to retrieve; return the trace-addressable ref of the (empty) snippet set."""
    # input_ref is acknowledged but unused — the real W2-M14 worker runs the hybrid
    # retriever over it; the stub produces nothing beyond its addressable output slot.
    del input_ref
    return f"trace:{correlation_id}/hop-{turn}/{WORKER_NAME}/output"


# Backward-compatible M3 alias; the B3 graph calls the explicit function above.
run = run_evidence_retriever_stub
