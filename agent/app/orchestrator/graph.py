"""B3 LangGraph topology with canonical worker boundaries and verified composition.

W2-D2 locks LangGraph as the router. The supervisor routes by state readiness: missing
extraction output -> intake extractor; missing evidence output -> evidence retriever;
both present -> composer; composed answer -> done. Budget exhaustion -> refuse. Real
workers are dependency-injected as the frozen WorkerInput -> WorkerOutput callable;
the defaults are compatibility workers for the frozen M3 tests.

Graph state is constructed per turn and discarded at turn end — no LangGraph
checkpointer (W2_ARCHITECTURE.md §2). Every hop emits a strict `HandoffRecord` (closed
enums, §2); the per-turn step budget (§2 working value 8) terminates any routing loop
with a terminal `reason_code=step_budget_exceeded` record surfacing as the W1-canonical
deterministic refusal — never a hang.

Flag: env var `W2_GRAPH_ENABLED`, read per call (unset/default = OFF, "1" = ON).
Promotion into `app/config.py` settings is a later feature ticket. With the flag OFF
this module is never invoked on the serving path (AC-4 tripwire-enforced).

Observability (§6): when a `RequestTracer` + `AccountabilityContext` are given, the turn
emits one Langfuse trace whose supervisor span is the PARENT of the worker spans, tagged
with the correlation id so the hop flow reconstructs from one ID. Tracing stays a soft
dependency: any export failure increments `tracer.dropped` and never affects the turn.
W1 D16 content posture is unchanged — spans carry refs and PHI-minimized metadata only,
never clinical content.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TypeVar

from langgraph.graph import END, START, StateGraph

from app.logging import get_logger
from app.llm.provider import Usage
from app.llm.cost import estimate_cost
from app.observability.events import (
    EventComponent,
    EventEmitter,
    EventSeverity,
    EventType,
)
from app.observability.langfuse import LangfuseSink, RequestTracer
from app.observability.trace import (
    AccountabilityContext,
    hash_identifier,
    sanitize_request_url,
)
from app.orchestrator import composer, critic
from app.orchestrator.refs import RefResolver, TurnRefRegistry
# _DEFAULT_REFUSAL_TEXT is the W1-canonical refusal message (loop.py owns it; importing
# the constant keeps the graph's budget refusal byte-identical to W1's, never a fork).
from app.orchestrator.loop import _DEFAULT_REFUSAL_TEXT, BriefResult
from app.orchestrator.state import (
    GraphState,
    HandoffRecord,
    ReasonCode,
    SupervisorDecision,
)
from app.orchestrator.workers import stub_extractor, stub_retriever
from app.orchestrator.workers.contracts import WorkerCallable
from app.schemas.citations import CitationV2, EvidenceSnippet
from app.schemas.answers import GroundedAnswerContext
from app.schemas.extraction import ExtractionArtifact
from app.schemas.workers import WorkerInput, WorkerOutput

FLAG_ENV = "W2_GRAPH_ENABLED"

_log = get_logger("agent.orchestrator.graph")
_ANSWER_OUTCOME_CODES = frozenset(
    {
        "verified",
        "no_evidence",
        "no_claim",
        "all_blocked",
        "critic_rejected",
        "step_budget_exceeded",
        "transient",
        "client_error",
        "request_too_large",
        "cost_cap",
        "no_convergence",
        "policy_refusal",
    }
)

# Per-turn step budget — §2 working value 8: bounds the hop counter, so at most 8 hops
# are routed before the supervisor refuses with reason_code=step_budget_exceeded.
STEP_BUDGET = 8

_COMPOSER_NAME = "composer"
_CRITIC_NAME = "critic"
_SUPERVISOR_NAME = "supervisor"
_EXTRACT_NODE = "intake_extractor"
_RETRIEVE_NODE = "evidence_retriever"
_CRITIC_NODE = "critic"

RunBrief = Callable[[], Awaitable[BriefResult]]
RunBriefWithContext = Callable[[GroundedAnswerContext], Awaitable[BriefResult]]
SupervisorPolicy = Callable[..., SupervisorDecision]
T = TypeVar("T")


def _resolve_refs(
    refs: RefResolver, values: tuple[str, ...], expected: type[T]
) -> tuple[T, ...]:
    """Resolve known per-turn refs; external/unavailable refs remain non-renderable."""

    resolved: list[T] = []
    for ref in values:
        try:
            value = refs.resolve(ref)
        except KeyError:
            continue
        if isinstance(value, expected):
            resolved.append(value)
    return tuple(resolved)


def graph_enabled() -> bool:
    """The W2 graph flag, read from the environment PER CALL (never cached at import):
    unset/default = OFF; the literal "1" = ON (W2-M3 ticket design)."""
    return os.environ.get(FLAG_ENV, "").strip() == "1"


@dataclass(frozen=True)
class GraphTurnResult:
    """One graph turn's outcome: the W1 answer + the ordered hop audit trail."""

    brief: BriefResult
    handoffs: tuple[HandoffRecord, ...]
    composition: composer.VerifiedComposition = composer.VerifiedComposition()
    critic_approved: bool = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_ref(correlation_id: str, turn: int) -> str:
    """Trace-addressable id of the per-turn graph state as a hop's input (§2: refs only)."""
    return f"trace:{correlation_id}/hop-{turn}/state"


