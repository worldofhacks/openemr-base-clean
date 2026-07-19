"""Direct PNG/JPEG intake-form OCR into the canonical words+boxes layer.

Robustness contract (G-fix, 2026-07-19 — parity with the PDF path):

* Corrupt/undecodable image bytes NEVER raise out of this reader — they degrade to a
  typed single unreadable page (the same shape a killed OCR page gets), so an image
  upload can never 500 the ingestion pipeline.
* The OCR runner executes under the same hard per-page subprocess timeout as the PDF
  path (``reader._run_ocr_with_timeout``): a runaway or crashing runner is genuinely
  killed and the page is marked unreadable. Previously the runner was called inline with
  no budget — a hostile or pathological image could hang a request.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

from app.ingestion.reader import (
    _DEFAULT_PER_PAGE_OCR_TIMEOUT_S,
    _OCR_TIMEOUT_SENTINEL,
    RENDER_DPI,
    OcrRunner,
    PageWords,
    WordsBoxes,
    _default_ocr_runner,
    _ocr_words_from_data,
    _run_ocr_with_timeout,
)


def _single_page(
    width: int,
    height: int,
    words: list,
    *,
    unreadable: bool,
) -> WordsBoxes:
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="ocr",
                render_dpi=RENDER_DPI,
                page_pixel_dims=(max(1, width), max(1, height)),
                words=words,
                unreadable=unreadable,
            )
        ]
    )


def read_image_words_and_boxes(
    image_bytes: bytes,
    *,
    ocr_runner: OcrRunner | None = None,
    per_page_ocr_timeout_s: float = _DEFAULT_PER_PAGE_OCR_TIMEOUT_S,
) -> WordsBoxes:
    """OCR one image page; pixel coordinates normalize directly to NormBBox.

    Never raises on undecodable bytes or a runaway/crashing runner — both yield a
    single ``unreadable=True`` page so downstream (VLM extraction + grounding) treats
    the image as evidence-absent rather than failing the upload.
    """
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except Exception:
        # Decoder details must not cross the boundary; the typed unreadable page is the
        # whole story downstream.
        return _single_page(1, 1, [], unreadable=True)

    width, height = image.size
    runner = ocr_runner or _default_ocr_runner
    data = _run_ocr_with_timeout(image, runner, per_page_ocr_timeout_s)
    if data is _OCR_TIMEOUT_SENTINEL:
        return _single_page(width, height, [], unreadable=True)

    words = _ocr_words_from_data(data, width, height) if isinstance(data, dict) else []
    return _single_page(
        width,
        height,
        words,
        unreadable=not isinstance(data, dict),
    )
