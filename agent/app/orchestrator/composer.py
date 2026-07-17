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
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel

from app.orchestrator.loop import BriefResult
from app.schemas.answers import (
    GroundedAnswerContext,
    MAX_GUIDELINE_SNIPPETS,
    VerifiedClinicalClaim,
)
from app.schemas.citations import (
    CitationSourceType,
    CitationV2,
    EvidenceSnippet,
)
from app.schemas.extraction import ExtractionArtifact, GroundedField, NormBBox


RunBrief = Callable[[], Awaitable[BriefResult]]
RunBriefWithContext = Callable[[GroundedAnswerContext], Awaitable[BriefResult]]


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


_NO_EVIDENCE_TEXT = (
    "No verified chart or uploaded-document evidence is available for this question. "
    "Confirm that a patient is pinned and the expected source is available."
)
_NO_CLAIM_TEXT = (
    "No verified evidence matched this question. Ask about a condition or test, "
    "for example Magnesium."
)


def _answer_refusal(
    brief: BriefResult, *, reason_code: Literal["no_evidence", "no_claim"]
) -> BriefResult:
    """Replace an all-blocked render with a specific, citation-free safe refusal."""

    text = _NO_EVIDENCE_TEXT if reason_code == "no_evidence" else _NO_CLAIM_TEXT
    return replace(
        brief,
        text=text,
        source="deterministic_refusal",
        degraded=False,
        fallback_reason=None,
        fallback_kind=None,
        verdicts=[f"refused:{reason_code}"],
        citations=[],
        verified_claims=(),
        answer_reason_code=reason_code,
    )


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

    if not snippet.section.strip() or not snippet.quote.strip():
        raise ValueError("guideline snippets require a non-blank section and quote")
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


def verified_claims_from_artifact(
    artifact: ExtractionArtifact,
) -> tuple[VerifiedClinicalClaim, ...]:
    """Convert grounded artifact leaves to the one canonical internal claim shape."""

    claims: list[VerifiedClinicalClaim] = []
    for candidate in claims_from_artifact(artifact):
        if candidate.citation is None or candidate.page is None or candidate.bbox is None:
            continue
        claims.append(
            VerifiedClinicalClaim(
                text=candidate.text,
                citation=candidate.citation,
                page=candidate.page,
                bbox=candidate.bbox,
            )
        )
    return tuple(claims)


def _citation_key(citation: CitationV2) -> tuple[object, ...]:
    return (
        citation.source_type,
        citation.source_id,
        citation.page_or_section,
        citation.field_or_chunk_id,
        citation.quote_or_value,
    )


