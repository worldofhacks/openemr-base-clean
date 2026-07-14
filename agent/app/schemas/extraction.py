"""Extraction contracts + the canonical grounding wrapper (W2_ARCHITECTURE.md §2, W2-D3/D10).

This module owns:

* ``GroundedField[T]`` — the GENERIC leaf wrapper every extracted clinical value is
  wrapped in. A construct-time model validator enforces the SAFETY-CRITICAL
  citation/grounding biconditional (§2 composition rule / W2-D3):
    - ``grounded=True``  ⇒ a complete ``CitationV2`` AND a ``NormBBox`` (renders/writes
      as fact);
    - ``grounded=False`` ⇒ ``citation is None`` (renders UNSUPPORTED, never writes);
      the field MAY still carry a ``bbox`` as an UNSUPPORTED review region.
  Both contradictions are REJECTED at construction. The generic ``T`` is genuinely
  validated (``GroundedField[date]`` rejects a non-date value).
* ``NormBBox`` — the canonical normalized page-relative box. This is the CANONICAL
  home unified from the M4 reader (§2); ``app.ingestion.reader`` re-exports THIS class
  object by identity, never a copy.
* the lab (``LabResult``/``LabPdfExtraction``) and intake
  (``Demographics``/``VitalCandidate``/``IntakeVitals``/``IntakeFormExtraction``)
  extraction shapes, and the persisted ``ExtractionArtifact`` + typed ``VitalsWrite``
  (W2-D10). Every clinical leaf is a ``GroundedField`` of the right ``T``.

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.citations import CitationV2

T = TypeVar("T")


# --- the canonical box (unified from the M4 reader) -----------------------------------


class NormBBox(BaseModel):
    """Canonical normalized page-relative box (§2).

    Coordinates are normalized to ``[0, 1]``, origin TOP-LEFT, y-DOWN (a word near the
    page top has SMALL ``y0``). Frozen and strict: construction validates the range and
    non-degenerate/non-inverted invariants, unknown fields are rejected. This is the
    CANONICAL home — ``app.ingestion.reader`` re-exports this exact class object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _validate_canonical(self) -> "NormBBox":
        for name in ("x0", "y0", "x1", "y1"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name}={value} escaped canonical range [0, 1]")
        if not self.x0 < self.x1:
            raise ValueError(f"degenerate/inverted box: x0={self.x0} !< x1={self.x1}")
        if not self.y0 < self.y1:
            raise ValueError(f"degenerate/inverted box: y0={self.y0} !< y1={self.y1}")
        return self


# --- the grounding wrapper ------------------------------------------------------------


