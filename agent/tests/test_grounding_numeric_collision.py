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
    ("6.5", "65"),  # spec(W2-D3): decimal point — 10x error
    ("65", "6.5"),  # reverse direction
    ("98.6", "986"),  # temperature 10x
    ("-5", "5"),  # lost minus sign
    ("0.5", "05"),  # leading-zero / decimal
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


@pytest.mark.parametrize(
    ("value", "page_tokens", "field_id"),
    [
        ("6.5 %", ("Result", "65", "%"), "labs.value"),
        ("6.5 %", ("Result", "6.5"), "labs.value"),
        ("%", ("Result", "98"), "labs.unit"),
        ("mg/dL", ("Unit", "mg/L"), "labs.unit"),
        ("mg/dL", ("Unit", "mgdL"), "labs.unit"),
        ("10^9/L", ("Unit", "109L"), "labs.unit"),
        ("\u00b0C", ("Unit", "C"), "labs.unit"),
        ("\u00b5g", ("Unit", "g"), "labs.unit"),
        ("6.5 mg/dL", ("Result", "6.5", "mg/L"), "labs.value"),
        ("65", ("Result", "65%"), "labs.value"),
        ("6.5", ("Result", "6.5mg/dL"), "labs.value"),
    ],
)
def test_percent_and_complete_units_remain_significant(
    value: str, page_tokens: tuple[str, ...], field_id: str
) -> None:
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page(*page_tokens),
        source_document_id="doc-unit-significance",
        field_id=field_id,
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


@pytest.mark.parametrize(
    ("value", "page_tokens"),
    [
        ("16", ("1", "6")),
        ("65", ("6", "5")),
        ("+5", ("5",)),
        ("-0", ("0",)),
        ("<5", (">5",)),
        ("<=5", (">=5",)),
        ("12", ("1,2",)),
    ],
)
def test_atomic_formatting_never_assembles_or_changes_a_numeric_value(
    value: str, page_tokens: tuple[str, ...]
) -> None:
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page(*page_tokens),
        source_document_id="doc-atomic-boundary",
        field_id="labs.value",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


