"""Answer-composer shell for the B3 LangGraph topology.

The production composer will receive the handoff-defined verified fact, evidence
snippet, and CitationV2 types and enforce W2_ARCHITECTURE.md §5's verify-then-flush
rules. That interface is deliberately not guessed here while
``W2_B3_B4_HANDOFF.md`` is absent.

For the topology/observability skeleton, each collection contains trace-addressable
refs only. The shell acknowledges all three inputs and delegates unchanged to the
existing W1 loop, preserving W2-D2 and the frozen M3 equivalence contract. No
verification, rendering, or clinical-value logging happens in this module.

Traceability: W2-D2; W2_ARCHITECTURE.md §2/§5.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from app.orchestrator.loop import BriefResult


RunBrief = Callable[[], Awaitable[BriefResult]]


async def compose_answer_shell(
    *,
    verified_facts: Sequence[str],
    evidence_snippets: Sequence[str],
    citations: Sequence[str],
    run_brief: RunBrief,
) -> BriefResult:
    """Accept all future composer input lanes without implementing their contract yet.

    The three sequences intentionally carry refs, not raw clinical values. They become
    typed verified facts/snippets/CitationV2 objects only after the B3/B4 handoff. The
    W1 loop is the current shell body and its already-verified ``BriefResult`` is returned
    byte-for-byte unchanged.
    """

    del verified_facts, evidence_snippets, citations
    return await run_brief()
