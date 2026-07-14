#!/usr/bin/env python3
"""Build deterministic verbatim chunks and a pinned FastEmbed/ONNX dense index.

The sparse BM25 leg is instantiated at runtime with ``rank-bm25`` from the committed
chunks. This image-build step persists the bge-small-en-v1.5 passage vectors and binds
every artifact to the canonical manifest hash.

Traceability: W2-M13; W2-D4; W2-R2/R3; W2_ARCHITECTURE.md §2/§4a/§6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_SOURCE_REPO = "qdrant/bge-small-en-v1.5-onnx-q"
EMBED_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
EMBED_ONNX = "model_optimized.onnx"
EMBED_DIMENSION = 384
FIGURE_MARKERS = ("<visual_element", "<image", "data:image/", "image/png", "image/jpeg")
METHODOLOGY_SECTION_MARKERS = (
    "guideline development methodology",
    "evidence review methodology",
    "literature review search terms",
    "evidence table",
    "references",
)
SOURCE_ALLOWED_KEYS = {"pdf_index", "printed_page", "section", "text"}


class CorpusBuildError(ValueError):
    """A corpus policy or reproducibility invariant failed closed."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusBuildError(f"manifest unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise CorpusBuildError("manifest root must be an object")
    return value


def canonical_manifest_hash(manifest: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(manifest))