def _refusal_brief() -> BriefResult:
    """The W1-canonical deterministic refusal, as loop.py's D12 hard-stops render it —
    same text, same source, same `refused:<reason>` verdict convention."""
    return BriefResult(
        text=_DEFAULT_REFUSAL_TEXT,
        source="deterministic_refusal",
        degraded=False,
        usage=Usage(),
        iterations=0,
        tool_calls=[],
        verdicts=[f"refused:{ReasonCode.STEP_BUDGET_EXCEEDED.value}"],
        answer_reason_code="step_budget_exceeded",
    )


def _critic_refusal_brief(original: BriefResult | None = None) -> BriefResult:
    refusal = BriefResult(
        text=_DEFAULT_REFUSAL_TEXT,
        source="deterministic_refusal",
        degraded=False,
        usage=Usage(),
        iterations=0,
        tool_calls=[],
        verdicts=[f"refused:{ReasonCode.CRITIC_REJECTED.value}"],
        answer_reason_code="critic_rejected",
    )
    if original is None:
        return refusal
    # Discard every pending clinical byte/citation while retaining operational usage,
    # timing, tool-call, and presentation metadata for the terminal trace/event.
    return replace(
        original,
        text=refusal.text,
        source=refusal.source,
        degraded=refusal.degraded,
        fallback_reason=None,
        fallback_kind=None,
        verdicts=refusal.verdicts,
        citations=[],
        verified_claims=(),
        answer_reason_code="critic_rejected",
    )


def _record(correlation_id: str, turn: int, decision: SupervisorDecision,
            reason: ReasonCode, *, worker: str, input_ref: str,
            output_ref: str) -> HandoffRecord:
    return HandoffRecord(
        correlation_id=correlation_id,
        turn=turn,
        supervisor_decision=decision,
        reason_code=reason,
        worker=worker,
        input_ref=input_ref,
        output_ref=output_ref,
        handoff_ts=_now_iso(),
    )


def select_next_decision(state: GraphState) -> SupervisorDecision:
    """Select the next topology hop from completion state.

    The worker request itself contains only refs. A failed worker retry refuses; otherwise
    missing extraction/retrieval outputs route to the corresponding worker and completed
    outputs route to the composer.
    """
    if state.get("routing_failed"):
        return SupervisorDecision.REFUSE
    if state.get("extracted_ref") is None:
        return SupervisorDecision.ROUTE_EXTRACT
    if state.get("retrieved_ref") is None:
        return SupervisorDecision.ROUTE_RETRIEVE
    if state.get("brief") is None:
        return SupervisorDecision.COMPOSE_ANSWER
    if not state.get("critic_reviewed"):
        return SupervisorDecision.REVIEW_CRITIC
    if state.get("critic_finalized"):
        return SupervisorDecision.DONE
    if state.get("critic_approved"):
        return SupervisorDecision.CRITIC_APPROVE
    return SupervisorDecision.CRITIC_REJECT


