"""Trace sinks + the request tracer (ARCHITECTURE.md §7, §6, D5-rev).

The tracer builds a `RequestTrace` from steps recorded during a request and hands it to a
`TraceSink`. Observability is a SOFT dependency (§6): if the sink fails — Langfuse down,
misconfigured, SDK mismatch — the tracer swallows the error and increments a `dropped`
counter; serving is never affected. The `LangfuseSink` lazy-imports the SDK and maps the
trace to a Langfuse trace with the accountability metadata + the E5 degradation tags so
fallback-rate is alertable; any failure propagates to the tracer, which counts it.
"""

from __future__ import annotations

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
        }
        tags = [
            f"client:{trace.client_id}",
            f"source:{trace.source}",
            f"fallback:{trace.fallback_kind or 'none'}",  # dashboard filter for fallback-rate
        ]
        with client.start_as_current_span(name=f"previsit-brief:{trace.correlation_id}") as span:
            span.update_trace(
                name="previsit-brief",
                user_id=trace.user_hash,          # already hashed (D5)
                session_id=trace.correlation_id,
                # No `input=` payload: never surface the URL (or anything PHI-bearing) as the
                # visible trace input. The sanitized route lives in metadata only.
                metadata=metadata,
                tags=tags,
            )
            for st in trace.steps:
                with span.start_as_current_observation(name=st.name) as child:
                    child.update(metadata={"latency_ms": st.latency_ms, **st.detail})
        client.flush()

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
