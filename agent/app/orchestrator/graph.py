"""B3 LangGraph topology — supervisor, two stub workers, and a composer shell.

W2-D2 locks LangGraph as the router. The supervisor routes by state readiness: missing
extraction output -> intake extractor; missing evidence output -> evidence retriever;
both present -> composer; composed answer -> done. Budget exhaustion -> refuse. The two
workers call clearly named stub functions. The composer shell accepts verified-fact,
evidence-snippet, and citation lanes as refs while the B3/B4 handoff is pending, then
delegates unchanged to the W1 direct loop (AC-3). No real extraction or composer
verification is implemented here.

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
from dataclasses import dataclass
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph

from app.llm.provider import Usage
from app.observability.langfuse import LangfuseSink, RequestTracer
from app.observability.trace import (
    AccountabilityContext,
    hash_identifier,
    sanitize_request_url,
)
from app.orchestrator import composer
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

FLAG_ENV = "W2_GRAPH_ENABLED"

# Per-turn step budget — §2 working value 8: bounds the hop counter, so at most 8 hops
# are routed before the supervisor refuses with reason_code=step_budget_exceeded.
STEP_BUDGET = 8

_COMPOSER_NAME = "composer"
_SUPERVISOR_NAME = "supervisor"
_EXTRACT_NODE = "intake_extractor"
_RETRIEVE_NODE = "evidence_retriever"

RunBrief = Callable[[], Awaitable[BriefResult]]
SupervisorPolicy = Callable[..., SupervisorDecision]


def graph_enabled() -> bool:
    """The W2 graph flag, read from the environment PER CALL (never cached at import):
    unset/default = OFF; the literal "1" = ON (W2-M3 ticket design)."""
    return os.environ.get(FLAG_ENV, "").strip() == "1"


@dataclass(frozen=True)
class GraphTurnResult:
    """One graph turn's outcome: the W1 answer + the ordered hop audit trail."""

    brief: BriefResult
    handoffs: tuple[HandoffRecord, ...]


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

    Request-kind classification waits for the shared worker handoff. Within one planned
    B3 turn, a missing extraction ref routes to the intake stub, a missing retrieval ref
    routes to the evidence stub, completed worker lanes route to the composer shell, and
    a composed brief terminates the graph.
    """
    if state.get("extracted_ref") is None:
        return SupervisorDecision.ROUTE_EXTRACT
    if state.get("retrieved_ref") is None:
        return SupervisorDecision.ROUTE_RETRIEVE
    if state.get("brief") is None:
        return SupervisorDecision.COMPOSE_ANSWER
    return SupervisorDecision.DONE


_REASON_FOR_DECISION: dict[SupervisorDecision, ReasonCode] = {
    SupervisorDecision.ROUTE_EXTRACT: ReasonCode.EXTRACTION_REQUESTED,
    SupervisorDecision.ROUTE_RETRIEVE: ReasonCode.RETRIEVAL_REQUESTED,
    SupervisorDecision.COMPOSE_ANSWER: ReasonCode.WORKERS_COMPLETE,
    SupervisorDecision.REFUSE: ReasonCode.STEP_BUDGET_EXCEEDED,
    SupervisorDecision.DONE: ReasonCode.TURN_COMPLETE,
}


async def run_graph_turn(*, run_brief: RunBrief, correlation_id: str,
                         tracer: RequestTracer | None = None,
                         accountability: AccountabilityContext | None = None,
                         supervisor: SupervisorPolicy | None = None) -> GraphTurnResult:
    """THE single graph entrypoint (chat.py's flag-ON path goes through it; the AC-4
    flag-OFF tripwire monkeypatches it). Builds the per-turn graph, runs it to a
    terminal decision, and discards the state — no checkpointer, nothing persisted."""
    policy: SupervisorPolicy = supervisor if supervisor is not None else select_next_decision
    # Per-hop span timings for the post-hoc trace replay (name, start_ns, end_ns, hop).
    hop_spans: list[tuple[str, int, int, HandoffRecord]] = []
    turn_started_ns = time.time_ns()

    def _terminal(state: GraphState, decision: SupervisorDecision,
                  reason: ReasonCode) -> dict:
        turn = state["turn"]
        brief = state.get("brief")
        if decision is SupervisorDecision.REFUSE or brief is None:
            # Budget exhaustion (or any terminal state without a composed answer)
            # surfaces as the W1-canonical deterministic refusal — never an error/hang.
            brief = _refusal_brief()
        record = _record(
            correlation_id, turn, decision, reason,
            worker=_SUPERVISOR_NAME,
            input_ref=_state_ref(correlation_id, turn),
            output_ref=f"trace:{correlation_id}/hop-{turn}/{_SUPERVISOR_NAME}/terminal",
        )
        return {"handoffs": [record], "turn": turn + 1,
                "next_decision": decision, "brief": brief}

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
        return {"next_decision": decision}

    async def _worker_hop(
        state: GraphState,
        *,
        runner: Callable[..., Awaitable[str]],
        worker_name: str,
        decision: SupervisorDecision,
        state_key: str,
        composer_lane: str,
    ) -> dict:
        turn = state["turn"]
        input_ref = _state_ref(correlation_id, turn)
        started_ns = time.time_ns()
        output_ref = await runner(
            correlation_id=correlation_id, turn=turn, input_ref=input_ref)
        record = _record(correlation_id, turn, decision,
                         _REASON_FOR_DECISION[decision],
                         worker=worker_name,
                         input_ref=input_ref, output_ref=output_ref)
        hop_spans.append((f"graph.worker.{worker_name}",
                          started_ns, time.time_ns(), record))
        return {
            "handoffs": [record],
            "turn": turn + 1,
            state_key: output_ref,
            composer_lane: (output_ref,),
        }

    async def extract_node(state: GraphState) -> dict:
        return await _worker_hop(
            state,
            runner=stub_extractor.run_intake_extractor_stub,
            worker_name=stub_extractor.WORKER_NAME,
            decision=SupervisorDecision.ROUTE_EXTRACT,
            state_key="extracted_ref",
            composer_lane="verified_facts",
        )

    async def retrieve_node(state: GraphState) -> dict:
        return await _worker_hop(
            state,
            runner=stub_retriever.run_evidence_retriever_stub,
            worker_name=stub_retriever.WORKER_NAME,
            decision=SupervisorDecision.ROUTE_RETRIEVE,
            state_key="retrieved_ref",
            composer_lane="evidence_snippets",
        )

    async def compose_node(state: GraphState) -> dict:
        # The shell receives all three future composition lanes, but deliberately does
        # not implement §5 verification until W2_B3_B4_HANDOFF.md defines the shared
        # types. It passes the W1 BriefResult through byte-identical (W2-D2 / AC-3).
        turn = state["turn"]
        input_ref = _state_ref(correlation_id, turn)
        started_ns = time.time_ns()
        brief = await composer.compose_answer_shell(
            verified_facts=state["verified_facts"],
            evidence_snippets=state["evidence_snippets"],
            citations=state["citations"],
            run_brief=run_brief,
        )
        output_ref = f"trace:{correlation_id}/hop-{turn}/{_COMPOSER_NAME}/brief"
        record = _record(correlation_id, turn, SupervisorDecision.COMPOSE_ANSWER,
                         ReasonCode.WORKERS_COMPLETE, worker=_COMPOSER_NAME,
                         input_ref=input_ref, output_ref=output_ref)
        hop_spans.append((f"graph.{_COMPOSER_NAME}", started_ns, time.time_ns(), record))
        return {"handoffs": [record], "turn": turn + 1, "brief": brief}

    def route(state: GraphState) -> str:
        decision = state["next_decision"]
        if decision is SupervisorDecision.ROUTE_EXTRACT:
            return _EXTRACT_NODE
        if decision is SupervisorDecision.ROUTE_RETRIEVE:
            return _RETRIEVE_NODE
        if decision is SupervisorDecision.COMPOSE_ANSWER:
            return _COMPOSER_NAME
        return END  # refuse/done — the supervisor already emitted the terminal record

    builder = StateGraph(GraphState)
    builder.add_node(_SUPERVISOR_NAME, supervisor_node)
    builder.add_node(_EXTRACT_NODE, extract_node)
    builder.add_node(_RETRIEVE_NODE, retrieve_node)
    builder.add_node(_COMPOSER_NAME, compose_node)
    builder.add_edge(START, _SUPERVISOR_NAME)
    builder.add_conditional_edges(
        _SUPERVISOR_NAME, route,
        {
            _EXTRACT_NODE: _EXTRACT_NODE,
            _RETRIEVE_NODE: _RETRIEVE_NODE,
            _COMPOSER_NAME: _COMPOSER_NAME,
            END: END,
        },
    )
    builder.add_edge(_EXTRACT_NODE, _SUPERVISOR_NAME)
    builder.add_edge(_RETRIEVE_NODE, _SUPERVISOR_NAME)
    builder.add_edge(_COMPOSER_NAME, _SUPERVISOR_NAME)
    graph = builder.compile()  # no checkpointer — per-turn state only (§2)

    initial: GraphState = {
        "correlation_id": correlation_id,
        "turn": 0,
        "handoffs": [],
        "next_decision": None,
        "extracted_ref": None,
        "retrieved_ref": None,
        "verified_facts": (),
        "evidence_snippets": (),
        "citations": (),
        "brief": None,
    }
    # Our own §2 budget always terminates first; LangGraph's recursion limit is set
    # above it purely so the framework bound can never mask the semantic one.
    final_state = await graph.ainvoke(
        initial, config={"recursion_limit": 2 * STEP_BUDGET + 4})

    brief = final_state.get("brief") or _refusal_brief()
    result = GraphTurnResult(brief=brief, handoffs=tuple(final_state["handoffs"]))

    if tracer is not None and accountability is not None:
        _emit_graph_trace(tracer, accountability, correlation_id, result, hop_spans,
                          turn_started_ns, time.time_ns())
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
