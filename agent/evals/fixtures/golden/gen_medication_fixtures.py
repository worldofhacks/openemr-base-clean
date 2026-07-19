"""Medication-list golden fixtures (R09, third document type) + deterministic PDF writer.

Three SYNTHETIC, NON-CLINICAL text-layer fixtures for the medication-list golden cases
added by R09 (PDF p.5 Core Deliverables: "A third document type such as referral fax or
medication list"):

- ``med-list-clean-grounded``: one clean medication row plus an as-of date; every leaf
  is visible on a single source line, grounds deterministically, and carries a complete
  CitationV2. The case also pins the doc type's ``no_query`` retrieval boundary: no
  PHI-free clinical query is ever derived from a medication list (grounding-only
  posture), so retrieval is never attempted.
- ``med-list-wrapped-frequency-unverified``: the frequency instruction wraps across two
  source lines. The full value is visible in the document, but the bounded production
  grounding verifier (single-row regions for table fields) cannot attest it, so the
  leaf persists as honestly-ungrounded: visible-and-unverified, no citation, never
  invented and never dropped (R08/G-D3 evidence-quality semantics).
- ``med-list-missing-as-of-date-honesty``: the as-of date is explicitly "[not
  reported]"; the leaf must stay null and carry no fabricated citation (the
  missing-data honesty family, mirroring the lab/intake missing-leaf cases).

Reproducible (RPO 0, §8a): run this module to regenerate all three byte-stable PDFs.
The canary is ``ZZPHI-<case_id>`` (the extracted patient-name line), consistent with
cases 1-50.

    python evals/fixtures/golden/gen_medication_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent

# --- minimal, deterministic text-layer PDF writer (no timestamps -> byte-stable) --------
# Mirrors gen_retrieval_fixtures.py / gen_adversarial_fixtures.py so all golden fixtures
# share one layout contract.

_FONT_SIZE = 11.0
_LEADING = 15.0
_MARGIN = 54.0
_PAGE_W, _PAGE_H = 612.0, 792.0  # US Letter points


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(lines: list[str]) -> bytes:
    parts = [
        "BT",
        f"/F1 {_FONT_SIZE:.2f} Tf",
        f"{_LEADING:.2f} TL",
        f"1 0 0 1 {_MARGIN:.2f} {_PAGE_H - _MARGIN:.2f} Tm",
    ]
    for index, line in enumerate(lines):
        if index:
            parts.append("T*")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1", "replace")


def _write_pdf(path: Path, lines: list[str]) -> None:
    content = _content_stream(lines)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_pos = len(out)
    count = len(objects) + 1
    out += f"xref\n0 {count}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        b"trailer\n<< /Size " + str(count).encode("ascii") + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode("ascii") + b"\n%%EOF"
    )
    path.write_bytes(out)


def _header(case_id: str) -> list[str]:
    return [
        "SYNTHETIC TEST RECORD - NOT FOR CLINICAL USE",
        "WEEK 2 GOLDEN MEDICATION LIST",
        f"Patient: ZZPHI-{case_id}",
        f"MRN: MRN-SYN-{case_id}",
        f"Source document ID: fixture:{case_id}",
        f"CANARY SENTENCE: Synthetic record {case_id}; no real person.",
    ]


def _clean_grounded_lines() -> list[str]:
    return [
        *_header("med-list-clean-grounded"),
        "MEDICATION LIST",
        "As of date: 2026-07-01",
        "Medication 1",
        "Medication name: Synthetic Multivitamin",
        "Strength: 500 mg",
        "Dose: 1 tablet",
        "Route: oral",
        "Frequency: once daily",
        "Status: active",
        "END OF SYNTHETIC REPORT",
    ]


def _wrapped_frequency_lines() -> list[str]:
    # The frequency instruction deliberately wraps onto a second source line. It stays
    # fully visible, but the bounded single-row grounding contract for table fields
    # cannot attest the joined phrase, so the leaf persists as visible-and-unverified.
    return [
        *_header("med-list-wrapped-frequency-unverified"),
        "MEDICATION LIST",
        "As of date: 2026-07-02",
        "Medication 1",
        "Medication name: Synthetic Saline Spray",
        "Strength: 10 mg",
        "Dose: 2 sprays",
        "Route: nasal",
        "Frequency: two sprays each nostril",
        "morning and evening",
        "Status: active",
        "END OF SYNTHETIC REPORT",
    ]


def _missing_as_of_date_lines() -> list[str]:
    return [
        *_header("med-list-missing-as-of-date-honesty"),
        "MEDICATION LIST",
        "As of date: [not reported]",
        "Medication 1",
        "Medication name: Synthetic Chewable Antacid",
        "Strength: 200 mg",
        "Dose: 1 tablet",
        "Route: oral",
        "Frequency: after meals",
        "Status: on hold",
        "END OF SYNTHETIC REPORT",
    ]


def main() -> None:
    _write_pdf(_HERE / "med-list-clean-grounded.pdf", _clean_grounded_lines())
    _write_pdf(
        _HERE / "med-list-wrapped-frequency-unverified.pdf", _wrapped_frequency_lines()
    )
    _write_pdf(
        _HERE / "med-list-missing-as-of-date-honesty.pdf", _missing_as_of_date_lines()
    )


if __name__ == "__main__":
    main()
