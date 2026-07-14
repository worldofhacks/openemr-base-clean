"""Compatibility evidence worker for graph tests without a configured corpus worker.

No real retrieval happens here — the stub exists so the supervisor has a genuine
worker node to route to, exercising the handoff contract (HandoffRecord emission,
span nesting) end to end. It returns only a trace-addressable artifact ref (§2:
refs, never raw values, cross the handoff boundary). Production injects the real
``evidence_retriever.build_evidence_worker`` callable.

Traceability: W2-D2; W2-D4; W2_ARCHITECTURE.md §2.
"""

from __future__ import annotations

from app.schemas.workers import WorkerInput, WorkerOutput

WORKER_NAME = "evidence_retriever_stub"


async def run_evidence_retriever_stub(
    payload: WorkerInput,
) -> WorkerOutput:
    """Return a canonical empty result; a healthy miss remains distinct from outage."""

    return WorkerOutput(
        correlation_id=payload.correlation_id,
        worker=WORKER_NAME,
        status="complete",
        artifact_refs=[],
        citation_refs=[],
        reason_code=None,
    )


# Backward-compatible M3 alias; the B3 graph calls the explicit function above.
run = run_evidence_retriever_stub
