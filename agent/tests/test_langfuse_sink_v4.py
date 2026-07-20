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
import pytest

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


def test_langfuse_sink_emits_against_installed_sdk_without_error(monkeypatch):
    # A dummy-keyed client never connects (no flush is called); this only asserts the SDK API
    # is used correctly — v3 calls (start_as_current_span/update_trace) would raise here.
    # The backdate spy guards the PRIVATE SDK layout (_otel_span/_start_time) the
    # start-backdater relies on: because _backdate_span_start soft-fails by design, an
    # SDK-internal rename would otherwise silently revert spans to emission-time starts
    # with a green suite. Deps are range-pinned (langfuse >=4.13,<5), so this canary is
    # the tripwire that turns that drift into a red test.
    backdated: list[tuple[object, int]] = []
    real_backdate = langfuse_module._backdate_span_start

    def spy(observation, start_ns):
        real_backdate(observation, start_ns)
        backdated.append((observation, start_ns))

    monkeypatch.setattr(langfuse_module, "_backdate_span_start", spy)
    sink = LangfuseSink(host=None, public_key="pk-lf-11111111", secret_key="sk-lf-11111111")
    tracer = RequestTracer(sink)
    tracer._emit(_trace())
    assert tracer.dropped == 0  # emit ran clean → the v4 API is correct
    assert len(backdated) == 1 + len(_trace().steps)  # root + every step
    for observation, start_ns in backdated:
        assert observation._otel_span._start_time == start_ns, (
            "backdate silently no-opped against the installed SDK — its private span "
            "layout changed; update _backdate_span_start")


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


class _FakeOtelSpan:
    """Mirrors the two members the exporter's start-backdater touches on an SDK span."""

    def __init__(self):
        import time

        self._start_time = time.time_ns()  # SDK default: span starts at creation

    def is_recording(self):
        return True


class _FakeObservation:
    def __init__(self, data: dict):
        self.data = data
        self._otel_span = _FakeOtelSpan()
        data["otel"] = self._otel_span

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


def _sink_with_fake(monkeypatch, *, fail_scores=False):
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
    )
    return sink, fake


def test_content_mask_redacts_every_marked_surface(monkeypatch):
    """Exports retain useful structure but no prompt, chart, claim, or brief."""
    sink, fake = _sink_with_fake(monkeypatch)

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


def test_content_export_has_no_constructor_opt_in_and_is_always_redacted(monkeypatch):
    """No environment or constructor value can export prompt/claim/tool/answer content."""

    with pytest.raises(TypeError):
        LangfuseSink(
            host=None,
            public_key="pk-lf-11111111",
            secret_key="sk-lf-11111111",
            log_content=True,  # type: ignore[call-arg]
        )
    sink, fake = _sink_with_fake(monkeypatch)

    sink.emit(_content_trace())

    generation = next(o for o in fake.observations if o["as_type"] == "generation")
    fhir = next(o for o in fake.observations if o["name"] == "fhir.get_conditions")
    tool = next(o for o in fake.observations if o["name"] == "tool.get_conditions")
    verify = next(o for o in fake.observations if o["name"] == "verify")
    for value in (
        generation["input"],
        generation["output"],
        generation["metadata"]["raw_completion"],
        generation["metadata"]["raw_submit_claims"],
        fhir["output"],
        tool["input"],
        tool["output"],
        verify["input"],
        fake.root["output"],
    ):
        assert "redacted" in repr(value).lower()
    assert _CHART_TEXT not in repr(fake.root)
    assert _CHART_TEXT not in repr(fake.observations)


