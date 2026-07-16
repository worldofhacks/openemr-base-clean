"""Strict Anthropic document-extraction boundary (W2-D3; §2/§4/§5)."""

from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.ingestion.reader import PageWords, Word, WordsBoxes
from app.llm.provider import (
    AnthropicLLMProvider,
    LLMResponse,
    LLMTimeout,
    LLMUnavailable,
    TextBlock,
    ToolUseBlock,
    classify_llm_error,
)
from app.llm.vlm import (
    AnthropicVlmExtractor,
    VLM_PROMPT_HASH,
    VLM_PROMPT_VERSION,
    VlmResponseRejected,
    VlmTimeout,
    VlmUnavailable,
    vlm_prompt_hash,
)
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


def _unsupported(value=None, *, page: int | None = None):
    return GroundedField(value=value, page=page, grounded=False, citation=None)


def _lab_mapping(source_document_id: str = "synthetic-document") -> dict:
    extraction = LabPdfExtraction(
        results=[
            LabResult(
                test_name=_unsupported("HbA1c"),
                value=_unsupported("7.2"),
                unit=_unsupported("%"),
                reference_range=_unsupported("4.0-5.6"),
                abnormal_flag=_unsupported("H"),
                collection_date=_unsupported(date(2026, 7, 14)),
            )
        ],
        source_document_id=source_document_id,
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
        source_document_id="synthetic-document",
    )
    return extraction.model_dump(mode="json")


