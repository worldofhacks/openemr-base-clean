"""Supervisor-worker handoff contracts (W2_ARCHITECTURE.md §2; canonical home, W2-M6).

``SupervisorDecision`` and ``ReasonCode`` are the CLOSED enums of the supervisor-worker
boundary (§2 locked-decision): a worker cannot smuggle a new decision or an invented
reason code past validation. Each reason code is legal for exactly ONE decision
(``_ALLOWED_REASONS``), so the vocabulary is closed in both dimensions. ``HandoffRecord``
is strict Pydantic v2 (``extra="forbid"``, ``strict=True``, frozen) — one audited
supervisor-worker hop, with ``input_ref``/``output_ref`` as trace-addressable ids (refs,
never raw values, cross the handoff boundary — §2 WorkerInput/WorkerOutput rule).

This is the CANONICAL home for these classes (W2-M6): ``app.orchestrator.state``
re-exports the SAME class objects by identity so the M3 orchestrator and the schema
inventory can never drift into two parallel ``HandoffRecord`` shapes.

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SupervisorDecision(enum.Enum):
    """Closed routing vocabulary, including deterministic critic outcomes."""

    ROUTE_EXTRACT = "route_extract"
    ROUTE_RETRIEVE = "route_retrieve"
    COMPOSE_ANSWER = "compose_answer"
    REVIEW_CRITIC = "review_critic"
    CRITIC_APPROVE = "critic_approve"
    CRITIC_REJECT = "critic_reject"
    REFUSE = "refuse"
    DONE = "done"


class ReasonCode(enum.Enum):
    """Closed reason-code vocabulary; each member is legal for exactly one decision
    (§2: "a closed reason_code enum per decision" — see ``_ALLOWED_REASONS``)."""

    # route_extract
    EXTRACTION_REQUESTED = "extraction_requested"
    # route_retrieve
    RETRIEVAL_REQUESTED = "retrieval_requested"
    # compose_answer
    WORKERS_COMPLETE = "workers_complete"
    # deterministic critic
    CRITIC_REVIEW_REQUESTED = "critic_review_requested"
    CRITIC_APPROVED = "critic_approved"
    CRITIC_REJECTED = "critic_rejected"
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
    SupervisorDecision.REVIEW_CRITIC: frozenset({ReasonCode.CRITIC_REVIEW_REQUESTED}),
    SupervisorDecision.CRITIC_APPROVE: frozenset({ReasonCode.CRITIC_APPROVED}),
    SupervisorDecision.CRITIC_REJECT: frozenset({ReasonCode.CRITIC_REJECTED}),
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
