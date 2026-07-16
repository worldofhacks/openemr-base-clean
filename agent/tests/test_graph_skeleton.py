"""W2-M3 — LangGraph skeleton + SSE spike: frozen failing tests (RED-first).

Encodes W2-M3 AC-1..AC-6 (AC-7/AC-8 are [live-measure] evidence rows, not frozen tests)
against W2_ARCHITECTURE.md §2 (HandoffRecord + closed enums, per-turn step budget 8,
no-checkpointer graph-state lifecycle), §2a (the /chat SSE contract), §6 (supervisor
span ⊃ worker spans; correlation-ID reconstruction) and the W2-M3 ticket design section.

FROZEN PUBLIC CONTRACT these tests pin (the implementation must conform to the tests,
never the other way around):

- ``app.orchestrator.state``
    * ``SupervisorDecision`` — closed enum whose value set is exactly
      {route_extract, route_retrieve, compose_answer, refuse, done} (§2 locked-decision).
    * ``HandoffRecord`` — strict Pydantic v2 model (``extra="forbid"``) with fields
      correlation_id, turn, supervisor_decision, reason_code, worker, input_ref,
      output_ref, handoff_ts. ``reason_code`` is a member of a CLOSED enum (per-decision
      closed sets, §2); unknown decision/reason values and extra fields are rejected.
      ``turn`` is the per-turn hop counter (the quantity the step budget of 8 bounds):
      strictly increasing across one graph turn so the hop sequence is reconstructable
      from the correlation ID alone (§6 / AC-2).

- ``app.orchestrator.graph``
    * ``graph_enabled() -> bool`` — reads env var ``W2_GRAPH_ENABLED`` per call;
      unset/default = OFF; ``"1"`` = ON (ticket design: flag promotion into config.py
      is a later feature ticket).
    * ``run_graph_turn(*, run_brief, correlation_id, tracer=None, accountability=None,
      supervisor=None)`` — THE single graph entrypoint (chat.py's flag-ON path must go
      through it; the AC-4 flag-OFF tripwire monkeypatches it).
        - ``run_brief``: zero-arg async callable returning the W1 ``BriefResult`` — the
          W1 direct loop embedded unchanged inside the graph (W2-D2). This is the same
          seam chat.py owns via ``services.run_brief``.
        - ``tracer``/``accountability``: the W1 observability seam
          (``RequestTracer``/``AccountabilityContext``); when given, the turn emits one
          trace whose supervisor span is the parent of the worker spans.
        - ``supervisor``: optional routing-policy override — called instead of the
          default skeleton policy; returns a ``SupervisorDecision``. Test seam for the
          intentionally-looping stub graph (AC-5).
        - returns an object with ``.brief`` (the W1 ``BriefResult``) and ``.handoffs``
          (ordered sequence of ``HandoffRecord``, one per hop, emission order).

- ``POST /chat`` (§2a): flag OFF → bit-identical W1 JSON POST (graph never invoked);
  flag ON + SSE opt-in → ``text/event-stream`` of claim-block events shaped
  ``{claim_block, citations[], verdict}`` (W1 §5a, carried unchanged by §2a) ending in a
  terminal event. §2a/W1 §5a name the stream and the claim-block event shape but leave
  the opt-in mechanism and terminal marker unnamed; these tests freeze the minimal
  completions of that contract rather than a parallel invention:
    * opt-in = ``Accept: text/event-stream`` content negotiation on the same POST /chat
      body (keeps "W1 contract unchanged" — §2a's own words — for every non-opted caller),
    * terminal = a final SSE message with event name ``done`` (W1 §6's interrupted-stream
      row requires a complete stream to be distinguishable from a cut-off one).
  CitationV2 claim-block payload migration is explicitly out of scope (ticket Out of
  Scope) — citations are asserted as a list, their element shape is NOT pinned here.

All data is synthetic and non-clinical; no network, no live services, no secrets.
"""

from __future__ import annotations

import asyncio
import enum
import json
from datetime import datetime, timedelta, timezone

import langfuse
import pytest
from fastapi.testclient import TestClient

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, ToolUseBlock, Usage
from app.observability.langfuse import LangfuseSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator.loop import Orchestrator, ToolRegistry
from app.session.store import (
    Session,
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)
from app.tools.contracts import MedicationRecord, ToolResult, ToolStatus

