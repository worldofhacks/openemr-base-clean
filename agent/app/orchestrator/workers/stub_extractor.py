"""Compatibility intake worker for graph tests before the B2 callable is injected.

No real extraction happens here — the stub exists so the supervisor has a genuine
worker node to route to, exercising the handoff contract (HandoffRecord emission,
span nesting) end to end. It returns only a trace-addressable artifact ref (§2:
refs, never raw values, cross the handoff boundary). Production integration injects
B2 through ``extraction_adapter.bind_extraction_worker``.

Traceability: W2-D2; W2_ARCHITECTURE.md §2.
"""

from __future__ import annotations

from app.schemas.workers import WorkerInput, WorkerOutput

WORKER_NAME = "intake_extractor_stub"


async def run_intake_extractor_stub(
    payload: WorkerInput,
) -> WorkerOutput:
    """Return a canonical empty output; never fabricate an extraction artifact."""

    return WorkerOutput(
        correlation_id=payload.correlation_id,
        worker=WORKER_NAME,
        status="complete",
        artifact_refs=[],
        citation_refs=[],
        reason_code=None,
    )


# Preserve the original M3 stub seam for callers outside the graph while making the B3
# node call the clearly named function above. Remove only during the handoff-driven swap.
run = run_intake_extractor_stub
