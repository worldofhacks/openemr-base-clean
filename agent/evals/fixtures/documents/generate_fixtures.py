"""Reproducible generator for the W2-M4 seed document fixtures (AC-7).

Deterministic under a fixed seed: re-running reproduces the same synthetic PDFs such
that the reader (`app.ingestion.reader`) emits the SAME NormBBox geometry for the same
words (geometry-identical branch — byte-stability is NOT relied upon, per the ticket
Context). All content is SYNTHETIC and NON-CLINICAL — obvious placeholder tokens only;
never any PHI or real-looking patient data.

Three fixtures, all driven from ONE shared layout table so the text-layer box and the
OCR ink box for the same word coincide within the reader's tolerance:

* ``clean.pdf``     — born-digital: a hand-emitted PDF with a real Helvetica text layer.
* ``degraded.pdf``  — scan-style: the SAME layout rasterized at 200 DPI with light
                      SEEDED noise, saved as an image-only PDF (no text layer → the
                      reader's density heuristic routes it to OCR).
* ``junk_layer.pdf`` — multi-page: page 0 is a normal readable text-layer page; page 1
                      has a garbage text layer at plausible positions (heuristic rejects
                      it → OCR) with real glyphs rendered so Tesseract has ink to read.

Writes its outputs NEXT TO this file. Generation runs only under ``__main__`` so the
AC-7 test can copy this script into a scratch dir and run it there via ``runpy``.
"""

from __future__ import annotations

