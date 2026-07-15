"""Regression: the grounding verifier must NOT collapse numerically distinct values.

Final-adversarial-review finding (CRITICAL, W2-D3/§5): ``_normalize`` stripped every
non-alphanumeric character, so a decimal point, minus sign, or thousands comma vanished
before comparison and numerically DISTINCT clinical values collapsed to one token
(``6.5``↔``65`` is a 10x error; ``-5``↔``5`` loses the sign). A VLM value could then
ground ``grounded=True`` with a citation+bbox against a page that literally reads a
different number, and the wrong value propagated to the chart write.

Invariant (the week's safety thesis): a value the page does NOT literally support can
never become a grounded fact — every non-match is UNSUPPORTED (``grounded=False``,
``citation=None``). These cases pin the numeric surface faithfully; the fix lives in
``app.grounding.verifier`` and both the value tokenizer and the page tokenizer must
normalize identically so faithful matches keep grounding.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.grounding.verifier import GroundingVerifier
from app.ingestion.reader import NormBBox, PageWords, Word, WordsBoxes


def _page(*texts: str) -> WordsBoxes:
    words = [
        Word(text=text, bbox=NormBBox(x0=i / 8, y0=0.1, x1=(i + 1) / 8, y1=0.2))
        for i, text in enumerate(texts)
    ]
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1000, 1000),
                words=words,
                unreadable=False,
            )
        ]
    )


# value NOT literally on the page — differs only by a stripped punctuation char.
# Each MUST come back UNSUPPORTED, or an unsupported number becomes a cited fact.
_COLLISIONS = [
    ("6.5", "65"),      # spec(W2-D3): decimal point — 10x error
    ("65", "6.5"),      # reverse direction
    ("98.6", "986"),    # temperature 10x
    ("-5", "5"),        # lost minus sign
    ("0.5", "05"),      # leading-zero / decimal
]


@pytest.mark.parametrize("value,on_page", _COLLISIONS)
def test_numeric_punctuation_collision_is_unsupported(value: str, on_page: str) -> None:
    # spec(W2-D3/§5): a value the page does not literally support never grounds.
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page("Result", on_page, "unit"),
        source_document_id="doc-1",
        field_id="labs.value",
    )
    field = outcome.field
    assert field.grounded is False, (
        f"value {value!r} FALSELY grounded against page {on_page!r} "
        f"(quote={field.citation.quote_or_value if field.citation else None!r})"
    )
    assert field.citation is None
    assert field.bbox is None


def test_thousands_comma_is_the_same_number_and_still_grounds() -> None:
    # spec(W2-D3): "1,000" and "1000" are the SAME number — a faithful match still grounds.
    outcome = GroundingVerifier().ground_value(
        value="1000",
        words_boxes=_page("Count", "1,000", "cells"),
        source_document_id="doc-1",
        field_id="labs.value",
    )
    assert outcome.field.grounded is True
    assert outcome.field.citation is not None


def test_decimal_vitals_value_collision_is_unsupported() -> None:
    # spec(W2-D3/W2-D10): the vitals path uses Decimal; 6.5 must not ground against "65".
    outcome = GroundingVerifier().ground_value(
        value=Decimal("6.5"),
        words_boxes=_page("Weight", "65", "kg"),
        source_document_id="doc-1",
        field_id="vitals.weight.value",
    )
    assert outcome.field.grounded is False
    assert outcome.field.citation is None


def test_faithful_numeric_value_still_grounds() -> None:
    # spec(W2-D3): the fix must not over-reject — an on-page value still grounds.
    outcome = GroundingVerifier().ground_value(
        value="6.5",
        words_boxes=_page("HbA1c", "6.5", "%"),
        source_document_id="doc-1",
        field_id="labs.value",
    )
    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "6.5"
