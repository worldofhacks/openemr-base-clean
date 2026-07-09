"""Tool contracts — the strict, typed source of truth for tool I/O (ARCHITECTURE.md §5a, PRD).

Every FHIR read tool has a typed Pydantic input and returns a `ToolResult[...]` — a
tri-state envelope over evidence-record shapes. The tri-state is load-bearing:
  - OK          — records found.
  - NO_RECORDS  — queried successfully, zero records (e.g. the "no allergy records"
                  case → the templater says "confirm with patient", never "NKDA", §5 rule 3).
  - FAILED      — the tool errored/timed out; `missing_reason` NAMES what's missing so the
                  brief is a partial answer, never a silent omission (§6/F3).

The record shapes carry exactly the fields the §5/D7 verifier rules touch — nothing is
dropped that a rule needs, and fields the rules distrust (allergy criticality, encounter
status, immunization/med status) are optional and never assumed valid here.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# A validated FHIR patient id (parse, don't validate — reject empties at the boundary).
PatientId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ToolStatus(str, Enum):
    OK = "ok"
    NO_RECORDS = "no_records"
    FAILED = "failed"


# --- evidence record shapes (the typed data the EvidencePacket is built from, E4) ---

class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str  # the FHIR resource id (uuid); E4 derives the stable evidence id


class PatientRecord(_Record):
    name: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    # D12 deceased hard-stop keys on either of these (F-S.7).
    deceased_boolean: bool | None = None
    deceased_datetime: datetime | None = None


class ConditionRecord(_Record):
    code: str | None = None
    display: str | None = None
    # Rule 4: consume ALL conditions incl. inactive/resolved — keep the status, never filter it out.
    clinical_status: str | None = None
    onset: date | None = None
    recorded_date: date | None = None


class MedicationRecord(_Record):
    name: str | None = None
    rxnorm: str | None = None
    # Rule 6: dose may be absent (seed data / mapper) — optional; templater says "confirm before dosing".
    dose_text: str | None = None
    status: str | None = None            # low-trust (rule 1) — not asserted downstream
    intent: str | None = None            # order | plan — used to de-dup (rule 6)
    authored_on: date | None = None


class LabObservation(_Record):
    loinc: str | None = None
    display: str | None = None
    value: float | None = None
    value_string: str | None = None
    unit: str | None = None
    effective: date | None = None
    abnormal_flag: str | None = None
    category: str | None = None          # laboratory | vital-signs | ... (partition, don't conflate)


class EncounterRecord(_Record):
    status: str | None = None            # hardcoded "finished" upstream (rule 1) — non-asserted
    class_: str | None = Field(default=None, alias="class")
    type_display: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    reason: str | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AllergyRecord(_Record):
    substance: str | None = None
    # Rule 2: criticality is null dataset-wide (F-D.4) — optional and never trusted as risk.
    criticality: str | None = None
    clinical_status: str | None = None
    verification_status: str | None = None
    reaction: str | None = None
    category: str | None = None


# --- tool inputs -----------------------------------------------------------

class _PatientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: PatientId


class PatientSummaryInput(_PatientInput):
    pass


class ConditionsInput(_PatientInput):
    pass  # NO clinical-status filter (rule 4 — the OpenEMR filter is broken, F-D.6)


class MedicationsInput(_PatientInput):
    pass


class RecentLabsInput(_PatientInput):
    # F-P.2: pass an explicit category to prune the 10-way Observation fan-out.
    category: str = "laboratory"
    lookback_days: int | None = None


class EncountersInput(_PatientInput):
    count: int = 50


class AllergiesInput(_PatientInput):
    pass


class ChangesSinceLastVisitInput(_PatientInput):
    pass


# --- the tri-state result envelope -----------------------------------------

RecordT = TypeVar("RecordT", bound=_Record)


class ToolResult(BaseModel, Generic[RecordT]):
    model_config = ConfigDict(extra="forbid")

    tool: str
    status: ToolStatus
    records: list[RecordT] = Field(default_factory=list)
    # Set iff FAILED — the human-readable "what's missing" for the partial answer (§6/F3).
    missing_reason: str | None = None

    @model_validator(mode="after")
    def _failed_names_what_is_missing(self) -> "ToolResult[RecordT]":
        if self.status == ToolStatus.FAILED and not self.missing_reason:
            raise ValueError("a FAILED ToolResult must carry a missing_reason (never a silent omission)")
        if self.status != ToolStatus.FAILED and self.records is None:
            raise ValueError("non-failed ToolResult must have a records list")
        return self
