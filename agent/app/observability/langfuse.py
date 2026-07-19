"""Trace sinks + the request tracer (ARCHITECTURE.md §7, §6, D5-rev).

The tracer builds a `RequestTrace` from steps recorded during a request and hands it to a
`TraceSink`. Observability is a SOFT dependency (§6): if the sink fails — Langfuse down,
misconfigured, SDK mismatch — the tracer swallows the error and increments a `dropped`
counter; serving is never affected. The `LangfuseSink` lazy-imports the SDK and maps the
trace to a Langfuse trace with the accountability metadata + the E5 degradation tags so
fallback-rate is alertable; any failure propagates to the tracer, which counts it.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Protocol, get_args, runtime_checkable

from app.llm.cost import estimate_cost
from app.llm.provider import Usage
from app.observability.trace import (
    AccountabilityContext,
    RequestTrace,
    TraceStep,
    hash_identifier,
    sanitize_request_url,
)
from app.observability.events import (
    EventComponent,
    EventEmitter,
    EventSeverity,
    EventType,
    OperationalStepCode,
    RerankerModeCode,
    VerificationOutcomeCode,
)
from app.observability.summary import encounter_summary_attributes


@runtime_checkable
class TraceSink(Protocol):
    def emit(self, trace: RequestTrace) -> None: ...


class InMemoryTraceSink:
    """Captures traces in-process — for tests and as a degradation buffer."""

    def __init__(self) -> None:
        self.traces: list[RequestTrace] = []

    def emit(self, trace: RequestTrace) -> None:
        self.traces.append(trace)

    def flush(self) -> None:  # parity with LangfuseSink
        return None


class NullTraceSink:
    """Observability disabled — accepts and discards. Never raises."""

    def emit(self, trace: RequestTrace) -> None:
        return None

    def flush(self) -> None:
        return None


def _usage_details(detail: dict) -> dict[str, int]:
    """Per-generation token usage for a Langfuse generation (native token + cost display)."""
    return {
        "input": int(detail.get("input_tokens", 0) or 0),
        "output": int(detail.get("output_tokens", 0) or 0),
        "cache_read_input": int(detail.get("cache_read_tokens", 0) or 0),
    }


def _cost_details(detail: dict, model: str) -> dict[str, float] | None:
    """Explicit per-generation USD cost (D4 pricing) so the native cost widget never depends on
    Langfuse knowing this model's price. None on an unpriced model — never a silent zero."""
    try:
        usage = Usage(
            input_tokens=int(detail.get("input_tokens", 0) or 0),
            output_tokens=int(detail.get("output_tokens", 0) or 0),
            cache_read_input_tokens=int(detail.get("cache_read_tokens", 0) or 0),
        )
        return {"total": estimate_cost(usage, model)}
    except Exception:
        return None


_CONTENT_MARKER = "__copilot_marked_content_v1__"
_CONTENT_VALUE = "value"
_REDACTED_CONTENT = "<redacted clinical trace content>"
_COUNT_DETAIL_KEYS = frozenset(
    {
        "records",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    }
)
_CLOSED_TEXT_DETAIL_VALUES = {
    "status": frozenset({"ok", "no_records", "failed", "error"}),
    "stop_reason": frozenset(
        {
            "end_turn",
            "max_tokens",
            "model_context_window_exceeded",
            "pause_turn",
            "refusal",
            "stop_sequence",
            "tool_use",
        }
    ),
    "verdict": frozenset({"pass", "flagged", "blocked", "refused"}),
    "claim_type": frozenset(
        {
            "AllergyClaim",
            "ConditionClaim",
            "ImmunizationClaim",
            "LabValueClaim",
            "MedicationClaim",
            "TextClaim",
        }
    ),
}
_CLOSED_STEP_NAMES = frozenset(
    {*get_args(OperationalStepCode), "graph.supervisor"}
)
_CLOSED_VERDICTS = frozenset(
    value
    for value in get_args(VerificationOutcomeCode)
    if value not in {"complete", "failed"}
)


def _safe_step_identity(name: object, latency_ms: object) -> bool:
    """Only closed step codes and finite non-negative latency may enter telemetry."""

    if type(name) is not str or name not in _CLOSED_STEP_NAMES:
        return False
    if isinstance(latency_ms, bool) or not isinstance(latency_ms, (int, float)):
        return False
    return math.isfinite(latency_ms) and latency_ms >= 0