PID = "a234b786-539a-4f9a-96a0-432293226f02"  # synthetic patient uuid (W1 fixture value)
QUESTION = "Give me the pre-visit brief."
FLAG = "W2_GRAPH_ENABLED"
TURN_TIMEOUT_S = 15.0  # every graph turn must finish in bounded wall-clock, never hang

# The W1 JSON /chat envelope (routes/chat.py ChatResponse) — frozen for bit-identity.
W1_ENVELOPE_KEYS = {
    "brief", "source", "degraded", "verdicts", "citations", "patient", "correlation_id",
}
CLOSED_DECISIONS = {
    "route_extract",
    "route_retrieve",
    "compose_answer",
    "review_critic",
    "critic_approve",
    "critic_reject",
    "refuse",
    "done",
}


# --- shared synthetic serving tail (same seams as test_chat_route.py) -----------------


class _SubmitClaimsProvider:
    """Fake LLM: answers with one supported (500 mg) and one unsupported (5000 mg) claim,
    so verify-then-flush observably drops the unsupported one on every path under test."""

    model = "claude-sonnet-4-6"

    def __init__(self, claims):
        self._claims = claims

    async def complete(self, *, system, messages, tools):
        return LLMResponse(
            content=[ToolUseBlock(id="tu1", name="submit_claims",
                                  input={"claims": self._claims})],
            stop_reason="tool_use", usage=Usage(input_tokens=5, output_tokens=2),
            model=self.model)


def _packet_and_provider():
    packet = build_evidence_packet(PID, {"get_active_medications": ToolResult(
        tool="get_active_medications", status=ToolStatus.OK,
        records=[MedicationRecord(resource_id="m1", name="metformin", dose_text="500 mg")])})
    eid = packet.by_type("MedicationRequest")[0].evidence_id
    provider = _SubmitClaimsProvider([
        {"type": "medication", "name": "metformin", "dose": "500 mg", "evidence_ids": [eid]},
        {"type": "medication", "name": "metformin", "dose": "5000 mg", "evidence_ids": [eid]},
    ])
    return packet, provider


async def _run_w1_loop(packet, provider):
    return await Orchestrator(provider).run_previsit_brief(
        packet, QUESTION, tools=ToolRegistry([]))


def _loop_runner(packet, provider):
    async def run_brief():
        return await _run_w1_loop(packet, provider)
    return run_brief


async def _graph_turn(correlation_id, **kwargs):
    from app.orchestrator.graph import run_graph_turn

    packet, provider = _packet_and_provider()
    return await asyncio.wait_for(
        run_graph_turn(run_brief=_loop_runner(packet, provider),
                       correlation_id=correlation_id, **kwargs),
        timeout=TURN_TIMEOUT_S)


def _enum_value(member):
    """Enum member -> its value; tolerate str-enum reprs without pinning the enum flavor."""
    return getattr(member, "value", member)


def _session(patient_id=PID) -> Session:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    return Session(session_id="sess-1", clinician_sub="clin-1", patient_id=patient_id,
                   created_at=now, last_activity_at=now,
                   token_expires_at=now + timedelta(hours=1),
                   idle_timeout_s=1800, turn_cap=20)


class _FakeServices:
    """Minimal ChatService protocol object: resolvable session + run_brief backed by the
    REAL W1 orchestrator over the synthetic metformin packet (no live OpenEMR/Anthropic)."""

    def __init__(self, *, session=None, resolve_error=None):
        self._session = session
        self._resolve_error = resolve_error

    async def resolve_session(self, session_id):
        if self._resolve_error is not None:
            raise self._resolve_error
        return self._session

    async def run_brief(self, session, message, *, request_url):
        packet, provider = _packet_and_provider()
        return await _run_w1_loop(packet, provider)


def _client(services) -> TestClient:
    from app.main import create_app
    return TestClient(create_app(services=services, readiness_checks=[]))


def _arm_graph_tripwire(monkeypatch):
    """AC-4: make ANY graph-entrypoint invocation an immediate, attributable failure."""
    import app.orchestrator.graph as graph_mod  # RED: module missing -> the right failure

    def boom(*args, **kwargs):
        raise AssertionError("graph entrypoint invoked while W2_GRAPH_ENABLED is OFF")

    monkeypatch.setattr(graph_mod, "run_graph_turn", boom)
    import app.routes.chat as chat_mod
    if hasattr(chat_mod, "run_graph_turn"):  # early-bound import in chat.py, if any
        monkeypatch.setattr(chat_mod, "run_graph_turn", boom)


