"""Verify-then-render composer for the Week 2 graph.

The W1 loop remains the verifier for chart claims. Week 2 document and guideline
claims enter this module only as canonical grounded/citation models. Rendering is
the final gate: an unverified or incomplete claim is omitted, uploaded-document
claims additionally require page/bbox overlay metadata, and source classes remain
explicitly separated.

Traceability: W2-D2/W2-D3/W2-D6; W2_ARCHITECTURE.md §2/§2a/§3/§5.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from app.orchestrator.loop import BriefResult
from app.schemas.citations import (
    CitationSourceType,
    CitationV2,
    EvidenceSnippet,
)
from app.schemas.extraction import ExtractionArtifact, GroundedField, NormBBox


RunBrief = Callable[[], Awaitable[BriefResult]]


@dataclass(frozen=True)
class CandidateClaim:
    """Internal claim candidate; never crosses the supervisor/worker boundary."""

    text: str
    citation: CitationV2 | None
    verified: bool
    page: int | None = None
    bbox: NormBBox | None = None


@dataclass(frozen=True)
class BBoxOverlay:
    """Overlay metadata retained only for a grounded uploaded-document claim."""

    source_id: str
    page: int
    bbox: NormBBox


@dataclass(frozen=True)
class RenderedClaim:
    """A claim that passed the final structural render gate."""

    text: str
    citation: CitationV2
    source_class: CitationSourceType
    overlay: BBoxOverlay | None = None


@dataclass(frozen=True)
class VerifiedComposition:
    """Rendered W2 claims, kept separate from the unchanged W1 ``BriefResult``."""

    claims: tuple[RenderedClaim, ...] = ()

    def for_source(self, source: CitationSourceType) -> tuple[RenderedClaim, ...]:
        return tuple(claim for claim in self.claims if claim.source_class is source)


@dataclass(frozen=True)
class ComposerResult:
    """The unchanged W1 answer plus separately verified Week 2 claim blocks."""

    brief: BriefResult
    composition: VerifiedComposition


def _has_required_location(citation: CitationV2) -> bool:
    if citation.source_type is CitationSourceType.PATIENT_RECORD:
        return True
    location = citation.page_or_section
    return isinstance(location, str) and bool(location.strip())


def verify_then_render(claims: Iterable[CandidateClaim]) -> VerifiedComposition:
    """Drop every claim that cannot satisfy the frozen W2 render contract.

    ``verified`` is evidence agreement established before this final gate: W1's
    verifier for chart facts, ``GroundedField.grounded`` for document facts, or an
    exact retrieved guideline snippet. This function never promotes model prose.
    """

    rendered: list[RenderedClaim] = []
    for claim in claims:
        citation = claim.citation
        if not claim.verified or citation is None or not claim.text.strip():
            continue
        if not _has_required_location(citation):
            continue

        overlay: BBoxOverlay | None = None
        if citation.source_type is CitationSourceType.UPLOADED_DOCUMENT:
            if claim.page is None or claim.page < 0 or claim.bbox is None:
                continue
            if citation.page_or_section != str(claim.page):
                continue
            overlay = BBoxOverlay(
                source_id=citation.source_id,
                page=claim.page,
                bbox=claim.bbox,
            )

        rendered.append(
            RenderedClaim(
                text=claim.text.strip(),
                citation=citation,
                source_class=citation.source_type,
                overlay=overlay,
            )
        )
    return VerifiedComposition(tuple(rendered))


def citation_for_guideline(snippet: EvidenceSnippet) -> CitationV2:
    """Map one canonical retrieval hit to the exact CitationV2 source class."""

    return CitationV2(
        source_type=CitationSourceType.GUIDELINE,
        source_id=snippet.source_id,
        page_or_section=snippet.section,
        field_or_chunk_id=snippet.chunk_id,
        quote_or_value=snippet.quote,
    )


def citation_for_chart_fact(
    *,
    resource_type: str,
    resource_id: str,
    evidence_id: str,
    verified_value: str,
) -> CitationV2:
    """Apply the frozen W1 evidence-id -> CitationV2 migration mapping (§2a)."""

    if not all(value.strip() for value in (
        resource_type, resource_id, evidence_id, verified_value
    )):
        raise ValueError("chart citation mapping requires a resolvable verified fact")
    return CitationV2(
        source_type=CitationSourceType.PATIENT_RECORD,
        source_id=f"{resource_type}/{resource_id}",
        page_or_section=None,
        field_or_chunk_id=evidence_id,
        quote_or_value=verified_value,
    )


def _walk_grounded_fields(
    value: object, path: str = ""
) -> Iterable[tuple[str, GroundedField[object]]]:
    if isinstance(value, GroundedField):
        yield path, value
        return
    if isinstance(value, BaseModel):
        for name in value.__class__.model_fields:
            child_path = f"{path}.{name}" if path else name
            yield from _walk_grounded_fields(getattr(value, name), child_path)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk_grounded_fields(item, f"{path}.{index}")


def claims_from_artifact(artifact: ExtractionArtifact) -> tuple[CandidateClaim, ...]:
    """Turn only grounded extraction leaves into document claim candidates."""

    candidates: list[CandidateClaim] = []
    for path, field in _walk_grounded_fields(artifact.extraction):
        if not field.grounded or field.value is None:
            continue
        candidates.append(
            CandidateClaim(
                text=f"{path}: {field.value}",
                citation=field.citation,
                verified=True,
                page=field.page,
                bbox=field.bbox,
            )
        )
    return tuple(candidates)


def claims_from_inputs(
    *,
    verified_facts: Sequence[object],
    evidence_snippets: Sequence[object],
    citations: Sequence[object],
) -> tuple[CandidateClaim, ...]:
    """Build candidates only when their complete citation is in the citation lane."""

    allowed = {
        (
            citation.source_type,
            citation.source_id,
            citation.page_or_section,
            citation.field_or_chunk_id,
            citation.quote_or_value,
        )
        for citation in citations
        if isinstance(citation, CitationV2)
    }

    def is_allowed(citation: CitationV2 | None) -> bool:
        if citation is None:
            return False
        return (
            citation.source_type,
            citation.source_id,
            citation.page_or_section,
            citation.field_or_chunk_id,
            citation.quote_or_value,
        ) in allowed

    candidates: list[CandidateClaim] = []
    for fact in verified_facts:
        if isinstance(fact, CandidateClaim):
            if is_allowed(fact.citation):
                candidates.append(fact)
        elif isinstance(fact, ExtractionArtifact):
            candidates.extend(
                claim
                for claim in claims_from_artifact(fact)
                if is_allowed(claim.citation)
            )

    for snippet in evidence_snippets:
        if not isinstance(snippet, EvidenceSnippet):
            continue
        citation = citation_for_guideline(snippet)
        if is_allowed(citation):
            candidates.append(
                CandidateClaim(
                    text=snippet.quote,
                    citation=citation,
                    verified=True,
                )
            )
    return tuple(candidates)


async def compose_answer(
    *,
    verified_facts: Sequence[object],
    evidence_snippets: Sequence[object],
    citations: Sequence[object],
    run_brief: RunBrief,
) -> ComposerResult:
    """Run W1 verification, then independently gate W2 document/guideline claims."""

    brief = await run_brief()
    candidates = claims_from_inputs(
        verified_facts=verified_facts,
        evidence_snippets=evidence_snippets,
        citations=citations,
    )
    return ComposerResult(brief=brief, composition=verify_then_render(candidates))


async def compose_answer_shell(
    *,
    verified_facts: Sequence[object],
    evidence_snippets: Sequence[object],
    citations: Sequence[object],
    run_brief: RunBrief,
) -> BriefResult:
    """Compatibility entrypoint preserving the frozen M3 W1-equivalence contract."""

    result = await compose_answer(
        verified_facts=verified_facts,
        evidence_snippets=evidence_snippets,
        citations=citations,
        run_brief=run_brief,
    )
    return result.brief