def _marked_content(value: Any) -> dict[str, Any]:
    """Mark legacy/malformed clinical content so one client-level mask always redacts it.

    ``TraceBuilder`` removes content before constructing ``RequestTrace``.  This marker is a
    second fail-closed boundary for a manually constructed or older trace: adding a new
    observation mapping cannot opt prompts, transcripts, claims, tool payloads, credentials,
    or served text into export.
    """
    return {_CONTENT_MARKER: True, _CONTENT_VALUE: value}


def _mask_marked(value: Any) -> Any:
    """Recursively redact every marked envelope without inspecting its payload.

    Langfuse invokes its mask once for an entire input/output/metadata value, so marked content
    can be nested beside a PHI-free summary. There is intentionally no configuration path that
    unwraps marked data. Exact built-in container checks avoid invoking hostile mapping/sequence
    hooks; any exception is handled by the public mask fail-closed.
    """
    if type(value) is dict:
        if value.get(_CONTENT_MARKER) is True:
            return _REDACTED_CONTENT
        return {key: _mask_marked(item) for key, item in value.items()}
    if type(value) is list:
        return [_mask_marked(item) for item in value]
    if type(value) is tuple:
        return [_mask_marked(item) for item in value]
    return value


def _content_mask() -> Callable[..., Any]:
    """Build the unconditional Langfuse SDK clinical-content mask."""

    def mask(*, data: Any, **_kwargs: Any) -> Any:
        try:
            return _mask_marked(data)
        except Exception:
            # Masking is both a safety and a soft-dependency boundary: malformed content must
            # neither escape nor make an otherwise valid request fail.
            return _REDACTED_CONTENT

    return mask


def _content_free_detail(detail: dict) -> dict:
    """Return closed operational metadata; keys alone never make a value safe.

    Counts must be non-negative integers and text must be one of the closed execution enums.
    Free-text reasons are deliberately excluded because exception, query, and clinical text can
    ride in them. Unknown keys and malformed values are dropped before in-process storage and
    again at export for legacy/manually constructed traces.
    """

    sanitized: dict[str, int | str] = {}
    for key in _COUNT_DETAIL_KEYS:
        value = detail.get(key)
        if type(value) is int and value >= 0:
            sanitized[key] = value
    for key, allowed in _CLOSED_TEXT_DETAIL_VALUES.items():
        value = detail.get(key)
        if type(value) is str and value in allowed:
            sanitized[key] = value
    return sanitized


def _step_content(step: TraceStep, *, served_output: Any | None) -> tuple[Any | None, Any | None]:
    """Map RequestTrace content contracts to marked Langfuse observation input/output fields."""
    detail = step.detail
    if step.name == "llm.complete":
        generation_input = (
            _marked_content(detail["prompt"]) if "prompt" in detail else None
        )
        generation_output = (
            _marked_content(served_output) if served_output is not None else None
        )
        return generation_input, generation_output
    if step.name.startswith("fhir.") and "content" in detail:
        return None, _marked_content(detail["content"])
    if step.name.startswith("tool."):
        tool_input = _marked_content(detail["tool_input"]) if "tool_input" in detail else None
        tool_output = _marked_content(detail["content"]) if "content" in detail else None
        return tool_input, tool_output
    if step.name == "verify" and "claim" in detail:
        return _marked_content(detail["claim"]), None
    return None, None


def _step_metadata(step: TraceStep) -> dict:
    """Allowlisted operational fields; legacy raw artifacts stay marked and redacted."""
    metadata = _content_free_detail(step.detail)
    if step.name == "llm.complete":
        for key in ("raw_completion", "raw_submit_claims"):
            if key in step.detail:
                metadata[key] = _marked_content(step.detail[key])
    return metadata