def _load_pages(path: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CorpusBuildError(f"curated source unreadable: {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        try:
            page = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusBuildError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(page, dict) or not set(page).issubset(SOURCE_ALLOWED_KEYS):
            raise CorpusBuildError(f"unexpected curated-page shape at {path}:{line_number}")
        if not isinstance(page.get("printed_page"), int) or page["printed_page"] < 1:
            raise CorpusBuildError(f"invalid printed page at {path}:{line_number}")
        if not isinstance(page.get("section"), str) or not page["section"].strip():
            raise CorpusBuildError(f"missing section at {path}:{line_number}")
        if not isinstance(page.get("text"), str) or not page["text"].strip():
            raise CorpusBuildError(f"empty text at {path}:{line_number}")
        pages.append(page)
    return pages


def validate_manifest(manifest: dict[str, Any], corpus_dir: Path) -> None:
    if manifest.get("schema_version") != "1.0":
        raise CorpusBuildError("unsupported manifest schema")
    curation = manifest.get("curation")
    if not isinstance(curation, dict) or curation.get("text_only") is not True:
        raise CorpusBuildError("text-only corpus policy is required")
    if curation.get("figures_excluded") is not True:
        raise CorpusBuildError("figure-strip policy is required")
    chunking = curation.get("chunking", {})
    target = chunking.get("target_words")
    maximum = chunking.get("max_words")
    overlap = chunking.get("overlap_sentences")
    if not all(isinstance(value, int) for value in (target, maximum, overlap)):
        raise CorpusBuildError("chunking parameters must be integers")
    if not (1 <= target <= maximum and 0 <= overlap <= 2):
        raise CorpusBuildError("invalid chunking bounds")

    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise CorpusBuildError("manifest must contain documents")
    source_ids: set[str] = set()
    for doc in documents:
        if not isinstance(doc, dict):
            raise CorpusBuildError("document entry must be an object")
        source_id = doc.get("source_id")
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            raise CorpusBuildError("source_id must be unique and non-empty")
        source_ids.add(source_id)
        if doc.get("license", {}).get("status") != "verified":
            raise CorpusBuildError(f"unverified license for {source_id}")
        relative = doc.get("curated_file")
        if not isinstance(relative, str) or Path(relative).suffix != ".jsonl":
            raise CorpusBuildError(f"curated source must be text JSONL for {source_id}")
        source_path = corpus_dir / relative
        if not source_path.is_file():
            raise CorpusBuildError(f"curated source missing for {source_id}")
        expected_hash = doc.get("curated_sha256")
        if sha256_file(source_path) != expected_hash:
            raise CorpusBuildError(f"curated source hash mismatch for {source_id}")
        page_ranges = doc.get("curation", {}).get("included_printed_page_ranges")
        if not isinstance(page_ranges, list) or not page_ranges:
            raise CorpusBuildError(f"invalid curated page ranges for {source_id}")
        allowed_pages: set[int] = set()
        for page_range in page_ranges:
            if not (
                isinstance(page_range, list)
                and len(page_range) == 2
                and all(isinstance(value, int) for value in page_range)
                and 1 <= page_range[0] <= page_range[1]
            ):
                raise CorpusBuildError(f"invalid curated page ranges for {source_id}")
            pages_in_range = set(range(page_range[0], page_range[1] + 1))
            if allowed_pages & pages_in_range:
                raise CorpusBuildError(f"overlapping curated page ranges for {source_id}")
            allowed_pages.update(pages_in_range)
        pages = _load_pages(source_path)
        if {page["printed_page"] for page in pages} != allowed_pages:
            raise CorpusBuildError(f"curated page coverage mismatch for {source_id}")
        for page in pages:
            lowered_section = page["section"].lower()
            if any(marker in lowered_section for marker in METHODOLOGY_SECTION_MARKERS):
                raise CorpusBuildError(f"methodology appendix included for {source_id}")
            lowered_text = page["text"].lower()
            if any(marker in lowered_text for marker in FIGURE_MARKERS):
                raise CorpusBuildError(f"figure or visual payload included for {source_id}")

    totals = manifest.get("totals", {})
    if totals.get("documents") != len(documents):
        raise CorpusBuildError("manifest document count mismatch")


def _hard_word_spans(text: str, maximum: int) -> list[tuple[int, int]]:
    words = list(re.finditer(r"\S+", text))
    return [
        (words[start].start(), words[min(start + maximum, len(words)) - 1].end())
        for start in range(0, len(words), maximum)
    ]


def _sentence_spans(text: str, maximum: int) -> list[tuple[int, int]]:
    boundaries = [0]
    for match in re.finditer(r"(?<=[.!?])\s+(?=(?:[A-Z0-9•]|Recommendation\s+\d))", text):
        boundaries.append(match.end())
    boundaries.append(len(text))
    spans: list[tuple[int, int]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start >= end:
            continue
        fragment = text[start:end]
        if len(fragment.split()) <= maximum:
            spans.append((start, end))
        else:
            spans.extend((start + left, start + right) for left, right in _hard_word_spans(fragment, maximum))
    return spans


def _chunk_page(text: str, *, target_words: int, max_words: int, overlap_sentences: int) -> list[str]:
    spans = _sentence_spans(text, max_words)
    if not spans:
        return []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(spans):
        end_cursor = cursor
        words = 0
        while end_cursor < len(spans):
            candidate_words = len(text[spans[end_cursor][0] : spans[end_cursor][1]].split())
            if end_cursor > cursor and words + candidate_words > max_words:
                break
            words += candidate_words
            end_cursor += 1
            if words >= target_words:
                break
        if end_cursor == cursor:
            end_cursor += 1
        quote = text[spans[cursor][0] : spans[end_cursor - 1][1]].strip()
        if quote:
            chunks.append(quote)
        if end_cursor >= len(spans):
            break
        next_cursor = max(cursor + 1, end_cursor - overlap_sentences)
        cursor = next_cursor
    return chunks


def build_chunks(
    manifest: dict[str, Any], corpus_dir: Path, *, enforce_counts: bool = True
) -> list[dict[str, Any]]:
    validate_manifest(manifest, corpus_dir)
    chunking = manifest["curation"]["chunking"]
    chunks: list[dict[str, Any]] = []
    per_document: dict[str, int] = {}
    for doc in manifest["documents"]:
        source_id = doc["source_id"]
        per_document[source_id] = 0
        for page in _load_pages(corpus_dir / doc["curated_file"]):
            page_quotes = _chunk_page(
                page["text"],
                target_words=chunking["target_words"],
                max_words=chunking["max_words"],
                overlap_sentences=chunking["overlap_sentences"],
            )
            for page_ordinal, quote in enumerate(page_quotes, 1):
                per_document[source_id] += 1
                quote_hash = _sha256_bytes(quote.encode("utf-8"))[:12]
                chunks.append(
                    {
                        "chunk_id": (
                            f"{source_id}:p{page['printed_page']:04d}:"
                            f"c{page_ordinal:03d}:{quote_hash}"
                        ),
                        "document_id": source_id,
                        "section": page["section"],
                        "printed_page": page["printed_page"],
                        "quote": quote,
                    }
                )
        if enforce_counts and doc.get("chunk_count") != per_document[source_id]:
            raise CorpusBuildError(f"chunk count mismatch for {source_id}")
    if enforce_counts and manifest.get("totals", {}).get("chunks") != len(chunks):
        raise CorpusBuildError("manifest total chunk count mismatch")
    return chunks


def serialize_chunks(chunks: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_json(chunk) + b"\n" for chunk in chunks)


def _download_embed_model(cache_dir: Path) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(
        repo_id=EMBED_SOURCE_REPO,
        revision=EMBED_REVISION,
        cache_dir=cache_dir,
        allow_patterns=[
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "preprocessor_config.json",
            EMBED_ONNX,
        ],
    )


def build_dense_vectors(quotes: Sequence[str], cache_dir: Path) -> Any:
    """Return a float32 ``(chunks, 384)`` matrix using pinned FastEmbed/ONNX."""
    import numpy as np
    from fastembed import TextEmbedding

    model_path = _download_embed_model(cache_dir)
    embedder = TextEmbedding(
        EMBED_MODEL,
        cache_dir=str(cache_dir),
        specific_model_path=model_path,
    )
    matrix = np.asarray(list(embedder.passage_embed(list(quotes))), dtype=np.float32)
    if matrix.shape != (len(quotes), EMBED_DIMENSION):
        raise CorpusBuildError(f"unexpected dense shape: {matrix.shape}")
    return matrix


def build_index(corpus_dir: Path, *, cache_dir: Path) -> dict[str, Any]:
    manifest_path = corpus_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    chunks = build_chunks(manifest, corpus_dir)
    chunks_bytes = serialize_chunks(chunks)
    chunks_path = corpus_dir / "chunks.jsonl"
    chunks_path.write_bytes(chunks_bytes)

    index_dir = corpus_dir / "index"
    index_dir.mkdir(exist_ok=True)
    matrix = build_dense_vectors([chunk["quote"] for chunk in chunks], cache_dir)
    dense_path = index_dir / "dense.f32"
    matrix.tofile(dense_path)

    manifest_hash = canonical_manifest_hash(manifest)
    metadata = {
        "schema_version": "1.0",
        "corpus_version": f"{manifest['corpus_id']}@{manifest_hash}",
        "manifest_sha256": manifest_hash,
        "chunks_sha256": _sha256_bytes(chunks_bytes),
        "dense_sha256": sha256_file(dense_path),
        "chunk_count": len(chunks),
        "dense": {
            "format": "raw-float32-row-major-v1",
            "dimension": EMBED_DIMENSION,
            "model": EMBED_MODEL,
            "source_repo": EMBED_SOURCE_REPO,
            "revision": EMBED_REVISION,
            "model_file": EMBED_ONNX,
            "runtime": "FastEmbed/ONNX",
        },
        "sparse": {
            "engine": "rank-bm25",
            "algorithm": "BM25Okapi",
            "k1": 1.5,
            "b": 0.75,
        },
    }
    (index_dir / "metadata.json").write_bytes(_canonical_json(metadata) + b"\n")
    return metadata


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--corpus-dir", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("/tmp/w2-fastembed-cache")
    )
    parser.add_argument(
        "--print-counts",
        action="store_true",
        help="print computed per-document/total counts without requiring manifest counts",
    )
    args = parser.parse_args(argv)
    manifest = load_manifest(args.corpus_dir / "manifest.json")
    if args.print_counts:
        chunks = build_chunks(manifest, args.corpus_dir, enforce_counts=False)
        counts: dict[str, int] = {}
        for chunk in chunks:
            counts[chunk["document_id"]] = counts.get(chunk["document_id"], 0) + 1
        print(json.dumps({"documents": counts, "total": len(chunks)}, sort_keys=True))
        return 0
    metadata = build_index(args.corpus_dir, cache_dir=args.cache_dir)
    print(
        f"built {metadata['chunk_count']} chunks; "
        f"corpus_version={metadata['corpus_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