# --- fake Langfuse client capturing the observation tree (test_langfuse_sink_v4 pattern) --


class _FakeObservation:
    def __init__(self, client, record, as_current):
        self._client = client
        self.record = record
        self._as_current = as_current
        self._pushed = False

    def __enter__(self):
        if self._as_current:
            self._client._stack.append(self.record)
            self._pushed = True
        return self

    def __exit__(self, *_exc):
        if self._pushed:
            self._client._stack.pop()
            self._pushed = False
        return False

    # Explicit child creation (v4 SDK: an observation can start child observations).
    def start_observation(self, **kwargs):
        return self._client._new(kwargs, parent_id=self.record["id"], as_current=False)

    def start_as_current_observation(self, **kwargs):
        return self._client._new(kwargs, parent_id=self.record["id"], as_current=True)

    start_span = start_observation
    start_generation = start_observation

    def update(self, **kwargs):
        self.record.setdefault("updates", []).append(kwargs)

    def end(self, **kwargs):
        self.record["end"] = kwargs


class _FakeLangfuseClient:
    """Captures every observation with an id + parent_id so parent-child nesting is
    assertable. Children come from either explicit ``obs.start_observation`` calls or the
    current-context stack (``start_as_current_observation`` used as a context manager)."""

    def __init__(self, **kwargs):
        self.constructor = kwargs
        self.observations: list[dict] = []
        self.scores: list[dict] = []
        self.propagate_calls: list[dict] = []
        self._stack: list[dict] = []
        self._count = 0

    def _current_id(self):
        return self._stack[-1]["id"] if self._stack else None

    def _new(self, kwargs, *, parent_id, as_current):
        self._count += 1
        record = {"id": f"obs-{self._count}", "parent_id": parent_id, **kwargs}
        self.observations.append(record)
        return _FakeObservation(self, record, as_current)

    def start_as_current_observation(self, **kwargs):
        return self._new(kwargs, parent_id=self._current_id(), as_current=True)

    def start_observation(self, **kwargs):
        return self._new(kwargs, parent_id=self._current_id(), as_current=False)

    start_span = start_observation
    start_generation = start_observation

    def score_current_trace(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self):
        return None


def _fake_langfuse(monkeypatch):
    holder: dict = {}

    def build_client(**kwargs):
        client = _FakeLangfuseClient(**kwargs)
        holder["client"] = client
        return client

    from contextlib import contextmanager

    @contextmanager
    def propagate_attributes(**kwargs):
        if "client" in holder:
            holder["client"].propagate_calls.append(kwargs)
        yield

    monkeypatch.setattr(langfuse, "Langfuse", build_client)
    monkeypatch.setattr(langfuse, "propagate_attributes", propagate_attributes)
    sink = LangfuseSink(host=None, public_key="pk-lf-11111111", secret_key="sk-lf-11111111")
    return sink, holder


def _acct(correlation_id) -> AccountabilityContext:
    return AccountabilityContext(
        correlation_id=correlation_id, client_id="w2m3-test-client",
        exercised_scopes=("openid",), request_url="https://agent.test/chat",
        user_id="clin-1", patient_id=PID, utc_timestamp="2026-07-14T12:00:00+00:00")


# --- SSE parsing ----------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:
    """Parse a raw SSE body into [{event, data, data_json}] preserving stream order."""
    events: list[dict] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        name = None
        data_lines: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        data = "\n".join(data_lines)
        try:
            data_json = json.loads(data) if data else None
        except ValueError:
            data_json = None
        events.append({"event": name, "data": data, "data_json": data_json})
    return events


def _claim_block_events(events: list[dict]) -> list[dict]:
    return [e for e in events
            if isinstance(e["data_json"], dict)
            and {"claim_block", "citations", "verdict"} <= set(e["data_json"])]


# =======================================================================================
# AC-1 — every hop emits a HandoffRecord; closed enums; populated fields; strict model
# =======================================================================================


