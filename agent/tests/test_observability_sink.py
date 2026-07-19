"""E7.1 — the trace sink is a SOFT dependency (ARCHITECTURE.md §6/§7, D5-rev).

Observability must never affect serving: if Langfuse is down or misconfigured, the export
is dropped (with a counter) and the request still completes. These tests prove the tracer
swallows sink failures and counts drops, and that the in-memory sink captures for tests.
The LangfuseSink itself is lazy/defensive — a construction/emit failure degrades, never raises.

R05 / AF-P1-04 additions: the production composition must emit W2 structured events to a
PHI-safe sink (previously `NullEventSink` — production events silently discarded), and the
registered ``retrieval.completed`` event must be emitted at true retrieval completion
(previously registered but never emitted anywhere).
"""

from __future__ import annotations

import io
import json

import pytest

from app.llm.provider import Usage
from app.observability.events import (
    EventComponent,
    EventEmitter,
    EventSeverity,
    EventType,
    InMemoryEventSink,
)
from app.observability.langfuse import (
    InMemoryTraceSink,
    LangfuseSink,
    RequestTracer,
)
from app.observability.trace import AccountabilityContext
from app.schemas.workers import WorkerInput, WorkerOutput


def _acct():
    return AccountabilityContext(
        correlation_id="c1", client_id="client", exercised_scopes=("openid",),
        request_url="https://a/chat", user_id="u", patient_id="p",
        utc_timestamp="2026-07-09T12:00:00+00:00")


class _RaisingSink:
    def emit(self, trace):
        raise RuntimeError("langfuse unreachable")


def test_inmemory_sink_captures_the_trace():
    sink = InMemoryTraceSink()
    b = RequestTracer(sink).begin(_acct())
    b.record_usage(Usage(input_tokens=10, output_tokens=5))
    t = b.finish(model="claude-sonnet-4-6", fallback_kind=None, degraded=False, source="llm")
    assert sink.traces == [t]


def test_langfuse_down_serving_continues():  # boundary — §6 soft dependency
    tracer = RequestTracer(_RaisingSink())
    b = tracer.begin(_acct())
    b.record_usage(Usage(input_tokens=10, output_tokens=5))
    # finish must NOT raise even though the sink throws; the trace is still returned to the caller.
    t = b.finish(model="claude-sonnet-4-6", fallback_kind=None, degraded=False, source="llm")
    assert t is not None
    assert tracer.dropped == 1  # the failed export is counted, not silently lost


def test_dropped_counter_accumulates_across_failures():
    tracer = RequestTracer(_RaisingSink())
    for _ in range(3):
        tracer.begin(_acct()).finish(model="claude-sonnet-4-6", fallback_kind=None,
                                     degraded=False, source="llm")
    assert tracer.dropped == 3


def test_langfuse_sink_construction_never_raises_without_keys():
    # An unconfigured/misconfigured sink must be constructible and degrade on emit, not crash
    # at import/build time (the live client is lazy). Emitting through the tracer is swallowed.
    sink = LangfuseSink(host=None, public_key=None, secret_key=None)
    tracer = RequestTracer(sink)
    t = tracer.begin(_acct()).finish(model="claude-sonnet-4-6", fallback_kind=None,
                                     degraded=False, source="llm")
    assert t is not None
    assert tracer.dropped == 1  # no credentials → export degraded, serving unaffected


# --- R05 / AF-P1-04: the production structured-events sink -------------------


def test_structured_log_event_sink_writes_one_searchable_json_line():
    from app.observability.events import StructuredLogEventSink

    stream = io.StringIO()
    emitter = EventEmitter(StructuredLogEventSink(stream))
    emitted = emitter.emit(
        EventType.RETRIEVAL_COMPLETED,
        {"hit_count": 3, "latency_ms": 41.5, "degraded": False, "reranker_mode": "local"},
        component=EventComponent.RETRIEVAL,
        correlation_id="corr-structured-1",
        job_id="job-structured-1",
    )
    assert emitted is not None
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["log_type"] == "w2.event"
    assert record["event_type"] == "retrieval.completed"
    # Searchable by the identifiers the RTM requires (W2-REQ-63).
    assert record["correlation_id"] == "corr-structured-1"
    assert record["job_id"] == "job-structured-1"
    assert record["attributes"] == {
        "hit_count": 3,
        "latency_ms": 41.5,
        "degraded": False,
        "reranker_mode": "local",
    }


def test_structured_log_sink_receives_only_registry_validated_events():
    """PHI leak guard: an unregistered free-text field never reaches the log lane."""
    from app.observability.events import StructuredLogEventSink

    stream = io.StringIO()
    emitter = EventEmitter(StructuredLogEventSink(stream))
    emitted = emitter.emit(
        EventType.RETRIEVAL_COMPLETED,
        {
            "hit_count": 1,
            "latency_ms": 5.0,
            "degraded": False,
            "reranker_mode": "local",
            "query_text": "metformin 500 mg patient Jane Doe",
        },
        component=EventComponent.RETRIEVAL,
        correlation_id="corr-leak-guard",
    )
    assert emitted is None
    assert emitter.dropped == 1
    assert stream.getvalue() == ""


