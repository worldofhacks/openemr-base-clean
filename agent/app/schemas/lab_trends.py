"""Patient-pinned, artifact-backed lab-trend response contracts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.citations import CitationSourceType, CitationV2
from app.schemas.extraction import NormBBox


class LabTrendPoint(BaseModel):
    """One numeric, locally grounded result from a verified lab artifact."""

    model_config = ConfigDict(extra="forbid", strict=True)

    document_id: str = Field(min_length=1)
    result_index: int = Field(ge=0)
    collection_date: date
    value: Decimal
    display_value: str = Field(min_length=1)
    citation: CitationV2
    date_citation: CitationV2
    page: int = Field(ge=1)
    bbox: NormBBox

    @model_validator(mode="after")
    def _validate_source_anchors(self) -> "LabTrendPoint":
        if not self.value.is_finite():
            raise ValueError("lab trend values must be finite decimals")
        for citation in (self.citation, self.date_citation):
            if citation.source_type is not CitationSourceType.UPLOADED_DOCUMENT:
                raise ValueError("lab trend citations must resolve to uploaded documents")
            if citation.source_id != self.document_id:
                raise ValueError("lab trend citation does not match its document")
            if citation.page_or_section is None:
                raise ValueError("lab trend citations require a page")
        if self.citation.page_or_section != str(self.page):
            raise ValueError("lab trend point page does not match its value citation")
        return self


class LabTrendSeries(BaseModel):
    """One exact-name/exact-unit series; mixed units are never combined."""

    model_config = ConfigDict(extra="forbid", strict=True)

    test_name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    points: list[LabTrendPoint] = Field(min_length=1)


class LabTrendResponse(BaseModel):
    """Read-only trends derived exclusively from persisted lab artifacts."""

    model_config = ConfigDict(extra="forbid", strict=True)

    series: list[LabTrendSeries] = Field(default_factory=list)
