"""Integration: realistic table formatting grounds without weakening invention guards."""

from __future__ import annotations

import runpy
from datetime import date
from pathlib import Path
from typing import cast

from app.grounding.verifier import (
    GroundingSummary,
    GroundingVerifier,
    _normalize,
    _phrase_tokens,
)
from app.ingestion.pipeline import _reground
from app.ingestion.reader import WordsBoxes, read_pdf_bytes_words_and_boxes
from app.schemas.citations import CitationSourceType
from app.schemas.extraction import (
    Demographics,
    GroundedField,
    IntakeFormExtraction,
    IntakeVitals,
)


def _fixture_bytes() -> bytes:
    generator = (
        Path(__file__).parent / "fixtures" / "grounding" / "table_layout_intake.py"
    )
    namespace = runpy.run_path(str(generator))
    build = namespace["build_table_layout_intake_pdf"]
    payload = build()
    assert isinstance(payload, bytes)
    assert payload == build()
    return payload


def _candidate(value):
    return GroundedField(
        value=value,
        page=None,
        bbox=None,
        grounded=False,
        citation=None,
    )


def _proposal(document_id: str) -> IntakeFormExtraction:
    return IntakeFormExtraction(
        demographics=Demographics(
            name=_candidate("SYNTHETIC TEST PATIENT"),
            dob=_candidate(date(1983, 12, 19)),
            sex=_candidate("X"),
            contact=_candidate("synthetic-contact@example.invalid"),
        ),
        chief_concern=_candidate("Recurring morning headaches for two weeks"),
        current_medications=[
            _candidate("Metformin 500 mg twice daily"),
            _candidate("Lisinopril 10 mg at bedtime"),
            _candidate("Warfarin 5 mg"),  # deliberately absent fabrication
        ],
        allergies=[
            _candidate("Penicillin rash"),
            _candidate("Latex dermatitis"),
        ],
        family_history=_candidate("Father type 2 diabetes Mother hypertension"),
        vitals=IntakeVitals(),
        source_document_id=document_id,
    )


def _legacy_contiguous_matches(value: object, words_boxes: WordsBoxes) -> bool:
    """The former matcher, retained only to make the recall regression measurable."""

    wanted = _phrase_tokens(value)
    for page in words_boxes.pages:
        normalized = [_normalize(word.text) for word in page.words]
        for start in range(0, len(normalized) - len(wanted) + 1):
            if tuple(normalized[start : start + len(wanted)]) == wanted:
                return True
    return False


def _legitimate_candidates(proposal: IntakeFormExtraction) -> tuple[object, ...]:
    return (
        proposal.demographics.name.value,
        proposal.demographics.dob.value,
        proposal.demographics.sex.value,
        proposal.demographics.contact.value,
        proposal.chief_concern.value,
        proposal.current_medications[0].value,
        proposal.current_medications[1].value,
        proposal.allergies[0].value,
        proposal.allergies[1].value,
        proposal.family_history.value,
    )


def test_table_layout_intake_improves_three_of_ten_to_ten_of_ten_without_invention():
    document_id = "fixture:table-layout-intake"
    words_boxes = read_pdf_bytes_words_and_boxes(_fixture_bytes())
    proposal = _proposal(document_id)

    before = sum(
        _legacy_contiguous_matches(value, words_boxes)
        for value in _legitimate_candidates(proposal)
    )
    assert before == 3

    extraction, outcomes = _reground(
        proposal,
        words_boxes=words_boxes,
        document_id=document_id,
        verifier=GroundingVerifier(),
    )
    grounded = cast(IntakeFormExtraction, extraction)
    summary = GroundingSummary.from_outcomes(outcomes)

    assert summary.fields_grounded == 10
    assert summary.fields_unsupported == 1

    legitimate_fields = (
        grounded.demographics.name,
        grounded.demographics.dob,
        grounded.demographics.sex,
        grounded.demographics.contact,
        grounded.chief_concern,
        grounded.current_medications[0],
        grounded.current_medications[1],
        grounded.allergies[0],
        grounded.allergies[1],
        grounded.family_history,
    )
    for field in legitimate_fields:
        assert field.grounded is True
        assert field.page == 1
        assert field.bbox is not None
        assert field.citation is not None
        assert field.citation.source_type is CitationSourceType.UPLOADED_DOCUMENT
        assert field.citation.source_id == document_id
        assert field.citation.page_or_section == "1"
        assert field.citation.quote_or_value

    assert grounded.demographics.dob.citation is not None
    assert grounded.demographics.dob.citation.quote_or_value == "12/19/1983"
    assert grounded.chief_concern.citation is not None
    assert "persistent" in grounded.chief_concern.citation.quote_or_value

    fabricated = grounded.current_medications[2]
    assert fabricated.value == "Warfarin 5 mg"
    assert fabricated.grounded is False
    assert fabricated.bbox is None
    assert fabricated.citation is None
