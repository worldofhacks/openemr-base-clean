"""Typed contracts for the Week 2 eval gate (W2-D5/D7/D8, §7/§7a).

The executor observation is deliberately separate from the golden expectation.  A
runner must supply an observation; the harness never manufactures one from the
golden case.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.citations import CitationV2


class EvalCategoryV2(str, Enum):
    BOUNDARY = "boundary"
    INVARIANT = "invariant"
    REGRESSION = "regression"
    ADVERSARIAL = "adversarial"


class Rubric(str, Enum):
    SCHEMA_VALID = "schema_valid"
    CITATION_PRESENT = "citation_present"
    FACTUALLY_CONSISTENT = "factually_consistent"
    SAFE_REFUSAL = "safe_refusal"
    NO_PHI_IN_LOGS = "no_phi_in_logs"


class RunStatus(str, Enum):
    """Closed gate outcome; infrastructure exhaustion is never scored as a case fail."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class SafetyCode(str, Enum):
    """The complete Week 2 safety-event vocabulary used by the graded gate."""

    EMBEDDED_COMMAND_IGNORED = "embedded_command_ignored"
    CROSS_PATIENT_WRITE_BLOCKED = "cross_patient_write_blocked"
    IDENTIFIER_QUERY_BLOCKED = "identifier_query_blocked"
    CONFIRM_WITH_PATIENT = "confirm_with_patient"
    UNSUPPORTED_FIELD = "unsupported_field"
    ARTIFACT_ONLY_SKIP = "artifact_only_skip"


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldenCase(_ClosedModel):
    """One manifest entry, validated without assuming a fixed case count or ID set."""

    case_id: str = Field(min_length=1)
    category: EvalCategoryV2
    doc_type: str = Field(min_length=1)
    fixture_path: str = Field(min_length=1)
    expected_fields: dict[str, Any]
    expected_citations: list[CitationV2]
    expected_verdict: str = Field(min_length=1)
    guards: str = Field(min_length=1)
    pass_criteria: list[str] = Field(min_length=1)
    maps_to: Rubric
    safety_expectations: list["SafetyExpectation"] = Field(default_factory=list)


class SafetyExpectation(_ClosedModel):
    """A required safe behavior, separate from the clinical extraction verdict."""

    code: SafetyCode


class SafetyEvent(_ClosedModel):
    """PHI-free evidence captured from the executor's side-effect boundary."""

    code: SafetyCode
    blocked: bool
    prohibited_side_effect_count: int = Field(default=0, ge=0)
    target_patient_pinned: bool | None = None
    outbound_query_validated: bool | None = None


class GeneratedSurfaces(_ClosedModel):
    """Generated artifacts that are allowed to contain metadata, never PHI."""

    logs: list[Any] = Field(default_factory=list)
    traces: list[Any] = Field(default_factory=list)
    results: list[Any] = Field(default_factory=list)
    reports: list[Any] = Field(default_factory=list)
    recordings: list[Any] = Field(default_factory=list)
    screenshots: list[Any] = Field(default_factory=list)


class RefusalObservation(_ClosedModel):
    code: str = Field(min_length=1)
    blocked: bool
    message: str = Field(min_length=1)


class CaseObservation(_ClosedModel):
    """Executor-produced result plus generated telemetry/artifact surfaces.

    ``fields`` and ``citations`` are intentionally permissive at this boundary so a
    malformed executor result can reach the boolean schema/citation scorers and be
    reported as a rubric failure rather than being mistaken for harness success.
    """

    case_id: str = Field(min_length=1)
    fields: dict[str, Any]
    citations: list[Any]
    verdict: str = Field(min_length=1)
    refusal: RefusalObservation | None = None
    output: Any = None
    factual_judgement: bool | None = None
    safety_events: list[SafetyEvent] = Field(default_factory=list)
    generated: GeneratedSurfaces = Field(default_factory=GeneratedSurfaces)


class CaseRubricResult(_ClosedModel):
    case_id: str = Field(min_length=1)
    rubric: Rubric
    applicable: bool
    passed: bool | None
    detail: str = Field(min_length=1)


class CaseEvaluationResult(_ClosedModel):
    case_id: str = Field(min_length=1)
    status: RunStatus = RunStatus.PASS
    scores: list[CaseRubricResult]


class RubricSummary(_ClosedModel):
    rubric: Rubric
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    inconclusive: int = Field(default=0, ge=0)
    score: float = Field(ge=0.0, le=1.0)
    current_score: float = Field(ge=0.0, le=1.0)
    baseline_score: float | None = Field(default=None, ge=0.0, le=1.0)
    percentage_point_delta: float | None = None
    threshold: float = Field(ge=0.0, le=1.0)
    passed: bool
    trigger: str = Field(min_length=1)


class HarnessReport(_ClosedModel):
    status: RunStatus
    passed: bool
    cases: list[CaseEvaluationResult]
    categories: list[RubricSummary]


class BaselineCategory(_ClosedModel):
    rubric: Rubric
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    score: float = Field(ge=0.0, le=1.0)


class EvalBaseline(_ClosedModel):
    """Reviewed live baseline. CI may read this object but never mutates it."""

    status: Literal[RunStatus.PASS] = RunStatus.PASS
    tier: Literal["live"] = "live"
    case_count: int = Field(ge=50)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha: str = Field(min_length=1)
    generated_from_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    categories: list[BaselineCategory] = Field(min_length=1)
