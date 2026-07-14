"""PHI-free hybrid retrieval over the pinned VA/DoD guideline corpus.

The committed dense matrix is searched with the same pinned bge-small model used
at build time, then unioned with ``rank-bm25`` by reciprocal-rank fusion.  A
single ``RERANKER=cohere|local`` seam selects Cohere or the pinned local mxbai
ONNX cross-encoder.  Model construction and network clients are intentionally
lazy: importing the route never downloads a model or contacts a vendor.

Traceability: W2-M14; W2-D4; W2-R3; W2_ARCHITECTURE.md §2/§4/§5.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from corpus.check_index_manifest import check_index_manifest


K_MAX = 10
CANDIDATE_POOL = 30
RRF_CONSTANT = 60
DENSE_MIN_SIMILARITY = 0.60
RERANK_MIN_SCORE = 0.10

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_SOURCE_REPO = "qdrant/bge-small-en-v1.5-onnx-q"
EMBED_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
EMBED_ONNX = "model_optimized.onnx"
EMBED_DIMENSION = 384

RERANK_MODEL = "mixedbread-ai/mxbai-rerank-base-v1"
RERANK_REVISION = "800f24c113213a187e65bde9db00c15a2bb12738"
RERANK_ONNX = "onnx/model_quantized.onnx"
COHERE_MODEL = "rerank-v3.5"
COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"

_HF_COMMON_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "preprocessor_config.json",
)
_TOKEN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_ALLOWED_TERM = re.compile(r"[a-z0-9α-ω%+./'()\-\s]+", re.IGNORECASE)
_CONVERSATION = re.compile(
    r"\b(?:what|when|where|who|why|how|should|could|would|please|tell|show|"
    r"patient|name|named|born|address|phone|email|my|me|his|her|their)\b",
    re.IGNORECASE,
)
_PROMPT_INJECTION = re.compile(
    r"\b(?:ignore|disregard|override|jailbreak|prompt|system|assistant|instructions?|"
    r"reveal|respond)\b",
    re.IGNORECASE,
)
_PHI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "identifier_label",
        re.compile(
            r"\b(?:mrn|medical\s+record(?:\s+number)?|patient\s+id|date\s+of\s+birth|"
            r"dob|ssn|social\s+security)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "email",
        re.compile(r"\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.I),
    ),
    (
        "phone",
        re.compile(
            r"(?<!\w)(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])"
            r"\d{3}[ .-]\d{4}(?!\w)"
        ),
    ),
    (
        "date",
        re.compile(r"\b(?:\d{1,4})[-/.](?:\d{1,2})[-/.](?:\d{1,4})\b"),
    ),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-"
            r"[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
    ),
    ("long_number", re.compile(r"(?<!\d)\d{6,}(?!\d)")),
    ("alphanumeric_identifier", re.compile(r"\b[a-z]{1,4}\d{4,}\b", re.IGNORECASE)),
)

_log = logging.getLogger("agent.evidence_retrieval")


class QueryContractError(ValueError):
    """A proposed query is not limited to PHI-free clinical terms."""


class RetrievalUnavailableError(RuntimeError):
    """The corpus/index cannot safely answer; distinct from a healthy miss."""


class RerankerConfigurationError(ValueError):
    """The selected reranker mode is outside the frozen seam."""


@dataclass(frozen=True)
class PhiScreenResult:
    safe: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class EvidenceHit:
    source_id: str
    section: str
    chunk_id: str
    quote: str
    score: float
    corpus_version: str


@dataclass(frozen=True)
class RetrievalOutcome:
    items: tuple[EvidenceHit, ...]
    corpus_version: str
    manifest_hash: str
    degraded_reasons: tuple[str, ...]


class DenseEmbedder(Protocol):
    def query_vector(self, query: str) -> Any: ...


class Reranker(Protocol):
    model_name: str

    def scores(self, query: str, documents: list[str]) -> list[float]: ...


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _ZERO_WIDTH.sub("", normalized)
    return " ".join(normalized.split())


def screen_phi(
    query: str, *, demographic_strings: Sequence[str] = ()
) -> PhiScreenResult:
    """Deterministically reject identifier shapes before any managed egress.

    The result exposes only a fixed reason code.  It never echoes the query or a
    matched demographic into an exception or log record.
    """

    normalized = _normalize(query).casefold()
    if not normalized:
        return PhiScreenResult(False, "empty_query")
    for reason, pattern in _PHI_PATTERNS:
        if pattern.search(normalized):
            return PhiScreenResult(False, reason)
    for value in demographic_strings:
        demographic = _normalize(value).casefold()
        if len(demographic) >= 3 and demographic in normalized:
            return PhiScreenResult(False, "session_demographic")
    return PhiScreenResult(True)


def _validate_canonical_query(
    query: str, *, demographic_strings: Sequence[str] = ()
) -> str:
    if not isinstance(query, str):
        raise QueryContractError("query must be a string")
    canonical = _normalize(query).casefold()
    if not canonical or len(canonical) > 180:
        raise QueryContractError("query is empty or too long")
    if (
        not _ALLOWED_TERM.fullmatch(canonical)
        or _CONVERSATION.search(canonical)
        or _PROMPT_INJECTION.search(canonical)
    ):
        raise QueryContractError("query must contain condition/test terms only")
    if not screen_phi(canonical, demographic_strings=demographic_strings).safe:
        raise QueryContractError("query contains identifier-shaped material")
    tokens = _TOKEN.findall(canonical)
    if not tokens or len(tokens) > 20:
        raise QueryContractError("query exceeds the clinical-term limit")
    return canonical


def build_clinical_query(
    terms: Sequence[str], *, demographic_strings: Sequence[str] = ()
) -> str:
    """Build one canonical query from condition/test terms, never conversation."""

    if isinstance(terms, (str, bytes)) or not terms or len(terms) > 8:
        raise QueryContractError("query requires one to eight clinical terms")

    canonical: list[str] = []
    token_count = 0
    for raw_term in terms:
        if not isinstance(raw_term, str):
            raise QueryContractError("clinical terms must be strings")
        term = _normalize(raw_term).casefold()
        if not term or len(term) > 80:
            raise QueryContractError("clinical term is empty or too long")
        if (
            not _ALLOWED_TERM.fullmatch(term)
            or _CONVERSATION.search(term)
            or _PROMPT_INJECTION.search(term)
        ):
            raise QueryContractError("query must contain condition/test terms only")
        if not screen_phi(term).safe:
            raise QueryContractError("query contains identifier-shaped material")
        phrase_tokens = _TOKEN.findall(term)
        if not phrase_tokens or len(phrase_tokens) > 8:
            raise QueryContractError("clinical term has an invalid token count")
        token_count += len(phrase_tokens)
        if term not in canonical:
            canonical.append(term)

    query = " ".join(canonical)
    if token_count > 20 or len(query) > 180:
        raise QueryContractError("query exceeds the clinical-term limit")
    return _validate_canonical_query(query, demographic_strings=demographic_strings)


def reciprocal_rank_fusion(
    *,
    sparse_ids: Sequence[str],
    dense_ids: Sequence[str],
    rank_constant: int = RRF_CONSTANT,
) -> dict[str, float]:
    """Union sparse/dense rankings with deterministic de-duplication."""

    if rank_constant < 1:
        raise ValueError("rank_constant must be positive")
    scores: dict[str, float] = {}
    for ranking in (sparse_ids, dense_ids):
        seen: set[str] = set()
        for rank, chunk_id in enumerate(ranking, 1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rank_constant + rank)
    return scores


class _CircuitBreaker:
    """Small dependency breaker with one half-open probe after the cooldown."""

    def __init__(self, *, failure_threshold: int = 2, recovery_seconds: float = 30.0):
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._failures = 0
        self._open_until = 0.0
        self._half_open_probe = False
        self._state = "closed"
        self._lock = threading.Lock()

    def allow(self) -> bool:
        changed: str | None = None
        with self._lock:
            now = time.monotonic()
            if self._open_until == 0.0:
                allowed = True
            elif now < self._open_until or self._half_open_probe:
                allowed = False
            else:
                self._half_open_probe = True
                self._state = "half_open"
                changed = "half_open"
                allowed = True
        if changed is not None:
            _log.info(
                "breaker.state.changed",
                extra={"dependency": "cohere_reranker", "state": changed},
            )
        return allowed

    def success(self) -> None:
        changed = False
        with self._lock:
            changed = self._state != "closed"
            self._failures = 0
            self._open_until = 0.0
            self._half_open_probe = False
            self._state = "closed"
        if changed:
            _log.info(
                "breaker.state.changed",
                extra={"dependency": "cohere_reranker", "state": "closed"},
            )

    def failure(self) -> None:
        changed = False
        with self._lock:
            self._half_open_probe = False
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open_until = time.monotonic() + self._recovery_seconds
                changed = self._state != "open"
                self._state = "open"
        if changed:
            _log.info(
                "breaker.state.changed",
                extra={"dependency": "cohere_reranker", "state": "open"},
            )


class CohereReranker:
    """Minimal Cohere v2 client; the API key is accepted only at construction."""

    model_name = COHERE_MODEL

    def __init__(
        self,
        api_key: str,
        *,
        client: Any | None = None,
        timeout_seconds: float = 4.0,
        demographic_strings: Sequence[str] = (),
    ):
        if not api_key:
            raise RerankerConfigurationError("Cohere mode requires a production API key")
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._demographic_strings = tuple(demographic_strings)

    def scores(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if not screen_phi(query, demographic_strings=self._demographic_strings).safe:
            raise QueryContractError("managed reranker query refused")
        client = self._client
        if client is None:
            import httpx

            response = httpx.post(
                COHERE_RERANK_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "X-Client-Name": "openemr-clinical-copilot",
                },
                json={
                    "model": self.model_name,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                },
                timeout=self._timeout_seconds,
            )
        else:
            response = client.post(
                COHERE_RERANK_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "X-Client-Name": "openemr-clinical-copilot",
                },
                json={
                    "model": self.model_name,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                },
                timeout=self._timeout_seconds,
            )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise RuntimeError("reranker returned an invalid result shape")
        scores = [0.0] * len(documents)
        seen: set[int] = set()
        for result in results:
            if not isinstance(result, dict):
                raise RuntimeError("reranker returned an invalid item")
            index = result.get("index")
            score = result.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(documents)
                or index in seen
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
            ):
                raise RuntimeError("reranker returned an invalid score")
            seen.add(index)
            scores[index] = float(score)
        if len(seen) != len(documents):
            raise RuntimeError("reranker omitted a candidate")
        return scores


class LocalMxbaiReranker:
    """Pinned quantized mxbai cross-encoder loaded through FastEmbed/ONNX."""

    model_name = f"{RERANK_MODEL}@{RERANK_REVISION}:{RERANK_ONNX}"

    def __init__(self, *, cache_dir: Path):
        self._cache_dir = cache_dir
        self._encoder: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        from fastembed.common.model_description import ModelSource
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        from huggingface_hub import snapshot_download

        supported = {item["model"] for item in TextCrossEncoder.list_supported_models()}
        if RERANK_MODEL not in supported:
            TextCrossEncoder.add_custom_model(
                model=RERANK_MODEL,
                sources=ModelSource(hf=RERANK_MODEL),
                model_file=RERANK_ONNX,
                description="W2-D4 pinned local guideline reranker",
                license="apache-2.0",
                size_in_gb=0.24,
            )
        model_path = snapshot_download(
            repo_id=RERANK_MODEL,
            revision=RERANK_REVISION,
            cache_dir=str(self._cache_dir),
            allow_patterns=[*_HF_COMMON_FILES, RERANK_ONNX],
        )
        self._encoder = TextCrossEncoder(
            RERANK_MODEL,
            cache_dir=str(self._cache_dir),
            specific_model_path=model_path,
        )
        return self._encoder

    def scores(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        with self._lock:
            raw_scores = list(self._load().rerank(query, documents))
        if len(raw_scores) != len(documents):
            raise RuntimeError("local reranker returned an invalid score count")
        scores: list[float] = []
        for raw_score in raw_scores:
            value = float(raw_score)
            if not math.isfinite(value):
                raise RuntimeError("local reranker returned a non-finite score")
            # The FastEmbed cross-encoder emits logits.  Sigmoid preserves order
            # while making the API's score contract stable and bounded.
            if value >= 0:
                score = 1.0 / (1.0 + math.exp(-min(value, 700.0)))
            else:
                exp_value = math.exp(max(value, -700.0))
                score = exp_value / (1.0 + exp_value)
            scores.append(score)
        return scores


class RerankerSeam:
    """Select managed/local reranking while enforcing zero unsafe egress."""

    def __init__(
        self,
        *,
        mode: str,
        cohere: Reranker | None = None,
        local: Reranker | None = None,
        demographic_strings: Sequence[str] = (),
    ):
        normalized_mode = mode.strip().casefold()
        if normalized_mode not in {"cohere", "local"}:
            raise RerankerConfigurationError("RERANKER must be 'cohere' or 'local'")
        self.mode = normalized_mode
        self._cohere = cohere
        self._local = local
        self._demographic_strings = tuple(demographic_strings)
        self._cohere_breaker = _CircuitBreaker()

    def _local_scores(self, query: str, documents: list[str]) -> list[float] | None:
        if self._local is None:
            return None
        try:
            return _validate_reranker_scores(self._local.scores(query, documents), len(documents))
        except Exception:
            return None

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        demographic_strings: Sequence[str] = (),
    ) -> tuple[list[float] | None, str | None]:
        if self.mode == "local":
            scores = self._local_scores(query, documents)
            return (scores, None) if scores is not None else (None, "local_unavailable")

        screen = screen_phi(
            query,
            demographic_strings=(*self._demographic_strings, *tuple(demographic_strings)),
        )
        if not screen.safe:
            return self._local_scores(query, documents), "cohere_phi_screen"

        if self._cohere is None or not self._cohere_breaker.allow():
            return self._local_scores(query, documents), "cohere_unavailable"
        try:
            scores = _validate_reranker_scores(
                self._cohere.scores(query, documents), len(documents)
            )
        except Exception:
            self._cohere_breaker.failure()
            return self._local_scores(query, documents), "cohere_unavailable"
        self._cohere_breaker.success()
        return scores, None

    def model_for(self, *, reason: str | None, scored: bool) -> str:
        if not scored:
            return "hybrid-rrf"
        if self.mode == "cohere" and reason is None and self._cohere is not None:
            return self._cohere.model_name
        return self._local.model_name if self._local is not None else "hybrid-rrf"


class _PinnedBgeEmbedder:
    """Lazy pinned query encoder matching the committed passage-vector build."""

    def __init__(self, *, cache_dir: Path):
        self._cache_dir = cache_dir
        self._embedder: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        from fastembed import TextEmbedding
        from huggingface_hub import snapshot_download

        model_path = snapshot_download(
            repo_id=EMBED_SOURCE_REPO,
            revision=EMBED_REVISION,
            cache_dir=str(self._cache_dir),
            allow_patterns=[*_HF_COMMON_FILES, EMBED_ONNX],
        )
        self._embedder = TextEmbedding(
            EMBED_MODEL,
            cache_dir=str(self._cache_dir),
            specific_model_path=model_path,
        )
        return self._embedder

    def query_vector(self, query: str) -> Any:
        try:
            import numpy as np

            with self._lock:
                vectors = list(self._load().query_embed(query))
            if len(vectors) != 1:
                raise RuntimeError("dense embedder returned an invalid vector count")
            vector = np.asarray(vectors[0], dtype=np.float32).reshape(-1)
            if vector.shape != (EMBED_DIMENSION,):
                raise RuntimeError("dense embedder returned an invalid vector dimension")
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(vector).all() or not math.isfinite(norm) or norm <= 0.0:
                raise RuntimeError("dense embedder returned an invalid vector")
            return vector
        except RetrievalUnavailableError:
            raise
        except Exception as exc:
            raise RetrievalUnavailableError("dense embedder unavailable") from exc


class HybridRetriever:
    """Read-only, integrity-bound BM25+dense retrieval with optional reranking."""

    def __init__(
        self,
        corpus_dir: Path,
        *,
        dense_embedder: DenseEmbedder | None = None,
        reranker: RerankerSeam | None = None,
        demographic_strings: Sequence[str] = (),
    ):
        self._corpus_dir = Path(corpus_dir)
        try:
            integrity = check_index_manifest(self._corpus_dir)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RetrievalUnavailableError("corpus integrity check failed") from exc
        if not integrity.ok:
            raise RetrievalUnavailableError("corpus integrity check failed")

        try:
            self._metadata = json.loads(
                (self._corpus_dir / "index" / "metadata.json").read_text(encoding="utf-8")
            )
            self._chunks = [
                json.loads(line)
                for line in (self._corpus_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
                if line
            ]
            import numpy as np
            from rank_bm25 import BM25Okapi

            dimension = self._metadata["dense"]["dimension"]
            self._dense = np.fromfile(
                self._corpus_dir / "index" / "dense.f32", dtype=np.float32
            ).reshape(len(self._chunks), dimension)
            self._dense_norms = np.linalg.norm(self._dense, axis=1)
            self._bm25 = BM25Okapi(
                [_tokenize(chunk["quote"]) for chunk in self._chunks],
                k1=float(self._metadata["sparse"]["k1"]),
                b=float(self._metadata["sparse"]["b"]),
            )
        except (ImportError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RetrievalUnavailableError("retrieval index unavailable") from exc

        chunk_ids = [chunk.get("chunk_id") for chunk in self._chunks]
        if any(not isinstance(chunk_id, str) for chunk_id in chunk_ids) or len(set(chunk_ids)) != len(
            chunk_ids
        ):
            raise RetrievalUnavailableError("retrieval index unavailable")
        self._index_by_id = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
        self.manifest_hash = integrity.manifest_hash
        self.corpus_version = self._metadata["corpus_version"]

        cache_dir = Path(os.getenv("FASTEMBED_CACHE_DIR", "/tmp/w2-fastembed-cache"))
        self._dense_embedder = dense_embedder or _PinnedBgeEmbedder(cache_dir=cache_dir)
        if reranker is None:
            local = LocalMxbaiReranker(cache_dir=cache_dir)
            mode = os.getenv("RERANKER", "local").strip().casefold()
            cohere: CohereReranker | None = None
            api_key = os.getenv("COHERE_API_KEY", "")
            if mode == "cohere" and api_key:
                cohere = CohereReranker(
                    api_key, demographic_strings=demographic_strings
                )
            try:
                reranker = RerankerSeam(
                    mode=mode,
                    cohere=cohere,
                    local=local,
                    demographic_strings=demographic_strings,
                )
            except RerankerConfigurationError as exc:
                raise RetrievalUnavailableError("reranker configuration unavailable") from exc
        self._reranker = reranker

    def search(
        self,
        query: str,
        *,
        k: int,
        demographic_strings: Sequence[str] = (),
    ) -> RetrievalOutcome:
        search_started = time.perf_counter()
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= K_MAX:
            raise QueryContractError(f"k must be between 1 and {K_MAX}")
        canonical_query = _validate_canonical_query(
            query, demographic_strings=demographic_strings
        )
        degraded: list[str] = []

        sparse_scores = self._bm25.get_scores(_tokenize(canonical_query))
        sparse_indices = sorted(
            (index for index, score in enumerate(sparse_scores) if float(score) > 0.0),
            key=lambda index: (-float(sparse_scores[index]), self._chunks[index]["chunk_id"]),
        )[:CANDIDATE_POOL]

        dense_indices: list[int] = []
        try:
            import numpy as np

            query_vector = np.asarray(
                self._dense_embedder.query_vector(canonical_query), dtype=np.float32
            ).reshape(-1)
            if query_vector.shape != (self._dense.shape[1],):
                raise ValueError("dense query dimension mismatch")
            query_norm = float(np.linalg.norm(query_vector))
            if not math.isfinite(query_norm) or query_norm <= 0.0:
                raise ValueError("dense query norm invalid")
            similarities = (self._dense @ query_vector) / (
                np.maximum(self._dense_norms, 1e-12) * query_norm
            )
            dense_indices = sorted(
                (
                    index
                    for index, score in enumerate(similarities)
                    if math.isfinite(float(score)) and float(score) >= DENSE_MIN_SIMILARITY
                ),
                key=lambda index: (-float(similarities[index]), self._chunks[index]["chunk_id"]),
            )[:CANDIDATE_POOL]
        except RetrievalUnavailableError:
            _log.info(
                "retrieval.unavailable",
                extra={
                    "reason": "embedder_unavailable",
                    "latency_ms": round((time.perf_counter() - search_started) * 1000, 3),
                },
            )
            raise
        except Exception:
            degraded.append("dense_unavailable")

        fused = reciprocal_rank_fusion(
            sparse_ids=[self._chunks[index]["chunk_id"] for index in sparse_indices],
            dense_ids=[self._chunks[index]["chunk_id"] for index in dense_indices],
        )
        candidate_ids = sorted(fused, key=lambda chunk_id: (-fused[chunk_id], chunk_id))[
            :CANDIDATE_POOL
        ]
        if not candidate_ids:
            _log.info(
                "retrieval.query.executed",
                extra={
                    "hit": False,
                    "k": k,
                    "latency_ms": round((time.perf_counter() - search_started) * 1000, 3),
                    "degraded_reasons": tuple(degraded),
                },
            )
            return RetrievalOutcome((), self.corpus_version, self.manifest_hash, tuple(degraded))

        candidates = [self._chunks[self._index_by_id[chunk_id]] for chunk_id in candidate_ids]
        rerank_started = time.perf_counter()
        rerank_scores, rerank_reason = self._reranker.rerank(
            canonical_query,
            [candidate["quote"] for candidate in candidates],
            demographic_strings=demographic_strings,
        )
        rerank_latency_ms = round((time.perf_counter() - rerank_started) * 1000, 3)
        if rerank_reason is not None:
            degraded.append(rerank_reason)
        if rerank_scores is not None and len(rerank_scores) == len(candidates):
            ordering = sorted(
                (
                    index
                    for index, score in enumerate(rerank_scores)
                    if float(score) >= RERANK_MIN_SCORE
                ),
                key=lambda index: (-float(rerank_scores[index]), candidates[index]["chunk_id"]),
            )
            final_scores = [_bounded_score(float(score)) for score in rerank_scores]
            scored = True
        else:
            max_fused = max(fused.values())
            ordering = list(range(len(candidates)))
            final_scores = [fused[candidate["chunk_id"]] / max_fused for candidate in candidates]
            scored = False

        items = tuple(
            EvidenceHit(
                source_id=f"{candidates[index]['document_id']}@{self.manifest_hash}",
                section=candidates[index]["section"],
                chunk_id=candidates[index]["chunk_id"],
                quote=candidates[index]["quote"],
                score=final_scores[index],
                corpus_version=self.corpus_version,
            )
            for index in ordering[:k]
        )
        reranker_model = self._reranker.model_for(reason=rerank_reason, scored=scored)
        _log.info(
            "rerank.executed",
            extra={
                "candidate_count": len(candidates),
                "latency_ms": rerank_latency_ms,
                "mode": self._reranker.mode,
                "model": reranker_model.split("@", 1)[0],
                "version": reranker_model,
                "degraded_reason": rerank_reason,
            },
        )
        _log.info(
            "retrieval.query.executed",
            extra={
                "hit": bool(items),
                "k": k,
                "candidate_count": len(candidates),
                "latency_ms": round((time.perf_counter() - search_started) * 1000, 3),
                "degraded_reasons": tuple(degraded),
            },
        )
        return RetrievalOutcome(items, self.corpus_version, self.manifest_hash, tuple(degraded))


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(_normalize(text).casefold())


def _bounded_score(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _validate_reranker_scores(scores: Sequence[float], expected: int) -> list[float]:
    if len(scores) != expected:
        raise ValueError("reranker score count mismatch")
    validated: list[float] = []
    for raw_score in scores:
        if isinstance(raw_score, bool):
            raise ValueError("reranker score is not numeric")
        score = float(raw_score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("reranker score is outside the response contract")
        validated.append(score)
    return validated