def test_operational_detail_values_cannot_smuggle_content() -> None:
    """Allowlisted key names do not make free text, mappings, or multiline values safe."""

    sink = langfuse_module.InMemoryTraceSink()
    builder = RequestTracer(sink).begin(
        langfuse_module.AccountabilityContext(
            correlation_id="safe-correlation",
            client_id="copilot",
            exercised_scopes=("openid",),
            request_url="https://agent/chat",
            user_id="synthetic-user",
            patient_id="synthetic-patient",
            utc_timestamp="2026-07-15T00:00:00Z",
        )
    )
    builder.step(
        "llm.complete",
        latency_ms=1.0,
        status=_CHART_TEXT,
        records={"patient": _CHART_TEXT},
        missing_reason=_CHART_TEXT,
        input_tokens=_CHART_TEXT,
        stop_reason=f"tool_use\n{_CHART_TEXT}",
        verdict=_CHART_TEXT,
        claim_type=_CHART_TEXT,
    )
    builder.step(
        f"tool.{_CHART_TEXT}",
        latency_ms=1.0,
        status="ok",
    )
    builder.record_verdict(_CHART_TEXT)
    builder.record_verdict("pass")
    trace = builder.finish(
        model="claude-sonnet-4-6",
        source="llm",
        degraded=False,
        fallback_kind=None,
    )

    assert [step.name for step in trace.steps] == ["llm.complete"]
    assert trace.steps[0].detail == {}
    assert trace.verdicts == ("pass",)
    assert _CHART_TEXT not in repr(trace)


def test_pathological_content_fails_closed_without_touching_the_value(monkeypatch):
    """An unserializable value must never bypass or break unconditional masking."""
    class Poison:
        def __repr__(self):
            raise AssertionError("mask inspected marked content while disabled")

        def __str__(self):
            raise AssertionError("mask inspected marked content while disabled")

    mask = langfuse_module._content_mask()
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
    sink, fake = _sink_with_fake(monkeypatch)
    tracer = RequestTracer(sink)

    tracer._emit(trace)

    assert tracer.dropped == 0
    generation = next(o for o in fake.observations if o["as_type"] == "generation")
    assert "redacted" in generation["output"].lower()
    assert "redacted" in generation["metadata"]["raw_completion"].lower()
    assert "redacted" in generation["metadata"]["raw_submit_claims"].lower()
    assert "redacted" in fake.root["output"]["served_output"].lower()


def test_live_scores_are_complete_and_phi_free_when_content_is_off(monkeypatch):
    sink, fake = _sink_with_fake(monkeypatch)

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
    sink, fake = _sink_with_fake(monkeypatch, fail_scores=True)
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


def test_sink_lays_out_steps_from_the_recorded_request_anchor(monkeypatch):
    # spec(owner cycle 2, 2026-07-19): spans must carry real timestamps, not
    # emission-time starts. The flat W1 trace records one real wall-clock anchor
    # (utc_timestamp, stamped at the request boundary) plus per-step measured
    # latencies, so the exporter lays the ordered steps out sequentially from that
    # anchor: durations stay exactly latency_ms and absolute times are real.
    from datetime import datetime

    sink, fake = _sink_with_fake(monkeypatch)
    trace = _trace()
    sink.emit(trace)

    anchor_ns = int(
        datetime.fromisoformat(trace.utc_timestamp).timestamp() * 1_000_000_000)
    ms = 1_000_000
    total_ns = int(sum(s.latency_ms for s in trace.steps) * ms)

    assert fake.root is not None
    assert fake.root["otel"]._start_time == anchor_ns, (
        "root span must start at the recorded request anchor, not emission time")
    assert fake.root["end"]["end_time"] == anchor_ns + total_ns

    cursor = anchor_ns
    step_observations = [o for o in fake.observations if "end" in o]
    assert len(step_observations) == len(trace.steps)
    for step, observation in zip(trace.steps, step_observations):
        want_start = cursor
        want_end = cursor + int(step.latency_ms * ms)
        assert observation["otel"]._start_time == want_start, (
            f"step {step.name}: start must be anchored, not emission time")
        assert observation["end"]["end_time"] == want_end, f"step {step.name}"
        assert want_end - want_start > 0
        cursor = want_end
