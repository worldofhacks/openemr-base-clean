"""CXR-05 — the accountable trace begins BEFORE the FHIR fan-out and records every read.

Pins the service-level trace assembly (§7, D5): the six PHI reads — including a failed one —
appear as `fhir.*` spans ORDERED AHEAD of the llm/verify spans, and the trace carries the
full accountability + degradation metadata. Proven with an in-memory sink, so emission
correctness needs no live Langfuse (the cross-agent key handoff is only for live validation).
"""

from __future__ import annotations

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, TextBlock, Usage
from app.observability.langfuse import InMemoryTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator.loop import Orchestrator, ToolRegistry
from app.tools.fhir_tools import run_previsit_fanout

PID = "a234b786-539a-4f9a-96a0-432293226f02"


class _FakeFhirClient:
    """A small bundle per resource; AllergyIntolerance raises → a FAILED read (still a span)."""

    async def search(self, resource: str, params: dict) -> dict:
        if resource == "AllergyIntolerance":
            raise RuntimeError("allergy service down")
        if resource == "Patient":
            return {"entry": [{"resource": {"id": PID, "name": [{"text": "Jane Doe"}],
                                            "birthDate": "1970-01-01"}}]}
        if resource == "Condition":
            return {"entry": [{"resource": {"id": "c1", "code": {"text": "Type 2 diabetes"},
                                            "clinicalStatus": {"coding": [{"code": "active"}]}}}]}
        return {"entry": []}  # medications / labs / encounters → NO_RECORDS


class _FakeProvider:
    def __init__(self, scripted, model="claude-sonnet-4-6"):
        self._scripted = list(scripted)
        self.model = model

    async def complete(self, *, system, messages, tools):
        return self._scripted.pop(0)


def _acct():
    return AccountabilityContext(
        correlation_id="req-fhir-1", client_id="copilot-42",
        exercised_scopes=("openid", "user/Condition.read"),
        request_url="https://agent/chat", user_id="clinician-7", patient_id=PID,
        utc_timestamp="2026-07-12T12:00:00+00:00")


async def test_trace_begins_before_fanout_and_records_every_fhir_read():
    sink = InMemoryTraceSink()
    tracer = RequestTracer(sink)
    builder = tracer.begin(_acct())  # begun BEFORE fan-out — exactly like service.py (CXR-05)

    def _record_fhir(name, latency_ms, result):
        builder.step(f"fhir.{name}", latency_ms=latency_ms, status=result.status.value,
                     records=len(result.records), missing_reason=result.missing_reason or "")

    fanout = await run_previsit_fanout(_FakeFhirClient(), PID, per_call_timeout=2.0,
                                       turn_budget=2.0, on_call=_record_fhir)
    packet = build_evidence_packet(PID, fanout)

    prov = _FakeProvider([LLMResponse(
        content=[TextBlock(text="brief")], stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5), model="claude-sonnet-4-6")])
    await Orchestrator(prov).run_previsit_brief(
        packet, "Summarize.", tools=ToolRegistry([]), builder=builder)

    assert len(sink.traces) == 1
    t = sink.traces[0]
    names = [s.name for s in t.steps]
    detail = {s.name: s.detail for s in t.steps}

    # every one of the six reads is a span — including the FAILED allergy read
    fhir_idx = [i for i, n in enumerate(names) if n.startswith("fhir.")]
    assert len(fhir_idx) == 6
    assert detail["fhir.get_allergies"]["status"] == "failed"
    assert detail["fhir.get_patient_summary"]["status"] == "ok"
    assert detail["fhir.get_active_medications"]["status"] == "no_records"

    # the FHIR spans PRECEDE the llm/verify spans → the trace began before fan-out (CXR-05)
    first_llm = next(i for i, n in enumerate(names) if n == "llm.complete")
    assert max(fhir_idx) < first_llm
    assert any(n == "verify" for n in names)  # per-verdict verification span present

    # accountability + degradation metadata is complete on the emitted trace
    assert t.client_id == "copilot-42" and t.correlation_id == "req-fhir-1"
    assert "user/Condition.read" in t.exercised_scopes
    assert t.verdicts and t.source == "deterministic_fallback" and t.fallback_kind == "all_blocked"
