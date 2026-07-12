"""Trace sinks + the request tracer (ARCHITECTURE.md §7, §6, D5-rev).

The tracer builds a `RequestTrace` from steps recorded during a request and hands it to a
`TraceSink`. Observability is a SOFT dependency (§6): if the sink fails — Langfuse down,
misconfigured, SDK mismatch — the tracer swallows the error and increments a `dropped`
counter; serving is never affected. The `LangfuseSink` lazy-imports the SDK and maps the
trace to a Langfuse trace with the accountability metadata + the E5 degradation tags so
fallback-rate is alertable; any failure propagates to the tracer, which counts it.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from app.llm.cost import estimate_cost
from app.llm.provider import Usage
from app.observability.trace import (
    AccountabilityContext,
    RequestTrace,
    TraceStep,
    hash_identifier,
    sanitize_request_url,
)


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


class LangfuseSink:
    """Maps a RequestTrace to a real Langfuse trace (D5 system-of-record). Lazy/defensive: the
    SDK is imported and the client built on first emit; a missing credential or SDK error
    RAISES so the tracer can count the drop (§6 soft dependency — the tracer swallows)."""

    def __init__(self, *, host: str | None, public_key: str | None, secret_key: str | None):
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
                public_key=self._public_key, secret_key=self._secret_key, host=self._host)
        return self._client

    def emit(self, trace: RequestTrace) -> None:
        client = self._get_client()
        # Trace attributes moved from `span.update_trace()` (v3) to `propagate_attributes()`
        # (Langfuse v4) — lazy-imported so an unconfigured deployment never touches the SDK.
        from langfuse import propagate_attributes

        # PHI-minimized metadata; client_id + scopes in the clear (accountability, not PHI, F-C.1).
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
        }
        tags = [
            f"client:{trace.client_id}",
            f"source:{trace.source}",
            f"fallback:{trace.fallback_kind or 'none'}",  # dashboard filter for fallback-rate
        ]
        now_ns = time.time_ns()
        total_ns = int(sum(s.latency_ms for s in trace.steps) * 1_000_000)
        # Trace-level attributes propagate to every observation created within this context (v4).
        with propagate_attributes(user_id=trace.user_hash, session_id=trace.correlation_id,
                                  trace_name="previsit-brief", tags=tags, metadata=metadata):
            # `end_on_exit=False`: we set the root's end explicitly so the trace carries the real
            # request duration (the tree is built at request end, so wall-clock spans are ~0).
            with client.start_as_current_observation(
                name="previsit-brief", as_type="span", metadata=metadata,
                level="ERROR" if trace.degraded else "DEFAULT",
                status_message=trace.fallback_kind, end_on_exit=False,
            ) as root:
                for st in trace.steps:
                    end_ns = now_ns + int(st.latency_ms * 1_000_000)
                    if st.name == "llm.complete":
                        # A native GENERATION carries model + token usage + cost, so Langfuse's
                        # native cost/latency widgets work (metadata alone cannot power them).
                        obs = client.start_observation(
                            name="llm", as_type="generation", model=trace.model,
                            usage_details=_usage_details(st.detail),
                            cost_details=_cost_details(st.detail, trace.model),
                            metadata={"latency_ms": st.latency_ms, **st.detail})
                    else:
                        obs = client.start_observation(
                            name=st.name, as_type="span",
                            level="ERROR" if st.detail.get("status") == "failed" else "DEFAULT",
                            metadata={"latency_ms": st.latency_ms, **st.detail})
                    obs.end(end_time=end_ns)      # give the observation its real duration
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

    @property
    def tracer(self) -> "RequestTracer":
        """The owning tracer — lets a caller that holds only the builder (service.py begins the
        trace before fan-out, CXR-05) count a finish/build drop against the soft-dep counter."""
        return self._tracer

    def step(self, name: str, *, latency_ms: float, **detail: Any) -> None:
        self._steps.append(TraceStep(order=self._order, name=name, latency_ms=latency_ms, detail=detail))
        self._order += 1

    def record_usage(self, usage: Usage) -> None:
        self._usage = self._usage.add(usage)

    def record_verdict(self, verdict: str) -> None:
        self._verdicts.append(str(verdict))

    def finish(self, *, model: str, source: str, degraded: bool,
               fallback_kind: str | None) -> RequestTrace:
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
        )
        self._tracer._emit(trace)
        return trace


class RequestTracer:
    def __init__(self, sink: TraceSink):
        self.sink = sink
        self.dropped = 0

    def begin(self, acct: AccountabilityContext) -> TraceBuilder:
        return TraceBuilder(self, acct)

    def _emit(self, trace: RequestTrace) -> None:
        try:
            self.sink.emit(trace)
        except Exception:
            self.dropped += 1  # §6: export dropped + counted; serving never affected
