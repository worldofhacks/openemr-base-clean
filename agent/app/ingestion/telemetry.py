"""PHI-free, failure-isolated telemetry for one persisted document job.

The durable job correlation and job id are the only identifiers admitted.  Clinical
values, filenames, patient/user ids, provider payloads, and exception text never enter
this object.  Event validation/export remains a soft dependency through ``EventEmitter``.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.llm.cost import estimate_cost
from app.llm.provider import Usage
from app.observability.events import (
    EventComponent,
    EventEmitter,
    EventSeverity,
    EventType,
    IngestionStageCode,
    OperationalStepCode,
    RerankerModeCode,
    VerificationOutcomeCode,
    WriteStateCode,
)
from app.observability.summary import encounter_summary_attributes
from app.schemas.writeback import WriteLeg, WriteResult


@dataclass
class StageSpan:
    """One in-process child span with no content-bearing attributes."""

    stage: IngestionStageCode
    latency_ms: float = 0.0


@dataclass
class DocumentTelemetry:
    """Accumulate and emit the operational record for one durable document job."""

    events: EventEmitter | None
    correlation_id: str
    job_id: str
    _steps: list[tuple[OperationalStepCode, float]] = field(default_factory=list)
    _usage: Usage = field(default_factory=Usage)
    _cost_usd: float = 0.0
    _grounding_rate: float = 0.0
    _retrieval_hit_count: int = 0
    _verification_outcomes: list[VerificationOutcomeCode] = field(default_factory=list)
    _finished: bool = False

    def _emit(
        self,
        event_type: EventType,
        attributes: dict[str, object],
        *,
        component: EventComponent,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        if self.events is None:
            return
        self.events.emit(
            event_type,
            attributes,
            component=component,
            severity=severity,
            job_id=self.job_id,
            correlation_id=self.correlation_id,
        )

    @asynccontextmanager
    async def stage(self, stage: IngestionStageCode) -> AsyncIterator[StageSpan]:
        """Emit start/end state and retain ordered latency for the terminal summary."""

        span = StageSpan(stage)
        self._emit(
            EventType.INGESTION_STAGE,
            {"stage": stage, "state": "started", "latency_ms": 0.0},
            component=EventComponent.WORKER,
        )
        started = time.perf_counter()
        try:
            yield span
        except BaseException:
            span.latency_ms = max((time.perf_counter() - started) * 1000, 0.0)
            self._steps.append((stage, span.latency_ms))
            self._emit(
                EventType.INGESTION_STAGE,
                {"stage": stage, "state": "failed", "latency_ms": span.latency_ms},
                component=EventComponent.WORKER,
                severity=EventSeverity.ERROR,
            )
            raise
        else:
            span.latency_ms = max((time.perf_counter() - started) * 1000, 0.0)
            self._steps.append((stage, span.latency_ms))
            self._emit(
                EventType.INGESTION_STAGE,
                {"stage": stage, "state": "completed", "latency_ms": span.latency_ms},
                component=EventComponent.WORKER,
            )

    def record_usage(self, usage: Usage, model: str) -> None:
        """Retain only aggregate usage/cost from the VLM response."""

        self._usage = self._usage.add(usage)
        try:
            self._cost_usd += max(estimate_cost(usage, model), 0.0)
        except Exception:
            # Unknown pricing must not break extraction or fabricate a cost.
            pass

    def record_grounding(
        self, *, fields_grounded: int, fields_unsupported: int
    ) -> None:
        total = fields_grounded + fields_unsupported
        self._grounding_rate = fields_grounded / total if total else 0.0
        self._emit(
            EventType.GROUNDING_COMPLETED,
            {
                "fields_total": total,
                "fields_grounded": fields_grounded,
                "fields_unsupported": fields_unsupported,
                "grounding_rate": self._grounding_rate,
            },
            component=EventComponent.WORKER,
        )

    def record_retrieval(
        self,
        *,
        hit_count: int,
        latency_ms: float,
        degraded: bool,
        reranker_mode: RerankerModeCode,
    ) -> None:
        """Record a retrieval completion for this job (R05 fused summary).

        Emits ``retrieval.completed`` at completion time and retains the hit count so
        the terminal summary carries it — the previous emitter structurally pinned
        ``retrieval_hit_count`` to zero even for lanes that had retrieval data.
        """

        self._retrieval_hit_count = min(
            self._retrieval_hit_count + max(int(hit_count), 0), 20
        )
        self._emit(
            EventType.RETRIEVAL_COMPLETED,
            {
                "hit_count": min(max(int(hit_count), 0), 20),
                "latency_ms": max(latency_ms, 0.0),
                "degraded": degraded,
                "reranker_mode": reranker_mode,
            },
            component=EventComponent.RETRIEVAL,
            severity=EventSeverity.WARNING if degraded else EventSeverity.INFO,
        )

    def record_write_result(
        self,
        leg: WriteLeg,
        result: WriteResult,
        *,
        latency_ms: float,
    ) -> None:
        """Observe the existing exactly-once outcome without changing its state machine."""

        self.record_write_transition(
            leg,
            state=result.state.value,
            verified=result.verified,
        )
        self.record_readback(leg, verified=result.verified, latency_ms=latency_ms)
        outcome: VerificationOutcomeCode = (
            "complete" if result.verified else "failed"
        )
        self._verification_outcomes.append(outcome)

    def record_write_transition(
        self,
        leg: WriteLeg,
        *,
        state: WriteStateCode,
        verified: bool,
    ) -> None:
        """Emit a content-free transition, including in-doubt reconciliation states."""

        self._emit(
            EventType.WRITE_INTENT_TRANSITION,
            {
                "leg": leg.value,
                "state": state,
                # WriteResult deliberately omits repository internals.  Zero means the
                # transition was observed after execution, not that no attempt occurred.
                "attempt_count": 0,
                "verified": verified,
            },
            component=EventComponent.WRITEBACK,
            severity=(
                EventSeverity.INFO if verified else EventSeverity.WARNING
            ),
        )

    def record_readback(
        self, leg: WriteLeg, *, verified: bool, latency_ms: float
    ) -> None:
        self._emit(
            EventType.READBACK_COMPLETED,
            {
                "leg": leg.value,
                "verified": verified,
                "latency_ms": max(latency_ms, 0.0),
            },
            component=EventComponent.WRITEBACK,
            severity=EventSeverity.INFO if verified else EventSeverity.WARNING,
        )

    def finish(self, *, success: bool) -> None:
        """Emit exactly one terminal aggregate for the worker's persisted correlation."""

        if self._finished:
            return
        self._finished = True
        outcome: VerificationOutcomeCode = "complete" if success else "failed"
        if not self._verification_outcomes:
            self._verification_outcomes.append(outcome)
        self._emit(
            EventType.ENCOUNTER_SUMMARY,
            encounter_summary_attributes(
                steps=list(self._steps),
                input_tokens=self._usage.input_tokens,
                output_tokens=self._usage.output_tokens,
                cost_usd=self._cost_usd,
                retrieval_hit_count=self._retrieval_hit_count,
                extraction_grounding_rate=self._grounding_rate,
                verification_outcomes=list(self._verification_outcomes),
            ),
            component=EventComponent.WORKER,
            severity=EventSeverity.INFO if success else EventSeverity.ERROR,
        )
