"""Retrieval-behavior golden fixtures (R02 / AF-P0-02) + deterministic PDF writer.

Two SYNTHETIC, NON-CLINICAL text-layer fixtures for the production-retrieval golden
cases added by R02:

- ``lab-retrieval-unavailable-degraded``: a clean lab whose derived clinical query
  ("serum copper") is deliberately recorded as UNAVAILABLE in
  ``evals/recordings/retrieval.json``, replaying a dense-embedder outage through the
  production ``RetrievalUnavailableError`` path. The case pins explicit, non-fabricated
  degradation.
- ``intake-followup-no-retrieval-query``: an intake form whose chief concern
  ("Follow-up.") yields no useful clinical term, so no PHI-free query can be built and
  retrieval is never attempted.

Reproducible (RPO 0, §8a): run this module to regenerate both byte-stable PDFs. The
canary is ``ZZPHI-<case_id>`` (the extracted patient name), consistent with cases 1-50.

    python evals/fixtures/golden/gen_retrieval_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent

# --- minimal, deterministic text-layer PDF writer (no timestamps -> byte-stable) --------
# Mirrors gen_adversarial_fixtures.py so all golden fixtures share one layout contract.

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


def _lab_unavailable_lines() -> list[str]:
    case_id = "lab-retrieval-unavailable-degraded"
    return [
        "SYNTHETIC TEST RECORD - NOT FOR CLINICAL USE",
        "WEEK 2 GOLDEN LAB REPORT",
        f"Patient: ZZPHI-{case_id}",
        f"MRN: MRN-SYN-{case_id}",
        f"Source document ID: fixture:{case_id}",
        f"CANARY SENTENCE: Synthetic record {case_id}; no real person.",
        "LAB RESULTS",
        "Result 1",
        "Test name: Serum Copper",
        "Value: 101",
        "Unit: ug/dL",
        "Reference range: 70-140",
        "Collection date: 2026-06-18",
        "Abnormal flag: N",
        "END OF SYNTHETIC REPORT",
    ]


def _intake_no_query_lines() -> list[str]:
    case_id = "intake-followup-no-retrieval-query"
    return [
        "SYNTHETIC TEST RECORD - NOT FOR CLINICAL USE",
        "WEEK 2 GOLDEN INTAKE FORM",
        f"MRN: MRN-SYN-{case_id}",
        f"Source document ID: fixture:{case_id}",
        f"CANARY SENTENCE: Synthetic record {case_id}; no real person.",
        "DEMOGRAPHICS",
        f"Name: ZZPHI-{case_id}",
        "DOB: 1990-02-17",
        "Sex: X",
        "Contact: synthetic-contact@example.invalid",
        "CHIEF CONCERN",
        "Follow-up.",
        "CURRENT MEDICATIONS",
        "1. Synthetic multivitamin daily",
        "ALLERGIES",
        "1. Pollen - sneezing",
        "FAMILY HISTORY",
        "Parent: hyperlipidemia.",
        "VITALS",
        "Pulse: 72 bpm",
        "Pulse measurement date: 2026-06-20T09:00:00Z",
        "END OF SYNTHETIC INTAKE FORM",
    ]


def main() -> None:
    _write_pdf(
        _HERE / "lab-retrieval-unavailable-degraded.pdf", _lab_unavailable_lines()
    )
    _write_pdf(
        _HERE / "intake-followup-no-retrieval-query.pdf", _intake_no_query_lines()
    )


if __name__ == "__main__":
    main()
