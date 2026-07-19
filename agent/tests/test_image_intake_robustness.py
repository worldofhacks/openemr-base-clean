"""G-fix (2026-07-19) — image intake robustness: never raise, never hang.

Pins the hardened ``read_image_words_and_boxes`` contract:

* Undecodable bytes → one typed ``unreadable=True`` page, NO exception (an image upload
  can never 500 the ingestion pipeline on decode).
* A runaway OCR runner is genuinely KILLED under the same per-page subprocess budget as
  the PDF path (previously the runner ran inline with no budget — a pathological image
  could hang a request).
* A CRASHING runner surfaces as unreadable promptly (dead-child detection), not after
  burning the whole budget.
* The happy path is unchanged: DICT runner output becomes normalized words.

Fake runners are module-level so they are picklable into the spawned OCR child
(mirrors test_reader_geometry's AC-4 seam discipline).
"""

from __future__ import annotations

import time
from io import BytesIO

from PIL import Image

from app.ingestion.image_reader import read_image_words_and_boxes

#: Far larger than any per-test timeout AND the wall-clock assertions below, so a broken
#: kill path fails on time, not by luck.
_RUNAWAY_SLEEP_S = 120.0
_TINY_TIMEOUT_S = 0.5
#: Generous wall-clock ceilings — spawn startup costs ~1s; a broken kill would blow far
#: past these (runaway sleeps 120s; crash previously waited the full 30s budget).
_KILL_WALLCLOCK_BUDGET_S = 25.0
_CRASH_WALLCLOCK_BUDGET_S = 20.0


def runaway_ocr_runner(*args: object, **kwargs: object) -> object:
    """Blocks far past any sane budget; only a real kill ends it in time."""
    time.sleep(_RUNAWAY_SLEEP_S)
    raise AssertionError("runaway_ocr_runner returned — the image OCR timeout is broken")


def crashing_ocr_runner(*args: object, **kwargs: object) -> object:
    """Dies immediately in the child — must surface as unreadable, fast."""
    raise RuntimeError("synthetic OCR crash")


def happy_ocr_runner(*args: object, **kwargs: object) -> dict[str, list[object]]:
    return {
        "text": ["SYNTHETIC", "FIXTURE"],
        "left": [5, 40],
        "top": [5, 5],
        "width": [30, 30],
        "height": [10, 10],
    }


def _png_bytes(width: int = 80, height: int = 60) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_undecodable_bytes_yield_unreadable_page_not_exception():
    for bad in (b"not an image at all", b"\x89PNG\r\n\x1a\ntruncated-junk", b""):
        result = read_image_words_and_boxes(bad)
        assert len(result.pages) == 1
        page = result.pages[0]
        assert page.unreadable is True
        assert page.words == []
        assert page.source == "ocr"


def test_runaway_ocr_runner_is_killed_and_page_marked_unreadable():
    started = time.monotonic()
    result = read_image_words_and_boxes(
        _png_bytes(),
        ocr_runner=runaway_ocr_runner,
        per_page_ocr_timeout_s=_TINY_TIMEOUT_S,
    )
    elapsed = time.monotonic() - started
    assert elapsed < _KILL_WALLCLOCK_BUDGET_S, (
        f"image OCR kill took {elapsed:.1f}s — the runaway was waited out, not killed"
    )
    assert result.pages[0].unreadable is True
    assert result.pages[0].words == []


def test_crashing_ocr_runner_surfaces_unreadable_promptly():
    started = time.monotonic()
    result = read_image_words_and_boxes(
        _png_bytes(),
        ocr_runner=crashing_ocr_runner,
    )
    elapsed = time.monotonic() - started
    assert elapsed < _CRASH_WALLCLOCK_BUDGET_S, (
        f"crashed OCR child took {elapsed:.1f}s to surface — dead-child detection broken"
    )
    assert result.pages[0].unreadable is True


def test_happy_path_words_normalized_by_image_pixel_dims():
    width, height = 80, 60
    result = read_image_words_and_boxes(
        _png_bytes(width, height),
        ocr_runner=happy_ocr_runner,
    )
    page = result.pages[0]
    assert page.unreadable is False
    assert page.page_pixel_dims == (width, height)
    assert [word.text for word in page.words] == ["SYNTHETIC", "FIXTURE"]
    first = page.words[0].bbox
    assert abs(first.x0 - 5 / width) < 1e-9
    assert abs(first.y1 - 15 / height) < 1e-9