class GroundedField(BaseModel, Generic[T]):
    """The generic grounded leaf wrapper (§2 composition rule / W2-D3).

    ``value`` is the (nullable) extracted value of the parametrized type ``T``; ``page``
    and ``bbox`` locate it on the source page; ``grounded`` is the BINARY grounding
    verdict (grounding agreement, never a VLM self-report); ``citation`` is the complete
    ``CitationV2`` when — and only when — the field is grounded.

    The ``strict`` config keeps ``T`` from silently coercing (a ``Decimal`` weight stays
    a ``Decimal``); the model validator enforces the biconditional at construction.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    value: Optional[T] = None
    page: Optional[int] = None
    bbox: Optional[NormBBox] = None
    grounded: bool
    citation: Optional[CitationV2] = None

    @model_validator(mode="after")
    def _validate_grounding_biconditional(self) -> "GroundedField[T]":
        if self.grounded:
            # grounded=True renders/writes as FACT — it MUST carry a complete citation
            # AND an on-page bbox to anchor the overlay/write to (§2, safety-critical).
            if self.citation is None:
                raise ValueError(
                    "grounded=True requires a complete CitationV2 (citation is None)"
                )
            if self.bbox is None:
                raise ValueError("grounded=True requires a bbox (bbox is None)")
        else:
            # grounded=False renders UNSUPPORTED and MUST NOT write as fact — a citation
            # here would let a failed-grounding field render as fact anyway. A bbox is
            # still allowed as an UNSUPPORTED review region.
            if self.citation is not None:
                raise ValueError("grounded=False forbids a citation (citation is set)")
        return self


# --- lab extraction (§2) --------------------------------------------------------------


class LabResult(BaseModel):
    """One lab result row (§2). Every leaf is a ``GroundedField`` of the right ``T``.

    ``collection_date`` is RESULT-level (``GroundedField[date]``), not report-level —
    the former report-level ``collection_date`` shape is superseded (§2).
    """

    model_config = ConfigDict(extra="forbid")

    test_name: GroundedField[str]
    value: GroundedField[str]
    unit: GroundedField[str]
    reference_range: GroundedField[str]
    abnormal_flag: GroundedField[str]
    collection_date: GroundedField[date]


class LabPdfExtraction(BaseModel):
    """A lab-PDF extraction: an ordered list of results + the source document id (§2).

    An empty ``results`` list is valid (nothing extracted).
    """

    model_config = ConfigDict(extra="forbid")

    results: list[LabResult] = Field(default_factory=list)
    source_document_id: str = Field(min_length=1)


# --- intake extraction (§2, W2-D10) ---------------------------------------------------


class Demographics(BaseModel):
    """Intake demographics (§2). Every leaf is ``GroundedField``-wrapped."""

    model_config = ConfigDict(extra="forbid")

    name: GroundedField[str]
    dob: GroundedField[date]
    sex: GroundedField[str]
    contact: GroundedField[str]


class VitalCandidate(BaseModel):
    """One candidate vital measurement (§2, W2-D10).

    ``value`` is a ``GroundedField[Decimal]`` — the numeric value is preserved EXACTLY
    as a ``Decimal`` (never coerced to a lossy ``float``) so the unit-mismatch and
    range checks operate on the true reading. ``unit`` and ``measurement_date`` are
    grounded strings/datetimes.
    """

    model_config = ConfigDict(extra="forbid")

    value: GroundedField[Decimal]
    unit: GroundedField[str]
    measurement_date: GroundedField[datetime]


class IntakeVitals(BaseModel):
    """The eight optional intake vitals (§2, W2-D10).

    Each slot is a ``VitalCandidate`` or ``None`` — an all-``None`` instance (nothing
    measured) is valid.
    """

    model_config = ConfigDict(extra="forbid")

    bps: Optional[VitalCandidate] = None
    bpd: Optional[VitalCandidate] = None
    weight: Optional[VitalCandidate] = None
    height: Optional[VitalCandidate] = None
    temperature: Optional[VitalCandidate] = None
    pulse: Optional[VitalCandidate] = None
    respiration: Optional[VitalCandidate] = None
    oxygen_saturation: Optional[VitalCandidate] = None


class IntakeFormExtraction(BaseModel):
    """A patient-intake-form extraction (§2, W2-D10).

    Every clinical leaf is ``GroundedField``-wrapped. ``note`` is deliberately ABSENT
    from the extracted-field set: the note is generated provenance/correlation metadata
    (produced on the write, §2 ``VitalsWrite``), NEVER an extracted field.
    """

    model_config = ConfigDict(extra="forbid")

    demographics: Demographics
    chief_concern: GroundedField[str]
    current_medications: list[GroundedField[str]] = Field(default_factory=list)
    allergies: list[GroundedField[str]] = Field(default_factory=list)
    family_history: GroundedField[str]
    vitals: IntakeVitals
    source_document_id: str = Field(min_length=1)


# --- persisted artifact + typed write (§2, W2-D10) ------------------------------------


class ExtractionArtifact(BaseModel):
    """The persisted extraction artifact (§2).

    Stored as ``application/json`` under the OpenEMR document category
    "AI-Extractions". ``extraction`` is the lab OR intake extraction; ``content_hash``
    and ``correlation_id`` tie it to the source document and the request that produced
    it; ``grounding_summary`` records the grounded/unsupported field tallies.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_version: int
    document_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    doc_type: Literal["lab_pdf", "intake_form"]
    extraction: LabPdfExtraction | IntakeFormExtraction
    grounding_summary: dict[str, int] = Field(default_factory=dict)
    created_ts: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)


class VitalsWrite(BaseModel):
    """The typed vitals write payload (§2, W2-D10 / W2-F16).

    Constructed ONLY from the grounded ``IntakeVitals`` mapping. It carries NO caller
    ``user``/``group`` attribution fields — caller attribution is stripped (W2-F16), so
    a request body cannot spoof the write performer. ``note`` here is generated
    provenance, not an extracted field.

    ``strict`` is ON so the numeric legs cannot silently accept a lossy ``float`` even if
    a caller builds this off the grounded ``IntakeVitals`` path — the exact Decimal reading
    is preserved to the write (§2/W2-D10; reviewer hardening).
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    bps: Optional[Decimal] = None
    bpd: Optional[Decimal] = None
    weight: Optional[Decimal] = None
    height: Optional[Decimal] = None
    temperature: Optional[Decimal] = None
    pulse: Optional[Decimal] = None
    respiration: Optional[Decimal] = None
    oxygen_saturation: Optional[Decimal] = None
    date: str = Field(min_length=1)
    note: str
