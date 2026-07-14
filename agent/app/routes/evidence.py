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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from app.middleware.correlation import correlation_id_var
from corpus.retrieval import (
    K_MAX,
    HybridRetriever,
    QueryContractError,
    RetrievalOutcome,
    RetrievalUnavailableError,
    build_clinical_query,
)


router = APIRouter()


class EvidenceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=180)
    k: int = Field(default=5, ge=1, le=K_MAX)

    @field_validator("query")
    @classmethod
    def validate_clinical_query(cls, value: str) -> str:
        # Separators let trusted callers pass several coded condition/test terms
        # without turning this endpoint into a free-form conversation surface.
        terms = re.split(r"[,;|]+", value)
        try:
            return build_clinical_query(terms)
        except QueryContractError as exc:
            raise ValueError("query must contain PHI-free condition/test terms only") from exc


class EvidenceSnippet(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    corpus_version: str = Field(min_length=1)


class EvidenceSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    items: list[EvidenceSnippet]
    corpus_version: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_versioned_items(self) -> EvidenceSearchResponse:
        if len(self.items) > K_MAX:
            raise ValueError("evidence response exceeds the result cap")
        chunk_ids = [item.chunk_id for item in self.items]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("evidence response contains duplicate chunks")
        _corpus_id, separator, manifest_hash = self.corpus_version.rpartition("@")
        if not separator or re.fullmatch(r"[0-9a-f]{64}", manifest_hash) is None:
            raise ValueError("corpus version is not manifest-bound")
        for item in self.items:
            if item.corpus_version != self.corpus_version:
                raise ValueError("evidence response mixes corpus versions")
            if not item.source_id.endswith("@" + manifest_hash):
                raise ValueError("evidence source is not manifest-bound")
        return self


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
        outcome = await run_in_threadpool(
            retriever.search,
            payload.query,
            k=payload.k,
            demographic_strings=demographic_strings,
        )
    except QueryContractError:
        raise HTTPException(
            status_code=422,
            detail="query must contain PHI-free condition/test terms only",
        ) from None
    except RetrievalUnavailableError:
        raise HTTPException(status_code=503, detail="guideline retrieval unavailable") from None

    return EvidenceSearchResponse(
        items=[
            EvidenceSnippet(
                source_id=item.source_id,
                section=item.section,
                chunk_id=item.chunk_id,
                quote=item.quote,
                score=item.score,
                corpus_version=item.corpus_version,
            )
            for item in outcome.items
        ],
        corpus_version=outcome.corpus_version,
        correlation_id=correlation_id_var.get(),
    )
