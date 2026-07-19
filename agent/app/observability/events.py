"""Closed, PHI-free structured event contracts and soft-failure sinks.

``LogEventEnvelope`` retains the frozen ten-field shape.  Event names and attributes are
now validated through a closed registry, so arbitrary clinical values, query/document
text, identifiers, exception bodies, token material, and unknown keys cannot enter the
event lane.
"""

from __future__ import annotations

import enum
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional, Protocol, TextIO, Union, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


_MAX_ATTRIBUTE_STR_LEN = 256
LogScalarStr = Annotated[
    str,
    StringConstraints(max_length=_MAX_ATTRIBUTE_STR_LEN, pattern=r"^[^\r\n]*$"),
]
LogScalar = Union[LogScalarStr, bool, int, float]
LogAttributeValue = Union[LogScalar, list[LogScalar]]
SafeCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=96,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
    ),
]
SafeId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
    ),
]

# Attribute values are closed as well as attribute names.  Regex-only "safe strings"
# are not sufficient here: a short patient identifier or medication name is perfectly
# capable of matching ``[A-Za-z0-9_.:-]+``.  These literals are operational vocabulary
# owned by the application, never caller/provider text.
FailureReasonCode = Literal[
    "patient_mismatch",
    "encounter_mismatch",
    "unit_mismatch",
    "range_violation",
    "scope_mismatch",
    "category_mismatch",
    "binary_readback_unsafe",
    "upload_rejected",
    "unsupported_media_type",
    "size_or_page_cap_exceeded",
    "storage_write_failed",
    "ocr_failed",
    "vlm_timeout",
    "vlm_unavailable",
    "schema_violation",
    "auth_expired",
    "writeback_failed",
    "writeback_verify_failed",
    "doc_type_mismatch",
    "worker_restart",
]
# ``source`` is retained as the frozen envelope's original operational label;
# ``source_document`` is the canonical exactly-once write-leg value.
WriteLegCode = Literal["source", "source_document", "extraction_artifact", "vital"]
WriteStateCode = Literal["pending", "unknown", "complete", "failed"]
IngestionStageCode = Literal[
    "source_write",
    "source_readback",
    "ocr",
    "vlm",
    "schema_parse",
    "grounding",
    "artifact_write",
    "vital_write",
]
StageStateCode = Literal["started", "completed", "failed"]
QueueStateCode = Literal[
    "storing",
    "reconciling",
    "queued",
    "claimed",
    "extracting",
    "grounding",
    "writing",
    "rescheduled",
    "complete",
    "failed",
]
HandoffDecisionCode = Literal[
    "route_extract",
    "route_retrieve",
    "compose_answer",
    "review_critic",
    "critic_approve",
    "critic_reject",
    "refuse",
    "done",
]
HandoffReasonCode = Literal[
    "extraction_requested",
    "retrieval_requested",
    "workers_complete",
    "critic_review_requested",
    "critic_approved",
    "critic_rejected",
    "step_budget_exceeded",
    "turn_complete",
]
WorkerCode = Literal[
    "supervisor",
    "intake_extractor",
    "intake_extractor_stub",
    "evidence_retriever",
    "evidence_retriever_stub",
    "composer",
    "critic",
]
RerankerModeCode = Literal["local", "cohere", "disabled"]
BreakerDependencyCode = Literal[
    "postgres",
    "openemr",
    "anthropic",
    "reranker",
    "langfuse",
    "document_worker",
]
BreakerStateCode = Literal["closed", "open", "half_open", "degraded", "unavailable"]
EvalCategoryCode = Literal[
    "schema_valid",
    "citation_present",
    "factually_consistent",
    "safe_refusal",
    "no_phi_in_logs",
]
OperationalStepCode = Literal[
    "source_write",
    "source_readback",
    "ocr",
    "vlm",
    "schema_parse",
    "grounding",
    "artifact_write",
    "vital_write",
    "fhir.get_patient_summary",
    "fhir.get_conditions",
    "fhir.get_active_medications",
    "fhir.get_recent_labs",
    "fhir.get_encounters",
    "fhir.get_allergies",
    "fhir.synthetic",
    "llm.complete",
    "tool.get_conditions",
    "verify",
    "graph.worker.intake_extractor",
    "graph.worker.intake_extractor_stub",
    "graph.worker.evidence_retriever",
    "graph.worker.evidence_retriever_stub",
    "graph.composer",
    "graph.critic",
]
VerificationOutcomeCode = Literal[
    "pass",
    "flagged",
    "blocked",
    "refused",
    "refused:deceased",
    "refused:treatment_advice",
    "refused:wrong_patient",
    "refused:ambiguous",
    "refused:expired_session",
    "refused:critic_rejected",
    "refused:step_budget_exceeded",
    "complete",
    "failed",
]


