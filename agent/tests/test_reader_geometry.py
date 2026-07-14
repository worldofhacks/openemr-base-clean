"""W2-M4 — PDF words+boxes reader spike: frozen failing tests (RED-first).

Encodes W2-M4 AC-1..AC-5 and AC-7 (AC-6 is a [live-measure] segmentation-winner
evidence row recorded in the ticket report, NOT a frozen test) against
W2_ARCHITECTURE.md §2 (the single canonical NormBBox coordinate space — normalized
∈[0,1], origin top-left, y-down; RENDER_DPI locked at 200; page pixel dims recorded)
and §3 (read step: text-layer first | junk-density → OCR fallback), plus W2-D3
(text-layer first, OCR fallback, junk-layer sanity check) and W2-R6 (pypdfium2 +
pdfplumber + Tesseract only; PyMuPDF/AGPL ban is binding).

FROZEN PUBLIC CONTRACT these tests pin (module ``app.ingestion.reader``). The
implementation conforms to these tests, never the other way around. When W2-M6 freezes
schemas, ``NormBBox`` is the shape unified into the canonical §2 contract module — do
NOT improvise a second shape afterward.

- ``RENDER_DPI: int`` — module constant, locked to 200 (§2 render DPI).

- ``NormBBox`` — the canonical box. A FROZEN Pydantic v2 model
  (``model_config = ConfigDict(frozen=True, extra="forbid")``) with float fields
  ``x0, y0, x1, y1``. Invariants (enforced at construction, rejected otherwise):
    * every coordinate ∈ [0.0, 1.0],
    * ``x0 < x1`` and ``y0 < y1`` (non-degenerate, non-inverted),
    * origin top-left, y-DOWN (y grows downward). A word near the TOP of the page has
      SMALL y — this is the load-bearing convention the PDF path must flip into.
  Constructing an out-of-range or inverted box raises ``pydantic.ValidationError``;
  passing an unknown field raises ``pydantic.ValidationError`` (extra="forbid").

- ``Word`` — ``{text: str, bbox: NormBBox}``.

- ``PageWords`` — per-page words+boxes layer:
    * ``page_index: int`` (0-based),
    * ``source: Literal["text_layer", "ocr"]`` — which path produced this page,
    * ``render_dpi: int`` (== RENDER_DPI == 200),
    * ``page_pixel_dims: tuple[int, int]`` — (width_px, height_px) at 200 DPI,
      non-degenerate (both > 0),
    * ``words: list[Word]``,
    * ``unreadable: bool`` — True ONLY when the per-page OCR was KILLED by the hard
      timeout (AC-4); False on every normal page.

- ``WordsBoxes`` — document-level: ``{pages: list[PageWords]}``.

- ``read_words_and_boxes(pdf_path, *, ocr_runner=None, per_page_ocr_timeout_s=<default>)
  -> WordsBoxes`` — the single entry point:
    * text-layer first (pypdfium2/pdfplumber), normalized by the media box with y
      FLIPPED (PDF space is y-up → canonical is y-down);
    * a junk-text-layer DENSITY heuristic routes a page to OCR (AC-3),
      recording ``source="ocr"``;
    * the OCR path renders the page via pypdfium2 at 200 DPI → Tesseract, normalized by
      pixel dims;
    * each OCR page runs under a HARD per-page timeout that genuinely KILLS a runaway
      (process-based, not a non-cancellable thread). The kill mechanism
      (multiprocessing / subprocess / signal) is the implementer's choice — these tests
      pin only the OBSERVABLE behavior.
    * ``ocr_runner`` — INJECTABLE OCR seam (default = the real Tesseract runner). The
      AC-4 test injects a MODULE-LEVEL fake slow runner (defined at top level below so it
      is importable/picklable should the implementer spawn a process) plus a tiny
      ``per_page_ocr_timeout_s`` to deterministically trigger the kill.

All content is synthetic and NON-CLINICAL; no PHI, no network, no live services, no
secrets. OCR-dependent assertions are tesseract-version-tolerant: geometry within the
named ``NORMBBOX_TOL`` on large clear synthetic glyphs, never exact pixel/text equality
beyond the known synthetic markers.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import time
from pathlib import Path

import pytest

# --- fixture paths (implementer AUTHORS these under agent/evals/fixtures/documents/;
#     these tests only REFERENCE the committed paths). Resolve robustly from __file__,
#     never a hardcoded absolute path: tests/ -> agent/ -> evals/fixtures/documents/. ---

_AGENT_ROOT = Path(__file__).resolve().parents[1]
_DOCUMENTS_DIR = _AGENT_ROOT / "evals" / "fixtures" / "documents"
CLEAN_PDF = _DOCUMENTS_DIR / "clean.pdf"          # born-digital ("clean") seed fixture
DEGRADED_PDF = _DOCUMENTS_DIR / "degraded.pdf"    # degraded scan-style seed (seeded noise)
JUNK_LAYER_PDF = _DOCUMENTS_DIR / "junk_layer.pdf"  # garbage text layer at plausible pos.
GENERATOR = _DOCUMENTS_DIR / "generate_fixtures.py"  # reproducible generator (AC-7)

# --- tolerances / constants -----------------------------------------------------------

# Per-coordinate normalized tolerance for cross-engine bbox agreement (AC-1). Two wholly
# independent engines — pdfium/pdfplumber reading the vector text layer vs. Tesseract
# reading a 200-DPI raster of the same page — are NEVER bit-equal: rasterization rounds
# glyph edges to whole pixels, Tesseract's box is the ink bounding box while the text
# layer's box is the font advance/ascent box, and antialiasing shifts edges by a fraction
# of a pixel. At 200 DPI on US-Letter (~1700x2200 px) one pixel is ~0.0006 of the page;
# 0.02 (~34 px width / ~44 px height) comfortably covers glyph-metric + raster rounding
# on large clear synthetic glyphs while still catching a real coordinate-space bug (a
# missing y-flip is a ~0.5+ error; a swapped axis is gross). This is an EXPLICIT named
# constant, not a hidden fudge factor.
NORMBBOX_TOL = 0.02

# A word placed near the TOP of the page must yield a SMALL normalized y on BOTH paths.
# This is the y-DOWN proof (AC-2): the PDF path (native y-up) must have flipped y, or a
# top-of-page word would come back with a LARGE y. Generously below the midline so the
# assertion is robust to fixture layout, but well under 0.5.
TOP_OF_PAGE_Y_MAX = 0.35

# Reader deps whose license we assert are permissive Apache/BSD/MIT-family (AC-5 scope).
# Distribution names as seen by importlib.metadata.
READER_DEPS = ("pypdfium2", "pdfplumber", "pdfminer.six", "pillow", "pytesseract")

# Permissive identifiers we accept for the reader stack. Substring match, case-insensitive,
# against the dist's declared license (License-Expression, License field, or a
# "License :: OSI Approved :: ..." classifier). Apache/BSD/MIT family only.
PERMISSIVE_LICENSE_MARKERS = (
    "mit",
    "bsd",
    "apache",
)

# Explicit allowlist for a permissive-EQUIVALENT identifier that is not literally in the
# Apache/BSD/MIT family but is non-copyleft and accepted (AC-5, proposed). Each entry
# carries a justification. pillow ships as "MIT-CMU" (a.k.a. HPND — Historical Permission
# Notice and Disclaimer): a permissive, non-copyleft, MIT-derived license. It is the
# required imaging dependency of the pdfplumber/pytesseract stack and mirrors the
# agent/pyproject.toml W2-M1 allowlist note.
LICENSE_ALLOWLIST: dict[str, str] = {
    # dist name -> justification
    "pillow": "MIT-CMU/HPND — permissive, non-copyleft, MIT-derived; imaging dep of the "
              "pdfplumber/pytesseract reader stack (mirrors pyproject W2-M1 allowlist).",
}

# Forbidden copyleft identifiers for the reader stack — an AGPL/GPL/LGPL leak here is the
# exact W2-R6 violation (PyMuPDF is AGPL and is why it is banned).
FORBIDDEN_COPYLEFT_MARKERS = ("agpl", "gpl", "lgpl")

# Known synthetic, NON-CLINICAL content markers the generator must emit (AC-7). No PHI,
# no real-looking patient data — deliberately obvious placeholder tokens.
SYNTHETIC_CONTENT_MARKERS = ("SYNTHETIC", "NON-CLINICAL", "FIXTURE")

# Strings that must NEVER appear in a synthetic fixture (a coarse PHI/clinical tripwire).
FORBIDDEN_CLINICAL_MARKERS = (
    "metformin",
    "diagnosis",
    "patient name",
    "date of birth",
    "mrn",
    "ssn",
)

# Wall-clock upper bound for the AC-4 timeout call: a tiny per-page timeout over a handful
# of pages, plus kill + remaining-page overhead, must finish FAST. If the kill mechanism
# is broken (a non-cancellable thread, a missing join timeout), the fake runner's long
# sleep would blow past this — so the bound makes a HANG fail the test instead of blocking.
AC4_WALLCLOCK_BUDGET_S = 20.0

# Fed to the injected fake runner: far larger than the per-page timeout AND larger than
# the wall-clock budget, so if the "kill" merely waits it out, the test fails on time.
FAKE_OCR_SLEEP_S = 120.0
TINY_OCR_TIMEOUT_S = 0.5


# --- module-level fake OCR runner (AC-4 seam) -----------------------------------------
# Defined at top level (NOT a closure/lambda) so it is importable and PICKLABLE — the
# implementer may run each OCR page in a spawned process, which requires a picklable
# target. It must genuinely block for FAKE_OCR_SLEEP_S so only a real KILL (not a thread
# that finishes on its own) can end it within the wall-clock budget.


def slow_ocr_runner(*args: object, **kwargs: object) -> object:
    """A pathological OCR runner: blocks far past any sane per-page timeout.

    Signature is intentionally permissive (``*args, **kwargs``) so it satisfies whatever
    call shape the implementer's real Tesseract runner uses without pinning it here.
    """
    time.sleep(FAKE_OCR_SLEEP_S)
    raise AssertionError(
        "slow_ocr_runner returned — the per-page OCR timeout did NOT kill the runaway"
    )


# --- helpers --------------------------------------------------------------------------


def _require_fixture(path: Path) -> Path:
    """Fail clearly (legitimate RED until the implementer authors it) if a fixture is
    missing, rather than erroring obscurely deep in a reader call."""
    if not path.exists():
        pytest.fail(
            f"required fixture missing: {path} "
            "(implementer authors it under agent/evals/fixtures/documents/)"
        )
    return path


def _all_boxes(words_boxes: object) -> list[object]:
    boxes: list[object] = []
    for page in words_boxes.pages:  # type: ignore[attr-defined]
        for word in page.words:
            boxes.append(word.bbox)
    return boxes


def _assert_canonical(bbox: object) -> None:
    """Every NormBBox obeys the §2 canonical-space invariants."""
    for coord_name in ("x0", "y0", "x1", "y1"):
        value = getattr(bbox, coord_name)
        assert isinstance(value, float), f"{coord_name} must be a float"
        assert 0.0 <= value <= 1.0, f"{coord_name}={value} escaped [0,1]"
    assert bbox.x0 < bbox.x1, "degenerate/inverted box: x0 !< x1"  # type: ignore[attr-defined]
    assert bbox.y0 < bbox.y1, "degenerate/inverted box: y0 !< y1"  # type: ignore[attr-defined]


def _find_word(words_boxes: object, needle: str) -> object:
    """Return the first Word whose text contains ``needle`` (case-insensitive), across all
    pages. tesseract-version-tolerant: substring, not exact-equality, match."""
    needle_l = needle.lower()
    for page in words_boxes.pages:  # type: ignore[attr-defined]
        for word in page.words:
            if needle_l in word.text.lower():
                return word
    raise AssertionError(f"word containing {needle!r} not found in output")


def _declared_licenses(dist_name: str) -> str:
    """All license signals a dist declares, lowercased and joined: the License-Expression
    (PEP 639), the legacy License field, and every 'License :: ...' classifier. Robust to
    the fact that different reader deps expose their license via different metadata keys."""
    metadata = importlib_metadata.metadata(dist_name)
    signals: list[str] = []
    for key in ("License-Expression", "License"):
        value = metadata.get(key)
        if value:
            signals.append(str(value))
    for classifier in metadata.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            signals.append(classifier)
    return " ".join(signals).lower()


# ======================================================================================
# AC-1 — dual-path NormBBox equivalence within an explicit named tolerance
# ======================================================================================


def test_same_word_text_layer_and_ocr_agree_within_tolerance():
    # spec(W2-M4:AC-1)
    # guards: the two readers silently emitting DIFFERENT coordinate spaces — a text-layer
    # box in one convention and an OCR box in another would make every downstream overlay
    # land in the wrong place depending on which path fired.
    from app.ingestion.reader import read_words_and_boxes

    _require_fixture(CLEAN_PDF)

    # Force the SAME clean fixture through each path independently: the text-layer path is
    # the default; the OCR path is forced by making the text-layer look empty to the
    # heuristic is not available here, so we assert the seam by reading twice — once
    # normally (text_layer) and once with the real OCR runner over the rendered page.
    text_result = read_words_and_boxes(CLEAN_PDF)

    # A word we know the generator places on the clean page (synthetic marker, AC-7).
    marker = SYNTHETIC_CONTENT_MARKERS[0]
    text_word = _find_word(text_result, marker)
    assert text_result.pages, "text-layer path produced no pages"
    # The clean fixture must resolve via the text layer, not OCR.
    assert any(p.source == "text_layer" for p in text_result.pages), (
        "born-digital clean fixture did not resolve via the text-layer path"
    )

    # Now the OCR path over the SAME page's raster. The reader exposes OCR via the same
    # entry point; a page with a suppressed/undetectable text layer routes to OCR. The
    # degraded fixture is the scan-style companion carrying the SAME synthetic marker, so
    # its OCR box for that marker is the cross-engine comparand at 200 DPI.
    _require_fixture(DEGRADED_PDF)
    ocr_result = read_words_and_boxes(DEGRADED_PDF)
    assert any(p.source == "ocr" for p in ocr_result.pages), (
        "degraded scan fixture did not resolve via the OCR path"
    )
    ocr_word = _find_word(ocr_result, marker)

    # Both boxes are canonical, and agree per-coordinate within the named tolerance.
    _assert_canonical(text_word.bbox)
    _assert_canonical(ocr_word.bbox)
    for coord_name in ("x0", "y0", "x1", "y1"):
        delta = abs(getattr(text_word.bbox, coord_name) - getattr(ocr_word.bbox, coord_name))
        assert delta <= NORMBBOX_TOL, (
            f"{coord_name}: |text-layer - ocr| = {delta:.4f} exceeds NORMBBOX_TOL="
            f"{NORMBBOX_TOL} — the two engines are not in one NormBBox space"
        )


# ======================================================================================
# AC-2 — canonical-space invariants on BOTH paths over the degraded fixture; y-flip proof;
#         render_dpi==200 + non-degenerate page_pixel_dims recorded
# ======================================================================================


def test_canonical_invariants_and_yflip_across_text_and_ocr_paths():
    # spec(W2-M4:AC-2)
    # guards: a page emitted in PDF-native y-UP (no flip), coordinates leaking outside
    # [0,1], inverted boxes, or a page that forgot to record its render DPI / pixel dims —
    # any of which corrupts the one canonical space §2 depends on.
    from app.ingestion.reader import RENDER_DPI, read_words_and_boxes

    assert RENDER_DPI == 200, "RENDER_DPI is locked to 200 (§2)"

    _require_fixture(CLEAN_PDF)
    _require_fixture(DEGRADED_PDF)

    # BOTH paths are load-bearing: the clean fixture exercises the text-layer path, the
    # committed degraded scan fixture exercises the OCR path (Tesseract at 200 DPI).
    text_result = read_words_and_boxes(CLEAN_PDF)
    ocr_result = read_words_and_boxes(DEGRADED_PDF)

    assert any(p.source == "text_layer" for p in text_result.pages)
    assert any(p.source == "ocr" for p in ocr_result.pages)

    # Every box on every page of both documents obeys the canonical invariants.
    for words_boxes in (text_result, ocr_result):
        boxes = _all_boxes(words_boxes)
        assert boxes, "a path produced no boxes to validate"
        for bbox in boxes:
            _assert_canonical(bbox)

    # Each page records the locked render DPI and non-degenerate pixel dims.
    for words_boxes in (text_result, ocr_result):
        for page in words_boxes.pages:
            assert page.render_dpi == 200, "page did not record render_dpi==200"
            width_px, height_px = page.page_pixel_dims
            assert isinstance(width_px, int) and isinstance(height_px, int)
            assert width_px > 0 and height_px > 0, "degenerate page_pixel_dims"

    # y-DOWN proof: a word the generator places near the TOP of the page must have a SMALL
    # normalized y on BOTH paths. If the PDF path had NOT flipped y (PDF is y-up), this
    # same top word would come back with a LARGE y — so a small-y on the text-layer path
    # is the flip, demonstrated.
    top_marker = "TOP"  # generator places the literal token "TOP" at the page top (AC-7)
    text_top = _find_word(text_result, top_marker)
    ocr_top = _find_word(ocr_result, top_marker)
    assert text_top.bbox.y0 < TOP_OF_PAGE_Y_MAX, (
        f"text-layer top-of-page word has y0={text_top.bbox.y0:.3f}; expected small "
        f"(< {TOP_OF_PAGE_Y_MAX}) — the PDF path did not flip y-up → y-down"
    )
    assert ocr_top.bbox.y0 < TOP_OF_PAGE_Y_MAX, (
        f"OCR top-of-page word has y0={ocr_top.bbox.y0:.3f}; expected small "
        f"(< {TOP_OF_PAGE_Y_MAX}) — OCR pixel normalization is not top-left y-down"
    )


# ======================================================================================
# AC-3 — junk text layer routes to OCR (source == "ocr")
# ======================================================================================


def test_junk_text_layer_routes_to_ocr():
    # spec(W2-M4:AC-3)
    # guards: the density heuristic being absent or too lenient — a page with a garbage
    # text layer at plausible positions that gets TRUSTED would feed nonsense boxes
    # downstream instead of falling back to OCR (W2-D3 junk-layer sanity check).
    from app.ingestion.reader import read_words_and_boxes

    _require_fixture(JUNK_LAYER_PDF)
    result = read_words_and_boxes(JUNK_LAYER_PDF)

    assert result.pages, "junk-layer fixture produced no pages"
    # The page(s) with the garbage text layer must be routed to OCR.
    assert any(page.source == "ocr" for page in result.pages), (
        "junk-text-layer page was trusted (source != 'ocr') — the density heuristic did "
        "not route it to OCR"
    )


# ======================================================================================
# AC-4 — injected slow OCR runner + tiny timeout → that page .unreadable, others still
#         process, bounded wall-clock (never a hang)
# ======================================================================================


def test_slow_ocr_runner_is_killed_page_marked_unreadable_no_hang():
    # spec(W2-M4:AC-4)
    # guards: a pathological OCR page HANGING the whole ingestion job — a non-cancellable
    # thread or a missing hard kill would block the request forever and burn the worker,
    # instead of marking one page unreadable and moving on.
    from app.ingestion.reader import read_words_and_boxes

    # Route to OCR (junk layer or degraded scan), so the injected runner is actually used.
    _require_fixture(JUNK_LAYER_PDF)

    started = time.monotonic()
    result = read_words_and_boxes(
        JUNK_LAYER_PDF,
        ocr_runner=slow_ocr_runner,
        per_page_ocr_timeout_s=TINY_OCR_TIMEOUT_S,
    )
    elapsed = time.monotonic() - started

    # Bounded wall-clock: the runner sleeps FAKE_OCR_SLEEP_S (120s); if the kill were fake
    # (merely waiting it out, or a thread that can't be cancelled) this blows the budget.
    assert elapsed < AC4_WALLCLOCK_BUDGET_S, (
        f"OCR timeout took {elapsed:.1f}s (budget {AC4_WALLCLOCK_BUDGET_S}s) — the runaway "
        "page was NOT hard-killed; a non-cancellable thread or missing kill hangs the job"
    )

    assert result.pages, "timeout path produced no pages at all"
    # The killed OCR page is marked unreadable with the typed outcome on PageWords.
    unreadable = [page for page in result.pages if page.unreadable]
    assert unreadable, (
        "no page was marked .unreadable — the killed OCR page must carry the typed "
        "unreadable outcome, not be silently dropped or errored"
    )
    for page in unreadable:
        assert page.source == "ocr", "an unreadable page must be an OCR-path page"
        assert page.words == [], "a killed/unreadable page must not fabricate words"

    # Remaining pages still process: every page is accounted for and non-unreadable pages
    # are normal readable pages (the killed page did not poison the rest of the document).
    for page in result.pages:
        if not page.unreadable:
            assert page.render_dpi == 200


# ======================================================================================
# AC-5 — reader-stack license verification: permissive family, PyMuPDF absent, no copyleft
# ======================================================================================


def test_reader_deps_are_permissive_and_pymupdf_is_absent():
    # spec(W2-M4:AC-5)
    # guards: a GPL/AGPL dependency (the exact reason PyMuPDF is banned, W2-R6) sneaking
    # into the reader stack, or PyMuPDF/fitz being present at all — either would poison the
    # license posture of the whole shipped agent.
    for dist_name in READER_DEPS:
        declared = _declared_licenses(dist_name)
        assert declared, (
            f"{dist_name} declared no license metadata at all — cannot verify AC-5"
        )

        # No copyleft identifier anywhere in the reader stack.
        for forbidden in FORBIDDEN_COPYLEFT_MARKERS:
            assert forbidden not in declared, (
                f"{dist_name} declares a copyleft license ({forbidden!r} in {declared!r}) "
                "— forbidden for the reader stack (W2-R6)"
            )

        # Either an Apache/BSD/MIT-family marker, or an explicit allowlist entry.
        permissive = any(marker in declared for marker in PERMISSIVE_LICENSE_MARKERS)
        allowlisted = dist_name in LICENSE_ALLOWLIST
        assert permissive or allowlisted, (
            f"{dist_name} license {declared!r} is neither Apache/BSD/MIT-family nor on the "
            f"justified allowlist {sorted(LICENSE_ALLOWLIST)}"
        )
        # A dist may only be on the allowlist WITH a justification comment / value.
        if allowlisted:
            assert LICENSE_ALLOWLIST[dist_name].strip(), (
                f"{dist_name} allowlist entry must carry a justification"
            )

    # PyMuPDF is ABSENT from the environment: the import fails AND no dist metadata exists
    # under either of its distribution names.
    with pytest.raises(ImportError):
        import fitz  # noqa: F401  (PyMuPDF's import name — must NOT be installed)

    for banned_dist in ("PyMuPDF", "fitz"):
        with pytest.raises(importlib_metadata.PackageNotFoundError):
            importlib_metadata.metadata(banned_dist)


# ======================================================================================
# AC-7 — generator reproducibility (single deterministic branch) + synthetic-content check
# ======================================================================================


def test_generator_reproduces_seed_fixtures_geometry_identical_and_content_synthetic():
    # spec(W2-M4:AC-7)
    # CHOSEN reproducibility branch (single, deterministic): GEOMETRY-IDENTICAL. Re-running
    # the committed generator with its fixed seed reproduces the two seed fixtures such that
    # the reader emits the SAME NormBBox geometry for the same synthetic words (within
    # NORMBBOX_TOL on large clear glyphs). Byte/hash-stability is NOT asserted here — PDF
    # producers embed timestamps/xref noise that make byte-equality brittle across library
    # versions; geometry-identity is the robust, single pass condition (per ticket Context:
    # "geometry-identical is the fallback assertion if byte-stability proves infeasible").
    # guards: a non-reproducible generator (unseeded noise, wandering layout) that makes the
    # frozen geometry assertions non-deterministic, and any PHI/clinical string leaking into
    # a fixture that is supposed to be purely synthetic.
    import hashlib
    import runpy
    import shutil
    import tempfile

    from app.ingestion.reader import read_words_and_boxes

    _require_fixture(GENERATOR)
    _require_fixture(CLEAN_PDF)
    _require_fixture(DEGRADED_PDF)

    # Content safety: every committed fixture carries the synthetic markers and NONE of the
    # forbidden clinical/PHI strings (checked over the reader's extracted text — the OCR
    # path is tesseract-version-tolerant, so the marker check is substring, never exact).
    for fixture in (CLEAN_PDF, DEGRADED_PDF):
        result = read_words_and_boxes(fixture)
        text = " ".join(
            word.text for page in result.pages for word in page.words
        ).lower()
        assert any(marker.lower() in text for marker in SYNTHETIC_CONTENT_MARKERS), (
            f"{fixture.name} carries no synthetic content marker "
            f"{SYNTHETIC_CONTENT_MARKERS} — cannot prove it is non-clinical"
        )
        for forbidden in FORBIDDEN_CLINICAL_MARKERS:
            assert forbidden not in text, (
                f"{fixture.name} contains forbidden clinical/PHI string {forbidden!r}"
            )

    # Reproducibility (GEOMETRY-IDENTICAL branch): regenerate into a scratch dir with the
    # generator's fixed seed, then compare the reader's geometry against the committed
    # fixtures. Baseline geometry from the committed fixtures:
    def _geometry(words_boxes: object) -> list[tuple[str, tuple[float, float, float, float]]]:
        geom: list[tuple[str, tuple[float, float, float, float]]] = []
        for page in words_boxes.pages:  # type: ignore[attr-defined]
            for word in page.words:
                bbox = word.bbox
                geom.append((word.text, (bbox.x0, bbox.y0, bbox.x1, bbox.y1)))
        return geom

    committed_clean_geom = _geometry(read_words_and_boxes(CLEAN_PDF))
    assert committed_clean_geom, "clean fixture yielded no geometry to compare"

    with tempfile.TemporaryDirectory() as scratch:
        scratch_dir = Path(scratch)
        # The generator writes its fixtures next to itself by default; copy it into the
        # scratch dir and run it there so regeneration does not touch committed fixtures.
        local_generator = scratch_dir / GENERATOR.name
        shutil.copy2(GENERATOR, local_generator)
        runpy.run_path(str(local_generator), run_name="__main__")

        regenerated_clean = scratch_dir / CLEAN_PDF.name
        assert regenerated_clean.exists(), (
            "generator (fixed seed) did not reproduce clean.pdf in a fresh dir"
        )
        regenerated_geom = _geometry(read_words_and_boxes(regenerated_clean))

    # Geometry-identical: same words in the same order, each box within NORMBBOX_TOL.
    assert len(regenerated_geom) == len(committed_clean_geom), (
        "regenerated clean fixture has a different word count than the committed one — "
        "the generator is not deterministic under its fixed seed"
    )
    for (word_a, box_a), (word_b, box_b) in zip(regenerated_geom, committed_clean_geom):
        assert word_a == word_b, (
            f"regenerated word {word_a!r} != committed {word_b!r} (non-deterministic order)"
        )
        for coord_regen, coord_committed, coord_name in zip(box_a, box_b, ("x0", "y0", "x1", "y1")):
            delta = abs(coord_regen - coord_committed)
            assert delta <= NORMBBOX_TOL, (
                f"{word_a!r} {coord_name}: regenerated vs committed |Δ|={delta:.4f} exceeds "
                f"NORMBBOX_TOL={NORMBBOX_TOL} — geometry not reproduced under the fixed seed"
            )

    # (hashlib imported to make explicit we deliberately did NOT choose the byte/hash-stable
    # branch — a single deterministic pass condition is asserted above, per the docstring.)
    _ = hashlib


# ======================================================================================
# NormBBox contract — the frozen strict model rejects invalid geometry (supports AC-1/2)
# ======================================================================================


def test_normbbox_is_frozen_strict_and_rejects_invalid_geometry():
    # spec(W2-M4:AC-2)
    # guards: a permissive NormBBox (plain dataclass / extra="allow" / no range checks)
    # that would let an out-of-range or inverted box into the canonical space unnoticed and
    # corrupt every downstream overlay. Pins the model shape W2-M6 will unify.
    import pydantic

    from app.ingestion.reader import NormBBox

    # A valid canonical box constructs and is frozen (immutable).
    box = NormBBox(x0=0.1, y0=0.1, x1=0.4, y1=0.2)
    assert (box.x0, box.y0, box.x1, box.y1) == (0.1, 0.1, 0.4, 0.2)
    with pytest.raises(pydantic.ValidationError):
        box.x0 = 0.9  # frozen model: mutation rejected  # type: ignore[misc]

    # Out-of-range coordinates are rejected (must be ∈ [0,1]).
    with pytest.raises(pydantic.ValidationError):
        NormBBox(x0=-0.01, y0=0.1, x1=0.4, y1=0.2)
    with pytest.raises(pydantic.ValidationError):
        NormBBox(x0=0.1, y0=0.1, x1=1.01, y1=0.2)

    # Inverted / degenerate boxes are rejected (need x0 < x1 and y0 < y1).
    with pytest.raises(pydantic.ValidationError):
        NormBBox(x0=0.4, y0=0.1, x1=0.1, y1=0.2)  # x0 > x1
    with pytest.raises(pydantic.ValidationError):
        NormBBox(x0=0.1, y0=0.2, x1=0.4, y1=0.2)  # y0 == y1 (degenerate)

    # Unknown fields are rejected (extra="forbid").
    with pytest.raises(pydantic.ValidationError):
        NormBBox(x0=0.1, y0=0.1, x1=0.4, y1=0.2, smuggled=1)  # type: ignore[call-arg]
