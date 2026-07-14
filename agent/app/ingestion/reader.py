"""Words+boxes reader — one canonical NormBBox space (W2_ARCHITECTURE.md §2, §3).

The single ingestion read path behind ``read_words_and_boxes``:

* **text-layer first** — pdfplumber (over the pypdfium2-openable PDF) extracts
  per-word boxes; they are normalized by the media box. pdfplumber already reports
  ``top``/``bottom`` measured from the page TOP, i.e. the y-up PDF space is flipped
  into the canonical origin-top-left, y-DOWN space (§2). Every page — even a
  text-layer page — records ``render_dpi == 200`` and the page pixel dims computed
  at 200 DPI from the media box (``px = pts / 72 * 200``).
* **junk-density heuristic** — a page whose embedded text layer is sparse or
  dominated by non-word junk is not trusted; that page is routed to OCR (W2-D3).
* **OCR fallback** — the page is rendered via pypdfium2 at 200 DPI to a PIL image,
  then Tesseract (`pytesseract.image_to_data`) reads it; boxes are normalized by the
  rendered pixel dims (already top-left, y-down).
* **per-page hard OCR timeout** — each OCR page runs the ``ocr_runner`` inside a
  spawned subprocess (``concurrent.futures.ProcessPoolExecutor`` with a spawn
  context). A runaway page is genuinely KILLED (the pool is shut down with
  ``cancel_futures=True`` and its worker terminated) — a thread cannot be
  force-killed, so a thread is never used. The killed page is marked
  ``unreadable=True`` with an empty word list and ``source="ocr"``, and the reader
  CONTINUES to the remaining pages; it never raises or hangs.

The reader stack is **pypdfium2 + pdfplumber + Tesseract only**. PyMuPDF is banned
(AGPL, W2-R6) and is never imported here.

``NormBBox`` is defined here for the spike; when W2-M6 freezes schemas it is the
shape unified into the canonical §2 contract module — no second shape is improvised.

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

import multiprocessing
import re
from pathlib import Path
from typing import Callable, Literal, Optional

import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from PIL import Image
from pydantic import BaseModel, ConfigDict, model_validator

# --- locked constants -----------------------------------------------------------------

#: Render DPI locked by §2. Both the OCR raster and every page's recorded pixel dims
#: are computed at this DPI.
RENDER_DPI: int = 200

#: PDF user-space is 72 units (points) per inch.
_POINTS_PER_INCH: int = 72

#: Default per-page OCR wall-clock budget (seconds). The AC-4 test injects a much
#: smaller value to trigger the kill deterministically.
_DEFAULT_PER_PAGE_OCR_TIMEOUT_S: float = 30.0

#: Junk-density heuristic thresholds (W2-D3). A text layer is TRUSTED only when it
#: carries a non-trivial number of alphanumeric-bearing tokens AND those tokens are
#: mostly "word-like" (contain letters/digits rather than being punctuation/mojibake
#: runs). Otherwise the page is routed to OCR.
_MIN_TRUSTWORTHY_WORDS: int = 3
_MIN_WORDLIKE_FRACTION: float = 0.5

#: A token counts as "word-like" when it contains at least this many alphanumeric
#: characters — a garbage run of symbols at a plausible position does not.
_MIN_ALNUM_PER_WORDLIKE_TOKEN: int = 2

_WORDLIKE_RE = re.compile(r"[A-Za-z0-9]")


# --- the canonical box ----------------------------------------------------------------


class NormBBox(BaseModel):
    """Canonical normalized page-relative box (§2).

    Coordinates are normalized to ``[0, 1]``, origin TOP-LEFT, y-DOWN (a word near the
    page top has SMALL ``y0``). Frozen and strict: construction validates the range and
    non-degenerate/non-inverted invariants, unknown fields are rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _validate_canonical(self) -> "NormBBox":
        for name in ("x0", "y0", "x1", "y1"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name}={value} escaped canonical range [0, 1]")
        if not self.x0 < self.x1:
            raise ValueError(f"degenerate/inverted box: x0={self.x0} !< x1={self.x1}")
        if not self.y0 < self.y1:
            raise ValueError(f"degenerate/inverted box: y0={self.y0} !< y1={self.y1}")
        return self


