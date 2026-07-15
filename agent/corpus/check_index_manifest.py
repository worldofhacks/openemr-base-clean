#!/usr/bin/env python3
"""Fail closed unless the dense index, chunks, and manifest are hash-aligned.

Track D may call this standalone script; it intentionally edits no workflow file.
Traceability: W2-M13; W2-D4; W2_ARCHITECTURE.md §6.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

try:
    from corpus.build import (
        EMBED_DIMENSION,
        EMBED_MODEL,
        EMBED_ONNX,
        EMBED_REVISION,
        EMBED_SOURCE_REPO,
        canonical_manifest_hash,
        load_manifest,
        sha256_file,
    )
except ModuleNotFoundError:  # direct ``python corpus/check_index_manifest.py`` execution
    from build import (  # type: ignore[no-redef]
        EMBED_DIMENSION,
        EMBED_MODEL,
        EMBED_ONNX,
        EMBED_REVISION,
        EMBED_SOURCE_REPO,
        canonical_manifest_hash,
        load_manifest,
        sha256_file,
    )


@dataclass(frozen=True)
class IntegrityResult:
    ok: bool
    reason: str
    manifest_hash: str = ""
    chunk_count: int = 0


def check_index_manifest(corpus_dir: Path) -> IntegrityResult:
    try:
        manifest = load_manifest(corpus_dir / "manifest.json")
        manifest_hash = canonical_manifest_hash(manifest)
        metadata = json.loads((corpus_dir / "index" / "metadata.json").read_text(encoding="utf-8"))
        chunks_path = corpus_dir / "chunks.jsonl"
        dense_path = corpus_dir / "index" / "dense.f32"
        chunks_hash = sha256_file(chunks_path)
        dense_hash = sha256_file(dense_path)
        chunk_count = sum(
            1 for line in chunks_path.read_text(encoding="utf-8").splitlines() if line
        )
        dense_size = dense_path.stat().st_size
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return IntegrityResult(False, f"retrieval index unavailable: {exc}")

    if metadata.get("manifest_sha256") != manifest_hash:
        return IntegrityResult(False, "index manifest hash mismatch", manifest_hash)
    if metadata.get("chunks_sha256") != chunks_hash:
        return IntegrityResult(False, "index chunks hash mismatch", manifest_hash)
    if metadata.get("dense_sha256") != dense_hash:
        return IntegrityResult(False, "dense index hash mismatch", manifest_hash)

    if metadata.get("chunk_count") != chunk_count or manifest.get("totals", {}).get("chunks") != chunk_count:
        return IntegrityResult(False, "index chunk count mismatch", manifest_hash, chunk_count)
    expected_dense = {
        "model": EMBED_MODEL,
        "source_repo": EMBED_SOURCE_REPO,
        "revision": EMBED_REVISION,
        "model_file": EMBED_ONNX,
        "dimension": EMBED_DIMENSION,
        "runtime": "FastEmbed/ONNX",
    }
    dense_metadata = metadata.get("dense")
    if not isinstance(dense_metadata, dict) or any(
        dense_metadata.get(key) != value for key, value in expected_dense.items()
    ):
        return IntegrityResult(False, "dense model metadata mismatch", manifest_hash, chunk_count)
    dimension = dense_metadata["dimension"]
    if dense_size != chunk_count * dimension * 4:
        return IntegrityResult(False, "dense index size mismatch", manifest_hash, chunk_count)
    expected_version = f"{manifest.get('corpus_id')}@{manifest_hash}"
    if metadata.get("corpus_version") != expected_version:
        return IntegrityResult(False, "index corpus version mismatch", manifest_hash, chunk_count)
    return IntegrityResult(True, "ok", manifest_hash, chunk_count)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--corpus-dir", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args()
    result = check_index_manifest(args.corpus_dir)
    if not result.ok:
        print(result.reason)
        return 1
    print(f"ok: {result.chunk_count} chunks @ {result.manifest_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
