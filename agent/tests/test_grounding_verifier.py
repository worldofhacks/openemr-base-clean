"""Frozen B2 grounding behavior (W2-D3; W2_ARCHITECTURE §2/§5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
        words_boxes=_pages(("trusted", "phrase"),),
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