def _words(*lines: str) -> WordsBoxes:
    words: list[Word] = []
    for row_index, line in enumerate(lines):
        y0 = 0.05 + row_index * 0.05
        for word_index, text in enumerate(line.split()):
            x0 = 0.05 + word_index * 0.07
            words.append(
                Word(
                    text=text,
                    bbox=NormBBox(x0=x0, y0=y0, x1=x0 + 0.06, y1=y0 + 0.02),
                )
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


class _Provider:
    def __init__(self, response: LLMResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _response(
    mapping: dict | None = None,
    *,
    name: str = "extract_lab_pdf",
    content: list | None = None,
    stop_reason: str = "tool_use",
) -> LLMResponse:
    blocks = content or [
        ToolUseBlock(id="tool-1", name=name, input=mapping or _lab_mapping())
    ]
    return LLMResponse(content=blocks, stop_reason=stop_reason, model="synthetic-model")


def _extract(
    provider: _Provider,
    *,
    source: bytes = b"%PDF-1.7 synthetic",
    words_boxes: WordsBoxes | None = None,
):
    return asyncio.run(
        AnthropicVlmExtractor(provider).extract(
            doc_type="lab_pdf",
            source=source,
            words_boxes=words_boxes or WordsBoxes(pages=[]),
            source_document_id="synthetic-document",
        )
    )


def test_pdf_is_untrusted_data_and_one_frozen_lab_tool_is_forced():
    provider = _Provider(_response())
    source = b"%PDF-1.7 synthetic"

    result = _extract(provider, source=source)

    assert result["source_document_id"] == "synthetic-document"
    # JSON date strings are validated through the frozen schema and returned as typed
    # Python values so the pipeline's strict second validation cannot coerce them.
    assert result["results"][0]["collection_date"]["value"] == date(2026, 7, 14)

    call = provider.calls[0]
    assert len(call["tools"]) == 1
    assert call["tools"][0]["name"] == "extract_lab_pdf"
    assert call["tools"][0]["input_schema"] == LabPdfExtraction.model_json_schema()
    assert call["tool_choice"] == {
        "type": "tool",
        "name": "extract_lab_pdf",
        "disable_parallel_tool_use": True,
    }
    assert "untrusted" in call["system"][0]["text"].casefold()
    assert "every visible row" in call["system"][0]["text"].casefold()
    assert "exact visible value and magnitude" in call["system"][0]["text"].casefold()
    assert "page=null" in call["system"][0]["text"]
    assert VLM_PROMPT_VERSION.endswith("-v2")
    assert VLM_PROMPT_HASH == vlm_prompt_hash()
    document = next(
        block for block in call["messages"][0]["content"] if block["type"] == "document"
    )
    assert document["source"] == {
        "type": "base64",
        "media_type": "application/pdf",
        "data": base64.b64encode(source).decode("ascii"),
    }


def test_png_intake_uses_image_block_and_frozen_intake_schema():
    provider = _Provider(
        _response(
            _intake_mapping(),
            name="extract_intake_form",
        )
    )
    source = b"\x89PNG\r\n\x1a\nsynthetic"

    result = asyncio.run(
        AnthropicVlmExtractor(provider).extract(
            doc_type="intake_form",
            source=source,
            words_boxes=WordsBoxes(pages=[]),
            source_document_id="synthetic-document",
        )
    )

    assert result["source_document_id"] == "synthetic-document"
    call = provider.calls[0]
    assert call["tools"][0]["input_schema"] == IntakeFormExtraction.model_json_schema()
    image = next(
        block for block in call["messages"][0]["content"] if block["type"] == "image"
    )
    assert image["source"]["media_type"] == "image/png"
    assert image["source"]["data"] == base64.b64encode(source).decode("ascii")


def test_provider_cannot_supply_grounding_or_location_assertions():
    mapping = _lab_mapping()
    mapping["results"][0]["value"]["page"] = 1
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract(_Provider(_response(mapping)))

    mapping = _lab_mapping()
    mapping["results"][0]["value"]["bbox"] = {
        "x0": 0.1,
        "y0": 0.1,
        "x1": 0.2,
        "y1": 0.2,
    }
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract(_Provider(_response(mapping)))

    mapping = _lab_mapping()
    mapping["results"][0]["value"].update(
        {
            "page": 1,
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2},
            "grounded": True,
            "citation": {
                "source_type": "uploaded_document",
                "source_id": "synthetic-document",
                "page_or_section": "page 1",
                "field_or_chunk_id": "results[0].value",
                "quote_or_value": "synthetic-value",
            },
        }
    )
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract(_Provider(_response(mapping)))


def test_repeated_labeled_lab_rows_must_all_be_returned_in_source_order():
    words_boxes = _words(
        "Lab Results",
        "Test: HbA1c",
        "Value: 7.2",
        "Test: HbA1c",
        "Value: 6.8",
    )
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract(_Provider(_response()), words_boxes=words_boxes)

    complete = _lab_mapping()
    second = deepcopy(complete["results"][0])
    second["value"]["value"] = "6.8"
    complete["results"].append(second)
    result = _extract(_Provider(_response(complete)), words_boxes=words_boxes)
    assert len(result["results"]) == 2


def test_visible_lab_outlier_magnitude_cannot_be_scaled_or_corrected():
    mapping = _lab_mapping()
    mapping["results"][0]["value"]["value"] = "6.5"
    words_boxes = _words("Lab Results", "Test: HbA1c", "Value: 65")

    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract(_Provider(_response(mapping)), words_boxes=words_boxes)


def test_recognized_intake_vital_cannot_be_omitted_or_rescaled():
    words_boxes = _words("Vitals", "Pulse: 72 bpm")
    source = b"\x89PNG\r\n\x1a\nsynthetic"

    for mapping in (_intake_mapping(), _intake_mapping(pulse=Decimal("7.2"))):
        provider = _Provider(_response(mapping, name="extract_intake_form"))
        with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
            asyncio.run(
                AnthropicVlmExtractor(provider).extract(
                    doc_type="intake_form",
                    source=source,
                    words_boxes=words_boxes,
                    source_document_id="synthetic-document",
                )
            )

    provider = _Provider(
        _response(_intake_mapping(pulse=Decimal("72")), name="extract_intake_form")
    )
    result = asyncio.run(
        AnthropicVlmExtractor(provider).extract(
            doc_type="intake_form",
            source=source,
            words_boxes=words_boxes,
            source_document_id="synthetic-document",
        )
    )
    assert result["vitals"]["pulse"]["value"]["value"] == Decimal("72")


@pytest.mark.parametrize(
    "response",
    [
        _response(content=[TextBlock("prose"), ToolUseBlock("t", "extract_lab_pdf", _lab_mapping())]),
        _response(
            content=[
                ToolUseBlock("t1", "extract_lab_pdf", _lab_mapping()),
                ToolUseBlock("t2", "extract_lab_pdf", _lab_mapping()),
            ]
        ),
        _response(name="wrong_tool"),
        _response(stop_reason="end_turn"),
    ],
    ids=["prose", "multiple-tools", "wrong-tool", "wrong-stop-reason"],
)
def test_prose_multiple_wrong_tool_or_non_tool_stop_is_rejected(response):
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract(_Provider(response))


@pytest.mark.parametrize(
    "mapping",
    [
        {"source_document_id": "synthetic-document", "results": [], "extra": True},
        {"source_document_id": "other-document", "results": []},
        {"source_document_id": "synthetic-document", "results": "not-a-list"},
    ],
    ids=["extra-field", "wrong-source", "wrong-type"],
)
def test_wrong_or_non_frozen_tool_schema_is_rejected(mapping):
    with pytest.raises(VlmResponseRejected, match="invalid VLM response"):
        _extract(_Provider(_response(mapping)))


@pytest.mark.parametrize(
    ("provider_error", "expected"),
    [
        (LLMTimeout("sensitive source value"), VlmTimeout),
        (LLMUnavailable("sensitive source value"), VlmUnavailable),
    ],
)
def test_provider_failures_are_classified_with_fixed_non_content_messages(
    provider_error, expected
):
    with pytest.raises(expected) as caught:
        _extract(_Provider(error=provider_error))
    assert "sensitive" not in str(caught.value)


def test_provider_classifies_builtin_timeout_separately():
    assert isinstance(classify_llm_error(TimeoutError("timed out")), LLMTimeout)


def test_anthropic_provider_forwards_explicit_strict_tool_choice():
    captured: dict = {}

    class Messages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[], stop_reason="tool_use", usage=None, model="synthetic-model"
            )

    client = SimpleNamespace(messages=Messages())
    provider = AnthropicLLMProvider(
        api_key="synthetic-key", model="synthetic-model", client=client
    )
    choice = {
        "type": "tool",
        "name": "extract_lab_pdf",
        "disable_parallel_tool_use": True,
    }

    asyncio.run(
        provider.complete(
            system=[],
            messages=[{"role": "user", "content": "synthetic"}],
            tools=[{"name": "extract_lab_pdf", "input_schema": {"type": "object"}}],
            tool_choice=choice,
        )
    )

    assert captured["tool_choice"] == choice