def build_grounded_answer_context(
    *,
    verified_facts: Sequence[object],
    evidence_snippets: Sequence[object],
    citations: Sequence[object],
) -> GroundedAnswerContext:
    """Build the bounded evidence block supplied to the answer model.

    Guideline order is the retriever/reranker order.  Anything outside the first five,
    or without the exact canonical citation in the citation lane, is excluded.
    """

    allowed = {
        _citation_key(citation)
        for citation in citations
        if isinstance(citation, CitationV2)
    }
    documents: list[VerifiedClinicalClaim] = []
    for fact in verified_facts:
        if isinstance(fact, ExtractionArtifact):
            documents.extend(
                claim
                for claim in verified_claims_from_artifact(fact)
                if _citation_key(claim.citation) in allowed
            )
        elif (
            isinstance(fact, VerifiedClinicalClaim)
            and fact.citation.source_type is CitationSourceType.UPLOADED_DOCUMENT
            and _citation_key(fact.citation) in allowed
        ):
            documents.append(fact)

    snippets: list[EvidenceSnippet] = []
    for item in evidence_snippets:
        if len(snippets) >= MAX_GUIDELINE_SNIPPETS:
            break
        if not isinstance(item, EvidenceSnippet):
            continue
        try:
            citation = citation_for_guideline(item)
        except ValueError:
            continue
        if _citation_key(citation) in allowed:
            snippets.append(item)

    return GroundedAnswerContext(
        document_claims=tuple(documents),
        guideline_snippets=tuple(snippets),
    )


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
        elif isinstance(fact, VerifiedClinicalClaim):
            if is_allowed(fact.citation):
                candidates.append(
                    CandidateClaim(
                        text=fact.text,
                        citation=fact.citation,
                        verified=True,
                        page=fact.page,
                        bbox=fact.bbox,
                    )
                )
        elif isinstance(fact, ExtractionArtifact):
            candidates.extend(
                claim
                for claim in claims_from_artifact(fact)
                if is_allowed(claim.citation)
            )

    for snippet in evidence_snippets:
        if not isinstance(snippet, EvidenceSnippet):
            continue
        try:
            citation = citation_for_guideline(snippet)
        except ValueError:
            continue
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
    run_brief_with_context: RunBriefWithContext | None = None,
) -> ComposerResult:
    """Run answer generation over bounded context, then gate canonical W2 claims."""

    context = build_grounded_answer_context(
        verified_facts=verified_facts,
        evidence_snippets=evidence_snippets,
        citations=citations,
    )
    brief = (
        await run_brief_with_context(context)
        if run_brief_with_context is not None
        else await run_brief()
    )
    candidates = claims_from_inputs(
        verified_facts=verified_facts,
        evidence_snippets=context.guideline_snippets,
        citations=citations,
    )
    if run_brief_with_context is not None:
        # In the production context-aware path, patient/document text renders only when the
        # typed answer selected an exact allowed claim. Guideline text uses an exact selected
        # chunk or the single anchored fallback below. Canonical persisted objects supply every
        # clinical/citation byte; model-authored values and source metadata are never used.
        selected_chart = {
            _citation_key(claim.citation)
            for claim in brief.verified_claims
            if claim.citation.source_type is CitationSourceType.PATIENT_RECORD
        }
        selected_documents = {
            _citation_key(claim.citation)
            for claim in brief.verified_claims
            if claim.citation.source_type is CitationSourceType.UPLOADED_DOCUMENT
        }
        selected_guidelines = {
            _citation_key(claim.citation)
            for claim in brief.verified_claims
            if claim.citation.source_type is CitationSourceType.GUIDELINE
        }
        # A relevant patient/document selection anchors this as an in-scope clinical
        # answer.  If the answer model omitted the separate guideline selector, retain
        # exactly the highest-ranked canonical snippet rather than silently losing the
        # retrieval lane. An attempted-but-unresolved selector stays empty and fail-closed.
        # ``context.guideline_snippets`` contains only bounded snippets
        # whose complete CitationV2 is already present in the allowed citation lane, so
        # this cannot promote model prose or an invented source.  An explicit model
        # selection remains authoritative and is never replaced by this fallback.
        if (
            not selected_guidelines
            and not brief.guideline_selector_attempted
            and (selected_chart or selected_documents)
            and brief.source != "deterministic_refusal"
            and brief.answer_reason_code != "all_blocked"
            and context.guideline_snippets
        ):
            selected_guidelines.add(
                _citation_key(citation_for_guideline(context.guideline_snippets[0]))
            )
        candidates = tuple(
            claim
            for claim in candidates
            if claim.citation is None
            or (
                claim.citation.source_type is CitationSourceType.PATIENT_RECORD
                and _citation_key(claim.citation) in selected_chart
            )
            or (
                claim.citation.source_type is CitationSourceType.UPLOADED_DOCUMENT
                and _citation_key(claim.citation) in selected_documents
            )
            or (
                claim.citation.source_type is CitationSourceType.GUIDELINE
                and _citation_key(claim.citation) in selected_guidelines
            )
        )
    composition = verify_then_render(candidates)
    if run_brief_with_context is not None and brief.answer_reason_code == "all_blocked":
        evidence_available = bool(
            brief.verified_claims
            or context.document_claims
            or context.guideline_snippets
        )
        brief = _answer_refusal(
            brief,
            reason_code="no_claim" if evidence_available else "no_evidence",
        )
        # ``_grounded_supersede`` is intentionally broad for the pre-visit fallback, but a
        # question-answer turn must not append unrelated chart/document candidates after every
        # submitted claim was blocked. The refusal is therefore the whole answer.
        composition = VerifiedComposition()
    return ComposerResult(brief=brief, composition=composition)


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
