"""Verified-answer contracts shared by the graph, composer, LLM loop, and HTTP boundary.

``VerifiedClinicalClaim`` and ``GroundedAnswerContext`` are internal: the single
representation of a clinical claim that has already been resolved against an allowed
source.  ``GroundedAnswerContext`` is deliberately bounded: at most five guideline
snippets, retained in reranker order, may be shown to the answer model.

``ResponseClaim`` (with ``CitationOverlay``) is the PUBLIC per-claim citation contract
(AF-P0-03; W2-REQ-27/28/98 — PDF p.5 Core Req 5): every clinical claim served by
``POST /chat`` carries its own machine-readable CitationV2 set.  It serializes
identically in the JSON envelope, the initial SSE claim block, and the fallback UI.
"""

from __future__ import annotations

from typing import Annotated, Literal

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


class CitationOverlay(BaseModel):
    """Click-to-source overlay reference for one uploaded-document claim (W2-D6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    bbox: NormBBox


class ResponseClaim(BaseModel):
    """One externally served clinical claim owning its machine-readable citations.

    The public claims[] lane (PDF p.5 Core Req 5; W2-REQ-27/28/98): each served claim
    carries its text, its CLOSED source class (patient-record vs uploaded-document vs
    guideline — p.2's chart/guideline separation), its verdict, and exactly its
    CitationV2 set.  A claim with zero citations, a citation from a different source
    class, or an overlay pointing at an uncited document is unrepresentable —
    construction fails and the serving boundary fails closed rather than presenting
    an uncited or ambiguously cited claim as fact.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: ClaimText
    source_class: CitationSourceType
    verdict: Literal["pass", "flagged"]
    citations: list[CitationV2] = Field(min_length=1)
    overlay: CitationOverlay | None = None

    @model_validator(mode="after")
    def _citations_are_unambiguously_assigned(self) -> "ResponseClaim":
        if any(
            citation.source_type is not self.source_class
            for citation in self.citations
        ):
            raise ValueError(
                "claim citations must all belong to the claim's source class"
            )
        if self.overlay is not None:
            if self.source_class is not CitationSourceType.UPLOADED_DOCUMENT:
                raise ValueError("overlay refs are uploaded-document only")
            if all(
                citation.source_id != self.overlay.source_id
                for citation in self.citations
            ):
                raise ValueError("overlay must reference a cited source document")
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