_MINIMAL_SETTINGS = {
    "openemr_fhir_base_url": "https://openemr.test/apis/default/fhir",
    "openemr_oauth_base_url": "https://openemr.test/oauth2/default",
    "smart_client_id": "synthetic-client",
    "smart_client_secret": "synthetic-secret",
    "anthropic_api_key": "synthetic-provider-key",
    "session_store_dsn": "postgresql://u:p@localhost:5432/agent",
}


def test_agent_services_default_event_sink_is_the_structured_log_lane():
    """service.py:204 root cause — production composition previously passed NullEventSink."""
    from app.config import Settings
    from app.observability.events import StructuredLogEventSink
    from app.service import AgentServices

    services = AgentServices(Settings(**_MINIMAL_SETTINGS))
    assert isinstance(services.events.sink, StructuredLogEventSink)


def test_agent_services_event_sink_stays_injectable():
    from app.config import Settings
    from app.service import AgentServices

    sink = InMemoryEventSink()
    services = AgentServices(Settings(**_MINIMAL_SETTINGS), event_sink=sink)
    assert services.events.sink is sink


# --- R05 / AF-P1-04: retrieval.completed is emitted at retrieval completion --


def _worker_input() -> WorkerInput:
    return WorkerInput(
        correlation_id="corr-retrieval-1",
        turn=0,
        patient_ref="session:synthetic",
        document_refs=[],
        evidence_refs=["evidence-request-1"],
        request_kind="previsit_brief",
    )


def _worker_output(status: str, citation_refs: list[str]) -> WorkerOutput:
    return WorkerOutput(
        correlation_id="corr-retrieval-1",
        worker="evidence_retriever",
        status=status,
        artifact_refs=list(citation_refs),
        citation_refs=list(citation_refs),
        reason_code=None,
    )


def _fake_clock(values: list[float]):
    def clock() -> float:
        return values.pop(0)

    return clock


async def test_retrieval_wrapper_emits_registered_event_at_completion():
    from app.observability.retrieval import observe_retrieval_worker

    sink = InMemoryEventSink()
    events = EventEmitter(sink)

    async def worker(payload: WorkerInput) -> WorkerOutput:
        return _worker_output("complete", ["c1", "c2"])

    wrapped = observe_retrieval_worker(
        worker, events=events, reranker_mode="local",
        clock=_fake_clock([1.0, 1.25]),
    )
    output = await wrapped(_worker_input())

    assert output.status == "complete"
    assert [e.event_type for e in sink.events] == [EventType.RETRIEVAL_COMPLETED]
    event = sink.events[0]
    assert event.correlation_id == "corr-retrieval-1"
    assert event.component is EventComponent.RETRIEVAL
    assert event.severity is EventSeverity.INFO
    assert event.attributes == {
        "hit_count": 2,
        "latency_ms": 250.0,
        "degraded": False,
        "reranker_mode": "local",
    }


async def test_retrieval_wrapper_marks_degraded_completion():
    from app.observability.retrieval import observe_retrieval_worker

    sink = InMemoryEventSink()
    events = EventEmitter(sink)

    async def worker(payload: WorkerInput) -> WorkerOutput:
        return _worker_output("degraded", [])

    wrapped = observe_retrieval_worker(
        worker, events=events, reranker_mode="disabled",
        clock=_fake_clock([0.0, 0.1]),
    )
    await wrapped(_worker_input())

    event = sink.events[0]
    assert event.severity is EventSeverity.WARNING
    assert event.attributes["degraded"] is True
    assert event.attributes["hit_count"] == 0
    assert event.attributes["reranker_mode"] == "disabled"


async def test_retrieval_wrapper_records_failure_and_reraises():
    from app.observability.retrieval import observe_retrieval_worker

    sink = InMemoryEventSink()
    events = EventEmitter(sink)

    async def worker(payload: WorkerInput) -> WorkerOutput:
        raise RuntimeError("synthetic retrieval outage")

    wrapped = observe_retrieval_worker(
        worker, events=events, reranker_mode="local",
        clock=_fake_clock([0.0, 0.05]),
    )
    with pytest.raises(RuntimeError):
        await wrapped(_worker_input())

    event = sink.events[0]
    assert event.event_type is EventType.RETRIEVAL_COMPLETED
    assert event.severity is EventSeverity.WARNING
    assert event.attributes["degraded"] is True
    assert event.attributes["hit_count"] == 0
    # The exception text never rides the event lane.
    assert "synthetic retrieval outage" not in repr(sink.events)


def test_retrieval_event_rejects_clinical_looking_reranker_mode():
    """PHI leak guard for the newly emitted field set: values are a closed vocabulary."""
    sink = InMemoryEventSink()
    emitter = EventEmitter(sink)
    emitted = emitter.emit(
        EventType.RETRIEVAL_COMPLETED,
        {
            "hit_count": 1,
            "latency_ms": 5.0,
            "degraded": False,
            "reranker_mode": "metformin",
        },
        component=EventComponent.RETRIEVAL,
        correlation_id="corr-closed-vocab",
    )
    assert emitted is None
    assert emitter.dropped == 1
    assert sink.events == []


def test_resolve_reranker_mode_reflects_the_production_seam():
    from app.observability.retrieval import resolve_reranker_mode

    assert resolve_reranker_mode({}) == "local"
    assert resolve_reranker_mode({"RERANKER": "cohere"}) == "cohere"
    assert resolve_reranker_mode({"RERANKER": " Cohere "}) == "cohere"
    assert resolve_reranker_mode({"RERANKER": "unexpected"}) == "local"