import random
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- page + render geometry (must match the reader's RENDER_DPI = 200) -----------------

_POINTS_PER_INCH = 72
_RENDER_DPI = 200
_PX_PER_PT = _RENDER_DPI / _POINTS_PER_INCH

# US-Letter, in PDF points.
_PAGE_W_PT = 612.0
_PAGE_H_PT = 792.0
_PAGE_W_PX = round(_PAGE_W_PT * _PX_PER_PT)   # 1700
_PAGE_H_PX = round(_PAGE_H_PT * _PX_PER_PT)   # 2200

# Deterministic noise seed for the degraded raster.
_SEED = 20260714

# Clear glyphs at a size chosen empirically for cross-engine agreement: the text-layer
# box is the font ADVANCE box while OCR gives the INK box, and that gap grows with word
# width. 24 pt keeps the widest coordinate delta on the AC-1 comparand ("SYNTHETIC") at
# ~0.009 of page width — comfortably inside the reader's 0.02 tolerance — while staying
# large enough that Tesseract reads every marker cleanly through the seeded scan noise.
_FONT_SIZE_PT = 24.0


# --- shared layout table --------------------------------------------------------------
# Each entry: (text, x_left_pt, baseline_y_from_top_pt). The baseline is measured from
# the PAGE TOP (y-down) so the same number drives both the raster (PIL, y-down) and the
# PDF text stream (converted to PDF y-up as page_height - baseline). One table = one
# geometry for all fixtures.

_LAYOUT: tuple[tuple[str, float, float], ...] = (
    ("TOP", 72.0, 110.0),          # near the page top → small normalized y (y-flip proof)
    ("SYNTHETIC", 72.0, 230.0),    # AC-1 cross-engine comparand (markers[0])
    ("NON-CLINICAL", 72.0, 350.0),
    ("FIXTURE", 72.0, 470.0),
)


# --- minimal hand-emitted PDF (born-digital text layer) -------------------------------


def _pdf_escape(text: str) -> str:
    """Escape a string literal for a PDF ``(...)`` content-stream operand."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream() -> bytes:
    """A PDF content stream placing every layout word with Helvetica. PDF text space is
    y-UP, so the baseline (measured from the top) is emitted as ``page_h - baseline``."""
    parts: list[str] = ["BT", f"/F1 {_FONT_SIZE_PT:.2f} Tf"]
    for text, x_left, baseline_from_top in _LAYOUT:
        y_up = _PAGE_H_PT - baseline_from_top
        parts.append(f"1 0 0 1 {x_left:.2f} {y_up:.2f} Tm")
        parts.append(f"({_pdf_escape(text)}) Tj")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("ascii")


def _write_clean_pdf(out_path: Path) -> None:
    """Emit a minimal single-page PDF by hand with a real Helvetica text layer.

    Timestamps/metadata are pinned (none emitted) so regeneration is stable; the xref is
    built from measured byte offsets. Geometry — not bytes — is the asserted invariant.
    """
    content = _content_stream()

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {_PAGE_W_PT:.2f} {_PAGE_H_PT:.2f}] "
            f"/Resources << /Font << /F1 5 0 R >> >> "
            f"/Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]

    _write_pdf(out_path, objects)


def _write_pdf(out_path: Path, object_bodies: list[bytes]) -> None:
    """Assemble a PDF from numbered object bodies with a correct xref table. No dates,
    no /ID, no producer string — nothing time-varying, so bytes are stable too."""
    header = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    out = bytearray(header)
    offsets: list[int] = []
    for index, body in enumerate(object_bodies, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_pos = len(out)
    count = len(object_bodies) + 1
    out += f"xref\n0 {count}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        b"trailer\n"
        + f"<< /Size {count} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_pos).encode("ascii")
        + b"\n%%EOF\n"
    )
    out_path.write_bytes(bytes(out))


# --- rasterized (scan-style) fixture --------------------------------------------------


def _load_font() -> ImageFont.FreeTypeFont:
    """A scalable font sized to the 200-DPI equivalent of ``_FONT_SIZE_PT``. Pillow's
    bundled default font is used (deterministic across environments, no system fonts)."""
    size_px = round(_FONT_SIZE_PT * _PX_PER_PT)
    return ImageFont.load_default(size=size_px)


def _draw_layout(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont) -> None:
    """Draw every layout word at its 200-DPI pixel position. The PDF baseline (from top)
    maps to the glyph baseline; PIL's ``anchor='ls'`` draws from the left baseline so the
    ink aligns with the text-layer box."""
    for text, x_left, baseline_from_top in _LAYOUT:
        x_px = round(x_left * _PX_PER_PT)
        y_px = round(baseline_from_top * _PX_PER_PT)
        draw.text((x_px, y_px), text, fill=(0, 0, 0), font=font, anchor="ls")


def _write_degraded_pdf(out_path: Path) -> None:
    """Rasterize the shared layout at 200 DPI, add light SEEDED noise, save image-only."""
    rng = random.Random(_SEED)
    image = Image.new("RGB", (_PAGE_W_PX, _PAGE_H_PX), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    _draw_layout(draw, _load_font())

    _apply_seeded_noise(image, rng)
    image.save(out_path, "PDF", resolution=float(_RENDER_DPI))


def _apply_seeded_noise(image: Image.Image, rng: random.Random) -> None:
    """Light, deterministic salt-and-pepper noise — enough to look scan-degraded without
    obscuring the large glyphs. Sparse so Tesseract still reads the markers cleanly."""
    pixels = image.load()
    width, height = image.size
    speckles = (width * height) // 800  # ~0.125% of pixels
    for _ in range(speckles):
        x = rng.randrange(width)
        y = rng.randrange(height)
        shade = rng.randint(90, 180)
        pixels[x, y] = (shade, shade, shade)


# --- junk-text-layer multi-page fixture -----------------------------------------------

# Page 1's garbage text layer: symbol runs at plausible positions (NOT word-like) so the
# reader's density heuristic rejects it and routes the page to OCR. Real glyphs are drawn
# on that page's raster so the default Tesseract path has ink to read.
_JUNK_TOKENS: tuple[tuple[str, float, float], ...] = (
    ("#$%^&*", 72.0, 120.0),
    ("!!!~~~", 72.0, 240.0),
    ("@@@@@@", 72.0, 360.0),
    (".....", 72.0, 480.0),
)

# The readable text drawn (as glyphs) on the OCR-routed junk page — synthetic, so the
# fixture still carries a marker and Tesseract has real words to segment.
_JUNK_OCR_LAYOUT: tuple[tuple[str, float, float], ...] = (
    ("FIXTURE", 72.0, 130.0),
    ("SYNTHETIC", 72.0, 260.0),
)


def _junk_page0_content() -> bytes:
    """A normal readable text-layer page (routes to text_layer)."""
    parts: list[str] = ["BT", f"/F1 {_FONT_SIZE_PT:.2f} Tf"]
    for text, x_left, baseline_from_top in _LAYOUT:
        y_up = _PAGE_H_PT - baseline_from_top
        parts.append(f"1 0 0 1 {x_left:.2f} {y_up:.2f} Tm")
        parts.append(f"({_pdf_escape(text)}) Tj")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("ascii")


def _junk_page1_content(image_xobject_name: str) -> bytes:
    """Page 1: a garbage text layer (rejected by the heuristic) PLUS a drawn raster of
    real glyphs (so OCR has ink). The image XObject is painted full-page under the junk
    text so pdfplumber sees garbage words but Tesseract reads the rendered glyphs."""
    parts: list[str] = [
        "q",
        f"{_PAGE_W_PT:.2f} 0 0 {_PAGE_H_PT:.2f} 0 0 cm",
        f"/{image_xobject_name} Do",
        "Q",
        "BT",
        f"/F1 {_FONT_SIZE_PT:.2f} Tf",
        # Render junk text invisibly (Tr 3 = invisible) so it exists in the text layer
        # for the heuristic to reject, but does not visually corrupt the OCR raster.
        "3 Tr",
    ]
    for text, x_left, baseline_from_top in _JUNK_TOKENS:
        y_up = _PAGE_H_PT - baseline_from_top
        parts.append(f"1 0 0 1 {x_left:.2f} {y_up:.2f} Tm")
        parts.append(f"({_pdf_escape(text)}) Tj")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("ascii")


def _junk_ocr_raster_bytes() -> tuple[bytes, int, int]:
    """A raster of the junk page's readable glyphs, returned as raw RGB stream bytes for
    embedding as a PDF image XObject (zlib-compressed)."""
    image = Image.new("RGB", (_PAGE_W_PX, _PAGE_H_PX), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = _load_font()
    for text, x_left, baseline_from_top in _JUNK_OCR_LAYOUT:
        x_px = round(x_left * _PX_PER_PT)
        y_px = round(baseline_from_top * _PX_PER_PT)
        draw.text((x_px, y_px), text, fill=(0, 0, 0), font=font, anchor="ls")
    raw = image.tobytes()
    return zlib.compress(raw, 9), image.width, image.height


def _write_junk_layer_pdf(out_path: Path) -> None:
    """Multi-page: page 0 readable text layer, page 1 junk text layer over an OCR raster."""
    page0_content = _junk_page0_content()
    img_bytes, img_w, img_h = _junk_ocr_raster_bytes()
    page1_content = _junk_page1_content("Im1")

    media_box = f"/MediaBox [0 0 {_PAGE_W_PT:.2f} {_PAGE_H_PT:.2f}]"
    font_obj = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"

    image_obj = (
        b"<< /Type /XObject /Subtype /Image "
        + f"/Width {img_w} /Height {img_h} ".encode("ascii")
        + b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
        + b"/Filter /FlateDecode /Length "
        + str(len(img_bytes)).encode("ascii")
        + b" >>\nstream\n"
        + img_bytes
        + b"\nendstream"
    )

    # Object numbering:
    # 1 Catalog, 2 Pages, 3 Page0, 4 Page0-content, 5 Font,
    # 6 Page1, 7 Page1-content, 8 Image
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
        (
            f"<< /Type /Page /Parent 2 0 R {media_box} "
            f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length "
        + str(len(page0_content)).encode("ascii")
        + b" >>\nstream\n"
        + page0_content
        + b"endstream",
        font_obj,
        (
            f"<< /Type /Page /Parent 2 0 R {media_box} "
            f"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 8 0 R >> >> "
            f"/Contents 7 0 R >>"
        ).encode("ascii"),
        b"<< /Length "
        + str(len(page1_content)).encode("ascii")
        + b" >>\nstream\n"
        + page1_content
        + b"endstream",
        image_obj,
    ]
    _write_pdf(out_path, objects)


# --- entry point ----------------------------------------------------------------------


def generate_all(out_dir: Path) -> None:
    """Generate all three seed fixtures into ``out_dir`` (deterministic under ``_SEED``)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_clean_pdf(out_dir / "clean.pdf")
    _write_degraded_pdf(out_dir / "degraded.pdf")
    _write_junk_layer_pdf(out_dir / "junk_layer.pdf")


if __name__ == "__main__":
    generate_all(Path(__file__).resolve().parent)