_REASON_FOR_DECISION: dict[SupervisorDecision, ReasonCode] = {
    SupervisorDecision.ROUTE_EXTRACT: ReasonCode.EXTRACTION_REQUESTED,
    SupervisorDecision.ROUTE_RETRIEVE: ReasonCode.RETRIEVAL_REQUESTED,
    SupervisorDecision.COMPOSE_ANSWER: ReasonCode.WORKERS_COMPLETE,
    SupervisorDecision.REVIEW_CRITIC: ReasonCode.CRITIC_REVIEW_REQUESTED,
    SupervisorDecision.CRITIC_APPROVE: ReasonCode.CRITIC_APPROVED,
    SupervisorDecision.CRITIC_REJECT: ReasonCode.CRITIC_REJECTED,
    SupervisorDecision.REFUSE: ReasonCode.STEP_BUDGET_EXCEEDED,
    SupervisorDecision.DONE: ReasonCode.TURN_COMPLETE,
}


async def run_graph_turn(
    *,
    run_brief: RunBrief,
    run_brief_with_context: RunBriefWithContext | None = None,
    correlation_id: str,
    tracer: RequestTracer | None = None,
    accountability: AccountabilityContext | None = None,
    supervisor: SupervisorPolicy | None = None,
    worker_input: WorkerInput | None = None,
    extraction_worker: WorkerCallable | None = None,
    retrieval_worker: WorkerCallable | None = None,
    ref_registry: RefResolver | None = None,
    events: EventEmitter | None = None,
) -> GraphTurnResult:
    """THE single graph entrypoint (chat.py's flag-ON path goes through it; the AC-4
    flag-OFF tripwire monkeypatches it). Builds the per-turn graph, runs it to a
    terminal decision, and discards the state — no checkpointer, nothing persisted."""
    policy: SupervisorPolicy = supervisor if supervisor is not None else select_next_decision
    refs = ref_registry or TurnRefRegistry(correlation_id)
    initial_worker_input = worker_input or WorkerInput(
        correlation_id=correlation_id,
        turn=0,
        patient_ref=f"trace:{correlation_id}/patient",
        document_refs=[],
        evidence_refs=[],
        request_kind="previsit_brief",
    )
    if initial_worker_input.correlation_id != correlation_id:
        raise ValueError("worker_input correlation_id must match the graph turn")
    extract_runner = extraction_worker or stub_extractor.run_intake_extractor_stub
    retrieve_runner = retrieval_worker or stub_retriever.run_evidence_retriever_stub
    # Per-hop span timings for the post-hoc trace replay (name, start_ns, end_ns, hop).
    hop_spans: list[tuple[str, int, int, HandoffRecord]] = []
    turn_started_ns = time.time_ns()
    retrieval_hit_count = 0
    grounding_rate = 0.0

    def _emit_handoff(record: HandoffRecord, latency_ms: float) -> None:
        if events is None:
            return
        events.emit(
            EventType.HANDOFF_COMPLETED,
            {
                "turn": record.turn,
                "decision": record.supervisor_decision.value,
                "reason_code": record.reason_code.value,
                "worker": record.worker,
                "latency_ms": max(latency_ms, 0.0),
            },
            component=EventComponent.ORCHESTRATOR,
            correlation_id=correlation_id,
        )

    def _terminal(state: GraphState, decision: SupervisorDecision,
                  reason: ReasonCode) -> dict:
        turn = state["turn"]
        brief = state.get("brief")
        if decision is SupervisorDecision.CRITIC_REJECT:
            brief = _critic_refusal_brief(brief)
        elif decision is SupervisorDecision.REFUSE or brief is None:
            # Budget exhaustion (or any terminal state without a composed answer)
            # surfaces as the W1-canonical deterministic refusal — never an error/hang.
            brief = _refusal_brief()
        input_ref = refs.put(state["worker_input"], kind="supervisor-input")
        output_ref = refs.put(
            {"decision": decision.value, "reason": reason.value},
            kind="supervisor-output",
        )
        record = _record(
            correlation_id, turn, decision, reason,
            worker=_SUPERVISOR_NAME,
            input_ref=input_ref,
            output_ref=output_ref,
        )
        _emit_handoff(record, 0.0)
        return {"handoffs": [record], "turn": turn + 1,
                "next_decision": decision, "brief": brief}

    def _record_critic_outcome(
        state: GraphState,
        decision: SupervisorDecision,
        reason: ReasonCode,
    ) -> dict:
        """Record the closed critic outcome, then continue to the terminal done hop."""

        turn = state["turn"]
        brief = state.get("brief")
        pending = state.get("pending_composition")
        if decision is SupervisorDecision.CRITIC_REJECT:
            brief = _critic_refusal_brief(brief)
            pending = composer.VerifiedComposition()
        input_ref = refs.put(state["worker_input"], kind="supervisor-input")
        output_ref = refs.put(
            {"decision": decision.value, "reason": reason.value},
            kind="supervisor-output",
        )
        record = _record(
            correlation_id,
            turn,
            decision,
            reason,
            worker=_SUPERVISOR_NAME,
            input_ref=input_ref,
            output_ref=output_ref,
        )
        _emit_handoff(record, 0.0)
        return {
            "handoffs": [record],
            "turn": turn + 1,
            "next_decision": decision,
            "brief": brief,
            "pending_composition": pending,
            "critic_finalized": True,
        }

    async def supervisor_node(state: GraphState) -> dict:
        turn = state["turn"]
        if turn >= STEP_BUDGET:
            # §2 step budget: the hop counter hit the working value 8 — refuse.
            return _terminal(state, SupervisorDecision.REFUSE,
                             ReasonCode.STEP_BUDGET_EXCEEDED)
        decision = policy(state)
        if decision is SupervisorDecision.REFUSE:
            return _terminal(state, SupervisorDecision.REFUSE,
                             ReasonCode.STEP_BUDGET_EXCEEDED)
        if decision is SupervisorDecision.DONE:
            return _terminal(state, SupervisorDecision.DONE, ReasonCode.TURN_COMPLETE)
        if decision is SupervisorDecision.CRITIC_APPROVE:
            return _record_critic_outcome(
                state, SupervisorDecision.CRITIC_APPROVE, ReasonCode.CRITIC_APPROVED
            )
        if decision is SupervisorDecision.CRITIC_REJECT:
            return _record_critic_outcome(
                state, SupervisorDecision.CRITIC_REJECT, ReasonCode.CRITIC_REJECTED
            )
        return {"next_decision": decision}

    async def _worker_hop(
        state: GraphState,
        *,
        runner: WorkerCallable,
        worker_name: str,
        decision: SupervisorDecision,
        state_key: str,
        composer_lane: str,
    ) -> dict:
        turn = state["turn"]
        records: list[HandoffRecord] = []
        ref_kind = worker_name.replace("_", "-")
        for attempt in range(2):
            payload = state["worker_input"].model_copy(update={"turn": turn})
            input_ref = refs.put(payload, kind=f"{ref_kind}-input")
            started_ns = time.time_ns()
            try:
                raw_output = await runner(payload)
                output = (
                    raw_output
                    if isinstance(raw_output, WorkerOutput)
                    else WorkerOutput.model_validate(raw_output)
                )
                if output.correlation_id != correlation_id:
                    raise ValueError("worker output correlation mismatch")
                output_ref = refs.put(output, kind=f"{ref_kind}-output")
                record = _record(
                    correlation_id,
                    turn,
                    decision,
                    _REASON_FOR_DECISION[decision],
                    worker=output.worker,
                    input_ref=input_ref,
                    output_ref=output_ref,
                )
                records.append(record)
                hop_spans.append(
                    (f"graph.worker.{output.worker}", started_ns, time.time_ns(), record)
                )
                _emit_handoff(
                    record, (time.time_ns() - started_ns) / 1_000_000
                )
                return {
                    "handoffs": records,
                    "turn": turn + 1,
                    state_key: output_ref,
                    composer_lane: tuple(output.artifact_refs),
                    "citations": tuple(state["citations"]) + tuple(output.citation_refs),
                    "extraction_output" if decision is SupervisorDecision.ROUTE_EXTRACT
                    else "retrieval_output": output,
                }
            except Exception:  # noqa: BLE001 - bounded fail-closed worker boundary
                output_ref = refs.put(
                    {"failure": "malformed_worker_output", "attempt": attempt + 1},
                    kind=f"{ref_kind}-failure",
                )
                record = _record(
                    correlation_id,
                    turn,
                    decision,
                    _REASON_FOR_DECISION[decision],
                    worker=worker_name,
                    input_ref=input_ref,
                    output_ref=output_ref,
                )
                records.append(record)
                hop_spans.append(
                    (f"graph.worker.{worker_name}", started_ns, time.time_ns(), record)
                )
                _emit_handoff(
                    record, (time.time_ns() - started_ns) / 1_000_000
                )
                turn += 1
        return {
            "handoffs": records,
            "turn": turn,
            "routing_failed": True,
        }

    async def extract_node(state: GraphState) -> dict:
        return await _worker_hop(
            state,
            runner=extract_runner,
            worker_name=stub_extractor.WORKER_NAME,
            decision=SupervisorDecision.ROUTE_EXTRACT,
            state_key="extracted_ref",
            composer_lane="verified_facts",
        )

    async def retrieve_node(state: GraphState) -> dict:
        return await _worker_hop(
            state,
            runner=retrieve_runner,
            worker_name=stub_retriever.WORKER_NAME,
            decision=SupervisorDecision.ROUTE_RETRIEVE,
            state_key="retrieved_ref",
            composer_lane="evidence_snippets",
        )

    async def compose_node(state: GraphState) -> dict:
        nonlocal retrieval_hit_count, grounding_rate
        turn = state["turn"]
        input_ref = refs.put(state["worker_input"], kind="composer-input")
        started_ns = time.time_ns()
        verified_facts = _resolve_refs(refs, state["verified_facts"], object)
        evidence_snippets = _resolve_refs(
            refs, state["evidence_snippets"], EvidenceSnippet
        )
        retrieval_hit_count = min(len(evidence_snippets), 5)
        artifacts = _resolve_refs(refs, state["verified_facts"], ExtractionArtifact)
        grounded = sum(
            int(item.grounding_summary.get("fields_grounded", 0))
            for item in artifacts
        )
        unsupported = sum(
            int(item.grounding_summary.get("fields_unsupported", 0))
            for item in artifacts
        )
        grounding_rate = (
            grounded / (grounded + unsupported)
            if grounded + unsupported
            else 0.0
        )
        citations = _resolve_refs(refs, state["citations"], CitationV2)
        composed = await composer.compose_answer(
            verified_facts=verified_facts,
            evidence_snippets=evidence_snippets,
            citations=citations,
            run_brief=run_brief,
            run_brief_with_context=run_brief_with_context,
        )
        brief = composed.brief
        output_ref = refs.put(composed, kind="composer-output")
        record = _record(correlation_id, turn, SupervisorDecision.COMPOSE_ANSWER,
                         ReasonCode.WORKERS_COMPLETE, worker=_COMPOSER_NAME,
                         input_ref=input_ref, output_ref=output_ref)
        hop_spans.append((f"graph.{_COMPOSER_NAME}", started_ns, time.time_ns(), record))
        _emit_handoff(record, (time.time_ns() - started_ns) / 1_000_000)
        return {
            "handoffs": [record],
            "turn": turn + 1,
            "brief": brief,
            "pending_composition": composed.composition,
        }

    async def critic_node(state: GraphState) -> dict:
        turn = state["turn"]
        started_ns = time.time_ns()
        pending = state.get("pending_composition")
        input_ref = refs.put(
            {"pending": pending is not None}, kind="critic-input"
        )
        approved = False
        reason = critic.CriticReason.CRITIC_EXCEPTION
        try:
            if not isinstance(pending, composer.VerifiedComposition):
                raise TypeError("critic received no verified composition")
            allowed = _resolve_refs(refs, state["citations"], CitationV2)
            decision = critic.review_composition(
                brief=state["brief"] or _critic_refusal_brief(),
                composition=pending,
                allowed_citations=allowed,
            )
            approved = decision.approved
            reason = decision.reason
        except Exception:  # noqa: BLE001 - the critic is a fail-closed boundary
            approved = False
            reason = critic.CriticReason.CRITIC_EXCEPTION
        output_ref = refs.put(
            {"approved": approved, "reason": reason.value}, kind="critic-output"
        )
        record = _record(
            correlation_id,
            turn,
            SupervisorDecision.REVIEW_CRITIC,
            ReasonCode.CRITIC_REVIEW_REQUESTED,
            worker=_CRITIC_NAME,
            input_ref=input_ref,
            output_ref=output_ref,
        )
        hop_spans.append(("graph.critic", started_ns, time.time_ns(), record))
        _emit_handoff(record, (time.time_ns() - started_ns) / 1_000_000)
        return {
            "handoffs": [record],
            "turn": turn + 1,
            "critic_reviewed": True,
            "critic_approved": approved,
            "critic_reason": reason.value,
            "pending_composition": (
                pending if approved else composer.VerifiedComposition()
            ),
        }

    def route(state: GraphState) -> str:
        decision = state["next_decision"]
        if decision is SupervisorDecision.ROUTE_EXTRACT:
            return _EXTRACT_NODE
        if decision is SupervisorDecision.ROUTE_RETRIEVE:
            return _RETRIEVE_NODE
        if decision is SupervisorDecision.COMPOSE_ANSWER:
            return _COMPOSER_NAME
        if decision is SupervisorDecision.REVIEW_CRITIC:
            return _CRITIC_NODE
        if decision in {
            SupervisorDecision.CRITIC_APPROVE,
            SupervisorDecision.CRITIC_REJECT,
        }:
            return _SUPERVISOR_NAME
        return END  # refuse/done — the supervisor already emitted the terminal record

    builder = StateGraph(GraphState)
    builder.add_node(_SUPERVISOR_NAME, supervisor_node)
    builder.add_node(_EXTRACT_NODE, extract_node)
    builder.add_node(_RETRIEVE_NODE, retrieve_node)
    builder.add_node(_COMPOSER_NAME, compose_node)
    builder.add_node(_CRITIC_NODE, critic_node)
    builder.add_edge(START, _SUPERVISOR_NAME)
    builder.add_conditional_edges(
        _SUPERVISOR_NAME, route,
        {
            _EXTRACT_NODE: _EXTRACT_NODE,
            _RETRIEVE_NODE: _RETRIEVE_NODE,
            _COMPOSER_NAME: _COMPOSER_NAME,
            _CRITIC_NODE: _CRITIC_NODE,
            _SUPERVISOR_NAME: _SUPERVISOR_NAME,
            END: END,
        },
    )
    builder.add_edge(_EXTRACT_NODE, _SUPERVISOR_NAME)
    builder.add_edge(_RETRIEVE_NODE, _SUPERVISOR_NAME)
    builder.add_edge(_COMPOSER_NAME, _SUPERVISOR_NAME)
    builder.add_edge(_CRITIC_NODE, _SUPERVISOR_NAME)
    graph = builder.compile()  # no checkpointer — per-turn state only (§2)

    initial: GraphState = {
        "correlation_id": correlation_id,
        "turn": 0,
        "handoffs": [],
        "next_decision": None,
        "worker_input": initial_worker_input,
        "extraction_output": None,
        "retrieval_output": None,
        "extracted_ref": None,
        "retrieved_ref": None,
        "routing_failed": False,
        "verified_facts": (),
        "evidence_snippets": (),
        "citations": (),
        "brief": None,
        "pending_composition": None,
        "critic_reviewed": False,
        "critic_approved": False,
        "critic_finalized": False,
        "critic_reason": None,
    }
    # Our own §2 budget always terminates first; LangGraph's recursion limit is set
    # above it purely so the framework bound can never mask the semantic one.
    final_state = await graph.ainvoke(
        initial, config={"recursion_limit": 2 * STEP_BUDGET + 4})

    brief = final_state.get("brief") or _refusal_brief()
    pending = final_state.get("pending_composition")
    approved = bool(final_state.get("critic_approved"))
    result = GraphTurnResult(
        brief=brief,
        handoffs=tuple(final_state["handoffs"]),
        composition=(
            pending
            if approved and isinstance(pending, composer.VerifiedComposition)
            else composer.VerifiedComposition()
        ),
        critic_approved=approved,
    )

    raw_reason = result.brief.answer_reason_code or result.brief.fallback_kind
    if raw_reason in _ANSWER_OUTCOME_CODES:
        reason_code = raw_reason
    elif result.critic_approved and result.brief.source == "deterministic_refusal":
        reason_code = "policy_refusal"
    else:
        reason_code = "verified" if result.critic_approved else "critic_rejected"
    log = _log.info if reason_code == "verified" else _log.warning
    log(
        "graph_answer_outcome",
        extra={
            "reason_code": reason_code,
            "critic_approved": result.critic_approved,
        },
    )

    if tracer is not None and accountability is not None:
        _emit_graph_trace(tracer, accountability, correlation_id, result, hop_spans,
                          turn_started_ns, time.time_ns())
    if events is not None:
        try:
            cost = estimate_cost(result.brief.usage, getattr(result.brief, "model", "claude-sonnet-4-6"))
        except Exception:
            try:
                cost = estimate_cost(result.brief.usage, "claude-sonnet-4-6")
            except Exception:
                cost = 0.0
        summary_steps: list[tuple[str, float]] = []
        for name, start, end, _hop_record in hop_spans:
            if name == f"graph.{_COMPOSER_NAME}":
                summary_steps.extend(result.brief.observability_steps)
            summary_steps.append((name, (end - start) / 1_000_000))
        bounded_steps = summary_steps[:64]
        events.emit(
            EventType.ENCOUNTER_SUMMARY,
            {
                "ordered_steps": [name for name, _latency in bounded_steps],
                "step_latencies_ms": [latency for _name, latency in bounded_steps],
                "input_tokens": result.brief.usage.input_tokens,
                "output_tokens": result.brief.usage.output_tokens,
                "cost_usd": max(cost, 0.0),
                "retrieval_hit_count": retrieval_hit_count,
                "extraction_grounding_rate": max(0.0, min(1.0, grounding_rate)),
                "verification_outcomes": list(result.brief.verdicts[:64]),
            },
            component=EventComponent.ORCHESTRATOR,
            severity=(
                EventSeverity.INFO
                if result.critic_approved and not result.brief.degraded
                else EventSeverity.WARNING
            ),
            correlation_id=correlation_id,
        )
    return result


