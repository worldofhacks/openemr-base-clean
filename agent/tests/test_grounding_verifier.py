"""Frozen B2 grounding behavior (W2-D3; W2_ARCHITECTURE §2/§5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.ingestion.reader import NormBBox, PageWords, Word, WordsBoxes
from app.schemas.citations import CitationV2
from app.schemas.extraction import GroundedField


def _words(*tokens: str, unreadable: bool = False) -> WordsBoxes:
    words = [
        Word(
            text=token,
            bbox=NormBBox(
                x0=0.05 + index * 0.12,
                y0=0.10,
                x1=0.14 + index * 0.12,
                y1=0.14,
            ),
        )
        for index, token in enumerate(tokens)
    ]
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1700, 2200),
                words=words,
                unreadable=unreadable,
            )
        ]
    )


def _pages(
    *page_tokens: tuple[str, ...], unreadable_pages: frozenset[int] = frozenset()
) -> WordsBoxes:
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=page_index,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1700, 2200),
                words=[
                    Word(
                        text=token,
                        bbox=NormBBox(
                            x0=0.05 + index * 0.12,
                            y0=0.10,
                            x1=0.14 + index * 0.12,
                            y1=0.14,
                        ),
                    )
                    for index, token in enumerate(tokens)
                ],
                unreadable=page_index in unreadable_pages,
            )
            for page_index, tokens in enumerate(page_tokens)
        ]
    )


def _layout_words(*placements: tuple[str, float, float, float, float]) -> WordsBoxes:
    """One readable page with explicitly controlled row/cell geometry."""

    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1700, 2200),
                words=[
                    Word(
                        text=text,
                        bbox=NormBBox(x0=x0, y0=y0, x1=x1, y1=y1),
                    )
                    for text, x0, y0, x1, y1 in placements
                ],
                unreadable=False,
            )
        ]
    )


def test_grounded_field_is_constructed_only_after_local_phrase_match():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="HbA1c 6.5",
        words_boxes=_words("HbA1c", "6.5"),
        source_document_id="doc-synthetic-1",
        field_id="lab.0.value",
    )

    assert outcome.field.grounded is True
    assert outcome.field.value == "HbA1c 6.5"
    assert outcome.field.page == 1
    assert outcome.field.bbox is not None
    assert outcome.field.bbox.x0 == 0.05
    assert outcome.field.bbox.x1 == 0.26
    assert outcome.field.citation == CitationV2(
        source_type="uploaded_document",
        source_id="doc-synthetic-1",
        page_or_section="1",
        field_or_chunk_id="lab.0.value",
        quote_or_value="HbA1c 6.5",
    )
    assert outcome.reason == "matched"


def test_percent_unit_is_a_groundable_clinical_token():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="%",
        words_boxes=_words("HbA1c", "6.5", "%"),
        source_document_id="fixture:percent-unit",
        field_id="results[0].unit",
        page=1,
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "%"


def test_missing_value_is_unsupported_and_has_no_citation():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="invented",
        words_boxes=_words("HbA1c", "6.5"),
        source_document_id="doc-synthetic-1",
        field_id="lab.0.value",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.reason == "not_found"


def test_unreadable_page_cannot_ground_a_value():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="HbA1c",
        words_boxes=_words("HbA1c", unreadable=True),
        source_document_id="doc-synthetic-1",
        field_id="lab.0.test_name",
        page=1,
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.reason == "page_unreadable"


def test_vlm_claimed_grounding_and_fake_citation_are_discarded():
    from app.grounding.verifier import GroundingVerifier

    untrusted = GroundedField[str](
        value="invented",
        page=1,
        bbox=NormBBox(x0=0.1, y0=0.1, x1=0.2, y1=0.2),
        grounded=True,
        citation=CitationV2(
            source_type="uploaded_document",
            source_id="forged",
            page_or_section="1",
            field_or_chunk_id="forged",
            quote_or_value="invented",
        ),
    )

    outcome = GroundingVerifier().reground_candidate(
        untrusted,
        words_boxes=_words("actual", "page", "text"),
        source_document_id="doc-synthetic-1",
        field_id="chief_concern",
    )

    assert outcome.field.grounded is False
    assert outcome.field.citation is None
    assert outcome.field.bbox is None
    assert outcome.reason == "not_found"


def test_reground_candidate_ignores_poisoned_unreadable_page_claim():
    from app.grounding.verifier import GroundingVerifier

    untrusted = GroundedField[str](
        value="trusted phrase",
        page=2,
        bbox=None,
        grounded=False,
        citation=None,
    )

    outcome = GroundingVerifier().reground_candidate(
        untrusted,
        words_boxes=_pages(
            ("trusted", "phrase"),
            ("unreadable",),
            unreadable_pages=frozenset({1}),
        ),
        source_document_id="doc-synthetic-1",
        field_id="chief_concern",
    )

    assert outcome.field.grounded is True
    assert outcome.field.page == 1
    assert outcome.field.citation is not None
    assert outcome.field.citation.page_or_section == "1"
    assert outcome.field.citation.quote_or_value == "trusted phrase"
    assert outcome.reason == "matched"


def test_reground_candidate_ignores_wrong_page_claim_and_searches_all_pages():
    from app.grounding.verifier import GroundingVerifier

    untrusted = GroundedField[str](
        value="trusted phrase",
        page=1,
        bbox=None,
        grounded=False,
        citation=None,
    )

    outcome = GroundingVerifier().reground_candidate(
        untrusted,
        words_boxes=_pages(("other", "text"), ("trusted", "phrase")),
        source_document_id="doc-synthetic-1",
        field_id="chief_concern",
    )

    assert outcome.field.grounded is True
    assert outcome.field.page == 2
    assert outcome.field.citation is not None
    assert outcome.field.citation.page_or_section == "2"
    assert outcome.field.citation.quote_or_value == "trusted phrase"
    assert outcome.reason == "matched"


def test_reground_candidate_ignores_zero_page_claim():
    from app.grounding.verifier import GroundingVerifier

    untrusted = GroundedField[str](
        value="trusted phrase",
        page=0,
        bbox=None,
        grounded=False,
        citation=None,
    )

    outcome = GroundingVerifier().reground_candidate(
        untrusted,
        words_boxes=_pages(
            ("trusted", "phrase"),
        ),
        source_document_id="doc-synthetic-1",
        field_id="chief_concern",
    )

    assert outcome.field.grounded is True
    assert outcome.field.page == 1
    assert outcome.field.citation is not None
    assert outcome.field.citation.page_or_section == "1"
    assert outcome.field.citation.quote_or_value == "trusted phrase"
    assert outcome.reason == "matched"


def test_datetime_grounding_accepts_utc_z_without_changing_non_utc_offsets():
    from app.grounding.verifier import GroundingVerifier

    utc = GroundingVerifier().ground_value(
        value=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
        words_boxes=_words("2026-07-14T12:00:00Z"),
        source_document_id="doc-synthetic-1",
        field_id="vitals.bps.measurement_date",
    )
    eastern = GroundingVerifier().ground_value(
        value=datetime(2026, 7, 14, 8, 0, tzinfo=timezone(timedelta(hours=-4))),
        words_boxes=_words("2026-07-14T08:00:00-04:00"),
        source_document_id="doc-synthetic-1",
        field_id="vitals.bpd.measurement_date",
    )

    assert utc.field.grounded is True
    assert utc.field.citation is not None
    assert utc.field.citation.quote_or_value == "2026-07-14T12:00:00Z"
    assert eastern.field.grounded is True


def test_exact_phrase_wins_globally_before_order_relaxed_match():
    from app.grounding.verifier import GroundingVerifier

    words_boxes = WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1700, 2200),
                words=[
                    Word(
                        text="daily",
                        bbox=NormBBox(x0=0.10, y0=0.10, x1=0.15, y1=0.12),
                    ),
                    Word(
                        text="Metformin",
                        bbox=NormBBox(x0=0.16, y0=0.10, x1=0.26, y1=0.12),
                    ),
                ],
            ),
            PageWords(
                page_index=1,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1700, 2200),
                words=[
                    Word(
                        text="Metformin",
                        bbox=NormBBox(x0=0.10, y0=0.10, x1=0.20, y1=0.12),
                    ),
                    Word(
                        text="daily",
                        bbox=NormBBox(x0=0.21, y0=0.10, x1=0.26, y1=0.12),
                    ),
                ],
            ),
        ]
    )

    outcome = GroundingVerifier().ground_value(
        value="Metformin daily",
        words_boxes=words_boxes,
        source_document_id="fixture:tier-priority",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.page == 2
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "Metformin daily"


def test_date_format_canonicalization_returns_source_quote_and_bbox():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value=date(1983, 12, 19),
        words_boxes=_layout_words(("12/19/1983", 0.40, 0.12, 0.52, 0.14)),
        source_document_id="fixture:date-format",
        field_id="demographics.dob",
    )

    assert outcome.field.grounded is True
    assert outcome.field.value == date(1983, 12, 19)
    assert outcome.field.bbox == NormBBox(x0=0.40, y0=0.12, x1=0.52, y1=0.14)
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "12/19/1983"


def test_number_unit_spacing_canonicalization_unions_real_word_boxes():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="1.6 mg/dL",
        words_boxes=_layout_words(
            ("1.6", 0.20, 0.20, 0.24, 0.22),
            ("mg", 0.245, 0.20, 0.275, 0.22),
            ("/", 0.278, 0.20, 0.283, 0.22),
            ("dL", 0.286, 0.20, 0.31, 0.22),
        ),
        source_document_id="fixture:atomic-unit-spacing",
        field_id="results[0].value",
    )

    assert outcome.field.grounded is True
    assert outcome.field.bbox == NormBBox(x0=0.20, y0=0.20, x1=0.31, y1=0.22)
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "1.6 mg / dL"


def test_order_relaxed_table_cell_uses_all_significant_tokens_and_union_bbox():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="Metformin 500 mg twice daily",
        words_boxes=_layout_words(
            ("twice", 0.10, 0.30, 0.15, 0.32),
            ("daily", 0.155, 0.30, 0.20, 0.32),
            ("Medication:Metformin", 0.205, 0.30, 0.405, 0.32),
            ("500", 0.41, 0.30, 0.445, 0.32),
            ("mg", 0.45, 0.30, 0.475, 0.32),
        ),
        source_document_id="fixture:table-order",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.bbox == NormBBox(x0=0.10, y0=0.30, x1=0.475, y1=0.32)
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == (
        "twice daily Medication:Metformin 500 mg"
    )


def test_free_text_subset_is_limited_to_a_small_aligned_region():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="Recurring morning headaches for two weeks",
        words_boxes=_layout_words(
            ("Patient", 0.10, 0.40, 0.16, 0.42),
            ("reports", 0.165, 0.40, 0.225, 0.42),
            ('"Recurring', 0.23, 0.40, 0.32, 0.42),
            ("persistent", 0.325, 0.40, 0.415, 0.42),
            ("morning", 0.42, 0.40, 0.49, 0.42),
            ("headaches", 0.23, 0.425, 0.31, 0.445),
            ("for", 0.315, 0.425, 0.34, 0.445),
            ("two", 0.345, 0.425, 0.375, 0.445),
            ('weeks"', 0.38, 0.425, 0.435, 0.445),
        ),
        source_document_id="fixture:free-text-subset",
        field_id="chief_concern",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert "persistent" in outcome.field.citation.quote_or_value
    assert outcome.field.bbox is not None
    assert outcome.field.bbox.y1 - outcome.field.bbox.y0 < 0.085


def test_loose_match_never_stitches_distant_rows():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="Metformin 500 mg daily",
        words_boxes=_layout_words(
            ("Metformin", 0.10, 0.10, 0.20, 0.12),
            ("500", 0.10, 0.30, 0.14, 0.32),
            ("mg", 0.145, 0.30, 0.17, 0.32),
            ("daily", 0.175, 0.30, 0.225, 0.32),
        ),
        source_document_id="fixture:cross-row",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_loose_match_never_stitches_separate_same_row_cells():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="Metformin 500 mg daily",
        words_boxes=_layout_words(
            ("Metformin", 0.05, 0.20, 0.15, 0.22),
            ("500", 0.70, 0.20, 0.74, 0.22),
            ("mg", 0.745, 0.20, 0.77, 0.22),
            ("daily", 0.775, 0.20, 0.825, 0.22),
        ),
        source_document_id="fixture:cross-cell",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_significant_token_multiplicity_and_absent_fabrication_remain_unsupported():
    from app.grounding.verifier import GroundingVerifier

    source = _layout_words(
        ("pain", 0.10, 0.20, 0.14, 0.22),
        ("Metformin", 0.15, 0.20, 0.25, 0.22),
        ("500", 0.255, 0.20, 0.29, 0.22),
        ("mg", 0.295, 0.20, 0.32, 0.22),
    )
    duplicate = GroundingVerifier().ground_value(
        value="pain pain",
        words_boxes=source,
        source_document_id="fixture:no-invention",
        field_id="allergies[0]",
    )
    fabricated = GroundingVerifier().ground_value(
        value="Warfarin 5 mg",
        words_boxes=source,
        source_document_id="fixture:no-invention",
        field_id="current_medications[1]",
    )

    for outcome in (duplicate, fabricated):
        assert outcome.field.grounded is False
        assert outcome.field.bbox is None
        assert outcome.field.citation is None


def test_atomic_date_never_falls_through_to_order_relaxed_token_matching():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value=date(1983, 5, 6),
        words_boxes=_layout_words(("1983-06-05", 0.10, 0.10, 0.24, 0.12)),
        source_document_id="fixture:date-value-swap",
        field_id="demographics.dob",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_atomic_unit_never_falls_through_to_inverted_unit_order():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="1.6 mg/dL",
        words_boxes=_layout_words(
            ("1.6", 0.10, 0.10, 0.14, 0.12),
            ("dL", 0.145, 0.10, 0.17, 0.12),
            ("/", 0.175, 0.10, 0.18, 0.12),
            ("mg", 0.185, 0.10, 0.21, 0.12),
        ),
        source_document_id="fixture:inverted-unit",
        field_id="results[0].value",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_order_relaxed_long_value_preserves_compound_unit_structure():
    from app.grounding.verifier import GroundingVerifier

    inverted = GroundingVerifier().ground_value(
        value="Magnesium 1.6 mg/dL",
        words_boxes=_layout_words(
            ("Magnesium", 0.10, 0.10, 0.19, 0.12),
            ("1.6", 0.195, 0.10, 0.23, 0.12),
            ("dL", 0.235, 0.10, 0.26, 0.12),
            ("/", 0.265, 0.10, 0.27, 0.12),
            ("mg", 0.275, 0.10, 0.30, 0.12),
        ),
        source_document_id="fixture:long-inverted-unit",
        field_id="results[0]",
    )
    faithful_reordered = GroundingVerifier().ground_value(
        value="Magnesium 1.6 mg/dL",
        words_boxes=_layout_words(
            ("1.6", 0.10, 0.10, 0.135, 0.12),
            ("mg", 0.14, 0.10, 0.165, 0.12),
            ("/", 0.17, 0.10, 0.175, 0.12),
            ("dL", 0.18, 0.10, 0.205, 0.12),
            ("Magnesium", 0.21, 0.10, 0.30, 0.12),
        ),
        source_document_id="fixture:long-faithful-unit",
        field_id="results[0]",
    )

    assert inverted.field.grounded is False
    assert inverted.field.citation is None
    assert faithful_reordered.field.grounded is True
    assert faithful_reordered.field.citation is not None
    assert faithful_reordered.field.citation.quote_or_value == "1.6 mg / dL Magnesium"


def test_order_relaxed_long_value_never_drops_unit_operators():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="Platelets 10^9/L",
        words_boxes=_layout_words(
            ("Platelets", 0.10, 0.10, 0.18, 0.12),
            ("10", 0.185, 0.10, 0.205, 0.12),
            ("9", 0.21, 0.10, 0.22, 0.12),
            ("L", 0.225, 0.10, 0.235, 0.12),
        ),
        source_document_id="fixture:missing-unit-operators",
        field_id="results[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_order_relaxed_long_value_preserves_comparator_number_binding():
    from app.grounding.verifier import GroundingVerifier

    inverted = GroundingVerifier().ground_value(
        value="Glucose <5 mg/dL",
        words_boxes=_layout_words(
            ("Glucose", 0.10, 0.10, 0.17, 0.12),
            ("5", 0.175, 0.10, 0.185, 0.12),
            ("<", 0.19, 0.10, 0.20, 0.12),
            ("mg/dL", 0.205, 0.10, 0.265, 0.12),
        ),
        source_document_id="fixture:inverted-comparator",
        field_id="results[0]",
    )
    faithful_reordered = GroundingVerifier().ground_value(
        value="Glucose <5 mg/dL",
        words_boxes=_layout_words(
            ("<", 0.10, 0.10, 0.11, 0.12),
            ("5", 0.115, 0.10, 0.125, 0.12),
            ("mg/dL", 0.13, 0.10, 0.19, 0.12),
            ("Glucose", 0.195, 0.10, 0.265, 0.12),
        ),
        source_document_id="fixture:faithful-comparator",
        field_id="results[0]",
    )

    assert inverted.field.grounded is False
    assert inverted.field.citation is None
    assert faithful_reordered.field.grounded is True
    assert faithful_reordered.field.citation is not None


def test_loose_match_never_stitches_tightly_adjacent_distinct_rows():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="Metformin 500 mg daily",
        words_boxes=_layout_words(
            ("Metformin", 0.10, 0.10, 0.20, 0.12),
            ("500", 0.10, 0.125, 0.14, 0.145),
            ("mg", 0.145, 0.125, 0.17, 0.145),
            ("daily", 0.175, 0.125, 0.225, 0.145),
        ),
        source_document_id="fixture:adjacent-cross-row",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_atomic_unit_does_not_join_unrelated_letter_word_boxes():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="mg",
        words_boxes=_layout_words(
            ("m", 0.10, 0.10, 0.11, 0.12),
            ("g", 0.115, 0.10, 0.125, 0.12),
        ),
        source_document_id="fixture:unit-letter-join",
        field_id="results[0].unit",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_capital_a_remains_significant_clinical_content():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="Vitamin A",
        words_boxes=_layout_words(("Vitamin", 0.10, 0.10, 0.17, 0.12)),
        source_document_id="fixture:capital-a",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_dose_first_composite_reaches_order_relaxed_table_matching():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="10 mg Lisinopril daily",
        words_boxes=_layout_words(
            ("Lisinopril", 0.10, 0.10, 0.20, 0.12),
            ("10", 0.205, 0.10, 0.225, 0.12),
            ("mg", 0.23, 0.10, 0.255, 0.12),
            ("daily", 0.26, 0.10, 0.31, 0.12),
        ),
        source_document_id="fixture:dose-first-composite",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is True
    assert outcome.field.bbox == NormBBox(x0=0.10, y0=0.10, x1=0.31, y1=0.12)
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "Lisinopril 10 mg daily"


def test_duration_first_free_text_reaches_order_relaxed_matching():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="2 weeks headaches",
        words_boxes=_layout_words(
            ("headaches", 0.10, 0.10, 0.19, 0.12),
            ("2", 0.195, 0.10, 0.205, 0.12),
            ("weeks", 0.21, 0.10, 0.26, 0.12),
        ),
        source_document_id="fixture:duration-first-free-text",
        field_id="chief_concern",
    )

    assert outcome.field.grounded is True
    assert outcome.field.citation is not None
    assert outcome.field.citation.quote_or_value == "headaches 2 weeks"


def test_dose_first_composite_still_rejects_inverted_compound_unit():
    from app.grounding.verifier import GroundingVerifier

    outcome = GroundingVerifier().ground_value(
        value="1.6 mg/dL Magnesium",
        words_boxes=_layout_words(
            ("Magnesium", 0.10, 0.10, 0.19, 0.12),
            ("1.6", 0.195, 0.10, 0.23, 0.12),
            ("dL", 0.235, 0.10, 0.26, 0.12),
            ("/", 0.265, 0.10, 0.27, 0.12),
            ("mg", 0.275, 0.10, 0.30, 0.12),
        ),
        source_document_id="fixture:dose-first-inverted-unit",
        field_id="results[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None


def test_composite_degree_unit_requires_its_real_symbol_and_order():
    from app.grounding.verifier import GroundingVerifier

    faithful = GroundingVerifier().ground_value(
        value="Temperature 37 \u00b0C",
        words_boxes=_layout_words(
            ("37", 0.10, 0.10, 0.12, 0.12),
            ("\u00b0", 0.125, 0.10, 0.135, 0.12),
            ("C", 0.14, 0.10, 0.15, 0.12),
            ("Temperature", 0.155, 0.10, 0.265, 0.12),
        ),
        source_document_id="fixture:faithful-degree-unit",
        field_id="results[0]",
    )
    missing = GroundingVerifier().ground_value(
        value="Temperature 37 \u00b0C",
        words_boxes=_layout_words(
            ("Temperature", 0.10, 0.10, 0.21, 0.12),
            ("37", 0.215, 0.10, 0.235, 0.12),
            ("C", 0.24, 0.10, 0.25, 0.12),
        ),
        source_document_id="fixture:missing-degree-unit",
        field_id="results[0]",
    )
    reversed_unit = GroundingVerifier().ground_value(
        value="Temperature 37 \u00b0C",
        words_boxes=_layout_words(
            ("Temperature", 0.10, 0.10, 0.21, 0.12),
            ("37", 0.215, 0.10, 0.235, 0.12),
            ("C", 0.24, 0.10, 0.25, 0.12),
            ("\u00b0", 0.255, 0.10, 0.265, 0.12),
        ),
        source_document_id="fixture:reversed-degree-unit",
        field_id="results[0]",
    )

    assert faithful.field.grounded is True
    assert faithful.field.citation is not None
    assert faithful.field.citation.quote_or_value == "37 \u00b0 C Temperature"
    for outcome in (missing, reversed_unit):
        assert outcome.field.grounded is False
        assert outcome.field.bbox is None
        assert outcome.field.citation is None


@pytest.mark.parametrize("operator", ["-", "_"])
def test_composite_joined_unit_preserves_hyphen_and_underscore_operators(
    operator: str,
) -> None:
    from app.grounding.verifier import GroundingVerifier

    value = f"Dose 5 mg{operator}hr/L"
    faithful = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_layout_words(
            ("5", 0.10, 0.10, 0.11, 0.12),
            ("mg", 0.115, 0.10, 0.14, 0.12),
            (operator, 0.145, 0.10, 0.155, 0.12),
            ("hr", 0.16, 0.10, 0.18, 0.12),
            ("/", 0.185, 0.10, 0.19, 0.12),
            ("L", 0.195, 0.10, 0.205, 0.12),
            ("Dose", 0.21, 0.10, 0.25, 0.12),
        ),
        source_document_id=f"fixture:faithful-{operator}-unit",
        field_id="results[0]",
    )
    missing = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_layout_words(
            ("Dose", 0.10, 0.10, 0.14, 0.12),
            ("5", 0.145, 0.10, 0.155, 0.12),
            ("mg", 0.16, 0.10, 0.185, 0.12),
            ("hr", 0.19, 0.10, 0.21, 0.12),
            ("/", 0.215, 0.10, 0.22, 0.12),
            ("L", 0.225, 0.10, 0.235, 0.12),
        ),
        source_document_id=f"fixture:missing-{operator}-unit",
        field_id="results[0]",
    )
    reversed_unit = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_layout_words(
            ("Dose", 0.10, 0.10, 0.14, 0.12),
            ("5", 0.145, 0.10, 0.155, 0.12),
            ("hr", 0.16, 0.10, 0.18, 0.12),
            (operator, 0.185, 0.10, 0.195, 0.12),
            ("mg", 0.20, 0.10, 0.225, 0.12),
            ("/", 0.23, 0.10, 0.235, 0.12),
            ("L", 0.24, 0.10, 0.25, 0.12),
        ),
        source_document_id=f"fixture:reversed-{operator}-unit",
        field_id="results[0]",
    )

    assert faithful.field.grounded is True
    assert faithful.field.citation is not None
    for outcome in (missing, reversed_unit):
        assert outcome.field.grounded is False
        assert outcome.field.bbox is None
        assert outcome.field.citation is None


def test_uncommon_underscore_unit_join_remains_significant_and_split_form_grounds():
    from app.grounding.verifier import GroundingVerifier

    value = "Dose 5 abc_xyz/L"
    faithful = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_layout_words(
            ("5", 0.10, 0.10, 0.11, 0.12),
            ("abc", 0.115, 0.10, 0.145, 0.12),
            ("_", 0.15, 0.10, 0.16, 0.12),
            ("xyz", 0.165, 0.10, 0.195, 0.12),
            ("/", 0.20, 0.10, 0.205, 0.12),
            ("L", 0.21, 0.10, 0.22, 0.12),
            ("Dose", 0.225, 0.10, 0.265, 0.12),
        ),
        source_document_id="fixture:uncommon-underscore-unit",
        field_id="results[0]",
    )
    missing = GroundingVerifier().ground_value(
        value=value,
        words_boxes=_layout_words(
            ("Dose", 0.10, 0.10, 0.14, 0.12),
            ("5", 0.145, 0.10, 0.155, 0.12),
            ("abc", 0.16, 0.10, 0.19, 0.12),
            ("xyz", 0.195, 0.10, 0.225, 0.12),
            ("/", 0.23, 0.10, 0.235, 0.12),
            ("L", 0.24, 0.10, 0.25, 0.12),
        ),
        source_document_id="fixture:missing-uncommon-underscore-unit",
        field_id="results[0]",
    )

    assert faithful.field.grounded is True
    assert faithful.field.citation is not None
    assert missing.field.grounded is False
    assert missing.field.citation is None


def test_relaxed_match_window_is_bounded_on_a_long_single_ocr_row():
    from app.grounding.verifier import GroundingVerifier

    placements = [("Metformin", 0.05, 0.10, 0.13, 0.12)]
    placements.extend(
        ("|", 0.135 + index * 0.015, 0.10, 0.145 + index * 0.015, 0.12)
        for index in range(25)
    )
    placements.append(("daily", 0.515, 0.10, 0.56, 0.12))
    outcome = GroundingVerifier().ground_value(
        value="Metformin daily",
        words_boxes=_layout_words(*placements),
        source_document_id="fixture:bounded-window",
        field_id="current_medications[0]",
    )

    assert outcome.field.grounded is False
    assert outcome.field.bbox is None
    assert outcome.field.citation is None
