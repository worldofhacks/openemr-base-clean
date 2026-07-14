#!/usr/bin/env python3
"""Extract the pinned VA/DoD management pages into committed text-only JSONL.

This maintainer tool is not a runtime scraper. It accepts six locally downloaded PDFs,
verifies their frozen SHA-256 values, extracts only the approved page ranges, removes
repeated running headers/footers, normalizes whitespace, and emits no image data. The
committed JSONL is the build input, so rebuilding chunks/indexes is network-free.

Traceability: W2-M13; W2-D4; W2-R2; W2_ARCHITECTURE.md §2 and §4a.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceSpec:
    filename: str
    source_id: str
    sha256: str
    pages: tuple[tuple[int, int, str], ...]  # (zero-based PDF index, printed page, section)


def _page_map(start: int, end: int, ranges: tuple[tuple[int, int, str], ...]) -> tuple[tuple[int, int, str], ...]:
    pages: list[tuple[int, int, str]] = []
    for printed_page in range(start, end + 1):
        matches = [section for first, last, section in ranges if first <= printed_page <= last]
        if len(matches) != 1:
            raise ValueError(f"printed page {printed_page} has {len(matches)} section mappings")
        pages.append((printed_page - 1, printed_page, matches[0]))
    return tuple(pages)


SPECS = (
    SourceSpec(
        filename="diabetes-2023-full.pdf",
        source_id="vadod-diabetes-2023",
        sha256="cc5f24ac1b38560d00a2e85e5361d4f741ea2b7be9f7df354c6104b385836a5d",
        pages=_page_map(
            24,
            71,
            (
                (24, 26, "Recommendations: Summary"),
                (27, 30, "Recommendations: Prediabetes"),
                (31, 32, "Recommendations: Telehealth"),
                (33, 46, "Recommendations: Diabetes Mellitus Management"),
                (47, 57, "Recommendations: Non-Pharmacotherapy"),
                (58, 71, "Recommendations: Pharmacotherapy"),
            ),
        )
        + _page_map(
            97,
            106,
            (
                (97, 98, "Management Appendix: Glycemic Control Targets and Monitoring"),
                (99, 106, "Management Appendix: Pharmacotherapy"),
            ),
        )
        + _page_map(
            145,
            146,
            ((145, 146, "Text Alternative: Type 2 Diabetes Management Algorithms"),),
        ),
    ),
    SourceSpec(
        filename="diabetes-2023-pocket.pdf",
        source_id="vadod-diabetes-2023-quick-reference",
        sha256="f10e4025f3682ed0d4a6971699fd8213cb3aea474da08bb0f0d625455c23315d",
        pages=(
            (2, 1, "Quick Reference: Prediabetes and Telehealth Recommendations"),
            (3, 2, "Quick Reference: Diabetes and Non-Pharmacotherapy Recommendations"),
            (4, 3, "Quick Reference: Non-Pharmacotherapy and Pharmacotherapy Recommendations"),
            (5, 4, "Quick Reference: Pharmacotherapy Recommendations"),
            (6, 5, "Quick Reference: Type 2 Diabetes Management Algorithm"),
            (7, 6, "Quick Reference: Self-Management Education and Support Algorithm"),
        ),
    ),
    SourceSpec(
        filename="hypertension-2020-full.pdf",
        source_id="vadod-hypertension-2020",
        sha256="7f2f56d3536350c832f00ecf7ae0680257ec069648e0eb2ccd97e933dc43a122",
        pages=_page_map(
            23,
            27,
            (
                (23, 25, "Recommendations: Summary"),
                (26, 27, "Recommendations: Screening, Diagnosis, and Monitoring"),
            ),
        )
        + _page_map(
            29,
            61,
            (
                (29, 32, "Recommendations: Screening, Diagnosis, and Monitoring"),
                (33, 44, "Recommendations: Treatment Goals and General Approaches"),
                (45, 53, "Recommendations: Non-Pharmacological Management"),
                (54, 61, "Recommendations: Pharmacological Treatment"),
            ),
        )
        + _page_map(
            97,
            101,
            (
                (97, 97, "Management Appendix: Dietary Information"),
                (98, 101, "Management Appendix: Drug Dosage Table"),
            ),
        )
        + _page_map(
            121,
            123,
            ((121, 123, "Text Alternative: Hypertension Screening and Treatment Algorithms"),),
        ),
    ),
    SourceSpec(
        filename="hypertension-2020-pocket.pdf",
        source_id="vadod-hypertension-2020-pocket-card",
        sha256="82d5be76a22bf67dcb3bed2e0861d0823c09ff0c91caf0233fd48025afaac542",
        pages=(
            (0, 1, "Pocket Card: Screening, Diagnosis, and Treatment"),
            (1, 2, "Pocket Card: Treatment Optimization and Blood Pressure Measurement"),
        ),
    ),
    SourceSpec(
        filename="lipids-2025-full.pdf",
        source_id="vadod-lipids-2025",
        sha256="2cf05fab73dd52241d51624440597248d0fecc0d471a81eba02945f6bf0c585c",
        pages=_page_map(
            24,
            62,
            (
                (24, 26, "Recommendations: Summary"),
                (27, 35, "Recommendations: Screening and Cardiovascular Risk Assessment"),
                (36, 52, "Recommendations: Pharmacotherapy"),
                (53, 55, "Recommendations: Statin Intolerance"),
                (56, 58, "Recommendations: Supplements and Nutraceuticals"),
                (59, 62, "Recommendations: Lifestyle Interventions"),
            ),
        )
        + _page_map(
            125,
            134,
            (
                (125, 127, "Text Alternative: Lipid Management Algorithm"),
                (128, 128, "Management Appendix: Cardiovascular Risk Calculators"),
                (129, 132, "Management Appendix: Pharmacotherapy"),
                (133, 134, "Management Appendix: Lifestyle Medicine Interventions"),
            ),
        ),
    ),
    SourceSpec(
        filename="lipids-2025-pocket.pdf",
        source_id="vadod-lipids-2025-pocket-card",
        sha256="fe8bd7d9d24082154df1ac0252b4ae34df2f857d220be02620cd2d026e681cc3",
        pages=((0, 1, "Pocket Card: Management Algorithm and Clinical Sidebars"),),
    ),
)


_RUNNING_HEADER = re.compile(
    r"^(?:VA/?DoD|VA/DOD) Clinical Practice Guideline(?:s)?(?: for| on| –)?.*$",
    re.IGNORECASE,
)
_RUNNING_FOOTER = re.compile(
    r"^(?:May 2023|March 2020|December 2025)\s+Page\s+\d+(?:\s+of\s+\d+)?\s*$",
    re.IGNORECASE,
)
_BARE_BANNER = re.compile(r"^VA/DOD CLINICAL PRACTICE GUIDELINES(?:\s+March 2020)?$", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_page_text(raw: str) -> str:
    """Apply layout-only normalization; never summarize or paraphrase source text."""
    kept: list[str] = []
    for raw_line in raw.replace("\x00", "").splitlines():
        line = unicodedata.normalize("NFC", raw_line).replace("\u00ad", "").strip()
        line = re.sub(r"\s+", " ", line)
        if not line or _RUNNING_FOOTER.fullmatch(line) or _BARE_BANNER.fullmatch(line):
            continue
        if _RUNNING_HEADER.fullmatch(line):
            continue
        kept.append(line)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def curate_page_text(source_id: str, printed_page: int, text: str) -> str:
    """Apply only the manifest-recorded, conservative section exclusions."""
    if source_id == "vadod-diabetes-2023" and printed_page == 71:
        marker = "X. Research Priorities"
        if marker not in text:
            raise SystemExit("Diabetes page 71 no longer contains the pinned Research Priorities boundary")
        text = text[: text.index(marker)].strip()
    if source_id == "vadod-hypertension-2020" and printed_page == 29:
        # Page 28 is an adapted AHA measurement table and is omitted entirely. Page 29
        # starts with a second adapted table; retain only the VA/DoD discussion after it.
        marker = "Although SRs and meta-analyses"
        if marker not in text:
            raise SystemExit("HTN page 29 third-party-table boundary changed")
        text = text[text.index(marker) :].strip()
    if source_id == "vadod-hypertension-2020-pocket-card" and printed_page == 1:
        # The card's office-measurement block mirrors material adapted from an AHA
        # publication. Downstream reuse rights are not explicit, so W2-R2's rule excludes it.
        start = "Sidebar 1: Office Blood Pressure Measurement"
        stop = "Sidebar 2: Confirm Diagnosis"
        if start not in text or stop not in text:
            raise SystemExit("HTN pocket-card page 1 exclusion boundaries changed")
        text = (text[: text.index(start)] + text[text.index(stop) :]).strip()
    if source_id == "vadod-hypertension-2020-pocket-card" and printed_page == 2:
        # Keep the VA/DoD treatment-optimization sidebar; exclude the adapted diet and
        # office/home-measurement blocks on the rest of this graphical card page.
        start = "Sidebar 6: Optimize Treatment"
        stop = "DASH Diet Protocol"
        if start not in text or stop not in text:
            raise SystemExit("HTN pocket-card page 2 exclusion boundaries changed")
        text = text[text.index(start) : text.index(stop)].strip()
    return text


def extract(input_dir: Path, output_dir: Path) -> None:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - maintainer-facing dependency message
        raise SystemExit("pdfplumber is required only to refresh curated source JSONL") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    for spec in SPECS:
        pdf_path = input_dir / spec.filename
        actual_hash = sha256_file(pdf_path)
        if actual_hash != spec.sha256:
            raise SystemExit(
                f"source hash mismatch for {spec.filename}: expected {spec.sha256}, got {actual_hash}"
            )
        records: list[str] = []
        with pdfplumber.open(pdf_path) as reader:
            for pdf_index, printed_page, section in spec.pages:
                if pdf_index >= len(reader.pages):
                    raise SystemExit(f"missing PDF page index {pdf_index} in {spec.filename}")
                text = normalize_page_text(reader.pages[pdf_index].extract_text() or "")
                text = curate_page_text(spec.source_id, printed_page, text)
                if not text:
                    raise SystemExit(f"empty approved page {printed_page} in {spec.filename}")
                records.append(
                    json.dumps(
                        {
                            "pdf_index": pdf_index,
                            "printed_page": printed_page,
                            "section": section,
                            "text": text,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
        target = output_dir / f"{spec.source_id}.jsonl"
        target.write_text("\n".join(records) + "\n", encoding="utf-8")
        print(f"{spec.source_id}: {len(records)} curated pages -> {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input-dir", type=Path, required=True, help="directory containing the six pinned PDFs")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "sources",
        help="text-only JSONL destination",
    )
    args = parser.parse_args()
    extract(args.input_dir, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