class Word(BaseModel):
    """A single extracted word and its canonical box."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    bbox: NormBBox


class PageWords(BaseModel):
    """Per-page words+boxes layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_index: int
    source: Literal["text_layer", "ocr"]
    render_dpi: int
    page_pixel_dims: tuple[int, int]
    words: list[Word]
    unreadable: bool = False


class WordsBoxes(BaseModel):
    """Document-level words+boxes output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pages: list[PageWords]


# --- OCR seam -------------------------------------------------------------------------

#: An OCR runner takes the rendered page image and returns Tesseract's ``image_to_data``
#: dict (the ``Output.DICT`` shape). Injectable so the AC-4 test can force a runaway.
OcrRunner = Callable[..., object]


def _default_ocr_runner(image: Image.Image) -> dict[str, list[object]]:
    """The real Tesseract runner: word-level boxes via ``image_to_data`` (DICT)."""
    return pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)


def _ocr_subprocess_entry(
    result_queue: "multiprocessing.Queue[object]",
    runner: OcrRunner,
    image_width: int,
    image_height: int,
    image_mode: str,
    image_bytes: bytes,
) -> None:
    """Module-level subprocess target (picklable) that reconstructs the page image,
    invokes the (possibly injected) ``runner``, and puts the result on ``result_queue``.

    Runs in a SPAWNED child. The image is passed as raw bytes so no PIL handle needs to
    cross the process boundary. A runaway runner never reaches the ``put`` because the
    parent terminates the child first.
    """
    image = Image.frombytes(image_mode, (image_width, image_height), image_bytes)
    result_queue.put(runner(image))


# --- geometry helpers -----------------------------------------------------------------


def _pixel_dims_from_points(width_pts: float, height_pts: float) -> tuple[int, int]:
    """Page pixel dims at ``RENDER_DPI`` from the media-box point dims (``px = pts/72*dpi``)."""
    scale = RENDER_DPI / _POINTS_PER_INCH
    width_px = max(1, round(width_pts * scale))
    height_px = max(1, round(height_pts * scale))
    return (width_px, height_px)


def _clamp_unit(value: float) -> float:
    """Clamp a normalized coordinate into ``[0, 1]`` to absorb sub-pixel edge rounding."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _make_bbox(x0: float, y0: float, x1: float, y1: float) -> Optional[NormBBox]:
    """Build a NormBBox from raw normalized edges, clamping to ``[0, 1]``. Returns None
    for a still-degenerate box (zero width/height) so callers can skip it rather than
    fabricating geometry."""
    nx0, nx1 = sorted((_clamp_unit(x0), _clamp_unit(x1)))
    ny0, ny1 = sorted((_clamp_unit(y0), _clamp_unit(y1)))
    if not (nx0 < nx1 and ny0 < ny1):
        return None
    return NormBBox(x0=nx0, y0=ny0, x1=nx1, y1=ny1)


# --- text-layer path ------------------------------------------------------------------


def _is_wordlike(token: str) -> bool:
    return len(_WORDLIKE_RE.findall(token)) >= _MIN_ALNUM_PER_WORDLIKE_TOKEN


def _text_layer_is_trustworthy(word_texts: list[str]) -> bool:
    """Density heuristic (W2-D3): trust the embedded text layer only when it has enough
    word-like tokens. A junk layer (garbage symbol runs at plausible positions, or a
    near-empty layer) is NOT trusted and the page is routed to OCR."""
    if len(word_texts) < _MIN_TRUSTWORTHY_WORDS:
        return False
    wordlike = sum(1 for token in word_texts if _is_wordlike(token))
    return wordlike >= max(
        _MIN_TRUSTWORTHY_WORDS, int(len(word_texts) * _MIN_WORDLIKE_FRACTION)
    )


