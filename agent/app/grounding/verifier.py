"""Deterministic words+boxes grounding for VLM-proposed fields.

The VLM proposes values; this module alone assigns the final ``grounded`` bit and
source citation.  Any bbox, citation, or grounding claim supplied by the model is
discarded before local matching (W2-D3).  A contiguous normalized phrase match on a
readable page is required for a fact; every other outcome is UNSUPPORTED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, TypeVar, cast

from app.ingestion.reader import NormBBox, PageWords, WordsBoxes
from app.schemas.citations import CitationV2
from app.schemas.extraction import GroundedField

T = TypeVar("T")
# Preserve numerically-significant characters: a decimal point and a leading/interior
# minus sign carry value. Stripping them (final-review CRITICAL, W2-D3/§5) let numerically
# DISTINCT clinical values collapse to one token — '6.5' grounded against a page reading
# '65' (a 10x error) and reached the chart write. Only true separators are removed.
_NON_ALNUM = re.compile(r"[^0-9a-z.\-]+")
# A comma between two digits is a thousands separator: '1,000' and '1000' are the SAME
# number and must still match. Every other comma is a separator and is dropped by _NON_ALNUM.
_THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d)")


@dataclass(frozen=True)
class GroundingOutcome(Generic[T]):
    """Final field plus a PHI-free deterministic outcome reason."""

    field: GroundedField[T]
    reason: str


@dataclass(frozen=True)
class GroundingSummary:
    """The persisted binary grounding tally (§2)."""

    fields_grounded: int
    fields_unsupported: int

    @classmethod
    def from_outcomes(
        cls, outcomes: list[GroundingOutcome[object]]
    ) -> "GroundingSummary":
        grounded = sum(1 for outcome in outcomes if outcome.field.grounded)
        return cls(grounded, len(outcomes) - grounded)


def _text(value: object) -> str:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _normalize(token: str) -> str:
    folded = _THOUSANDS_COMMA.sub("", token.casefold())
    return _NON_ALNUM.sub("", folded)


def _phrase_tokens(value: object) -> tuple[str, ...]:
    return tuple(
        normalized
        for token in _text(value).split()
        if (normalized := _normalize(token))
    )


def _phrase_token_variants(value: object) -> tuple[tuple[str, ...], ...]:
    """Canonicalize UTC datetimes to ``Z`` while accepting the ISO ``+00:00`` alias."""

    primary = _phrase_tokens(value)
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        return (primary,)
    iso = value.isoformat()
    if not iso.endswith("+00:00"):
        return (primary,)
    canonical = tuple(
        normalized
        for token in f"{iso[:-6]}Z".split()
        if (normalized := _normalize(token))
    )
    return (canonical, primary) if canonical != primary else (primary,)


def _union_bbox(words: list[object]) -> NormBBox:
    boxes = [word.bbox for word in words]  # type: ignore[attr-defined]
    return NormBBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def _match(page: PageWords, wanted: tuple[str, ...]) -> tuple[NormBBox, str] | None:
    if page.unreadable or not wanted:
        return None
    normalized = [_normalize(word.text) for word in page.words]
    width = len(wanted)
    for start in range(0, len(normalized) - width + 1):
        if tuple(normalized[start : start + width]) != wanted:
            continue
        matched = page.words[start : start + width]
        return _union_bbox(matched), " ".join(word.text for word in matched)
    return None


class GroundingVerifier:
    """Construct final canonical fields from local agreement only.

    Page numbers exposed in fields/citations are one-based. ``PageWords.page_index``
    remains the reader's zero-based internal index.
    """

    def ground_value(
        self,
        *,
        value: T | None,
        words_boxes: WordsBoxes,
        source_document_id: str,
        field_id: str,
        page: int | None = None,
    ) -> GroundingOutcome[T]:
        pages = [
            item
            for item in words_boxes.pages
            if page is None or item.page_index == page - 1
        ]
        if value is None:
            return self._unsupported(value, page, "missing_value")
        if pages and all(item.unreadable for item in pages):
            return self._unsupported(value, page, "page_unreadable")

        for source_page in pages:
            for wanted in _phrase_token_variants(value):
                match = _match(source_page, wanted)
                if match is None:
                    continue
                bbox, quote = match
                page_number = source_page.page_index + 1
                citation = CitationV2(
                    source_type="uploaded_document",
                    source_id=source_document_id,
                    page_or_section=str(page_number),
                    field_or_chunk_id=field_id,
                    quote_or_value=quote,
                )
                return GroundingOutcome(
                    field=cast(
                        GroundedField[T],
                        GroundedField(
                            value=value,
                            page=page_number,
                            bbox=bbox,
                            grounded=True,
                            citation=citation,
                        ),
                    ),
                    reason="matched",
                )
        return self._unsupported(value, page, "not_found")

    def reground_candidate(
        self,
        candidate: GroundedField[T],
        *,
        words_boxes: WordsBoxes,
        source_document_id: str,
        field_id: str,
    ) -> GroundingOutcome[T]:
        """Discard every VLM grounding assertion, then run the local verifier."""

        return self.ground_value(
            value=candidate.value,
            page=candidate.page,
            words_boxes=words_boxes,
            source_document_id=source_document_id,
            field_id=field_id,
        )

    @staticmethod
    def _unsupported(
        value: T | None, page: int | None, reason: str
    ) -> GroundingOutcome[T]:
        return GroundingOutcome(
            field=cast(
                GroundedField[T],
                GroundedField(
                    value=value,
                    page=page,
                    bbox=None,
                    grounded=False,
                    citation=None,
                ),
            ),
            reason=reason,
        )
