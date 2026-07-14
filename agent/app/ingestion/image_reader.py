"""Direct PNG/JPEG intake-form OCR into the canonical words+boxes layer."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

from app.ingestion.reader import (
    RENDER_DPI,
    OcrRunner,
    PageWords,
    WordsBoxes,
    _default_ocr_runner,
    _ocr_words_from_data,
)


def read_image_words_and_boxes(
    image_bytes: bytes, *, ocr_runner: OcrRunner | None = None
) -> WordsBoxes:
    """OCR one image page; pixel coordinates normalize directly to NormBBox."""

    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    runner = ocr_runner or _default_ocr_runner
    data = runner(image)
    words = _ocr_words_from_data(data, width, height) if isinstance(data, dict) else []
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="ocr",
                render_dpi=RENDER_DPI,
                page_pixel_dims=(width, height),
                words=words,
                unreadable=not isinstance(data, dict),
            )
        ]
    )