def _extract_text_layer_words(
    plumber_page: "pdfplumber.page.Page",
) -> list[tuple[str, float, float, float, float]]:
    """Return ``(text, x0, top, x1, bottom)`` per word, in points. pdfplumber measures
    ``top``/``bottom`` from the page TOP (y-down) — the y-up PDF space already flipped."""
    words: list[tuple[str, float, float, float, float]] = []
    for word in plumber_page.extract_words(use_text_flow=False):
        words.append(
            (
                str(word["text"]),
                float(word["x0"]),
                float(word["top"]),
                float(word["x1"]),
                float(word["bottom"]),
            )
        )
    return words


def _text_layer_page_words(
    plumber_page: "pdfplumber.page.Page",
) -> Optional[list[Word]]:
    """Build canonical Words from the text layer, or None if the density heuristic
    rejects it (→ route to OCR)."""
    raw = _extract_text_layer_words(plumber_page)
    if not _text_layer_is_trustworthy([text for text, *_ in raw]):
        return None

    page_width = float(plumber_page.width)
    page_height = float(plumber_page.height)
    if page_width <= 0 or page_height <= 0:
        return None

    words: list[Word] = []
    for text, x0, top, x1, bottom in raw:
        bbox = _make_bbox(
            x0 / page_width,
            top / page_height,
            x1 / page_width,
            bottom / page_height,
        )
        if bbox is not None:
            words.append(Word(text=text, bbox=bbox))
    return words


# --- OCR path -------------------------------------------------------------------------


def _render_page_to_image(pdfium_page: "pdfium.PdfPage") -> Image.Image:
    """Render a page to a PIL image at ``RENDER_DPI`` via pypdfium2."""
    scale = RENDER_DPI / _POINTS_PER_INCH
    bitmap = pdfium_page.render(scale=scale)
    image = bitmap.to_pil()
    return image.convert("RGB")


def _ocr_words_from_data(
    data: dict[str, list[object]], width_px: int, height_px: int
) -> list[Word]:
    """Normalize Tesseract ``image_to_data`` (DICT) word boxes by pixel dims. Pixel space
    is already top-left, y-down, so no flip is needed."""
    words: list[Word] = []
    texts = data.get("text", [])
    lefts = data.get("left", [])
    tops = data.get("top", [])
    widths = data.get("width", [])
    heights = data.get("height", [])
    count = len(texts)
    for i in range(count):
        text = str(texts[i]).strip()
        if not text:
            continue
        left = float(lefts[i])
        top = float(tops[i])
        w = float(widths[i])
        h = float(heights[i])
        bbox = _make_bbox(
            left / width_px,
            top / height_px,
            (left + w) / width_px,
            (top + h) / height_px,
        )
        if bbox is not None:
            words.append(Word(text=text, bbox=bbox))
    return words


#: Grace period (seconds) to join a terminated OCR child after SIGTERM before giving up
#: on the join and moving on. A real Tesseract child dies well within this.
_TERMINATE_JOIN_GRACE_S: float = 5.0


def _unreadable_page(page_index: int, width_px: int, height_px: int) -> PageWords:
    """The typed outcome for a killed/unreadable OCR page (AC-4)."""
    return PageWords(
        page_index=page_index,
        source="ocr",
        render_dpi=RENDER_DPI,
        page_pixel_dims=(width_px, height_px),
        words=[],
        unreadable=True,
    )