def _verification_summary(trace: RequestTrace) -> tuple[str, list[dict[str, Any]]]:
    """Return the PHI-free root summary and the exact D16 live-score payloads."""
    verdicts = tuple(str(verdict).lower() for verdict in trace.verdicts)
    submitted = len(verdicts)
    verified = sum(1 for verdict in verdicts if verdict in {"pass", "flagged"})
    dropped = submitted - verified
    source = "llm" if trace.source == "llm" else "fallback"
    drop_rate = dropped / submitted if submitted else 0.0
    summary = (
        f"submitted {submitted} · verified {verified} · dropped {dropped} · "
        f"source={source}"
    )
    scores: list[dict[str, Any]] = [
        {"name": "claims_submitted", "value": submitted, "data_type": "NUMERIC"},
        {"name": "claims_verified", "value": verified, "data_type": "NUMERIC"},
        {"name": "claims_dropped", "value": dropped, "data_type": "NUMERIC"},
        {"name": "verification_drop_rate", "value": drop_rate, "data_type": "NUMERIC"},
        {"name": "source", "value": source, "data_type": "CATEGORICAL"},
        {"name": "degraded", "value": bool(trace.degraded), "data_type": "BOOLEAN"},
    ]
    return summary, scores


def _emit_scores(client: Any, trace: RequestTrace, *, summary: str) -> None:
    """Attach PHI-free trace scores; one failed score can never fail export or serving (§6)."""
    _, scores = _verification_summary(trace)
    for score in scores:
        try:
            client.score_current_trace(
                **score,
                metadata={"content_summary": summary},
            )
        except Exception:
            # Scores are an enhancement to an already-emitted trace. Counting this as a dropped
            # trace would be false and, more importantly, could affect the serving soft boundary.
            continue


class LangfuseSink:
    """Maps a RequestTrace to a real Langfuse trace (D5 system-of-record). Lazy/defensive: the
    SDK is imported and the client built on first emit; a missing credential or SDK error
    RAISES so the tracer can count the drop (§6 soft dependency — the tracer swallows)."""

    def __init__(
        self, *, host: str | None, public_key: str | None, secret_key: str | None
    ):
        self._host = host
        self._public_key = public_key
        self._secret_key = secret_key
        self._client: Any = None

    def _get_client(self) -> Any:
        if not self._public_key or not self._secret_key:
            raise RuntimeError("langfuse not configured (missing public/secret key)")
        if self._client is None:
            from langfuse import Langfuse  # lazy: never imported unless configured

            self._client = Langfuse(
                public_key=self._public_key,
                secret_key=self._secret_key,
                host=self._host,
                mask=_content_mask(),
            )
        return self._client

    def emit(self, trace: RequestTrace) -> None:
        client = self._get_client()
        # Trace attributes moved from `span.update_trace()` (v3) to `propagate_attributes()`
        # (Langfuse v4) — lazy-imported so an unconfigured deployment never touches the SDK.
        from langfuse import propagate_attributes

        # PHI-minimized metadata; client_id + scopes in the clear (accountability, not PHI, F-C.1).
        content_summary, _ = _verification_summary(trace)
        metadata = {
            "client_id": trace.client_id,
            "exercised_scopes": list(trace.exercised_scopes),
            "correlation_id": trace.correlation_id,
            "request_url": trace.request_url,
            "patient_hash": trace.patient_hash,
            "utc_timestamp": trace.utc_timestamp,
            "input_tokens": trace.input_tokens,
            "output_tokens": trace.output_tokens,
            "cache_read_tokens": trace.cache_read_tokens,
            "cost_usd": trace.cost_usd,
            "verdicts": list(trace.verdicts),
            "source": trace.source,
            "degraded": trace.degraded,
            "fallback_kind": trace.fallback_kind,
            # Retry/latency posture for the ops agent: fallback_kind="transient" ⇒ the SDK
            # exhausted its retries; llm_calls>1 ⇒ the tool loop iterated (retried work).
            "llm_calls": sum(1 for s in trace.steps if s.name == "llm.complete"),
            "fhir_reads": sum(1 for s in trace.steps if s.name.startswith("fhir.")),
            "content_summary": content_summary,
        }
        tags = [
            f"client:{trace.client_id}",
            f"source:{trace.source}",
            f"fallback:{trace.fallback_kind or 'none'}",  # dashboard filter for fallback-rate
        ]
        now_ns = time.time_ns()
        total_ns = int(sum(s.latency_ms for s in trace.steps) * 1_000_000)
        root_output: dict[str, Any] = {"summary": content_summary}
        served_output = getattr(trace, "served_output", None)
        if served_output is not None:
            root_output["served_output"] = _marked_content(served_output)
        # Trace-level attributes propagate to every observation created within this context (v4).
        with propagate_attributes(user_id=trace.user_hash, session_id=trace.correlation_id,
                                  trace_name="previsit-brief", tags=tags, metadata=metadata):
            # `end_on_exit=False`: we set the root's end explicitly so the trace carries the real
            # request duration (the tree is built at request end, so wall-clock spans are ~0).
            with client.start_as_current_observation(
                name="previsit-brief", as_type="span", metadata=metadata,
                output=root_output,
                level="ERROR" if trace.degraded else "DEFAULT",
                status_message=trace.fallback_kind, end_on_exit=False,
            ) as root:
                for st in trace.steps:
                    # Defense in depth for manually constructed/legacy RequestTrace values.
                    # The normal TraceBuilder path has already enforced the same closed set.
                    if not _safe_step_identity(st.name, st.latency_ms):
                        continue
                    end_ns = now_ns + int(st.latency_ms * 1_000_000)
                    observation_input, observation_output = _step_content(
                        st, served_output=served_output)
                    operational_detail = _step_metadata(st)
                    if st.name == "llm.complete":
                        # A native GENERATION carries model + token usage + cost, so Langfuse's
                        # native cost/latency widgets work (metadata alone cannot power them).
                        obs = client.start_observation(
                            name="llm", as_type="generation", model=trace.model,
                            input=observation_input,
                            output=observation_output,
                            usage_details=_usage_details(st.detail),
                            cost_details=_cost_details(st.detail, trace.model),
                            metadata={"latency_ms": st.latency_ms, **operational_detail})
                    else:
                        obs = client.start_observation(
                            name=st.name, as_type="span",
                            input=observation_input,
                            output=observation_output,
                            level="ERROR" if st.detail.get("status") == "failed" else "DEFAULT",
                            metadata={"latency_ms": st.latency_ms, **operational_detail})
                    obs.end(end_time=end_ns)      # give the observation its real duration
                _emit_scores(client, trace, summary=content_summary)
            root.end(end_time=now_ns + total_ns)  # trace duration = summed step latency
        # No synchronous flush on the serving path (CXR-13, §6 latency isolation): the SDK
        # batches spans and exports them on its own background thread, so a slow or unreachable
        # Langfuse can never add user-visible latency. Delivery is guaranteed by the SDK's
        # periodic flush and the shutdown flush() below — "soft dependency" means BOTH failure-
        # isolated (the tracer swallows) AND latency-isolated (serving never waits on export).

    def flush(self) -> None:
        if self._client is not None:
            self._client.flush()