class EventType(str, enum.Enum):
    JOB_CLAIMED = "job.claimed"
    JOB_FAILED = "job.failed"
    INGESTION_STAGE = "ingestion.stage"
    GROUNDING_COMPLETED = "grounding.completed"
    RETRIEVAL_COMPLETED = "retrieval.completed"
    HANDOFF_COMPLETED = "handoff.completed"
    QUEUE_STATE = "queue.state"
    WRITE_INTENT_TRANSITION = "write_intent.transition"
    READBACK_COMPLETED = "readback.completed"
    BREAKER_STATE = "breaker.state"
    EVAL_RESULT = "eval.result"
    ENCOUNTER_SUMMARY = "encounter.summary"


class EventComponent(str, enum.Enum):
    API = "api"
    ORCHESTRATOR = "orchestrator"
    WORKER = "worker"
    RETRIEVAL = "retrieval"
    WRITEBACK = "writeback"
    EVAL = "eval"


class EventSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class _Attributes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class JobAttributes(_Attributes):
    reason: FailureReasonCode | None = None
    attempt_count: int = Field(default=0, ge=0)
    legs: list[WriteLegCode] = Field(default_factory=list, max_length=16)


class IngestionAttributes(_Attributes):
    stage: IngestionStageCode
    state: StageStateCode
    latency_ms: float = Field(ge=0)


class GroundingAttributes(_Attributes):
    fields_total: int = Field(ge=0)
    fields_grounded: int = Field(ge=0)
    fields_unsupported: int = Field(ge=0)
    grounding_rate: float = Field(ge=0, le=1)


class RetrievalAttributes(_Attributes):
    hit_count: int = Field(ge=0, le=20)
    latency_ms: float = Field(ge=0)
    degraded: bool
    reranker_mode: RerankerModeCode


class HandoffAttributes(_Attributes):
    turn: int = Field(ge=0)
    decision: HandoffDecisionCode
    reason_code: HandoffReasonCode
    worker: WorkerCode
    latency_ms: float = Field(ge=0)


class QueueAttributes(_Attributes):
    state: QueueStateCode
    attempt_count: int = Field(ge=0)
    queue_age_ms: float = Field(ge=0)


class WriteIntentAttributes(_Attributes):
    leg: WriteLegCode
    state: WriteStateCode
    attempt_count: int = Field(ge=0)
    verified: bool


class ReadbackAttributes(_Attributes):
    leg: WriteLegCode
    verified: bool
    latency_ms: float = Field(ge=0)


class BreakerAttributes(_Attributes):
    dependency: BreakerDependencyCode
    state: BreakerStateCode


class EvalAttributes(_Attributes):
    category: EvalCategoryCode
    passed: bool
    case_count: int = Field(ge=0)


class EncounterSummaryAttributes(_Attributes):
    ordered_steps: list[OperationalStepCode] = Field(default_factory=list, max_length=64)
    step_latencies_ms: list[float] = Field(default_factory=list, max_length=64)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    retrieval_hit_count: int = Field(ge=0, le=20)
    extraction_grounding_rate: float = Field(ge=0, le=1)
    verification_outcomes: list[VerificationOutcomeCode] = Field(
        default_factory=list, max_length=64
    )

    @model_validator(mode="after")
    def _steps_align(self) -> "EncounterSummaryAttributes":
        if len(self.ordered_steps) != len(self.step_latencies_ms):
            raise ValueError("ordered step names and latencies must align")
        return self


