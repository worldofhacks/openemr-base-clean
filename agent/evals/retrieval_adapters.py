"""Recorded embedding/rerank adapters for production retrieval in the eval gate.

R02 (AF-P0-02) offline-determinism design: the accepted evaluator route runs the real
``corpus.retrieval.HybridRetriever`` — committed corpus, committed dense matrix, real
BM25, real reciprocal-rank fusion, real reranker seam, real thresholds and breaker
paths — while the two model-inference seams (`DenseEmbedder`, `Reranker`) replay
outputs recorded once from the exact pinned model revisions.

Why recorded adapters instead of a pre-populated model cache in CI:

- Tier 1 must be fully network-free (``network_disabled()``): a model cache still
  needs a download on every cache miss, and cache restoration is a network fetch.
- ONNX inference low-order float bits differ across CPU architectures; near-tie
  rankings could flip between a contributor laptop and CI. Replayed vectors and
  scores are byte-identical everywhere, so the 50-case gate stays deterministic.

The recording file (``evals/recordings/retrieval.json``) is metadata-only: query
hashes, float vectors/scores, chunk-text hashes, and integrity pins. It never contains
clinical fixture values, document text, or guideline quotes. It binds the committed
corpus manifest hash and the exact pinned model revisions; a drifted corpus or model
pin fails closed with ``RetrievalRecordingError``.

Regeneration is an explicit ONLINE owner step (``python -m evals.record_retrieval
--write``) that downloads the pinned revisions and re-records; the graded gate never
performs model inference or network access.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from corpus.retrieval import (
    EMBED_DIMENSION,
    EMBED_ONNX,
    EMBED_REVISION,
    EMBED_SOURCE_REPO,
    RERANK_MODEL,
    RERANK_ONNX,
    RERANK_REVISION,
    HybridRetriever,
    RerankerSeam,
    RetrievalUnavailableError,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "agent" / "corpus"
DEFAULT_RETRIEVAL_RECORDINGS = Path(__file__).parent / "recordings" / "retrieval.json"

EMBEDDER_PIN = f"{EMBED_SOURCE_REPO}@{EMBED_REVISION}:{EMBED_ONNX}"
RERANKER_PIN = f"{RERANK_MODEL}@{RERANK_REVISION}:{RERANK_ONNX}"


class RetrievalRecordingError(RuntimeError):
    """The retrieval recording is absent, stale for the corpus/models, or corrupt."""


def query_key(query: str) -> str:
    """Hash the canonical query text; the recording never stores query text."""

    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def document_key(document: str) -> str:
    """Hash one candidate document text for rerank-score lookup."""

    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def corpus_manifest_sha256() -> str:
    return hashlib.sha256((CORPUS_DIR / "manifest.json").read_bytes()).hexdigest()


class RecordedRetrievalIndex:
    """Loaded, integrity-checked recorded model outputs keyed by query hash."""

    def __init__(self, *, embedder: str, reranker: str, entries: dict[str, dict[str, Any]]):
        self.embedder = embedder
        self.reranker = reranker
        self._entries = entries

    def entry_for(self, query: str) -> dict[str, Any] | None:
        return self._entries.get(query_key(query))

    @property
    def entry_count(self) -> int:
        return len(self._entries)


def load_retrieval_recordings(
    path: str | Path = DEFAULT_RETRIEVAL_RECORDINGS,
) -> RecordedRetrievalIndex:
    source = Path(path)
    if not source.is_file():
        raise RetrievalRecordingError("retrieval recording is missing")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("unsupported retrieval recording version")
        if raw.get("embedder") != EMBEDDER_PIN or raw.get("reranker") != RERANKER_PIN:
            raise ValueError("retrieval recording model pins drifted")
        if raw.get("corpus_manifest_sha256") != corpus_manifest_sha256():
            raise ValueError("retrieval recording is stale for the committed corpus")
        entries = raw.get("queries")
        if not isinstance(entries, dict) or not entries:
            raise ValueError("retrieval recording has no query entries")
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                raise ValueError("invalid retrieval recording entry")
            if entry.get("unavailable") is True:
                continue
            vector = entry.get("vector")
            if (
                not isinstance(vector, list)
                or len(vector) != EMBED_DIMENSION
                or not all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in vector
                )
            ):
                raise ValueError(f"invalid recorded query vector for {key}")
            rerank = entry.get("rerank")
            if not isinstance(rerank, dict) or not all(
                isinstance(score, (int, float))
                and not isinstance(score, bool)
                and math.isfinite(float(score))
                and 0.0 <= float(score) <= 1.0
                for score in rerank.values()
            ):
                raise ValueError(f"invalid recorded rerank scores for {key}")
        return RecordedRetrievalIndex(
            embedder=str(raw["embedder"]),
            reranker=str(raw["reranker"]),
            entries=entries,
        )
    except RetrievalRecordingError:
        raise
    except Exception as exc:
        raise RetrievalRecordingError("retrieval recording is corrupt or stale") from exc


class RecordedQueryEmbedder:
    """`DenseEmbedder` seam replaying the pinned bge query vectors.

    An unrecorded query replays the production embedder-outage contract exactly:
    ``RetrievalUnavailableError``, which `HybridRetriever.search` re-raises so the
    executor must handle unavailability explicitly (never a silent sparse-only answer).
    """

    def __init__(self, index: RecordedRetrievalIndex):
        self._index = index

    def query_vector(self, query: str) -> Any:
        import numpy as np

        entry = self._index.entry_for(query)
        if entry is None or entry.get("unavailable") is True:
            raise RetrievalUnavailableError("recorded dense embedding unavailable")
        return np.asarray(entry["vector"], dtype=np.float32).reshape(-1)


class RecordedReranker:
    """`Reranker` seam replaying pinned mxbai cross-encoder scores.

    A missing (query, document) pair raises; the production ``RerankerSeam`` then
    degrades to fused-order exactly as it does for a real local-model failure, and the
    degradation reason surfaces in the retrieval observation.
    """

    model_name = f"recorded:{RERANKER_PIN}"

    def __init__(self, index: RecordedRetrievalIndex):
        self._index = index

    def scores(self, query: str, documents: list[str]) -> list[float]:
        entry = self._index.entry_for(query)
        if entry is None or entry.get("unavailable") is True:
            raise RetrievalRecordingError("recorded rerank scores unavailable")
        rerank = entry["rerank"]
        try:
            return [float(rerank[document_key(document)]) for document in documents]
        except KeyError as exc:
            raise RetrievalRecordingError("recorded rerank candidate missing") from exc


_CACHED_RETRIEVER: HybridRetriever | None = None


def default_eval_retriever() -> HybridRetriever:
    """The accepted evaluator retriever: production ``HybridRetriever`` + recorded seams."""

    global _CACHED_RETRIEVER
    if _CACHED_RETRIEVER is None:
        index = load_retrieval_recordings()
        _CACHED_RETRIEVER = HybridRetriever(
            CORPUS_DIR,
            dense_embedder=RecordedQueryEmbedder(index),
            reranker=RerankerSeam(mode="local", local=RecordedReranker(index)),
        )
    return _CACHED_RETRIEVER


def reset_cached_retriever() -> None:
    """Drop the cached retriever (tests and drills only)."""

    global _CACHED_RETRIEVER
    _CACHED_RETRIEVER = None


def retrieval_provenance(
    *, recordings_path: str | Path = DEFAULT_RETRIEVAL_RECORDINGS
) -> dict[str, str]:
    """Corpus version and model/config pins for the aggregate eval result."""

    metadata = json.loads(
        (CORPUS_DIR / "index" / "metadata.json").read_text(encoding="utf-8")
    )
    return {
        "corpus_version": str(metadata["corpus_version"]),
        "corpus_manifest_sha256": corpus_manifest_sha256(),
        "embedder": EMBEDDER_PIN,
        "reranker": RERANKER_PIN,
        "retrieval_recordings_sha256": hashlib.sha256(
            Path(recordings_path).read_bytes()
        ).hexdigest(),
    }
