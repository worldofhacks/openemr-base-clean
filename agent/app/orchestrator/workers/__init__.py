"""Stub worker nodes for the W2-M3 LangGraph skeleton (W2_ARCHITECTURE.md §2).

Placeholders only: `stub_extractor` is replaced by the real intake-extractor in W2-M9
and `stub_retriever` by the real evidence-retriever in W2-M14. Each exposes
`WORKER_NAME` and an async `run(...)` that returns a trace-addressable output ref —
refs, never raw values, cross the handoff boundary (§2).
"""

from __future__ import annotations

from app.orchestrator.workers import stub_extractor, stub_retriever

__all__ = ["stub_extractor", "stub_retriever"]
