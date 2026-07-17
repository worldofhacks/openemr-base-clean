"""Optional clinic-style demo PDFs stay deterministic, born-digital, and groundable."""

from __future__ import annotations

from datetime import date
import runpy
from pathlib import Path
from typing import cast

from app.grounding.verifier import GroundingSummary, GroundingVerifier
from app.ingestion.pipeline import _reground
from app.ingestion.reader import read_pdf_bytes_words_and_boxes
from app.schemas.extraction import (
    Demographics,
    GroundedField,
    IntakeFormExtraction,
    IntakeVitals,
    LabPdfExtraction,
    LabResult,
)


_FIXTURES = Path(__file__).resolve().parents[1] / "demo" / "fixtures"
_GENERATOR = _FIXTURES / "generate_demo_pdfs.py"


def _candidate(value: object) -> GroundedField[object]:
    return GroundedField(value=value, page=None, bbox=None, grounded=False, citation=None)


def _generator() -> dict[str, object]:
    return runpy.run_path(str(_GENERATOR))


def test_committed_demo_pdfs_are_deterministic_born_digital_text_layers() -> None:
    namespace = _generator()
    expected = {
        "synthetic_clinic_intake.pdf": namespace["build_intake_pdf"](),
        "synthetic_lab_report.pdf": namespace["build_lab_pdf"](),
    }

    for filename, payload in expected.items():
        path = _FIXTURES / filename
        assert path.read_bytes() == payload
        words = read_pdf_bytes_words_and_boxes(payload)
        assert len(words.pages) == 1
        assert words.pages[0].source == "text_layer"
        assert words.pages[0].unreadable is False
        assert len(words.pages[0].words) >= 35
        page_text = " ".join(word.text for word in words.pages[0].words)
        assert "SYNTHETIC" in page_text
        assert "NOT A REAL PATIENT" in page_text


def test_demo_intake_legitimate_fields_ground_and_fabrication_does_not() -> None:
    payload = (_FIXTURES / "synthetic_clinic_intake.pdf").read_bytes()
    proposal = IntakeFormExtraction(
        demographics=Demographics(
            name=_candidate("CASEY DEMO"),
            dob=_candidate(date(1983, 12, 19)),
            sex=_candidate("X"),
            contact=_candidate("casey.demo@example.invalid"),
        ),
        chief_concern=_candidate("Persistent fatigue and muscle cramps for two weeks"),
        current_medications=[
            _candidate("Metformin 500 mg twice daily"),
            _candidate("Lisinopril 10 mg once daily"),
            _candidate("Warfarin 5 mg"),
        ],
        allergies=[_candidate("Penicillin rash")],
        family_history=_candidate("Father type 2 diabetes Mother hypertension"),
        vitals=IntakeVitals(),
        source_document_id="demo:intake",
    )
    extraction, outcomes = _reground(
        proposal,
        words_boxes=read_pdf_bytes_words_and_boxes(payload),
        document_id="demo:intake",
        verifier=GroundingVerifier(),
    )
    grounded = cast(IntakeFormExtraction, extraction)
    summary = GroundingSummary.from_outcomes(outcomes)

    assert summary.fields_grounded == 9
    assert summary.fields_unsupported == 1
    assert grounded.current_medications[2].grounded is False
    assert grounded.current_medications[2].citation is None
    for field in (
        grounded.demographics.name,
        grounded.demographics.dob,
        grounded.demographics.sex,
        grounded.demographics.contact,
        grounded.chief_concern,
        grounded.current_medications[0],
        grounded.current_medications[1],
        grounded.allergies[0],
        grounded.family_history,
    ):
        assert field.grounded is True
        assert field.page == 1
        assert field.bbox is not None
        assert field.citation is not None


def test_demo_lab_rows_ground_with_complete_page_citations() -> None:
    payload = (_FIXTURES / "synthetic_lab_report.pdf").read_bytes()

    def result(
        name: str,
        value: str,
        unit: str,
        reference: str,
        flag: str,
    ) -> LabResult:
        return LabResult(
            test_name=_candidate(name),
            value=_candidate(value),
            unit=_candidate(unit),
            reference_range=_candidate(reference),
            collection_date=_candidate(date(2026, 7, 15)),
            abnormal_flag=_candidate(flag),
        )

    proposal = LabPdfExtraction(
        results=[
            result("Magnesium", "1.6", "mg/dL", "1.7-2.4", "L"),
            result("Hemoglobin A1c", "7.4", "%", "4.0-5.6", "H"),
        ],
        source_document_id="demo:lab",
    )
    extraction, outcomes = _reground(
        proposal,
        words_boxes=read_pdf_bytes_words_and_boxes(payload),
        document_id="demo:lab",
        verifier=GroundingVerifier(),
    )
    grounded = cast(LabPdfExtraction, extraction)
    summary = GroundingSummary.from_outcomes(outcomes)

    assert summary.fields_grounded == 12
    assert summary.fields_unsupported == 0
    for item in grounded.results:
        for field in (
            item.test_name,
            item.value,
            item.unit,
            item.reference_range,
            item.collection_date,
            item.abnormal_flag,
        ):
            assert field.grounded is True
            assert field.page == 1
            assert field.bbox is not None
            assert field.citation is not None
            assert field.citation.page_or_section == "1"
