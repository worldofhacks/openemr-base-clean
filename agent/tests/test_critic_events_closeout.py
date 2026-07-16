"""Focused deterministic-critic and PHI-safe event-foundation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.provider import Usage
from app.observability.events import (
    EventComponent,
    EventEmitter,
    EventSeverity,
    EventType,
    InMemoryEventSink,
    LogEventEnvelope,
)
from app.orchestrator.composer import RenderedClaim, VerifiedComposition
from app.orchestrator.critic import CriticReason, review_composition
from app.orchestrator.graph import run_graph_turn
from app.orchestrator.loop import BriefResult, _DEFAULT_REFUSAL_TEXT
from app.schemas.answers import VerifiedClinicalClaim
from app.schemas.citations import CitationSourceType, CitationV2


def _citation(suffix: str = "one") -> CitationV2:
    return CitationV2(
        source_type=CitationSourceType.PATIENT_RECORD,
        source_id=f"Observation/synthetic-{suffix}",
        page_or_section=None,
        field_or_chunk_id=f"Observation:synthetic-{suffix}:12345678",
        quote_or_value="6.5",
    )


def _brief(text: str = "Verified synthetic observation: 6.5.") -> BriefResult:
    citation = _citation()
    return BriefResult(
        text=text,
        source="llm",
        degraded=False,
        usage=Usage(input_tokens=12, output_tokens=4),
        iterations=1,
        verdicts=["pass"],
        citations=[citation],
        verified_claims=(VerifiedClinicalClaim(text="Synthetic value: 6.5", citation=citation),),
        observability_steps=(("fhir.synthetic", 2.0), ("llm.complete", 3.0)),
    )


def test_critic_approves_only_resolved_policy_safe_claims() -> None:
    safe = _brief()
    assert review_composition(
        brief=safe, composition=VerifiedComposition(), allowed_citations=()
    ).reason is CriticReason.APPROVED

    treatment = review_composition(
        brief=_brief("Start synthetic medicine."),
        composition=VerifiedComposition(),
        allowed_citations=(),
    )
    assert treatment.approved is False
    assert treatment.reason is CriticReason.TREATMENT_CLAIM

    invented = _brief()
    invented.citations.append(_citation("invented"))
    unresolved = review_composition(
        brief=invented, composition=VerifiedComposition(), allowed_citations=()
    )
    assert unresolved.reason is CriticReason.UNRESOLVED_CITATION

    mixed = VerifiedComposition(
        claims=(
            RenderedClaim(
                text="Synthetic value: 6.5",
                citation=_citation(),
                source_class=CitationSourceType.GUIDELINE,
            ),
        )
    )
    assert review_composition(
        brief=safe, composition=mixed, allowed_citations=()
    ).reason is CriticReason.MIXED_SOURCE


async def test_graph_critic_discards_rejected_bytes_and_emits_complete_summary() -> None:
    sink = InMemoryEventSink()
    events = EventEmitter(sink)

    async def unsafe_brief() -> BriefResult:
        return _brief("Start synthetic medicine at 10 mg.")

    result = await run_graph_turn(
        run_brief=unsafe_brief,
        correlation_id="critic-correlation",
        events=events,
    )

    assert result.critic_approved is False
    assert result.brief.text == _DEFAULT_REFUSAL_TEXT
    assert "10 mg" not in result.brief.text
    assert result.brief.citations == []
    assert result.composition.claims == ()
    decisions = [handoff.supervisor_decision.value for handoff in result.handoffs]
    assert decisions[-3:] == ["review_critic", "critic_reject", "done"]

    summaries = [event for event in sink.events if event.event_type is EventType.ENCOUNTER_SUMMARY]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.correlation_id == "critic-correlation"
    assert summary.attributes["input_tokens"] == 12
    assert summary.attributes["output_tokens"] == 4
    assert "fhir.synthetic" in summary.attributes["ordered_steps"]
    assert "llm.complete" in summary.attributes["ordered_steps"]
    assert "graph.critic" in summary.attributes["ordered_steps"]
    assert len(summary.attributes["ordered_steps"]) == len(
        summary.attributes["step_latencies_ms"]
    )
    assert "10 mg" not in repr(sink.events)


async def test_critic_exception_is_fail_closed(monkeypatch) -> None:
    from app.orchestrator import graph as graph_module

    def explode(**_kwargs):
        raise RuntimeError("synthetic critic outage")

    monkeypatch.setattr(graph_module.critic, "review_composition", explode)

    async def safe_brief() -> BriefResult:
        return _brief()

    result = await run_graph_turn(
        run_brief=safe_brief, correlation_id="critic-exception"
    )
    assert result.critic_approved is False
    assert result.brief.text == _DEFAULT_REFUSAL_TEXT
    assert result.composition.claims == ()


def _valid_envelope(**overrides) -> dict:
    payload = {
        "schema_version": 1,
        "event_id": "event-1",
        "event_type": EventType.HANDOFF_COMPLETED,
        "occurred_at": "2026-07-15T12:00:00+00:00",
        "case_id": None,
        "job_id": None,
        "correlation_id": "correlation-1",
        "component": EventComponent.ORCHESTRATOR,
        "severity": EventSeverity.INFO,
        "attributes": {
            "turn": 1,
            "decision": "review_critic",
            "reason_code": "critic_review_requested",
            "worker": "critic",
            "latency_ms": 1.0,
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "forbidden",
    [
        {"patient_id": "synthetic-patient"},
        {"query_text": "clinical query"},
        {"token": "secret-material"},
        {"exception": "stack trace"},
    ],
)
def test_closed_event_registry_rejects_forbidden_and_unknown_attributes(forbidden) -> None:
    payload = _valid_envelope()
    payload["attributes"] = {**payload["attributes"], **forbidden}
    with pytest.raises(ValidationError):
        LogEventEnvelope.model_validate(payload)

    payload = _valid_envelope()
    payload["attributes"] = {**payload["attributes"], "worker": "critic\nclinical text"}
    with pytest.raises(ValidationError):
        LogEventEnvelope.model_validate(payload)


def test_event_validation_and_sink_failures_are_soft() -> None:
    class RaisingSink:
        def emit(self, _event) -> None:
            raise RuntimeError("synthetic sink outage")

    emitter = EventEmitter(RaisingSink())
    result = emitter.emit(
        EventType.HANDOFF_COMPLETED,
        _valid_envelope()["attributes"],
        component=EventComponent.ORCHESTRATOR,
        correlation_id="correlation-1",
    )
    assert result is None
    assert emitter.dropped == 1

    # Invalid operational metadata is rejected/dropped without escaping into serving.
    invalid = emitter.emit(
        EventType.HANDOFF_COMPLETED,
        {**_valid_envelope()["attributes"], "patient_id": "synthetic-patient"},
        component=EventComponent.ORCHESTRATOR,
    )
    assert invalid is None
    assert emitter.dropped == 2
