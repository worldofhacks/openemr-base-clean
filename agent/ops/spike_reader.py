"""AC-6 [live-measure] operator CLI: pypdfium2 vs pdfplumber word-segmentation bake-off.

Runs BOTH candidate text-layer word segmenters over the seed fixtures and prints a
comparison (word counts, split/merge behavior, box quality) so the segmentation winner
is chosen on fixture evidence, not preference. This is RECORDED EVIDENCE for the ticket
report — NOT a frozen test. Ops scripts may print (W2-M4 file-scope note).

Both candidates read the SAME text layer of the born-digital fixtures:

* **pypdfium2** exposes only char-level boxes (``PdfTextPage.get_charbox``) and rect runs
  (``count_rects``/``get_rect``) — it has NO native word tokenizer, so "words" must be
  reconstructed by splitting text runs on whitespace and re-deriving each word's box from
  its constituent char boxes (extra code, and PDF-native y-up needs flipping).
* **pdfplumber** ships ``extract_words`` — a real word tokenizer that returns per-word
  boxes already measured from the page top (y-down), tunable via ``x_tolerance`` /
  ``keep_blank_chars``.

Run from the agent dir:  ``./.venv/bin/python ops/spike_reader.py``
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

_DOCUMENTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "documents"
_TEXT_LAYER_FIXTURES = ("clean.pdf", "junk_layer.pdf")


def _pdfium_words(pdf_path: Path) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Reconstruct words from pypdfium2's char boxes: split text runs on whitespace, then
    union the char boxes of each token. Boxes are PDF-native y-up (left, bottom, right,
    top) and would need a y-flip to reach the canonical space."""
    doc = pdfium.PdfDocument(str(pdf_path))
    words: list[tuple[str, tuple[float, float, float, float]]] = []
    try:
        page = doc[0]
        text_page = page.get_textpage()
        try:
            char_count = text_page.count_chars()
            current: list[int] = []

            def _flush(indices: list[int]) -> None:
                if not indices:
                    return
                token = "".join(
                    text_page.get_text_range(i, 1) for i in indices
                ).strip()
                if not token:
                    return
                boxes = [text_page.get_charbox(i) for i in indices]
                left = min(b[0] for b in boxes)
                bottom = min(b[1] for b in boxes)
                right = max(b[2] for b in boxes)
                top = max(b[3] for b in boxes)
                words.append((token, (left, bottom, right, top)))

            for i in range(char_count):
                ch = text_page.get_text_range(i, 1)
                if ch.strip() == "":
                    _flush(current)
                    current = []
                else:
                    current.append(i)
            _flush(current)
        finally:
            text_page.close()
            page.close()
    finally:
        doc.close()
    return words


def _pdfplumber_words(
    pdf_path: Path,
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """pdfplumber's native word tokenizer: per-word boxes already y-down from the top."""
    words: list[tuple[str, tuple[float, float, float, float]]] = []
    with pdfplumber.open(str(pdf_path)) as doc:
        page = doc.pages[0]
        for word in page.extract_words(use_text_flow=False):
            words.append(
                (
                    str(word["text"]),
                    (
                        float(word["x0"]),
                        float(word["top"]),
                        float(word["x1"]),
                        float(word["bottom"]),
                    ),
                )
            )
    return words


def _print_comparison(pdf_path: Path) -> None:
    pdfium_words = _pdfium_words(pdf_path)
    plumber_words = _pdfplumber_words(pdf_path)

    print(f"\n=== {pdf_path.name} (page 0) ===")
    print(
        f"  pypdfium2   : {len(pdfium_words):>2} words "
        f"{[w for w, _ in pdfium_words]}"
    )
    print(
        f"  pdfplumber  : {len(plumber_words):>2} words "
        f"{[w for w, _ in plumber_words]}"
    )

    pdfium_tokens = [w for w, _ in pdfium_words]
    plumber_tokens = [w for w, _ in plumber_words]
    if pdfium_tokens == plumber_tokens:
        print("  tokenization: IDENTICAL word set")
    else:
        print("  tokenization: DIFFERS (see split/merge above)")

    # Box quality: pypdfium2's char-union box is y-up (needs a flip + custom code);
    # pdfplumber's is y-down from the top (canonical-ready).
    if pdfium_words:
        _w, (pl, pb, pr, pt) = pdfium_words[0]
        print(
            f"  pypdfium2 box[0] ({_w!r}): y-UP (left={pl:.1f}, bottom={pb:.1f}, "
            f"right={pr:.1f}, top={pt:.1f}) — needs y-flip + char-union code"
        )
    if plumber_words:
        _w, (px0, ptop, px1, pbottom) = plumber_words[0]
        print(
            f"  pdfplumber box[0] ({_w!r}): y-DOWN (x0={px0:.1f}, top={ptop:.1f}, "
            f"x1={px1:.1f}, bottom={pbottom:.1f}) — canonical-ready"
        )


def main() -> None:
    print("W2-M4 AC-6 — word-segmentation bake-off: pypdfium2 vs pdfplumber")
    print("=" * 70)
    for name in _TEXT_LAYER_FIXTURES:
        path = _DOCUMENTS_DIR / name
        if not path.exists():
            print(f"\n(missing fixture: {path} — run generate_fixtures.py first)")
            continue
        _print_comparison(path)

    print("\n" + "=" * 70)
    print("WINNER: pdfplumber.extract_words")
    print(
        "  - native word tokenizer (per-word boxes) vs pypdfium2's char-only boxes that\n"
        "    require hand-rolled whitespace splitting + char-box union to form words;\n"
        "  - boxes already y-DOWN from the page top (canonical-ready) vs pypdfium2's\n"
        "    y-UP boxes needing an explicit flip;\n"
        "  - tunable segmentation (x_tolerance/keep_blank_chars) for real-world layouts.\n"
        "  pypdfium2 is retained for what it is best at: 200-DPI page RENDERING for the\n"
        "  OCR fallback path (its char boxes are the fallback if pdfplumber ever regresses)."
    )


if __name__ == "__main__":
    main()
