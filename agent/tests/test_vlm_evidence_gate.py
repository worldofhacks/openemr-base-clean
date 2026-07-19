"""G-fix (2026-07-19) — evidence-quality gate on VLM source-completeness checks.

The failure these tests pin: cursive/handwritten forms and photographed PNGs produce
OCR garbage; comparing a VALID VLM extraction against garbage evidence used to raise
``VlmResponseRejected`` — the platform rejected exactly the messy documents the W2 PDF
requires it to handle ("useful even if the document scan is imperfect").

Contract now frozen here:

* Evidence that could not READ the document (sparse/garbage words, unreadable pages)
  never vetoes an extraction — grounding still marks unsupported values
  unverified-and-visible (W2-REQ-97).
* Trustworthy evidence keeps the FULL veto: clean conflicting values, dropped
  OCR-readable rows, and out-of-order rows still reject (anti-invention posture
  unchanged where the evidence actually saw the page).
* The VLM may report MORE rows than degraded OCR could parse (garbled rows are
  invisible to evidence, not absent from the document); it may never drop or reorder
  rows the OCR could read.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from app.ingestion.reader import PageWords, Word, WordsBoxes
from app.llm.provider import LLMResponse, ToolUseBlock
from app.llm.vlm import AnthropicVlmExtractor, VlmResponseRejected
from app.schemas.extraction import (
    Demographics,
    GroundedField,
    IntakeFormExtraction,
    IntakeVitals,
    LabPdfExtraction,
    LabResult,
    NormBBox,
    VitalCandidate,
)

_DOC_ID = "synthetic-document"


def _unsupported(value=None):
    return GroundedField(value=value, page=None, grounded=False, citation=None)


def _lab_row(test_name: str, value: str) -> LabResult:
    return LabResult(
        test_name=_unsupported(test_name),
        value=_unsupported(value),
        unit=_unsupported("%"),
        reference_range=_unsupported("4.0-5.6"),
        abnormal_flag=_unsupported("H"),
        collection_date=_unsupported(date(2026, 7, 14)),
    )


def _lab_mapping(*rows: tuple[str, str]) -> dict:
    picked = rows or (("HbA1c", "7.2"),)
    extraction = LabPdfExtraction(
        results=[_lab_row(name, value) for name, value in picked],
        source_document_id=_DOC_ID,
    )
    return extraction.model_dump(mode="json")


def _intake_mapping(*, pulse: Decimal | None = None) -> dict:
    pulse_candidate = (
        None
        if pulse is None
        else VitalCandidate(
            value=_unsupported(pulse),
            unit=_unsupported("bpm"),
            measurement_date=_unsupported(),
        )
    )
    extraction = IntakeFormExtraction(
        demographics=Demographics(
            name=_unsupported(),
            dob=_unsupported(),
            sex=_unsupported(),
            contact=_unsupported(),
        ),
        chief_concern=_unsupported(),
        current_medications=[],
        allergies=[],
        family_history=_unsupported(),
        vitals=IntakeVitals(pulse=pulse_candidate),
        source_document_id=_DOC_ID,
    )
    return extraction.model_dump(mode="json")


def _words(*lines: str) -> WordsBoxes:
    words: list[Word] = []
    for row_index, line in enumerate(lines):
        y0 = 0.05 + row_index * 0.05
        for word_index, text in enumerate(line.split()):
            x0 = 0.05 + word_index * 0.07
            words.append(
                Word(text=text, bbox=NormBBox(x0=x0, y0=y0, x1=x0 + 0.06, y1=y0 + 0.02))
            )
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1200, 1600),
                words=words,
            )
        ]
    )


def _unreadable_evidence() -> WordsBoxes:
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="ocr",
                render_dpi=200,
                page_pixel_dims=(1200, 1600),
                words=[],
                unreadable=True,
            )
        ]
    )


class _Provider:
    def __init__(self, response: LLMResponse):
        self.response = response

    async def complete(self, **kwargs):
        return self.response


def _response(mapping: dict, *, name: str = "extract_lab_pdf") -> LLMResponse:
    return LLMResponse(
        content=[ToolUseBlock(id="tool-1", name=name, input=mapping)],
        stop_reason="tool_use",
        model="synthetic-model",
    )


def _extract_lab(mapping: dict, words_boxes: WordsBoxes) -> dict:
    return asyncio.run(
        AnthropicVlmExtractor(_Provider(_response(mapping))).extract(
            doc_type="lab_pdf",
            source=b"%PDF-1.7 synthetic",
            words_boxes=words_boxes,
            source_document_id=_DOC_ID,
        )
    )


def _extract_intake(mapping: dict, words_boxes: WordsBoxes) -> dict:
    return asyncio.run(
        AnthropicVlmExtractor(
            _Provider(_response(mapping, name="extract_intake_form"))
        ).extract(
            doc_type="intake_form",
            source=b"\x89PNG\r\n\x1a\nsynthetic",
            words_boxes=words_boxes,
            source_document_id=_DOC_ID,
        )
    )


# --- document-level gate: evidence that read NOTHING never vetoes ---------------------


def test_garbage_evidence_does_not_veto_lab_extraction():
    # Cursive/photo simulation: the OCR layer produced only glyph noise. The valid VLM
    # extraction must be ACCEPTED (fields stay ungrounded until grounding verifies them).
    garbage = _words("~# ((~ !!)", "^%$ ~~ ))(", "## (( ~~")
    result = _extract_lab(_lab_mapping(), garbage)
    assert result["results"][0]["value"]["value"] == "7.2"


def test_unreadable_pages_do_not_veto_extraction():
    # A killed/undecodable page (e.g. hardened image intake) is evidence-absent.
    result = _extract_lab(_lab_mapping(), _unreadable_evidence())
    assert len(result["results"]) == 1

    intake = _extract_intake(_intake_mapping(pulse=Decimal("72")), _unreadable_evidence())
    assert intake["vitals"]["pulse"]["value"]["value"] == Decimal("72")


# --- value-level mercy: printed labels + handwritten values ---------------------------


def test_printed_label_with_garbled_value_is_not_vetoed():
    words = _words("Lab Results", "Test: HbA1c", "Value: ~~((")
    result = _extract_lab(_lab_mapping(), words)
    assert result["results"][0]["test_name"]["value"] == "HbA1c"


def test_garbled_vital_value_permits_any_or_no_candidate():
    words = _words("Intake Form Vitals Section", "Pulse: ~~##")
    for mapping in (_intake_mapping(), _intake_mapping(pulse=Decimal("72"))):
        result = _extract_intake(mapping, words)
        assert result["source_document_id"] == _DOC_ID


def test_digit_free_vital_evidence_cannot_corroborate_a_number():
    words = _words("Intake Form Vitals Section", "Pulse: strong")
    result = _extract_intake(_intake_mapping(pulse=Decimal("72")), words)
    assert result["vitals"]["pulse"]["value"]["value"] == Decimal("72")


# --- degraded-OCR surplus: VLM may see rows the evidence could not --------------------


def test_vlm_may_report_rows_ocr_could_not_parse():
    words = _words("Lab Results", "Test: HbA1c", "Value: 7.2")
    mapping = _lab_mapping(("HbA1c", "7.2"), ("Glucose", "95"))
    result = _extract_lab(mapping, words)
    assert [row["test_name"]["value"] for row in result["results"]] == ["HbA1c", "Glucose"]


def test_garbled_ocr_row_between_clean_rows_is_skipped():
    words = _words(
        "Lab Results",
        "Test: HbA1c",
        "Value: 7.2",
        "Test: ~~((",
        "Value: ##))",
        "Test: Glucose",
        "Value: 95",
    )
    mapping = _lab_mapping(("HbA1c", "7.2"), ("Creatinine", "1.1"), ("Glucose", "95"))
    result = _extract_lab(mapping, words)
    assert len(result["results"]) == 3


# --- the veto stays armed on trustworthy evidence (anti-invention regression) ---------


def test_clean_conflicting_value_still_rejects():
    words = _words("Lab Results", "Test: HbA1c", "Value: 65")
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract_lab(_lab_mapping(("HbA1c", "6.5")), words)


def test_dropping_an_ocr_readable_row_still_rejects():
    words = _words(
        "Lab Results",
        "Test: HbA1c",
        "Value: 7.2",
        "Test: Glucose",
        "Value: 95",
    )
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract_lab(_lab_mapping(("Glucose", "95")), words)


def test_reordering_ocr_readable_rows_still_rejects():
    words = _words(
        "Lab Results",
        "Test: HbA1c",
        "Value: 7.2",
        "Test: Glucose",
        "Value: 95",
    )
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract_lab(_lab_mapping(("Glucose", "95"), ("HbA1c", "7.2")), words)


def test_clean_vital_rescale_still_rejects():
    words = _words("Intake Form Vitals Section", "Pulse: 72 bpm")
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract_intake(_intake_mapping(pulse=Decimal("7.2")), words)


def test_omitting_a_clean_vital_still_rejects():
    words = _words("Intake Form Vitals Section", "Pulse: 72 bpm")
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract_intake(_intake_mapping(), words)
