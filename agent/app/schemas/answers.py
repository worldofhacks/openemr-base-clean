"""Internal verified-answer contracts shared by the graph, composer, and LLM loop.

These models never cross the HTTP boundary directly.  They are the single internal
representation of a clinical claim that has already been resolved against an allowed
source.  ``GroundedAnswerContext`` is deliberately bounded: at most five guideline
snippets, retained in reranker order, may be shown to the answer model.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.citations import CitationSourceType, CitationV2, EvidenceSnippet
from app.schemas.extraction import NormBBox


MAX_GUIDELINE_SNIPPETS = 5
ClaimText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class VerifiedClinicalClaim(BaseModel):
    """One canonical verified chart, document, or guideline claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: ClaimText
    citation: CitationV2
    page: int | None = Field(default=None, ge=1)
    bbox: NormBBox | None = None

    @model_validator(mode="after")
    def _source_location_is_complete(self) -> "VerifiedClinicalClaim":
        source = self.citation.source_type
        location = self.citation.page_or_section
        if not self.citation.quote_or_value.strip():
            raise ValueError("verified claims require a non-blank canonical value")
        if source is CitationSourceType.PATIENT_RECORD:
            if location is not None or self.page is not None or self.bbox is not None:
                raise ValueError("patient-record claims cannot carry page/bbox metadata")
        elif source is CitationSourceType.UPLOADED_DOCUMENT:
            if self.page is None or self.bbox is None or location != str(self.page):
                raise ValueError("uploaded-document claims require a matching page and bbox")
        else:
            if not isinstance(location, str) or not location.strip():
                raise ValueError("guideline claims require a section")
            if self.page is not None or self.bbox is not None:
                raise ValueError("guideline claims cannot carry document page/bbox metadata")
        return self


class GroundedAnswerContext(BaseModel):
    """The complete, bounded evidence payload supplied to answer generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chart_claims: tuple[VerifiedClinicalClaim, ...] = ()
    document_claims: tuple[VerifiedClinicalClaim, ...] = ()
    guideline_snippets: tuple[EvidenceSnippet, ...] = Field(
        default=(), max_length=MAX_GUIDELINE_SNIPPETS
    )

    @model_validator(mode="after")
    def _source_lanes_are_separated(self) -> "GroundedAnswerContext":
        if any(
            claim.citation.source_type is not CitationSourceType.PATIENT_RECORD
            for claim in self.chart_claims
        ):
            raise ValueError("chart_claims accepts patient-record claims only")
        if any(
            claim.citation.source_type is not CitationSourceType.UPLOADED_DOCUMENT
            for claim in self.document_claims
        ):
            raise ValueError("document_claims accepts uploaded-document claims only")
        return self

    def with_chart_claims(
        self, claims: tuple[VerifiedClinicalClaim, ...]
    ) -> "GroundedAnswerContext":
        # ``model_copy(update=...)`` intentionally skips validation in Pydantic.  Rebuild
        # instead so a caller can never smuggle a document/guideline claim into the chart lane.
        return GroundedAnswerContext(
            chart_claims=claims,
            document_claims=self.document_claims,
            guideline_snippets=self.guideline_snippets,
        )
