"""Typed PHI-free guideline evidence search endpoint.

The composition root may inject ``app.state.evidence_retriever``.  Otherwise the
committed corpus is initialized only on the first request, never at import time.

Traceability: W2-M14; W2-D4; W2-R3; W2_ARCHITECTURE.md §2a/§4/§5.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.middleware.correlation import correlation_id_var
from app.schemas.citations import EvidenceSnippet
from app.schemas.retrieval import (
    K_MAX,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
)
from corpus.retrieval import (
    HybridRetriever,
    QueryContractError,
    RetrievalOutcome,
    RetrievalUnavailableError,
    build_clinical_query,
)


router = APIRouter()


class EvidenceRetriever(Protocol):
    def search(
        self, query: str, *, k: int, demographic_strings: tuple[str, ...] = ()
    ) -> RetrievalOutcome: ...


_default_retriever: EvidenceRetriever | None = None
_default_retriever_lock = threading.Lock()


def _get_retriever(request: Request) -> EvidenceRetriever:
    injected = getattr(request.app.state, "evidence_retriever", None)
    if injected is not None:
        return injected

    factory = getattr(request.app.state, "evidence_retriever_factory", None)
    if factory is not None:
        return factory()

    global _default_retriever
    if _default_retriever is None:
        with _default_retriever_lock:
            if _default_retriever is None:
                default_corpus = Path(__file__).resolve().parents[2] / "corpus"
                corpus_dir = Path(os.getenv("EVIDENCE_CORPUS_DIR", str(default_corpus)))
                _default_retriever = HybridRetriever(corpus_dir)
    return _default_retriever


def _request_demographic_strings(request: Request) -> tuple[str, ...]:
    """Read the integration-provided, request-scoped demographic egress guard."""

    raw = getattr(request.state, "evidence_demographic_strings", ())
    if not isinstance(raw, (list, tuple)) or len(raw) > 16:
        raise RetrievalUnavailableError("invalid demographic guard context")
    if any(not isinstance(value, str) or not value.strip() for value in raw):
        raise RetrievalUnavailableError("invalid demographic guard context")
    return tuple(raw)


def _validated_items(outcome: RetrievalOutcome) -> list[EvidenceSnippet]:
    """Keep corpus-integrity checks without creating a parallel response schema."""

    if len(outcome.items) > K_MAX:
        raise RetrievalUnavailableError("evidence response exceeds the result cap")
    _corpus_id, separator, manifest_hash = outcome.corpus_version.rpartition("@")
    if not separator or re.fullmatch(r"[0-9a-f]{64}", manifest_hash) is None:
        raise RetrievalUnavailableError("corpus version is not manifest-bound")
    if manifest_hash != outcome.manifest_hash:
        raise RetrievalUnavailableError("retrieval manifest version mismatch")

    items: list[EvidenceSnippet] = []
    seen: set[str] = set()
    for hit in outcome.items:
        if hit.chunk_id in seen:
            raise RetrievalUnavailableError("evidence response contains duplicate chunks")
        if hit.corpus_version != outcome.corpus_version:
            raise RetrievalUnavailableError("evidence response mixes corpus versions")
        if not hit.source_id.endswith("@" + manifest_hash):
            raise RetrievalUnavailableError("evidence source is not manifest-bound")
        if not hit.section.strip() or not hit.quote.strip() or not 0.0 <= hit.score <= 1.0:
            raise RetrievalUnavailableError("retrieval returned an invalid evidence item")
        seen.add(hit.chunk_id)
        items.append(
            EvidenceSnippet(
                source_id=hit.source_id,
                section=hit.section,
                chunk_id=hit.chunk_id,
                quote=hit.quote,
                score=hit.score,
                corpus_version=hit.corpus_version,
            )
        )
    return items


@router.post(
    "/evidence/search",
    response_model=EvidenceSearchResponse,
    response_model_exclude_none=True,
)
async def search_evidence(
    payload: EvidenceSearchRequest, request: Request
) -> EvidenceSearchResponse:
    try:
        retriever = _get_retriever(request)
        demographic_strings = _request_demographic_strings(request)
        query = build_clinical_query(
            re.split(r"[,;|]+", payload.query),
            demographic_strings=demographic_strings,
        )
        outcome = await run_in_threadpool(
            retriever.search,
            query,
            k=payload.k,
            demographic_strings=demographic_strings,
        )
        items = _validated_items(outcome)
    except QueryContractError:
        raise HTTPException(
            status_code=422,
            detail="query must contain PHI-free condition/test terms only",
        ) from None
    except RetrievalUnavailableError:
        raise HTTPException(status_code=503, detail="guideline retrieval unavailable") from None

    return EvidenceSearchResponse(
        items=items,
        corpus_version=outcome.corpus_version,
        correlation_id=correlation_id_var.get(),
    )
