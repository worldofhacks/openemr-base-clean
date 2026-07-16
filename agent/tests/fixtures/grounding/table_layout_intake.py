"""Deterministic synthetic table-layout intake PDF used by grounding integration tests.

The fixture contains conspicuous test-only data and no real patient information. Its
layout deliberately separates labels from values, uses a US-formatted DOB, wraps a
quoted concern, and reorders words inside medication/allergy/family-history cells.
"""

from __future__ import annotations

from pathlib import Path

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
FONT_SIZE = 10.0

# text, x from left, baseline y from bottom (PDF's native y-up coordinate space)
PLACEMENTS: tuple[tuple[str, float, float], ...] = (
    ("SYNTHETIC TABLE-LAYOUT INTAKE - TEST DATA ONLY", 54.0, 756.0),
    ("DEMOGRAPHICS", 54.0, 726.0),
    ("Name:", 54.0, 706.0),
    ("SYNTHETIC TEST PATIENT", 190.0, 706.0),
    ("DOB:", 54.0, 686.0),
    ("12/19/1983", 190.0, 686.0),
    ("Sex:", 54.0, 666.0),
    ("X", 190.0, 666.0),
    ("Contact:", 54.0, 646.0),
    ("synthetic-contact@example.invalid", 190.0, 646.0),
    ("CHIEF CONCERN", 54.0, 612.0),
    ('Patient reports "recurring persistent morning', 190.0, 592.0),
    ('headaches for two weeks"', 190.0, 576.0),
    ("CURRENT MEDICATIONS", 54.0, 542.0),
    ("twice daily Medication:Metformin 500 mg", 190.0, 522.0),
    ("at bedtime Medication:Lisinopril 10 mg", 190.0, 502.0),
    ("ALLERGIES", 54.0, 468.0),
    ("rash Allergy:Penicillin", 190.0, 448.0),
    ("dermatitis Allergy:Latex", 190.0, 428.0),
    ("FAMILY HISTORY", 54.0, 394.0),
    ("type 2 diabetes Relative:Father", 190.0, 374.0),
    ("hypertension Relative:Mother", 190.0, 358.0),
)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream() -> bytes:
    commands: list[str] = []
    for text, x, y in PLACEMENTS:
        commands.extend(
            (
                "BT",
                f"/F1 {FONT_SIZE:.2f} Tf",
                f"1 0 0 1 {x:.2f} {y:.2f} Tm",
                f"({_escape(text)}) Tj",
                "ET",
            )
        )
    return ("\n".join(commands) + "\n").encode("ascii")


def build_table_layout_intake_pdf() -> bytes:
    """Return byte-stable, timestamp-free PDF bytes for the synthetic intake."""

    content = _content_stream()
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
            f"{PAGE_WIDTH:.2f} {PAGE_HEIGHT:.2f}] "
            f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]

    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_position = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode("ascii")
    output += (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_position).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(output)


if __name__ == "__main__":
    Path(__file__).with_suffix(".pdf").write_bytes(build_table_layout_intake_pdf())