async def test_graph_turn_emits_handoff_record_per_hop_through_both_stub_workers(monkeypatch):
    # spec(W2-M3:AC-1)
    # guards: a graph that routes silently — hops with no HandoffRecord, an open-ended
    # decision vocabulary, or blank refs would make W2 routing unauditable (UC-W2-3/4).
    monkeypatch.setenv(FLAG, "1")
    from app.orchestrator.state import HandoffRecord, SupervisorDecision

    # The decision enum is CLOSED and exactly the §2 locked set — no additions, no gaps.
    assert {member.value for member in SupervisorDecision} == CLOSED_DECISIONS

    result = await _graph_turn("w2m3-corr-ac1")
    records = list(result.handoffs)
    assert records, "a routed turn emitted no HandoffRecords"

    for record in records:
        assert isinstance(record, HandoffRecord)
        assert isinstance(record.supervisor_decision, SupervisorDecision)
        assert isinstance(record.reason_code, enum.Enum), (
            "reason_code must be a member of a closed enum, not a free-form value")
        assert record.correlation_id == "w2m3-corr-ac1"
        assert isinstance(record.turn, int) and record.turn >= 0
        assert record.handoff_ts is not None and record.handoff_ts != ""

    decisions = [_enum_value(r.supervisor_decision) for r in records]
    assert "route_extract" in decisions, "supervisor never routed to the extractor stub"
    assert "route_retrieve" in decisions, "supervisor never routed to the retriever stub"

    worker_hops = [r for r in records
                   if _enum_value(r.supervisor_decision) in {"route_extract", "route_retrieve"}]
    named_workers = " ".join(str(r.worker) for r in worker_hops)
    assert "extract" in named_workers and "retriev" in named_workers, (
        "worker field must name each of the two stub workers")
    for record in worker_hops:
        assert record.worker, "worker not populated on a routed hop"
        assert record.input_ref, "input_ref not populated on a routed hop"
        assert record.output_ref, "output_ref not populated on a routed hop"


async def test_handoff_record_rejects_unknown_and_extra_fields(monkeypatch):
    # spec(W2-M3:AC-1)
    # guards: a lazily-modeled HandoffRecord (plain strings / extra="allow") that lets a
    # worker smuggle new decisions, invented reason codes, or PHI-bearing side fields
    # through the supervisor-worker boundary unvalidated.
    monkeypatch.setenv(FLAG, "1")
    import pydantic

    from app.orchestrator.state import HandoffRecord

    result = await _graph_turn("w2m3-corr-strict")
    dump = list(result.handoffs)[0].model_dump()

    # Control: the untampered dump round-trips, so the failures below are the tamper's.
    HandoffRecord.model_validate(dump)

    with pytest.raises(pydantic.ValidationError):
        HandoffRecord.model_validate({**dump, "smuggled_field": "x"})
    with pytest.raises(pydantic.ValidationError):
        HandoffRecord.model_validate({**dump, "supervisor_decision": "route_dance"})
    with pytest.raises(pydantic.ValidationError):
        HandoffRecord.model_validate({**dump, "reason_code": "definitely_not_a_reason"})


# =======================================================================================
# AC-2 — supervisor span ⊃ worker spans; hop sequence reconstructable from correlation ID
# =======================================================================================


async def test_supervisor_span_is_parent_of_worker_spans_in_captured_observations(monkeypatch):
    # spec(W2-M3:AC-2)
    # guards: flat/sibling span emission — a Langfuse trace where worker spans dangle
    # beside the supervisor makes §6 flow reconstruction impossible in a real incident.
    monkeypatch.setenv(FLAG, "1")
    corr = "w2m3-corr-spans"
    sink, holder = _fake_langfuse(monkeypatch)
    tracer = RequestTracer(sink)

    await _graph_turn(corr, tracer=tracer, accountability=_acct(corr))

    assert tracer.dropped == 0, "the graph turn's trace export failed"
    fake = holder.get("client")
    assert fake is not None and fake.observations, "no observations were exported"

    def named(fragment):
        return [o for o in fake.observations
                if fragment in str(o.get("name", "")).lower()]

    supervisor_ids = {o["id"] for o in named("supervisor")}
    extractor_spans = named("extract")
    retriever_spans = named("retriev")
    assert supervisor_ids, "no supervisor span exported"
    assert extractor_spans, "no extractor worker span exported"
    assert retriever_spans, "no retriever worker span exported"

    parent_of = {o["id"]: o["parent_id"] for o in fake.observations}

    def ancestors(observation_id):
        seen = set()
        current = parent_of.get(observation_id)
        while current is not None and current not in seen:
            seen.add(current)
            current = parent_of.get(current)
        return seen

    for worker_span in extractor_spans + retriever_spans:
        assert ancestors(worker_span["id"]) & supervisor_ids, (
            f"worker span {worker_span.get('name')} is not nested under the supervisor span")

    # The supervisor span is never nested under a worker (⊃ is one-directional).
    worker_ids = {o["id"] for o in extractor_spans + retriever_spans}
    for supervisor_id in supervisor_ids:
        assert not (ancestors(supervisor_id) & worker_ids)

    # The exported trace carries the correlation id (§6 one-ID reconstruction).
    assert corr in repr(fake.propagate_calls)


