"""Record pinned embedding/rerank model outputs for the offline eval retriever.

EXPLICIT ONLINE OWNER STEP — never run by the graded gate or CI. ``--write`` downloads
the exact pinned model revisions (bge query encoder, mxbai cross-encoder), derives every
golden case's canonical PHI-free clinical query with the same production parsers the
executors use, and records:

- the 384-dim query vector per distinct query (float32, rounded to 8 significant digits);
- rerank scores for a generous candidate superset per query (BM25 top-45 union dense
  top-45), keyed by the sha256 of each candidate's text so replay never depends on
  candidate-pool boundary jitter;
- integrity pins: corpus manifest hash and both model revision strings.

Queries listed in ``UNAVAILABLE_CASE_IDS`` are deliberately recorded as unavailable to
replay a dense-embedder outage through the production ``RetrievalUnavailableError`` path
for the ``unavailable`` golden case.

``--check`` is OFFLINE: it verifies the committed recording covers every current golden
query, is bound to the committed corpus, and carries the exact model pins.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Sequence

from app.ingestion.reader import read_pdf_bytes_words_and_boxes
from corpus.retrieval import (
    QueryContractError,
    build_clinical_query,
    _tokenize,
)
from evals.execution import (
    _intake,
    _lab,
    _lines,
    _medication,
    _retrieval_terms,
    fixture_path,
)
from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from evals.retrieval_adapters import (
    CORPUS_DIR,
    DEFAULT_RETRIEVAL_RECORDINGS,
    EMBEDDER_PIN,
    RERANKER_PIN,
    RetrievalRecordingError,
    corpus_manifest_sha256,
    document_key,
    load_retrieval_recordings,
    query_key,
)


CANDIDATE_MARGIN = 45
UNAVAILABLE_CASE_IDS = frozenset({"lab-retrieval-unavailable-degraded"})


def eval_queries(
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> tuple[dict[str, str], set[str]]:
    """Derive each case's canonical query with the production parsing pipeline.

    Returns ``(query_by_case_id, unavailable_queries)``. Cases without a buildable
    PHI-free query are omitted (the ``no_query``/rejected classes).
    """

    queries: dict[str, str] = {}
    unavailable: set[str] = set()
    for case in load_golden_cases(manifest_path):
        source = fixture_path(case.fixture_path).read_bytes()
        words_boxes = read_pdf_bytes_words_and_boxes(source)
        lines = _lines(words_boxes)
        source_id = f"fixture:{case.case_id}"
        if case.doc_type == "lab_pdf":
            _, fields = _lab(lines, words_boxes, source_id)
        elif case.doc_type == "medication_list":
            # Grounding-only posture: no PHI-free clinical query is derived from a
            # medication list, so these cases never contribute retrieval recordings.
            _, fields = _medication(lines, words_boxes, source_id)
        else:
            _, fields, _ = _intake(lines, words_boxes, source_id)
        terms = _retrieval_terms(fields, case.doc_type)
        if not terms:
            continue
        try:
            query = build_clinical_query(terms)
        except QueryContractError:
            continue
        queries[case.case_id] = query
        if case.case_id in UNAVAILABLE_CASE_IDS:
            unavailable.add(query)
    return queries, unavailable


def _rounded(value: float) -> float:
    return float(f"{value:.8e}")


def build_retrieval_recordings(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    cache_dir: Path,
) -> dict[str, object]:
    """ONLINE: run the pinned models once and freeze their outputs."""

    import numpy as np
    from corpus.retrieval import LocalMxbaiReranker, _PinnedBgeEmbedder

    chunks = [
        json.loads(line)
        for line in (CORPUS_DIR / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    metadata = json.loads(
        (CORPUS_DIR / "index" / "metadata.json").read_text(encoding="utf-8")
    )
    dense = np.fromfile(CORPUS_DIR / "index" / "dense.f32", dtype=np.float32).reshape(
        len(chunks), int(metadata["dense"]["dimension"])
    )
    dense_norms = np.linalg.norm(dense, axis=1)
    from rank_bm25 import BM25Okapi

    bm25 = BM25Okapi(
        [_tokenize(chunk["quote"]) for chunk in chunks],
        k1=float(metadata["sparse"]["k1"]),
        b=float(metadata["sparse"]["b"]),
    )

    embedder = _PinnedBgeEmbedder(cache_dir=cache_dir)
    reranker = LocalMxbaiReranker(cache_dir=cache_dir)

    queries_by_case, unavailable_queries = eval_queries(manifest_path)
    entries: dict[str, dict[str, object]] = {}
    for query in sorted(set(queries_by_case.values())):
        key = query_key(query)
        if query in unavailable_queries:
            entries[key] = {"unavailable": True}
            continue
        vector = np.asarray(embedder.query_vector(query), dtype=np.float32).reshape(-1)

        sparse_scores = bm25.get_scores(_tokenize(query))
        sparse_top = sorted(
            (index for index, score in enumerate(sparse_scores) if float(score) > 0.0),
            key=lambda index: (-float(sparse_scores[index]), chunks[index]["chunk_id"]),
        )[:CANDIDATE_MARGIN]
        query_norm = float(np.linalg.norm(vector))
        similarities = (dense @ vector) / (np.maximum(dense_norms, 1e-12) * query_norm)
        dense_top = sorted(
            (
                index
                for index, score in enumerate(similarities)
                if math.isfinite(float(score))
            ),
            key=lambda index: (-float(similarities[index]), chunks[index]["chunk_id"]),
        )[:CANDIDATE_MARGIN]
        candidate_indices = sorted(
            set(sparse_top) | set(dense_top),
            key=lambda index: chunks[index]["chunk_id"],
        )
        documents = [chunks[index]["quote"] for index in candidate_indices]
        scores = reranker.scores(query, documents)
        entries[key] = {
            "vector": [_rounded(float(value)) for value in vector],
            "rerank": {
                document_key(document): _rounded(float(score))
                for document, score in zip(documents, scores, strict=True)
            },
        }
    return {
        "version": 1,
        "embedder": EMBEDDER_PIN,
        "reranker": RERANKER_PIN,
        "corpus_manifest_sha256": corpus_manifest_sha256(),
        "queries": entries,
    }


def check_retrieval_recordings(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    recordings_path: str | Path = DEFAULT_RETRIEVAL_RECORDINGS,
) -> None:
    """OFFLINE: fail unless every current golden query is covered by the recording."""

    index = load_retrieval_recordings(recordings_path)
    queries_by_case, _unavailable = eval_queries(manifest_path)
    missing = sorted(
        case_id
        for case_id, query in queries_by_case.items()
        if index.entry_for(query) is None
    )
    if missing:
        raise RetrievalRecordingError(
            f"retrieval recording does not cover cases: {', '.join(missing)}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="ONLINE: rerun pinned models")
    mode.add_argument("--check", action="store_true", help="offline coverage check")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_RETRIEVAL_RECORDINGS)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/w2-fastembed-cache"),
        help="pinned model cache used only by --write",
    )
    args = parser.parse_args(argv)

    if args.check:
        check_retrieval_recordings(
            manifest_path=args.manifest, recordings_path=args.output
        )
        return 0

    recording = build_retrieval_recordings(
        manifest_path=args.manifest, cache_dir=args.cache_dir
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(recording, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
