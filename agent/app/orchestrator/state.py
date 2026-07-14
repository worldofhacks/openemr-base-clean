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

import enum
import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.orchestrator.loop import BriefResult


class SupervisorDecision(enum.Enum):
    """Closed §2 decision vocabulary — exactly the locked five-member set."""

    ROUTE_EXTRACT = "route_extract"
    ROUTE_RETRIEVE = "route_retrieve"
    COMPOSE_ANSWER = "compose_answer"
    REFUSE = "refuse"
    DONE = "done"


class ReasonCode(enum.Enum):
    """Closed reason-code vocabulary; each member is legal for exactly one decision
    (§2: "a closed reason_code enum per decision" — see `_ALLOWED_REASONS`)."""

    # route_extract
    EXTRACTION_REQUESTED = "extraction_requested"
    # route_retrieve
    RETRIEVAL_REQUESTED = "retrieval_requested"
    # compose_answer
    WORKERS_COMPLETE = "workers_complete"
    # refuse — step-budget exhaustion is the only refusal this skeleton produces (§2)
    STEP_BUDGET_EXCEEDED = "step_budget_exceeded"
    # done
    TURN_COMPLETE = "turn_complete"


# The per-decision closed sets (§2). A HandoffRecord pairing a decision with a reason
# outside its set fails validation — the vocabulary is closed in both dimensions.
_ALLOWED_REASONS: dict[SupervisorDecision, frozenset[ReasonCode]] = {
    SupervisorDecision.ROUTE_EXTRACT: frozenset({ReasonCode.EXTRACTION_REQUESTED}),
    SupervisorDecision.ROUTE_RETRIEVE: frozenset({ReasonCode.RETRIEVAL_REQUESTED}),
    SupervisorDecision.COMPOSE_ANSWER: frozenset({ReasonCode.WORKERS_COMPLETE}),
    SupervisorDecision.REFUSE: frozenset({ReasonCode.STEP_BUDGET_EXCEEDED}),
    SupervisorDecision.DONE: frozenset({ReasonCode.TURN_COMPLETE}),
}


class HandoffRecord(BaseModel):
    """One supervisor-worker hop, emitted per hop in emission order (§2, UC-W2-3/4)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    correlation_id: str = Field(min_length=1)
    turn: int = Field(ge=0)  # per-turn hop counter; strictly increasing within one turn
    supervisor_decision: SupervisorDecision
    reason_code: ReasonCode
    worker: str = Field(min_length=1)
    input_ref: str = Field(min_length=1)   # trace-addressable id — never a raw value
    output_ref: str = Field(min_length=1)  # trace-addressable id — never a raw value
    handoff_ts: str = Field(min_length=1)  # ISO-8601 UTC

    @model_validator(mode="after")
    def _reason_matches_decision(self) -> "HandoffRecord":
        if self.reason_code not in _ALLOWED_REASONS[self.supervisor_decision]:
            raise ValueError(
                f"reason_code {self.reason_code.value!r} is not in the closed set for "
                f"decision {self.supervisor_decision.value!r}"
            )
        return self


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