async def test_hop_sequence_reconstructable_from_correlation_id_alone(monkeypatch):
    # spec(W2-M3:AC-2)
    # guards: HandoffRecords that share a correlation id but no total order — two
    # interleaved turns would become inseparable, and the §6 audit story collapses.
    monkeypatch.setenv(FLAG, "1")
    result_a = await _graph_turn("w2m3-corr-A")
    result_b = await _graph_turn("w2m3-corr-B")

    hops_a, hops_b = list(result_a.handoffs), list(result_b.handoffs)
    assert {r.correlation_id for r in hops_a} == {"w2m3-corr-A"}
    assert {r.correlation_id for r in hops_b} == {"w2m3-corr-B"}

    # Hop counters give a strict total order within one turn (budget-countable, sortable).
    turns_a = [r.turn for r in hops_a]
    assert turns_a == sorted(turns_a) and len(set(turns_a)) == len(turns_a)

    # From a shuffled pool of BOTH turns' records, the correlation id ALONE recovers
    # exactly turn A's hop sequence, in order.
    pooled = list(reversed(hops_a + hops_b))
    reconstructed = sorted((r for r in pooled if r.correlation_id == "w2m3-corr-A"),
                           key=lambda r: r.turn)
    assert reconstructed == hops_a


# =======================================================================================
# AC-3 — W1-loop-in-worker equivalence: same fake in, same answer out
# =======================================================================================


async def test_graph_worker_embedding_returns_identical_answer_to_w1_loop(monkeypatch):
    # spec(W2-M3:AC-3)
    # guards: the framework quietly rewriting the answer — a graph hop that re-renders,
    # truncates, or un-verifies the W1 loop's output would invalidate every W1 guarantee.
    monkeypatch.setenv(FLAG, "1")
    from app.orchestrator.graph import run_graph_turn

    packet, provider = _packet_and_provider()
    w1 = await _run_w1_loop(packet, provider)
    graph_result = await asyncio.wait_for(
        run_graph_turn(run_brief=_loop_runner(packet, provider),
                       correlation_id="w2m3-corr-eq"),
        timeout=TURN_TIMEOUT_S)
    served = graph_result.brief

    assert served.text == w1.text
    assert served.source == w1.source == "llm"
    assert served.degraded is w1.degraded
    assert list(served.verdicts) == list(w1.verdicts)
    assert list(served.citations) == list(w1.citations)
    # And the W1 verify-then-flush outcome is intact through the graph path:
    assert "500 mg" in served.text and "5000" not in served.text


# =======================================================================================
# AC-4 — flag OFF (default): /chat is bit-identical W1; graph provably never invoked
# =======================================================================================


def test_flag_off_chat_response_is_bit_identical_w1_and_graph_never_invoked(
        complete_env, monkeypatch):
    # spec(W2-M3:AC-4)
    # guards: the spike leaking into the default serving path — a changed envelope key,
    # altered brief text, or a sneaky graph hop would ship to every W1 caller unnoticed.
    monkeypatch.delenv(FLAG, raising=False)
    _arm_graph_tripwire(monkeypatch)

    client = _client(_FakeServices(session=_session()))
    resp = client.post("/chat",
                       json={"session_id": "sess-1", "message": QUESTION},
                       headers={"X-Copilot-Request-Id": "w2m3-corr-off"})
    assert resp.status_code == 200
    body = resp.json()

    # Exact W1 envelope — no key added, renamed, or dropped.
    assert set(body) == W1_ENVELOPE_KEYS

    # The payload equals the W1 loop's own answer, computed independently of the route.
    expected = asyncio.run(_run_w1_loop(*_packet_and_provider()))
    assert body["brief"] == expected.text
    assert body["source"] == expected.source == "llm"
    assert body["degraded"] is expected.degraded
    assert body["verdicts"] == list(expected.verdicts)
    assert body["citations"] == [
        citation.model_dump(mode="json") for citation in expected.citations
    ]
    assert body["patient"] is None
    assert body["correlation_id"] == "w2m3-corr-off"
    assert "500 mg" in body["brief"] and "5000" not in body["brief"]


