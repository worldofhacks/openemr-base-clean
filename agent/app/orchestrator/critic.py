"""Deterministic final critic for composed clinical output.

The critic is not another clinical judge.  It reuses the canonical verified claim and
citation contracts and applies only closed, deterministic structural/policy checks.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Iterable

from app.orchestrator.composer import VerifiedComposition
from app.orchestrator.loop import BriefResult
from app.schemas.citations import CitationSourceType, CitationV2
from app.verify.rules import contains_forbidden_phrase, contains_treatment_verb


class CriticReason(str, enum.Enum):
    APPROVED = "approved"
    INVALID_CLAIM = "invalid_claim"
    UNCITED_CLAIM = "uncited_claim"
    UNRESOLVED_CITATION = "unresolved_citation"
    MIXED_SOURCE = "mixed_source"
    TREATMENT_CLAIM = "treatment_claim"
    DIAGNOSIS_CLAIM = "diagnosis_claim"
    ORDERING_CLAIM = "ordering_claim"
    CRITIC_EXCEPTION = "critic_exception"


@dataclass(frozen=True)
class CriticResult:
    approved: bool
    reason: CriticReason


_DIAGNOSIS = re.compile(r"\bdiagnos(?:e|ed|es|ing|is|tic)\b", re.IGNORECASE)
_ORDERING = re.compile(r"\b(?:order|ordered|ordering|prescribe|prescribed|prescribing)\b", re.IGNORECASE)


def _key(citation: CitationV2) -> tuple[object, ...]:
    return (
        citation.source_type,
        citation.source_id,
        citation.page_or_section,
        citation.field_or_chunk_id,
        citation.quote_or_value,
    )


def _policy_reason(text: str) -> CriticReason | None:
    if contains_treatment_verb(text):
        return CriticReason.TREATMENT_CLAIM
    if _DIAGNOSIS.search(text):
        return CriticReason.DIAGNOSIS_CLAIM
    if _ORDERING.search(text):
        return CriticReason.ORDERING_CLAIM
    if contains_forbidden_phrase(text):
        return CriticReason.INVALID_CLAIM
    return None


def review_composition(
    *,
    brief: BriefResult,
    composition: VerifiedComposition,
    allowed_citations: Iterable[CitationV2],
) -> CriticResult:
    """Approve only a wholly resolvable, source-consistent pending answer."""

    if not brief.text.strip():
        return CriticResult(False, CriticReason.INVALID_CLAIM)
    if brief.source == "deterministic_refusal":
        # A refusal is the one valid uncited output.  It must remain the whole output;
        # appending a clinical composition to a hard-stop refusal is never allowed.
        if composition.claims or brief.citations or brief.verified_claims:
            return CriticResult(False, CriticReason.INVALID_CLAIM)
        return CriticResult(True, CriticReason.APPROVED)
    policy = _policy_reason(brief.text)
    if policy is not None:
        return CriticResult(False, policy)

    if any(not isinstance(citation, CitationV2) for citation in brief.citations):
        return CriticResult(False, CriticReason.INVALID_CLAIM)
    if not brief.verified_claims and not composition.claims:
        return CriticResult(False, CriticReason.UNCITED_CLAIM)

    allowed = {
        _key(citation)
        for citation in allowed_citations
        if isinstance(citation, CitationV2)
    }
    internal = {_key(claim.citation) for claim in brief.verified_claims}
    rendered = {_key(claim.citation) for claim in composition.claims}
    response_citations = {_key(citation) for citation in brief.citations}

    # A response citation is not an authority by itself: it must resolve to the internal
    # claim created by the verifier.  This prevents a complete-looking invented CitationV2
    # from becoming self-authenticating merely by appearing in ``brief.citations``.
    if any(_key(citation) not in internal for citation in brief.citations):
        return CriticResult(False, CriticReason.UNRESOLVED_CITATION)

    for verified_claim in brief.verified_claims:
        citation = verified_claim.citation
        key = _key(citation)
        if key not in rendered and key not in response_citations:
            return CriticResult(False, CriticReason.UNRESOLVED_CITATION)
        if citation.source_type is CitationSourceType.GUIDELINE:
            # Guideline recommendations are source-separated public evidence, not
            # model-authored patient instructions.  Permit recommendation language only
            # when the claim resolves to the worker's allowed citation lane and its text
            # is byte-identical to the canonical retrieved quote.  Altered/unknown text
            # therefore remains fail-closed, while the treatment screen below is
            # unchanged for chart/document claims and all model prose.
            if key not in allowed:
                return CriticResult(False, CriticReason.UNRESOLVED_CITATION)
            if verified_claim.text != citation.quote_or_value:
                return CriticResult(False, CriticReason.INVALID_CLAIM)
            if contains_forbidden_phrase(verified_claim.text):
                return CriticResult(False, CriticReason.INVALID_CLAIM)
        else:
            policy = _policy_reason(verified_claim.text)
            if policy is not None:
                return CriticResult(False, policy)

    for rendered_claim in composition.claims:
        citation = rendered_claim.citation
        if citation is None:
            return CriticResult(False, CriticReason.UNCITED_CLAIM)
        if rendered_claim.source_class is not citation.source_type:
            return CriticResult(False, CriticReason.MIXED_SOURCE)
        if _key(citation) not in allowed and _key(citation) not in internal:
            return CriticResult(False, CriticReason.UNRESOLVED_CITATION)
        if citation.source_type is CitationSourceType.PATIENT_RECORD:
            if citation.page_or_section is not None or rendered_claim.overlay is not None:
                return CriticResult(False, CriticReason.MIXED_SOURCE)
        elif citation.source_type is CitationSourceType.UPLOADED_DOCUMENT:
            overlay = rendered_claim.overlay
            if (
                overlay is None
                or citation.page_or_section != str(overlay.page)
                or citation.source_id != overlay.source_id
            ):
                return CriticResult(False, CriticReason.INVALID_CLAIM)
        else:
            if not citation.page_or_section or rendered_claim.overlay is not None:
                return CriticResult(False, CriticReason.MIXED_SOURCE)
            if _key(citation) not in allowed:
                return CriticResult(False, CriticReason.UNRESOLVED_CITATION)
            if rendered_claim.text != citation.quote_or_value:
                return CriticResult(False, CriticReason.INVALID_CLAIM)
            if contains_forbidden_phrase(rendered_claim.text):
                return CriticResult(False, CriticReason.INVALID_CLAIM)
            continue
        policy = _policy_reason(rendered_claim.text)
        if policy is not None:
            return CriticResult(False, policy)

    return CriticResult(True, CriticReason.APPROVED)
