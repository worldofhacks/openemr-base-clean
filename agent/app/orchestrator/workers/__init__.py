"""Stub worker nodes for the B3 LangGraph topology (W2-D2, W2_ARCHITECTURE.md §2).

Placeholders only: `stub_extractor.run_intake_extractor_stub` and
`stub_retriever.run_evidence_retriever_stub` are the named B3 swap seams. Each returns a
trace-addressable output ref — refs, never raw values, cross the handoff boundary (§2).
The real interfaces wait for ``W2_B3_B4_HANDOFF.md``.
"""

from __future__ import annotations

from app.orchestrator.workers import stub_extractor, stub_retriever

__all__ = ["stub_extractor", "stub_retriever"]