_W1_ERROR_CASES = [
    ("session_not_found_404",
     lambda: _FakeServices(resolve_error=SessionNotFound("sess-x")),
     {"session_id": "sess-x"}, 404),
    ("session_expired_401",
     lambda: _FakeServices(resolve_error=SessionExpiredError("sess-1")),
     {"session_id": "sess-1"}, 401),
    ("session_store_down_fails_closed_503",
     lambda: _FakeServices(resolve_error=SessionStoreUnavailable("down")),
     {"session_id": "sess-1"}, 503),
    ("cross_patient_refused_403",
     lambda: _FakeServices(session=_session()),
     {"session_id": "sess-1", "patient_id": "some-other-patient"}, 403),
]


@pytest.mark.parametrize(("_case", "services_factory", "payload", "status"),
                         _W1_ERROR_CASES, ids=[c[0] for c in _W1_ERROR_CASES])
def test_flag_off_w1_error_mappings_intact_with_graph_tripwire_armed(
        complete_env, monkeypatch, _case, services_factory, payload, status):
    # spec(W2-M3:AC-4)
    # guards: the flag plumbing reordering session/refusal checks — a 404/401/503/403
    # that turns into a 500 (or a graph call) only surfaces in production launch flows.
    monkeypatch.delenv(FLAG, raising=False)
    _arm_graph_tripwire(monkeypatch)
    client = _client(services_factory())
    assert client.post("/chat", json=payload).status_code == status


