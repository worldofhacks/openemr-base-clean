"""LangfuseSink emits against the INSTALLED Langfuse SDK without error — regression guard.

The sink was written against the v3 API (`start_as_current_span` / `update_trace`); the
installed SDK is v4 (`start_observation` / `propagate_attributes`), where the old calls raise.
Because tracing is a soft dependency, the tracer would SWALLOW that and emit nothing — a silent
"observability configured but zero traces". This test builds a representative trace and asserts
emit() runs clean (dropped == 0), and that the generation helpers carry real usage + cost so the
native Langfuse cost/latency widgets have data (metadata alone cannot power them).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import langfuse

import app.observability.langfuse as langfuse_module
from app.observability.langfuse import (
    LangfuseSink,
    RequestTracer,
    _cost_details,
    _usage_details,
)
from app.observability.trace import RequestTrace, TraceStep


def _trace() -> RequestTrace:
    steps = (
        TraceStep(order=0, name="fhir.get_patient_summary", latency_ms=12.0,
                  detail={"status": "ok", "records": 1, "missing_reason": ""}),
        TraceStep(order=1, name="fhir.get_allergies", latency_ms=8.0,
                  detail={"status": "failed", "records": 0, "missing_reason": "allergies unavailable"}),
        TraceStep(order=2, name="llm.complete", latency_ms=34000.0,
                  detail={"input_tokens": 5000, "output_tokens": 800,
                          "cache_read_tokens": 4000, "stop_reason": "tool_use"}),
        TraceStep(order=3, name="verify", latency_ms=1.0,
                  detail={"verdict": "pass", "claim_type": "ConditionClaim"}),
    )
    return RequestTrace(
        correlation_id="req-live-1", client_id="copilot-42",
        exercised_scopes=("openid", "user/Condition.read"), request_url="https://agent/chat",
        user_hash="uhash", patient_hash="phash", utc_timestamp="2026-07-12T12:00:00+00:00",
        steps=steps, model="claude-sonnet-4-6", input_tokens=5000, output_tokens=800,
        cache_read_tokens=4000, cache_creation_tokens=0, cost_usd=0.03,
        verdicts=("pass",), source="llm", degraded=False)


def test_langfuse_sink_emits_against_installed_sdk_without_error():
    # A dummy-keyed client never connects (no flush is called); this only asserts the SDK API
    # is used correctly — v3 calls (start_as_current_span/update_trace) would raise here.
    sink = LangfuseSink(host=None, public_key="pk-lf-11111111", secret_key="sk-lf-11111111")
    tracer = RequestTracer(sink)
    tracer._emit(_trace())
    assert tracer.dropped == 0  # emit ran clean → the v4 API is correct


def test_generation_helpers_carry_usage_and_cost():
    detail = {"input_tokens": 5000, "output_tokens": 800, "cache_read_tokens": 4000}
    assert _usage_details(detail) == {"input": 5000, "output": 800, "cache_read_input": 4000}
    cost = _cost_details(detail, "claude-sonnet-4-6")
    assert cost is not None and cost["total"] > 0  # real cost → native cost widget has data
    assert _cost_details(detail, "no-such-model") is None  # unpriced → None, never a silent zero


_CHART_TEXT = "José Secret-Chart has Type 2 diabetes"
_SERVED_BRIEF = f"Verified brief: {_CHART_TEXT}"


def _content_trace() -> RequestTrace:
    return replace(
        _trace(),
        steps=(
            TraceStep(
                order=0,
                name="fhir.get_conditions",
                latency_ms=12.0,
                detail={
                    "status": "ok",
                    "records": 1,
                    "content": {"records": [{"display": _CHART_TEXT}]},
                },
            ),
            TraceStep(
                order=1,
                name="llm.complete",
                latency_ms=20.0,
                detail={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "prompt": {
                        "system": [{"text": "Use the synthetic chart"}],
                        "messages": [{"role": "user", "content": _CHART_TEXT}],
                        "tools": [],
                    },
                    "raw_completion": [{"type": "text", "text": _CHART_TEXT}],
                    "raw_submit_claims": {"claims": [{"display": _CHART_TEXT}]},
                },
            ),
            TraceStep(
                order=2,
                name="tool.get_conditions",
                latency_ms=2.0,
                detail={
                    "status": "ok",
                    "tool_input": {"query": _CHART_TEXT},
                    "content": {"records": [{"display": _CHART_TEXT}]},
                },
            ),
            TraceStep(
                order=3,
                name="verify",
                latency_ms=1.0,
                detail={
                    "verdict": "pass",
                    "claim_type": "ConditionClaim",
                    "claim": {"display": _CHART_TEXT},
                },
            ),
        ),
        verdicts=("pass", "flagged", "blocked", "refused:treatment_advice"),
        served_output=_SERVED_BRIEF,
    )


class _FakeObservation:
    def __init__(self, data: dict):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def end(self, **kwargs):
        self.data["end"] = kwargs


class _FakeLangfuse:
    def __init__(self, *, fail_scores: bool = False, **kwargs):
        self.constructor = kwargs
        self.mask = kwargs["mask"]
        self.fail_scores = fail_scores
        self.root: dict | None = None
        self.observations: list[dict] = []
        self.scores: list[dict] = []

    def _masked(self, kwargs: dict) -> dict:
        data = dict(kwargs)
        for key in ("input", "output", "metadata"):
            if key in data:
                data[key] = self.mask(data=data[key])
        return data

    def start_as_current_observation(self, **kwargs):
        self.root = self._masked(kwargs)
        return _FakeObservation(self.root)

    def start_observation(self, **kwargs):
        data = self._masked(kwargs)
        self.observations.append(data)
        return _FakeObservation(data)

    def score_current_trace(self, **kwargs):
        self.scores.append(kwargs)
        if self.fail_scores:
            raise RuntimeError("score API unavailable")


@contextmanager
def _propagate_attributes(**_kwargs):
    yield


def _sink_with_fake(monkeypatch, *, log_content, fail_scores=False):
    fake = _FakeLangfuse(fail_scores=fail_scores, mask=lambda *, data: data)

    def build_client(**kwargs):
        fake.constructor = kwargs
        fake.mask = kwargs["mask"]
        return fake

    monkeypatch.setattr(langfuse, "Langfuse", build_client)
    monkeypatch.setattr(langfuse, "propagate_attributes", _propagate_attributes)
    sink = LangfuseSink(
        host=None,
        public_key="pk-lf-11111111",
        secret_key="sk-lf-11111111",
        log_content=log_content,
    )
    return sink, fake


def test_content_mask_off_redacts_every_marked_surface(monkeypatch):
    """D16/§7: default-off exports useful structure but no prompt, chart, claim, or brief."""
    sink, fake = _sink_with_fake(monkeypatch, log_content=False)

    sink.emit(_content_trace())

    assert fake.constructor["mask"] is not None
    generation = next(o for o in fake.observations if o["as_type"] == "generation")
    assert "redacted" in repr(generation["input"]).lower()
    assert "redacted" in repr(generation["output"]).lower()
    assert "redacted" in repr(generation["metadata"]["raw_completion"]).lower()
    assert "redacted" in repr(generation["metadata"]["raw_submit_claims"]).lower()
    assert _CHART_TEXT not in repr(fake.root)
    assert _CHART_TEXT not in repr(fake.observations)
    assert _CHART_TEXT not in repr(fake.scores)


def test_content_mask_on_exports_prompt_claims_fhir_and_served_brief(monkeypatch):
    """D16/§7: demo-only explicit opt-in makes each marked content surface inspectable."""
    sink, fake = _sink_with_fake(monkeypatch, log_content=True)

    sink.emit(_content_trace())

    generation = next(o for o in fake.observations if o["as_type"] == "generation")
    fhir = next(o for o in fake.observations if o["name"] == "fhir.get_conditions")
    tool = next(o for o in fake.observations if o["name"] == "tool.get_conditions")
    verify = next(o for o in fake.observations if o["name"] == "verify")
    assert _CHART_TEXT in repr(generation["input"])
    assert generation["output"] == _SERVED_BRIEF
    assert generation["metadata"]["raw_completion"][0]["text"] == _CHART_TEXT
    assert generation["metadata"]["raw_submit_claims"]["claims"][0]["display"] == _CHART_TEXT
    assert _CHART_TEXT in repr(fhir["output"])
    assert _CHART_TEXT in repr(tool["input"])
    assert _CHART_TEXT in repr(tool["output"])
    assert _CHART_TEXT in repr(verify["input"])
    assert _CHART_TEXT in repr(fake.root["output"])


def test_pathological_content_flag_fails_closed_without_touching_the_value(monkeypatch):
    """A non-boolean opt-in and an unserializable value must never bypass or break masking."""
    class Poison:
        def __repr__(self):
            raise AssertionError("mask inspected marked content while disabled")

        def __str__(self):
            raise AssertionError("mask inspected marked content while disabled")

    mask = langfuse_module._content_mask("true")  # type: ignore[arg-type]
    result = mask(data=langfuse_module._marked_content(Poison()))
    assert "redacted" in result.lower()

    poison = Poison()
    poison_steps = []
    for step in _content_trace().steps:
        if step.name != "llm.complete":
            poison_steps.append(step)
            continue
        detail = dict(step.detail)
        detail.update({
            "prompt": poison,
            "raw_completion": poison,
            "raw_submit_claims": poison,
        })
        poison_steps.append(replace(step, detail=detail))
    trace = replace(_content_trace(), steps=tuple(poison_steps), served_output=poison)
    sink, fake = _sink_with_fake(monkeypatch, log_content=False)
    tracer = RequestTracer(sink)

    tracer._emit(trace)

    assert tracer.dropped == 0
    generation = next(o for o in fake.observations if o["as_type"] == "generation")
    assert "redacted" in generation["output"].lower()
    assert "redacted" in generation["metadata"]["raw_completion"].lower()
    assert "redacted" in generation["metadata"]["raw_submit_claims"].lower()
    assert "redacted" in fake.root["output"]["served_output"].lower()


def test_live_scores_are_complete_and_phi_free_when_content_is_off(monkeypatch):
    sink, fake = _sink_with_fake(monkeypatch, log_content=False)

    sink.emit(_content_trace())

    scores = {score["name"]: score for score in fake.scores}
    assert set(scores) == {
        "claims_submitted",
        "claims_verified",
        "claims_dropped",
        "verification_drop_rate",
        "source",
        "degraded",
    }
    assert scores["claims_submitted"]["value"] == 4
    assert scores["claims_verified"]["value"] == 2
    assert scores["claims_dropped"]["value"] == 2
    assert scores["verification_drop_rate"]["value"] == 0.5
    assert scores["source"]["value"] == "llm"
    assert scores["degraded"]["value"] is False
    assert _CHART_TEXT not in repr(fake.scores)
    assert fake.root is not None
    assert fake.root["metadata"]["content_summary"] == (
        "submitted 4 · verified 2 · dropped 2 · source=llm"
    )
    assert fake.root["output"]["summary"] == fake.root["metadata"]["content_summary"]


def test_score_api_failure_never_drops_the_trace(monkeypatch):
    sink, fake = _sink_with_fake(monkeypatch, log_content=False, fail_scores=True)
    tracer = RequestTracer(sink)

    tracer._emit(_content_trace())

    assert tracer.dropped == 0
    assert fake.root is not None
    assert len(fake.observations) == 4
    assert [score["name"] for score in fake.scores] == [
        "claims_submitted",
        "claims_verified",
        "claims_dropped",
        "verification_drop_rate",
        "source",
        "degraded",
    ]
