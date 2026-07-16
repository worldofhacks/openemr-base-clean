"""Safe browser-facing extraction report (W2-D3/D6/D9; §2/§2a/§5).

The persisted ``ExtractionArtifact`` may retain an unsupported VLM proposal for audit and
reconciliation.  That value must never cross the physician render seam as a fact.  These
models expose grounded values and citations, but redact unsupported values structurally.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.citations import CitationV2
from app.schemas.extraction import NormBBox


class ExtractionReportField(BaseModel):
    """One persisted clinical leaf projected for safe review."""

    model_config = ConfigDict(extra="forbid", strict=True)

    field_path: str = Field(min_length=1)
    verdict: Literal["grounded", "unsupported"]
    display_value: str | None
    page: int | None
    bbox: NormBBox | None
    citation: CitationV2 | None

    @model_validator(mode="after")
    def _enforce_render_contract(self) -> "ExtractionReportField":
        if self.verdict == "grounded":
            if (
                self.display_value is None
                or self.page is None
                or self.bbox is None
                or self.citation is None
            ):
                raise ValueError(
                    "grounded report fields require value, page, bbox, and citation"
                )
        else:
            if self.display_value is not None or self.citation is not None:
                raise ValueError(
                    "unsupported report fields redact values and forbid citations"
                )
            if self.bbox is not None and self.page is None:
                raise ValueError("an unsupported review bbox requires its page")
        return self


class DocumentExtractionReport(BaseModel):
    """Complete, patient-pinned report projected from one persisted artifact."""

    model_config = ConfigDict(extra="forbid", strict=True)

    document_id: str = Field(min_length=1)
    doc_type: Literal["lab_pdf", "intake_form", "medication_list"]
    state: Literal["complete"]
    fields_grounded: int = Field(ge=0)
    fields_unsupported: int = Field(ge=0)
    fields: list[ExtractionReportField]

    @model_validator(mode="after")
    def _counts_match_fields(self) -> "DocumentExtractionReport":
        grounded = sum(field.verdict == "grounded" for field in self.fields)
        unsupported = sum(field.verdict == "unsupported" for field in self.fields)
        if (grounded, unsupported) != (
            self.fields_grounded,
            self.fields_unsupported,
        ):
            raise ValueError("extraction report counts do not match its fields")
        return self