def test_flag_off_sse_optin_is_ignored_and_w1_json_served(complete_env, monkeypatch):
    # spec(W2-M3:AC-4)
    # guards: the SSE surface answering while the flag is OFF — the spike endpoint
    # becoming reachable by Accept header alone, without the graph rollout decision.
    monkeypatch.delenv(FLAG, raising=False)
    _arm_graph_tripwire(monkeypatch)

    client = _client(_FakeServices(session=_session()))
    resp = client.post("/chat",
                       json={"session_id": "sess-1", "message": QUESTION},
                       headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == W1_ENVELOPE_KEYS
    assert "500 mg" in body["brief"]


def test_graph_flag_defaults_off_and_is_read_from_env_per_call(monkeypatch):
    # spec(W2-M3:AC-4)
    # guards: an import-time-cached or default-ON flag — "default OFF" silently becoming
    # "whatever the process saw first", which no deploy checklist would catch.
    from app.orchestrator.graph import graph_enabled

    monkeypatch.delenv(FLAG, raising=False)
    assert graph_enabled() is False
    monkeypatch.setenv(FLAG, "1")
    assert graph_enabled() is True
    monkeypatch.delenv(FLAG, raising=False)
    assert graph_enabled() is False


# =======================================================================================
# AC-5 — intentionally-looping stub graph terminates via the step budget (working value 8)
# =======================================================================================


async def test_looping_stub_graph_terminates_via_step_budget_as_w1_refusal(monkeypatch):
    # spec(W2-M3:AC-5)
    # guards: an unbounded supervisor-worker ping-pong — one adversarial routing bug
    # hangs the serving path and burns tokens forever instead of refusing at 8 steps.
    monkeypatch.setenv(FLAG, "1")
    from app.orchestrator.graph import run_graph_turn
    from app.orchestrator.state import SupervisorDecision

    def always_route_extract(*_args, **_kwargs):
        return SupervisorDecision("route_extract")

    packet, provider = _packet_and_provider()
    result = await asyncio.wait_for(
        run_graph_turn(run_brief=_loop_runner(packet, provider),
                       correlation_id="w2m3-corr-loop",
                       supervisor=always_route_extract),
        timeout=10.0)  # bounded wall-clock: budget exhaustion, never a hang

    records = list(result.handoffs)
    assert records, "budget exhaustion emitted no HandoffRecords"

    # Terminal record: reason_code=step_budget_exceeded (§2 locked working value 8).
    terminal = records[-1]
    assert _enum_value(terminal.reason_code) == "step_budget_exceeded"

    routed = [r for r in records
              if _enum_value(r.supervisor_decision) == "route_extract"]
    assert len(routed) >= 2, "the looping supervisor never actually looped"
    assert len(routed) <= 8, "step budget (working value 8) did not bound the hops"

    # Surfaces as the W1-canonical deterministic refusal, not an error or a hang.
    assert result.brief.source == "deterministic_refusal"
    assert "review the chart manually" in result.brief.text


# =======================================================================================
# AC-6 — flag ON + opt-in: /chat serves the §2a SSE stream from the graph path
# =======================================================================================


def test_flag_on_sse_optin_streams_claim_blocks_then_terminal_done(complete_env, monkeypatch):
    # spec(W2-M3:AC-6)
    # guards: a stream with no verified content, unverified content leaking as tokens,
    # or no terminal marker — a UI could never distinguish "done" from "died mid-answer".
    monkeypatch.setenv(FLAG, "1")
    client = _client(_FakeServices(session=_session()))

    resp = client.post("/chat",
                       json={"session_id": "sess-1", "message": QUESTION},
                       headers={"Accept": "text/event-stream",
                                "X-Copilot-Request-Id": "w2m3-corr-sse"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    assert events, "empty SSE stream"

    content = _claim_block_events(events)
    assert content, "no {claim_block, citations[], verdict} content event was streamed"
    for event in content:
        data = event["data_json"]
        assert isinstance(data["claim_block"], str) and data["claim_block"].strip()
        assert isinstance(data["citations"], list)
        assert isinstance(data["verdict"], str)

    # Verify-then-flush holds ON THE STREAM: the supported claim is served, the
    # unsupported 5000 mg claim never appears in any streamed claim block.
    assert any("500 mg" in e["data_json"]["claim_block"] for e in content)
    assert all("5000" not in e["data_json"]["claim_block"] for e in content)

    # Terminal event closes the stream cleanly — last event, nothing after it.
    assert events[-1]["event"] == "done", (
        f"stream must end with the terminal 'done' event, got {events[-1]!r}")


def test_flag_on_without_sse_optin_keeps_w1_json_envelope(complete_env, monkeypatch):
    # spec(W2-M3:AC-6)
    # guards: the flag alone flipping every existing caller to SSE — opt-in must mean
    # opt-in, or enabling the graph breaks each W1 JSON consumer (§2a: contract unchanged).
    monkeypatch.setenv(FLAG, "1")
    # Establish the GIVEN: the graph feature exists and its flag reads ON.
    from app.orchestrator.graph import graph_enabled
    assert graph_enabled() is True

    client = _client(_FakeServices(session=_session()))

    resp = client.post("/chat", json={"session_id": "sess-1", "message": QUESTION})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == W1_ENVELOPE_KEYS
    assert "500 mg" in body["brief"] and "5000" not in body["brief"]


def test_flag_on_sse_is_served_from_the_graph_entrypoint(complete_env, monkeypatch):
    # spec(W2-M3:AC-6)
    # guards: a fake spike — an SSE shim bolted onto the W1 path would "pass" streaming
    # while the LangGraph seam (the entire point of the V2 spike) goes unexercised.
    monkeypatch.setenv(FLAG, "1")
    import app.orchestrator.graph as graph_mod

    calls: list[dict] = []
    real_run_graph_turn = graph_mod.run_graph_turn

    async def spy(*args, **kwargs):
        calls.append({"args": args, "kwargs": sorted(kwargs)})
        return await real_run_graph_turn(*args, **kwargs)

    monkeypatch.setattr(graph_mod, "run_graph_turn", spy)
    import app.routes.chat as chat_mod
    if hasattr(chat_mod, "run_graph_turn"):  # early-bound import in chat.py, if any
        monkeypatch.setattr(chat_mod, "run_graph_turn", spy)

    client = _client(_FakeServices(session=_session()))
    resp = client.post("/chat",
                       json={"session_id": "sess-1", "message": QUESTION},
                       headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert calls, "flag-ON SSE must be served through the graph entrypoint run_graph_turn"
