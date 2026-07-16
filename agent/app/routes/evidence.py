"""Typed PHI-free guideline evidence search endpoint.

The composition root may inject ``app.state.evidence_retriever``.  Otherwise the
committed corpus is initialized only on the first request, never at import time.

Traceability: W2-M14; W2-D4; W2-R3; W2_ARCHITECTURE.md §2a/§4/§5.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.middleware.correlation import correlation_id_var
from app.routes.openapi_contract import documented_errors, documented_response
from app.schemas.citations import EvidenceSnippet
from app.schemas.retrieval import (
    K_MAX,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
)
from app.session.store import (
    Session,
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)
from corpus.retrieval import (
    HybridRetriever,
    QueryContractError,
    RetrievalOutcome,
    RetrievalUnavailableError,
    build_clinical_query,
)


router = APIRouter()

EVIDENCE_RATE_WINDOW_SECONDS = 60.0
EVIDENCE_MAX_REQUESTS_PER_WINDOW = 12
EVIDENCE_MAX_CONCURRENT_PER_SESSION = 2
EVIDENCE_LIMITER_MAX_SESSIONS = 10_000
EVIDENCE_LIMITER_IDLE_TTL_SECONDS = 600.0


class EvidenceRetriever(Protocol):
    def search(
        self, query: str, *, k: int, demographic_strings: tuple[str, ...] = ()
    ) -> RetrievalOutcome: ...


class EvidenceRouteServices(Protocol):
    async def resolve_session(self, session_id: str) -> Session: ...


class EvidenceRequestLimitExceeded(RuntimeError):
    """A session exhausted its content-free retrieval admission budget."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__("evidence request limit exceeded")
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _SessionBudget:
    requests: deque[float] = field(default_factory=deque)
    active: int = 0
    last_seen: float = 0.0


class EvidenceRequestLimiter:
    """Bounded per-process rate and concurrency admission keyed by opaque session hash.

    The deployed demo has one web process. The state map is capped and idle entries are
    evicted, so attacking the limiter cannot replace retrieval abuse with unbounded memory.
    Multi-replica production should put the same counters in the shared session backend.
    """

    def __init__(
        self,
        *,
        max_requests: int = EVIDENCE_MAX_REQUESTS_PER_WINDOW,
        window_seconds: float = EVIDENCE_RATE_WINDOW_SECONDS,
        max_concurrent: int = EVIDENCE_MAX_CONCURRENT_PER_SESSION,
        max_sessions: int = EVIDENCE_LIMITER_MAX_SESSIONS,
        idle_ttl_seconds: float = EVIDENCE_LIMITER_IDLE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            max_requests < 1
            or window_seconds <= 0
            or max_concurrent < 1
            or max_sessions < 1
            or idle_ttl_seconds < window_seconds
        ):
            raise ValueError("evidence limiter bounds are invalid")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_concurrent = max_concurrent
        self._max_sessions = max_sessions
        self._idle_ttl_seconds = idle_ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._budgets: dict[bytes, _SessionBudget] = {}

    @staticmethod
    def _key(session_id: str) -> bytes:
        return hashlib.sha256(session_id.encode("utf-8")).digest()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        stale: list[bytes] = []
        for key, budget in self._budgets.items():
            while budget.requests and budget.requests[0] <= cutoff:
                budget.requests.popleft()
            if (
                budget.active == 0
                and not budget.requests
                and now - budget.last_seen >= self._idle_ttl_seconds
            ):
                stale.append(key)
        for key in stale:
            self._budgets.pop(key, None)

    def _make_room(self) -> None:
        inactive = [
            (budget.last_seen, key)
            for key, budget in self._budgets.items()
            if budget.active == 0 and not budget.requests
        ]
        if not inactive:
            raise EvidenceRequestLimitExceeded(retry_after_seconds=1)
        _last_seen, oldest_key = min(inactive)
        self._budgets.pop(oldest_key, None)

    async def _acquire(self, session_id: str) -> bytes:
        key = self._key(session_id)
        async with self._lock:
            now = self._clock()
            self._prune(now)
            budget = self._budgets.get(key)
            if budget is None:
                if len(self._budgets) >= self._max_sessions:
                    self._make_room()
                budget = _SessionBudget(last_seen=now)
                self._budgets[key] = budget
            if budget.active >= self._max_concurrent:
                raise EvidenceRequestLimitExceeded(retry_after_seconds=1)
            if len(budget.requests) >= self._max_requests:
                retry_after = max(
                    1,
                    math.ceil(
                        budget.requests[0] + self._window_seconds - now
                    ),
                )
                raise EvidenceRequestLimitExceeded(
                    retry_after_seconds=retry_after
                )
            budget.requests.append(now)
            budget.active += 1
            budget.last_seen = now
            return key

    async def _release(self, key: bytes) -> None:
        async with self._lock:
            budget = self._budgets.get(key)
            if budget is None:
                return
            budget.active = max(0, budget.active - 1)
            budget.last_seen = self._clock()

    @asynccontextmanager
    async def slot(self, session_id: str) -> AsyncIterator[None]:
        key = await self._acquire(session_id)
        try:
            yield
        finally:
            await self._release(key)


_default_retriever: EvidenceRetriever | None = None
_default_retriever_lock = threading.Lock()


def _get_limiter(request: Request) -> EvidenceRequestLimiter:
    limiter = getattr(request.app.state, "evidence_request_limiter", None)
    if limiter is None:
        limiter = EvidenceRequestLimiter()
        request.app.state.evidence_request_limiter = limiter
    if not isinstance(limiter, EvidenceRequestLimiter):
        raise RuntimeError("evidence request limiter is unavailable")
    return limiter


async def _resolve_session(
    services: EvidenceRouteServices, session_id: str
) -> Session:
    try:
        return await services.resolve_session(session_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found") from None
    except SessionExpiredError:
        raise HTTPException(status_code=401, detail="session expired") from None
    except SessionStoreUnavailable:
        raise HTTPException(status_code=503, detail="session store unavailable") from None


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
    responses={
        200: documented_response(
            "Version-pinned guideline snippets in rank order."
        ),
        **documented_errors(401, 404, 413, 422, 429, 503),
    },
)
async def search_evidence(
    payload: EvidenceSearchRequest,
    request: Request,
    session_id: Annotated[
        str,
        Header(
            alias="X-Copilot-Session-Id",
            min_length=1,
            max_length=128,
        ),
    ],
) -> EvidenceSearchResponse:
    services: EvidenceRouteServices = request.app.state.services
    session = await _resolve_session(services, session_id)
    limiter = _get_limiter(request)
    try:
        async with limiter.slot(session.session_id):
            demographic_strings = _request_demographic_strings(request)
            query = build_clinical_query(
                re.split(r"[,;|]+", payload.query),
                demographic_strings=demographic_strings,
            )
            retriever = _get_retriever(request)
            outcome = await run_in_threadpool(
                retriever.search,
                query,
                k=payload.k,
                demographic_strings=demographic_strings,
            )
            items = _validated_items(outcome)
    except EvidenceRequestLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="evidence request limit exceeded",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None
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