_EVENT_REGISTRY: dict[EventType, type[_Attributes]] = {
    EventType.JOB_CLAIMED: JobAttributes,
    EventType.JOB_FAILED: JobAttributes,
    EventType.INGESTION_STAGE: IngestionAttributes,
    EventType.GROUNDING_COMPLETED: GroundingAttributes,
    EventType.RETRIEVAL_COMPLETED: RetrievalAttributes,
    EventType.HANDOFF_COMPLETED: HandoffAttributes,
    EventType.QUEUE_STATE: QueueAttributes,
    EventType.WRITE_INTENT_TRANSITION: WriteIntentAttributes,
    EventType.READBACK_COMPLETED: ReadbackAttributes,
    EventType.BREAKER_STATE: BreakerAttributes,
    EventType.EVAL_RESULT: EvalAttributes,
    EventType.ENCOUNTER_SUMMARY: EncounterSummaryAttributes,
}


class LogEventEnvelope(BaseModel):
    """The frozen structured-log envelope with registered attributes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    event_id: SafeId
    event_type: EventType
    occurred_at: LogScalarStr
    case_id: Optional[SafeId] = None
    job_id: Optional[SafeId] = None
    correlation_id: Optional[SafeId] = None
    component: EventComponent
    severity: EventSeverity
    attributes: dict[str, LogAttributeValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _registered_attributes_only(self) -> "LogEventEnvelope":
        model = _EVENT_REGISTRY[self.event_type]
        model.model_validate(self.attributes, strict=True)
        return self


@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: LogEventEnvelope) -> None: ...


class NullEventSink:
    def emit(self, event: LogEventEnvelope) -> None:
        del event


class InMemoryEventSink:
    def __init__(self) -> None:
        self.events: list[LogEventEnvelope] = []

    def emit(self, event: LogEventEnvelope) -> None:
        self.events.append(event)


class StructuredLogEventSink:
    """Production sink: one PHI-free JSON line per validated envelope on the log lane.

    Only ``EventEmitter``-validated envelopes reach a sink, so every attribute has
    already passed the closed registry — clinical values, free text, and unknown keys
    were rejected upstream. Each line lands on the same stdout stream as the
    structured application logs (§7), so the platform log export makes every W2 event
    searchable by ``correlation_id``, ``job_id``, and ``case_id`` (AF-P1-04).
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def emit(self, event: LogEventEnvelope) -> None:
        # Resolve lazily so pytest's stdout capture and process-level redirection work.
        stream = self._stream if self._stream is not None else sys.stdout
        payload: dict[str, object] = {"log_type": "w2.event"}
        payload.update(event.model_dump(mode="json", exclude_none=True))
        stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        stream.flush()


class EventEmitter:
    """Validate first, then emit through a failure-isolated injectable sink."""

    def __init__(self, sink: EventSink) -> None:
        self.sink = sink
        self.dropped = 0

    def emit(
        self,
        event_type: EventType,
        attributes: _Attributes | dict[str, object],
        *,
        component: EventComponent,
        severity: EventSeverity = EventSeverity.INFO,
        case_id: str | None = None,
        job_id: str | None = None,
        correlation_id: str | None = None,
    ) -> LogEventEnvelope | None:
        try:
            attribute_model = _EVENT_REGISTRY[event_type]
            validated = (
                attributes
                if isinstance(attributes, attribute_model)
                else attribute_model.model_validate(attributes, strict=True)
            )
            envelope = LogEventEnvelope(
                schema_version=1,
                event_id=uuid.uuid4().hex,
                event_type=event_type,
                occurred_at=datetime.now(timezone.utc).isoformat(),
                case_id=case_id,
                job_id=job_id,
                correlation_id=correlation_id,
                component=component,
                severity=severity,
                # The frozen envelope admits only scalar/list values, not JSON null.
                # Optional registered fields therefore remain absent when unset.
                attributes=validated.model_dump(mode="json", exclude_none=True),
            )
            self.sink.emit(envelope)
        except Exception:
            # Event construction/validation and export are the same soft boundary from
            # serving's perspective.  Invalid PHI-bearing attributes are rejected by being
            # dropped, never by changing the clinical response.
            self.dropped += 1
            return None
        return envelope


def event_registry() -> dict[EventType, type[_Attributes]]:
    """Return a copy so callers can inspect but never mutate the closed registry."""

    return dict(_EVENT_REGISTRY)
