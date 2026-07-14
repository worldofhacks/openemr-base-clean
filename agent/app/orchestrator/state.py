"""Per-turn typed graph state + HandoffRecord closed contracts (W2-M3, W2_ARCHITECTURE.md §2).

`SupervisorDecision` and `ReasonCode` are the CLOSED enums of the supervisor-worker
boundary (§2 locked-decision): a worker cannot smuggle a new decision or an invented
reason code past validation, and `HandoffRecord` is strict Pydantic v2
(`extra="forbid"`, `strict=True`, frozen) so extra/unknown fields are rejected at the
boundary rather than logged as-is. `input_ref`/`output_ref` are trace-addressable ids —
refs, never raw values, cross the handoff boundary (§2 WorkerInput/WorkerOutput rule).

`GraphState` is the per-turn LangGraph state (W2-R1): constructed per turn, discarded at
turn end, never checkpointed (§2 graph-state lifecycle — durability lives in the session
store and OpenEMR). `turn` is the per-turn hop counter — the quantity the §2 step budget
(working value 8) bounds — strictly increasing across one graph turn so the hop sequence
is reconstructable from the correlation ID alone (§6).
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.orchestrator.loop import BriefResult

# The CANONICAL home for these classes is app.schemas.handoff (W2-M6, §2). They are
# re-exported here (by identity, not a copy) so the M3 orchestrator and the schema
# inventory share ONE HandoffRecord/SupervisorDecision family and can never drift apart.
# The former local definitions were UNIFIED into schemas.handoff unchanged.
from app.schemas.handoff import (  # noqa: F401  (re-export)
    _ALLOWED_REASONS,
    HandoffRecord,
    ReasonCode,
    SupervisorDecision,
)


class GraphState(TypedDict):
    """The per-turn LangGraph state (W2-R1). Built fresh each turn, discarded at turn
    end — no checkpointer (§2). `handoffs` accumulates across nodes; scalars overwrite."""

    correlation_id: str
    turn: int
    handoffs: Annotated[list[HandoffRecord], operator.add]
    next_decision: SupervisorDecision | None
    extracted_ref: str | None   # trace-addressable stub-extraction artifact ref
    retrieved_ref: str | None   # trace-addressable stub-retrieval artifact ref
    brief: BriefResult | None   # the W1 loop's answer, passed through unchanged (W2-D2)
