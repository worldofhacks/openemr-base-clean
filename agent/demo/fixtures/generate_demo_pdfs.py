"""Build the optional, born-digital Week 2 demonstration PDFs.

The documents are deliberately synthetic and prominently marked as such.  They are
not part of ``evals/golden`` and cannot change the governed 50-case baseline.  This
small standard-library PDF writer keeps the committed fixtures deterministic while
providing a real text layer and vector form/table styling for source-page demos.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class _Canvas:
    commands: list[str]

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: tuple[float, float, float] | None = None,
        stroke: tuple[float, float, float] | None = None,
        line_width: float = 1.0,
    ) -> None:
        self.commands.append("q")
        if fill is not None:
            self.commands.append("%.3f %.3f %.3f rg" % fill)
        if stroke is not None:
            self.commands.extend(("%.3f %.3f %.3f RG" % stroke, f"{line_width:.2f} w"))
        operation = "B" if fill is not None and stroke is not None else "f" if fill else "S"
        self.commands.extend(
            (f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re {operation}", "Q")
        )

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        color: tuple[float, float, float] = (0.72, 0.76, 0.79),
        line_width: float = 0.7,
    ) -> None:
        self.commands.extend(
            (
                "q",
                "%.3f %.3f %.3f RG" % color,
                f"{line_width:.2f} w",
                f"{x0:.2f} {y0:.2f} m {x1:.2f} {y1:.2f} l S",
                "Q",
            )
        )

    def text(
        self,
        value: str,
        x: float,
        y: float,
        *,
        size: float = 9.0,
        bold: bool = False,
        color: tuple[float, float, float] = (0.09, 0.13, 0.16),
    ) -> None:
        font = "F2" if bold else "F1"
        self.commands.extend(
            (
                "BT",
                "%.3f %.3f %.3f rg" % color,
                f"/{font} {size:.2f} Tf",
                f"1 0 0 1 {x:.2f} {y:.2f} Tm",
                f"({_escape(value)}) Tj",
                "ET",
            )
        )


def _section(canvas: _Canvas, title: str, y: float) -> None:
    canvas.rect(36, y - 4, 540, 20, fill=(0.91, 0.94, 0.96))
    canvas.text(title, 44, y + 2, size=9.5, bold=True, color=(0.08, 0.25, 0.36))


def _page_header(canvas: _Canvas, subtitle: str) -> None:
    canvas.rect(0, 730, PAGE_WIDTH, 62, fill=(0.06, 0.20, 0.29))
    canvas.text("NORTHWIND FAMILY MEDICINE", 36, 763, size=16, bold=True, color=(1, 1, 1))
    canvas.text(subtitle, 36, 744, size=9, color=(0.84, 0.91, 0.95))
    canvas.rect(36, 706, 540, 17, fill=(1.0, 0.94, 0.75), stroke=(0.82, 0.63, 0.12))
    canvas.text(
        "SYNTHETIC DEMO - NOT A REAL PATIENT - NOT FOR CLINICAL USE",
        77,
        711,
        size=8.5,
        bold=True,
        color=(0.38, 0.27, 0.00),
    )


def _write_pdf(content: bytes) -> bytes:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:.2f} "
            f"{PAGE_HEIGHT:.2f}] /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> "
            f"/Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]

    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode("ascii")
    output += (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(output)


def build_intake_pdf() -> bytes:
    """Return a one-page clinic-style intake form with a real text layer."""

    canvas = _Canvas([])
    _page_header(canvas, "NEW PATIENT INTAKE")

    _section(canvas, "PATIENT INFORMATION", 680)
    canvas.text("Patient name", 44, 658, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("CASEY DEMO", 44, 644, size=10, bold=True)
    canvas.text("Date of birth", 230, 658, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("12/19/1983", 230, 644, size=10)
    canvas.text("Sex", 350, 658, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("X", 350, 644, size=10)
    canvas.text("Contact", 400, 658, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("casey.demo@example.invalid", 400, 644, size=8.5)
    for x0, x1 in ((44, 210), (230, 330), (350, 382), (400, 568)):
        canvas.line(x0, 639, x1, 639)

    _section(canvas, "REASON FOR VISIT", 610)
    canvas.text("Chief concern", 44, 587, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text('"Persistent fatigue and muscle cramps for two weeks."', 44, 569, size=10)
    canvas.line(44, 563, 568, 563)

    _section(canvas, "CURRENT MEDICATIONS", 530)
    canvas.text(
        "Medication / dose / frequency",
        44,
        507,
        size=7.5,
        bold=True,
        color=(0.34, 0.40, 0.44),
    )
    medication_rows = (
        "Metformin 500 mg twice daily",
        "Lisinopril 10 mg once daily",
    )
    for row, value in enumerate(medication_rows):
        y = 486 - row * 24
        canvas.text(value, 44, y, size=9.5)
        canvas.line(44, y - 6, 568, y - 6)

    _section(canvas, "ALLERGIES", 424)
    canvas.text("Allergen / reaction", 44, 401, size=7.5, bold=True, color=(0.34, 0.40, 0.44))
    canvas.text("Penicillin rash", 44, 380, size=9.5)
    canvas.line(44, 374, 568, 374)

    _section(canvas, "FAMILY HISTORY", 342)
    canvas.text("Relative / condition", 44, 319, size=7.5, bold=True, color=(0.34, 0.40, 0.44))
    family_rows = ("Father type 2 diabetes", "Mother hypertension")
    for row, value in enumerate(family_rows):
        y = 298 - row * 18
        canvas.text(value, 44, y, size=9.5)
        canvas.line(44, y - 6, 568, y - 6)

    _section(canvas, "VITALS", 238)
    vital_rows = (
        ("Blood pressure", "128 / 78 mm Hg", "Pulse", "72 /min"),
        ("Temperature", "98.4 deg F", "Oxygen saturation", "98 %"),
        ("Height", "66 in", "Weight", "174 lb"),
    )
    for row, values in enumerate(vital_rows):
        y = 211 - row * 28
        canvas.text(values[0], 44, y + 10, size=7.2, color=(0.34, 0.40, 0.44))
        canvas.text(values[1], 44, y - 3, size=9.5, bold=True)
        canvas.text(values[2], 310, y + 10, size=7.2, color=(0.34, 0.40, 0.44))
        canvas.text(values[3], 310, y - 3, size=9.5, bold=True)

    canvas.text("Completed electronically for demonstration purposes.", 36, 34, size=7.5, color=(0.40, 0.45, 0.48))
    return _write_pdf(("\n".join(canvas.commands) + "\n").encode("ascii"))


def build_lab_pdf() -> bytes:
    """Return a one-page clinic-style laboratory report with a real text layer."""

    canvas = _Canvas([])
    _page_header(canvas, "FINAL LABORATORY REPORT")

    _section(canvas, "SPECIMEN / PATIENT", 680)
    canvas.text("Patient", 44, 657, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("CASEY DEMO", 44, 643, size=10, bold=True)
    canvas.text("DOB", 200, 657, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("12/19/1983", 200, 643, size=9.5)
    canvas.text("Accession", 320, 657, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("DEMO-260715-01", 320, 643, size=9.5)
    canvas.text("Collected", 460, 657, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("07/15/2026", 460, 643, size=9.5)

    _section(canvas, "CHEMISTRY", 610)
    columns = ((44, "Test"), (250, "Result"), (330, "Flag"), (382, "Reference range"), (500, "Units"))
    for x, label in columns:
        canvas.text(label, x, 584, size=7.5, bold=True, color=(0.34, 0.40, 0.44))
    canvas.line(40, 576, 572, 576, color=(0.38, 0.47, 0.52), line_width=1.0)

    results = (
        ("Magnesium", "1.6", "L", "1.7-2.4", "mg/dL"),
        ("Hemoglobin A1c", "7.4", "H", "4.0-5.6", "%"),
        ("Glucose", "102", "H", "70-99", "mg/dL"),
    )
    for row, values in enumerate(results):
        y = 551 - row * 34
        canvas.text(values[0], 44, y, size=9.5, bold=row == 0)
        canvas.text(values[1], 250, y, size=9.5, bold=True)
        canvas.text(values[2], 334, y, size=9.5, bold=True, color=(0.65, 0.16, 0.12))
        canvas.text(values[3], 382, y, size=9.5)
        canvas.text(values[4], 500, y, size=9.5)
        canvas.line(40, y - 10, 572, y - 10)

    canvas.rect(40, 404, 532, 74, fill=(0.96, 0.97, 0.98), stroke=(0.74, 0.79, 0.82))
    canvas.text("Result note", 50, 458, size=8, bold=True, color=(0.08, 0.25, 0.36))
    canvas.text(
        "Values outside the listed reference interval are flagged for clinician review.",
        50,
        439,
        size=8.5,
    )
    canvas.text(
        "This synthetic report is for product demonstration only and is not medical advice.",
        50,
        421,
        size=8.5,
    )

    _section(canvas, "REPORT STATUS", 370)
    canvas.text("Status", 44, 344, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("FINAL", 44, 328, size=10, bold=True)
    canvas.text("Reporting laboratory", 200, 344, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("Northwind Demo Laboratory", 200, 328, size=9.5)
    canvas.text("Verified", 430, 344, size=7.5, color=(0.34, 0.40, 0.44))
    canvas.text("07/15/2026 14:20", 430, 328, size=9.5)

    canvas.text("END OF SYNTHETIC REPORT", 220, 34, size=7.5, bold=True, color=(0.40, 0.45, 0.48))
    return _write_pdf(("\n".join(canvas.commands) + "\n").encode("ascii"))


def write_fixtures(directory: Path | None = None) -> tuple[Path, Path]:
    target = directory or Path(__file__).resolve().parent
    target.mkdir(parents=True, exist_ok=True)
    intake = target / "synthetic_clinic_intake.pdf"
    lab = target / "synthetic_lab_report.pdf"
    intake.write_bytes(build_intake_pdf())
    lab.write_bytes(build_lab_pdf())
    return intake, lab


if __name__ == "__main__":
    write_fixtures()