def _emit_graph_trace(tracer: RequestTracer, acct: AccountabilityContext,
                      correlation_id: str,
                      result: GraphTurnResult,
                      hop_spans: list[tuple[str, int, int, HandoffRecord]],
                      started_ns: int, ended_ns: int) -> None:
    """Emit one Langfuse trace for the turn: supervisor span ⊃ worker spans (§6), the
    correlation id on the trace so the flow reconstructs from one ID. Post-hoc replay,
    exactly like the W1 sink (tree built at turn end). Soft dependency: any failure is
    counted on `tracer.dropped`, never raised into serving. PHI-minimized: hashed
    user/patient ids, sanitized URL, refs — no clinical content (D16 posture)."""
    sink = tracer.sink
    if not isinstance(sink, LangfuseSink):
        # The span-nesting spike targets the Langfuse sink; other sinks consume flat
        # RequestTraces (W1 shape) and gain a graph mapping with the feature tickets.
        return
    try:
        # The sink owns client construction (credentials + the D16 content mask); the
        # graph reuses that client rather than building a second, unmasked one. The
        # accessor is module-internal to observability, used read-only here.
        client = sink._get_client()
        from langfuse import propagate_attributes  # lazy, mirroring the sink

        serialized_handoffs = [
            record.model_dump(mode="json") for record in result.handoffs
        ]
        decisions = [record["supervisor_decision"] for record in serialized_handoffs]
        metadata = {
            "client_id": acct.client_id,
            "exercised_scopes": list(acct.exercised_scopes),
            # The graph argument is canonical for the records, parent trace, and session.
            # Never split one turn across IDs if an integration supplies a stale context.
            "correlation_id": correlation_id,
            "request_url": sanitize_request_url(acct.request_url),
            "patient_hash": hash_identifier(acct.patient_id),
            "utc_timestamp": acct.utc_timestamp,
            "hop_count": len(result.handoffs),
            "decisions": decisions,
            "handoffs": serialized_handoffs,
            "source": result.brief.source,
            "degraded": result.brief.degraded,
            "latency_ms": (ended_ns - started_ns) / 1_000_000,
        }
        with propagate_attributes(
                user_id=hash_identifier(acct.user_id),
                session_id=correlation_id,
                trace_name="graph-turn",
                tags=[f"client:{acct.client_id}", f"source:{result.brief.source}"],
                metadata=metadata):
            with client.start_as_current_observation(
                    name="graph.supervisor", as_type="span", metadata=metadata,
                    end_on_exit=False) as supervisor_span:
                for name, span_start_ns, span_end_ns, record in hop_spans:
                    hop = supervisor_span.start_observation(
                        name=name, as_type="span",
                        metadata={
                            "latency_ms": (span_end_ns - span_start_ns) / 1_000_000,
                            **record.model_dump(mode="json"),
                        })
                    hop.end(end_time=span_end_ns)
            supervisor_span.end(end_time=ended_ns)
    except Exception:
        tracer.dropped += 1  # §6 soft dependency: counted, never surfaced
