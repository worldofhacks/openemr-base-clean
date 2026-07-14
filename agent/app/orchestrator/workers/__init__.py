"""Worker nodes for the Week 2 LangGraph graph (W2_ARCHITECTURE.md §2).

The production B2 extractor is available alongside the original spike stubs until
B3 swaps the node wiring; `stub_retriever` is replaced by W2-M14. Each exposes
`WORKER_NAME` and an async `run(...)` that returns a trace-addressable output ref —
refs, never raw values, cross the handoff boundary (§2).
"""

from __future__ import annotations

from app.orchestrator.workers import stub_extractor, stub_retriever
from app.orchestrator.workers.intake_extractor import (
    run_extraction_worker,
    run_intake_extractor,
)

__all__ = [
    "run_extraction_worker",
    "run_intake_extractor",
    "stub_extractor",
    "stub_retriever",
]
