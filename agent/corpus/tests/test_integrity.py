"""Index↔manifest integrity assertion tests (W2-M13, architecture §6)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from corpus.check_index_manifest import check_index_manifest


CORPUS_DIR = Path(__file__).resolve().parents[1]


def test_committed_index_matches_manifest_and_chunks() -> None:
    result = check_index_manifest(CORPUS_DIR)
    assert result.ok, result.reason
    assert result.chunk_count > 0
    assert len(result.manifest_hash) == 64


def test_corrupt_index_metadata_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "index").mkdir()
    (tmp_path / "manifest.json").write_bytes((CORPUS_DIR / "manifest.json").read_bytes())
    (tmp_path / "chunks.jsonl").write_bytes((CORPUS_DIR / "chunks.jsonl").read_bytes())
    metadata = json.loads((CORPUS_DIR / "index" / "metadata.json").read_text(encoding="utf-8"))
    metadata["manifest_sha256"] = "0" * 64
    (tmp_path / "index" / "metadata.json").write_text(
        json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8"
    )
    (tmp_path / "index" / "dense.f32").write_bytes(
        (CORPUS_DIR / "index" / "dense.f32").read_bytes()
    )

    result = check_index_manifest(tmp_path)
    assert not result.ok
    assert "manifest" in result.reason.lower()


def test_missing_dense_artifact_fails_closed_without_a_traceback(tmp_path: Path) -> None:
    shutil.copytree(CORPUS_DIR, tmp_path / "corpus")
    (tmp_path / "corpus" / "index" / "dense.f32").unlink()
    result = check_index_manifest(tmp_path / "corpus")
    assert not result.ok
    assert "unavailable" in result.reason.lower()


def test_dense_model_revision_drift_is_rejected(tmp_path: Path) -> None:
    shutil.copytree(CORPUS_DIR, tmp_path / "corpus")
    metadata_path = tmp_path / "corpus" / "index" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["dense"]["revision"] = "0" * 40
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    result = check_index_manifest(tmp_path / "corpus")
    assert not result.ok
    assert "dense model" in result.reason.lower()