def _ocr_page(
    pdfium_page: "pdfium.PdfPage",
    page_index: int,
    ocr_runner: OcrRunner,
    per_page_ocr_timeout_s: float,
    width_px: int,
    height_px: int,
) -> PageWords:
    """OCR a single page under a HARD per-page timeout that genuinely kills a runaway.

    The runner runs in a SPAWNED ``multiprocessing.Process``; on timeout the child is
    ``terminate()``-d (SIGTERM) and joined, so a runaway page cannot outlive the call —
    a thread cannot be force-killed, so a process is used. On timeout the page is marked
    unreadable (empty words, ``source="ocr"``) and the caller continues; never raises on
    timeout, never hangs.
    """
    image = _render_page_to_image(pdfium_page)
    image_bytes = image.tobytes()
    image_mode = image.mode
    image_width, image_height = image.size

    context = multiprocessing.get_context("spawn")
    result_queue: "multiprocessing.Queue[object]" = context.Queue()
    process = context.Process(
        target=_ocr_subprocess_entry,
        args=(
            result_queue,
            ocr_runner,
            image_width,
            image_height,
            image_mode,
            image_bytes,
        ),
        daemon=True,
    )
    process.start()

    data: object = None
    timed_out = False
    try:
        data = result_queue.get(timeout=per_page_ocr_timeout_s)
    except Exception:
        # queue.Empty (no result within the budget) — treat as a runaway page.
        timed_out = True

    if timed_out:
        # Genuine kill: SIGTERM the child, then reap it so nothing lingers.
        process.terminate()
        process.join(_TERMINATE_JOIN_GRACE_S)
        if process.is_alive():
            process.kill()
            process.join(_TERMINATE_JOIN_GRACE_S)
        result_queue.close()
        return _unreadable_page(page_index, width_px, height_px)

    process.join(_TERMINATE_JOIN_GRACE_S)
    result_queue.close()

    if not isinstance(data, dict):
        words: list[Word] = []
    else:
        words = _ocr_words_from_data(data, width_px, height_px)
    return PageWords(
        page_index=page_index,
        source="ocr",
        render_dpi=RENDER_DPI,
        page_pixel_dims=(width_px, height_px),
        words=words,
        unreadable=False,
    )


# --- entry point ----------------------------------------------------------------------


def read_words_and_boxes(
    pdf_path: str | Path,
    *,
    ocr_runner: Optional[OcrRunner] = None,
    per_page_ocr_timeout_s: float = _DEFAULT_PER_PAGE_OCR_TIMEOUT_S,
) -> WordsBoxes:
    """Read a PDF into the canonical words+boxes layer (§2/§3).

    Text-layer first (pdfplumber, media-box normalization, y already flipped to y-down);
    a junk-density heuristic routes an untrustworthy page to OCR; the OCR path renders
    via pypdfium2 at 200 DPI and reads with Tesseract, each page under a hard per-page
    subprocess timeout. ``ocr_runner`` is the injectable OCR seam (default = the real
    Tesseract runner).
    """
    path = Path(pdf_path)
    runner: OcrRunner = ocr_runner if ocr_runner is not None else _default_ocr_runner

    pages: list[PageWords] = []
    pdfium_doc = pdfium.PdfDocument(str(path))
    try:
        with pdfplumber.open(str(path)) as plumber_doc:
            page_count = len(pdfium_doc)
            for page_index in range(page_count):
                plumber_page = plumber_doc.pages[page_index]
                width_pts = float(plumber_page.width)
                height_pts = float(plumber_page.height)
                width_px, height_px = _pixel_dims_from_points(width_pts, height_pts)

                text_words = _text_layer_page_words(plumber_page)
                if text_words is not None:
                    pages.append(
                        PageWords(
                            page_index=page_index,
                            source="text_layer",
                            render_dpi=RENDER_DPI,
                            page_pixel_dims=(width_px, height_px),
                            words=text_words,
                            unreadable=False,
                        )
                    )
                    continue

                pdfium_page = pdfium_doc[page_index]
                try:
                    pages.append(
                        _ocr_page(
                            pdfium_page,
                            page_index,
                            runner,
                            per_page_ocr_timeout_s,
                            width_px,
                            height_px,
                        )
                    )
                finally:
                    pdfium_page.close()
    finally:
        pdfium_doc.close()

    return WordsBoxes(pages=pages)