class TraceBuilder:
    """Accumulates the steps/usage/verdicts of one request, then builds + emits its trace."""

    def __init__(self, tracer: "RequestTracer", acct: AccountabilityContext):
        self._tracer = tracer
        self._acct = acct
        self._steps: list[TraceStep] = []
        self._usage = Usage()
        self._verdicts: list[str] = []
        self._order = 0
        # R05 fused-summary halves: recorded when the turn actually retrieved/grounded,
        # zero only as the honest default (previously hard-coded zero at emit time).
        self._retrieval_hit_count = 0
        self._grounding_rate = 0.0

    @property
    def tracer(self) -> "RequestTracer":
        """The owning tracer — lets a caller that holds only the builder (service.py begins the
        trace before fan-out, CXR-05) count a finish/build drop against the soft-dep counter."""
        return self._tracer

    def step(self, name: str, *, latency_ms: float, **detail: Any) -> None:
        # RequestTrace itself is an artifact surface, so sanitize before storage rather than
        # relying on an eventual Langfuse mask. Prompts, transcripts, provider responses,
        # claims, tool/FHIR payloads, credential/token values, and unknown future fields never
        # enter the trace. Aggregate token counts remain on the closed operational allowlist.
        if not _safe_step_identity(name, latency_ms):
            return
        operational_detail = _content_free_detail(detail)
        self._steps.append(
            TraceStep(
                order=self._order,
                name=name,
                latency_ms=latency_ms,
                detail=operational_detail,
            )
        )
        self._order += 1

    def record_usage(self, usage: Usage) -> None:
        self._usage = self._usage.add(usage)

    def record_verdict(self, verdict: str) -> None:
        if type(verdict) is str and verdict in _CLOSED_VERDICTS:
            self._verdicts.append(verdict)

    def record_retrieval(
        self,
        *,
        hit_count: int,
        latency_ms: float,
        degraded: bool,
        reranker_mode: RerankerModeCode,
    ) -> None:
        """Record the turn's retrieval half and emit ``retrieval.completed`` now.

        Emission happens at retrieval completion (not at finish) so the event's
        timestamp reflects when retrieval actually ended; the recorded hit count
        additionally rides the fused encounter summary.
        """

        self._retrieval_hit_count = min(
            self._retrieval_hit_count + max(int(hit_count), 0), 20
        )
        if self._tracer.events is None:
            return
        self._tracer.events.emit(
            EventType.RETRIEVAL_COMPLETED,
            {
                "hit_count": min(max(int(hit_count), 0), 20),
                "latency_ms": max(float(latency_ms), 0.0),
                "degraded": degraded,
                "reranker_mode": reranker_mode,
            },
            component=EventComponent.RETRIEVAL,
            severity=EventSeverity.WARNING if degraded else EventSeverity.INFO,
            correlation_id=self._acct.correlation_id,
        )

    def record_grounding(
        self, *, fields_grounded: int, fields_unsupported: int
    ) -> None:
        """Record the turn's extraction-confidence half for the fused summary."""

        total = max(int(fields_grounded), 0) + max(int(fields_unsupported), 0)
        self._grounding_rate = (
            max(int(fields_grounded), 0) / total if total else 0.0
        )

    def step_summary(self) -> tuple[tuple[str, float], ...]:
        """Return PHI-free ordered step names/latencies for the terminal event."""

        return tuple((step.name, step.latency_ms) for step in self._steps)

    def finish(self, *, model: str, source: str, degraded: bool,
               fallback_kind: str | None, served_output: str | None = None,
               emit_summary: bool = True) -> RequestTrace:
        # Keep the keyword for serving-call compatibility, but never retain clinical output in
        # the trace value object (including in-memory/test sinks).
        del served_output
        try:
            cost = estimate_cost(self._usage, model)
        except KeyError:
            cost = 0.0  # an unpriced model must not break the trace (soft dep)
        trace = RequestTrace(
            correlation_id=self._acct.correlation_id,
            client_id=self._acct.client_id,
            exercised_scopes=tuple(self._acct.exercised_scopes),
            request_url=sanitize_request_url(self._acct.request_url),  # D5: PHI-safe route template
            user_hash=hash_identifier(self._acct.user_id),        # D5: hashed, never raw
            patient_hash=hash_identifier(self._acct.patient_id),  # D5: hashed, never raw
            utc_timestamp=self._acct.utc_timestamp,
            steps=tuple(self._steps),
            model=model,
            input_tokens=self._usage.input_tokens,
            output_tokens=self._usage.output_tokens,
            cache_read_tokens=self._usage.cache_read_input_tokens,
            cache_creation_tokens=self._usage.cache_creation_input_tokens,
            cost_usd=cost,
            verdicts=tuple(self._verdicts),
            source=source,
            degraded=degraded,
            fallback_kind=fallback_kind,
            served_output=None,
        )
        self._tracer._emit(trace)
        if emit_summary and self._tracer.events is not None:
            self._tracer.events.emit(
                EventType.ENCOUNTER_SUMMARY,
                encounter_summary_attributes(
                    steps=[(step.name, step.latency_ms) for step in trace.steps],
                    input_tokens=trace.input_tokens,
                    output_tokens=trace.output_tokens,
                    cost_usd=trace.cost_usd,
                    retrieval_hit_count=self._retrieval_hit_count,
                    extraction_grounding_rate=self._grounding_rate,
                    verification_outcomes=list(trace.verdicts),
                ),
                component=EventComponent.ORCHESTRATOR,
                severity=(
                    EventSeverity.WARNING if trace.degraded else EventSeverity.INFO
                ),
                correlation_id=trace.correlation_id,
            )
        return trace


class RequestTracer:
    def __init__(self, sink: TraceSink, *, events: EventEmitter | None = None):
        self.sink = sink
        self.events = events
        self.dropped = 0

    def begin(self, acct: AccountabilityContext) -> TraceBuilder:
        return TraceBuilder(self, acct)

    def _emit(self, trace: RequestTrace) -> None:
        try:
            self.sink.emit(trace)
        except Exception:
            self.dropped += 1  # §6: export dropped + counted; serving never affected
