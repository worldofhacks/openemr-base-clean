"""W2-M13 corpus contracts (W2-D4, W2-R2, architecture §2/§4a/§6).

These tests are intentionally colocated with the new corpus because Track B's isolated
ownership does not include the shared ``agent/tests`` tree. Run them explicitly with
``pytest corpus/tests`` from ``agent/``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from corpus.build import (
    CorpusBuildError,
    build_chunks,
    canonical_manifest_hash,
    load_manifest,
    validate_manifest,
)


CORPUS_DIR = Path(__file__).resolve().parents[1]
EXPECTED_DOCUMENTS = {
    "vadod-diabetes-2023",
    "vadod-diabetes-2023-quick-reference",
    "vadod-hypertension-2020",
    "vadod-hypertension-2020-pocket-card",
    "vadod-lipids-2025",
    "vadod-lipids-2025-pocket-card",
}
FORBIDDEN_METHODOLOGY_TERMS = {
    "guideline development methodology",
    "evidence review methodology",
    "literature review search terms",
    "evidence table",
    "references",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_pins_the_licensed_trio_and_compact_clinician_artifacts() -> None:
    manifest = load_manifest(CORPUS_DIR / "manifest.json")
    validate_manifest(manifest, CORPUS_DIR)

    documents = manifest["documents"]
    assert {doc["source_id"] for doc in documents} == EXPECTED_DOCUMENTS
    assert manifest["curation"]["text_only"] is True
    assert manifest["curation"]["figures_excluded"] is True
    assert manifest["totals"]["documents"] == 6

    for doc in documents:
        assert doc["source_url"].startswith("https://www.healthquality.va.gov/")
        assert doc["license"]["status"] == "verified"
        assert doc["license"]["policy_url"] == "https://department.va.gov/copyright-policy/"
        assert len(doc["source_pdf_sha256"]) == 64
        curated = CORPUS_DIR / doc["curated_file"]
        assert curated.is_file()
        assert _sha256(curated) == doc["curated_sha256"]


def test_manifest_records_banned_and_version_mismatched_sources() -> None:
    manifest = load_manifest(CORPUS_DIR / "manifest.json")
    exclusions = {item["name"]: item["reason"] for item in manifest["excluded_sources"]}

    for name in ("ADA Standards of Care", "AHA/ACC", "JNC 8", "GINA", "KDIGO", "JAMA PDFs"):
        assert name in exclusions
    assert "Diabetes Pocket Card (2017)" in exclusions
    assert "version mismatch" in exclusions["Diabetes Pocket Card (2017)"].lower()


def test_curated_pages_exclude_methodology_and_build_verbatim_chunks() -> None:
    manifest = load_manifest(CORPUS_DIR / "manifest.json")
    chunks = build_chunks(manifest, CORPUS_DIR)

    assert len(chunks) == manifest["totals"]["chunks"]
    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)
    assert {chunk["document_id"] for chunk in chunks} == EXPECTED_DOCUMENTS

    source_pages: dict[tuple[str, int], str] = {}
    for doc in manifest["documents"]:
        source_path = CORPUS_DIR / doc["curated_file"]
        lines = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines()]
        assert lines
        included = doc["curation"]["included_printed_page_ranges"]
        allowed = {
            page
            for first, last in included
            for page in range(first, last + 1)
        }
        for page in lines:
            assert page["printed_page"] in allowed
            assert page["text"].strip()
            assert page["section"].strip()
            assert not any(term in page["section"].lower() for term in FORBIDDEN_METHODOLOGY_TERMS)
            source_pages[(doc["source_id"], page["printed_page"])] = page["text"]

    for chunk in chunks:
        source_text = source_pages[(chunk["document_id"], chunk["printed_page"])]
        assert chunk["quote"] in source_text  # no paraphrase: a contiguous source span
        assert 1 <= len(chunk["quote"].split()) <= manifest["curation"]["chunking"]["max_words"]
        assert chunk["section"]


def test_figure_strip_policy_fails_closed(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    bad_source = source_dir / "bad.jsonl"
    bad_source.write_text(
        json.dumps(
            {
                "printed_page": 1,
                "section": "Recommendations",
                "text": "<visual_element>embedded figure</visual_element>",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "curation": {
            "text_only": True,
            "figures_excluded": True,
            "chunking": {"target_words": 20, "max_words": 30, "overlap_sentences": 0},
        },
        "documents": [
            {
                "source_id": "bad",
                "curated_file": "sources/bad.jsonl",
                "curated_sha256": _sha256(bad_source),
                "license": {"status": "verified"},
                "curation": {"included_printed_page_ranges": [[1, 1]]},
            }
        ],
        "totals": {"documents": 1, "chunks": 1},
    }

    with pytest.raises(CorpusBuildError, match="figure|visual"):
        validate_manifest(manifest, tmp_path)


def test_manifest_hash_is_canonical_and_content_sensitive() -> None:
    manifest = load_manifest(CORPUS_DIR / "manifest.json")
    original = canonical_manifest_hash(manifest)
    reordered = json.loads(json.dumps(manifest, sort_keys=True))
    assert canonical_manifest_hash(reordered) == original

    reordered["documents"][0]["version"] += "-changed"
    assert canonical_manifest_hash(reordered) != original