def test_unicode_comparison_alias_is_format_only_and_still_grounds() -> None:
    outcome = GroundingVerifier().ground_value(
        value="<=5",
        words_boxes=_page("\u22645"),
        source_document_id="doc-comparison-format",
        field_id="labs.value",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "\u22645"


def test_unicode_micro_unit_alias_is_format_only_and_still_grounds() -> None:
    outcome = GroundingVerifier().ground_value(
        value="\u00b5g",
        words_boxes=_page("\u03bcg"),
        source_document_id="doc-micro-format",
        field_id="labs.unit",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "\u03bcg"


def test_invalid_numeric_comma_never_disappears_in_relaxed_matching() -> None:
    outcome = GroundingVerifier().ground_value(
        value="1,2",
        words_boxes=_page("1", "2"),
        source_document_id="doc-invalid-comma",
        field_id="labs.value",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


def test_invalid_numeric_comma_with_its_real_word_box_still_grounds() -> None:
    outcome = GroundingVerifier().ground_value(
        value="1,2",
        words_boxes=_page("1", ",", "2"),
        source_document_id="doc-faithful-comma",
        field_id="labs.value",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "1 , 2"


def test_compact_numeric_range_matches_spaced_source_with_real_bbox() -> None:
    outcome = GroundingVerifier().ground_value(
        value="70-100",
        words_boxes=_page("70", "-", "100"),
        source_document_id="doc-faithful-range",
        field_id="labs.reference_range",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "70 - 100"
    assert outcome.field.bbox is not None


@pytest.mark.parametrize("page_tokens", [("70", "100"), ("100", "-", "70")])
def test_numeric_range_never_loses_or_reverses_its_separator(
    page_tokens: tuple[str, ...],
) -> None:
    outcome = GroundingVerifier().ground_value(
        value="70-100",
        words_boxes=_page(*page_tokens),
        source_document_id="doc-unfaithful-range",
        field_id="labs.reference_range",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


@pytest.mark.parametrize(
    ("value", "page_tokens"),
    [
        ("Blood type A+", ("Blood", "type", "A")),
        ("Blood type A+", ("Blood", "type", "A-")),
        ("Urine protein +", ("Urine", "protein")),
        ("Nitrite -", ("Nitrite",)),
        ("Urine trace \u00b1", ("Urine", "trace")),
        ("Urine trace \u00b1", ("Urine", "trace", "+")),
        ("Urine trace \u00b1", ("Urine", "trace", "-")),
    ],
)
def test_qualitative_sign_never_disappears_or_flips(
    value: str, page_tokens: tuple[str, ...]
) -> None:
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page(*page_tokens),
        source_document_id="doc-qualitative-sign",
        field_id="results[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


@pytest.mark.parametrize(
    ("value", "page_tokens", "expected_quote"),
    [
        ("Blood type A+", ("type", "A+", "Blood"), "type A+ Blood"),
        ("Urine protein +", ("protein", "+", "Urine"), "protein + Urine"),
        ("Nitrite -", ("Nitrite", "-"), "Nitrite -"),
        ("Urine trace \u00b1", ("trace\u00b1", "Urine"), "trace\u00b1 Urine"),
        ("Urine trace \u00b1", ("trace", "\u00b1", "Urine"), "trace \u00b1 Urine"),
    ],
)
def test_qualitative_sign_with_real_word_box_still_grounds(
    value: str, page_tokens: tuple[str, ...], expected_quote: str
) -> None:
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page(*page_tokens),
        source_document_id="doc-faithful-qualitative-sign",
        field_id="results[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == expected_quote


@pytest.mark.parametrize(
    ("value", "page_tokens"),
    [
        ("Titer 1:2", ("Titer", "12")),
        ("Time 12:30", ("Time", "1230")),
        ("Titer 1:2", ("Titer", "1", ",", "2")),
    ],
)
def test_numeric_colon_never_disappears_or_becomes_another_separator(
    value: str, page_tokens: tuple[str, ...]
) -> None:
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page(*page_tokens),
        source_document_id="doc-numeric-colon",
        field_id="results[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


def test_numeric_colon_with_spaced_real_word_box_still_grounds() -> None:
    outcome = GroundingVerifier().ground_value(
        value="Titer 1:2",
        words_boxes=_page("1", ":", "2", "Titer"),
        source_document_id="doc-faithful-colon",
        field_id="results[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "1 : 2 Titer"


@pytest.mark.parametrize("dash", ["\u2013", "\u2014"])
def test_unicode_numeric_range_never_collapses_to_one_number(dash: str) -> None:
    outcome = GroundingVerifier().ground_value(
        value=f"70{dash}100",
        words_boxes=_page("70100"),
        source_document_id="doc-unicode-range",
        field_id="labs.reference_range",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


@pytest.mark.parametrize("dash", ["\u2013", "\u2014"])
def test_unicode_numeric_range_canonicalizes_to_spaced_source(dash: str) -> None:
    outcome = GroundingVerifier().ground_value(
        value=f"70{dash}100",
        words_boxes=_page("70", "-", "100"),
        source_document_id="doc-faithful-unicode-range",
        field_id="labs.reference_range",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "70 - 100"


@pytest.mark.parametrize("page_tokens", [("Height", "56"), ("Height", "5", "6")])
def test_height_markers_never_disappear(
    page_tokens: tuple[str, ...],
) -> None:
    outcome = GroundingVerifier().ground_value(
        value="Height 5'6\"",
        words_boxes=_page(*page_tokens),
        source_document_id="doc-height-markers",
        field_id="results[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


def test_height_markers_with_real_word_boxes_still_ground() -> None:
    outcome = GroundingVerifier().ground_value(
        value="Height 5'6\"",
        words_boxes=_page("5", "'", "6", '"', "Height"),
        source_document_id="doc-faithful-height-markers",
        field_id="results[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "5 ' 6 \" Height"


def test_ascii_height_markers_accept_unicode_prime_word_boxes() -> None:
    outcome = GroundingVerifier().ground_value(
        value="Height 5'6\"",
        words_boxes=_page("5", "\u2032", "6", "\u2033", "Height"),
        source_document_id="doc-unicode-height-markers",
        field_id="results[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "5 \u2032 6 \u2033 Height"


def test_approximation_marker_never_disappears_or_moves_after_number() -> None:
    missing = GroundingVerifier().ground_value(
        value="Approx ~5",
        words_boxes=_page("Approx", "5"),
        source_document_id="doc-missing-approximation",
        field_id="results[0]",
    )
    reversed_marker = GroundingVerifier().ground_value(
        value="Approx ~5",
        words_boxes=_page("5", "~", "Approx"),
        source_document_id="doc-reversed-approximation",
        field_id="results[0]",
    )

    for outcome in (missing, reversed_marker):
        assert outcome.field.grounded is False
        assert outcome.field.citation is None
        assert outcome.field.bbox is None


def test_approximation_marker_with_real_word_box_still_grounds() -> None:
    outcome = GroundingVerifier().ground_value(
        value="Approx ~5",
        words_boxes=_page("~", "5", "Approx"),
        source_document_id="doc-faithful-approximation",
        field_id="results[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "~ 5 Approx"


@pytest.mark.parametrize(
    ("value", "page_tokens"),
    [
        ("Dose \u00bd tablet", ("Dose", "12", "tablet")),
        ("Dose 1\u00bd tablets", ("Dose", "11/2", "tablets")),
        ("Platelets 10\u2079/L", ("Platelets", "109/L")),
        ("Area cm\u00b2", ("Area", "cm2")),
    ],
)
def test_unicode_fraction_and_superscript_structure_never_collapses(
    value: str, page_tokens: tuple[str, ...]
) -> None:
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page(*page_tokens),
        source_document_id="doc-unicode-math-collision",
        field_id="results[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None


@pytest.mark.parametrize(
    ("value", "page_tokens", "expected_quote"),
    [
        ("Dose \u00bd tablet", ("Dose", "1/2", "tablet"), "Dose 1/2 tablet"),
        (
            "Dose 1\u00bd tablets",
            ("Dose", "1", "1/2", "tablets"),
            "Dose 1 1/2 tablets",
        ),
        (
            "Platelets 10\u2079/L",
            ("Platelets", "10^9/L"),
            "Platelets 10^9/L",
        ),
        ("Area cm\u00b2", ("Area", "cm^2"), "Area cm^2"),
    ],
)
def test_unicode_fraction_and_superscript_aliases_still_ground(
    value: str, page_tokens: tuple[str, ...], expected_quote: str
) -> None:
    outcome = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_page(*page_tokens),
        source_document_id="doc-unicode-math-faithful",
        field_id="results[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == expected_quote
