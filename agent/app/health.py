"""Liveness + readiness probes (ARCHITECTURE.md §2, §7, §6).

`/ready` runs a real probe per dependency — never an unconditional 200. Dependencies
are classified:
  - HARD  (OpenEMR FHIR metadata, Anthropic, session store): if any is down the
    agent cannot serve, so /ready returns 503 and Railway pulls it from rotation.
  - SOFT  (Langfuse, retrieval index): observability and guideline augmentation are
    off the W1 critical path (D13/§6), so failures report `degraded` while /ready
    remains 200.

Probes are injectable (create_app(readiness_checks=...)) so tests exercise the
hard/soft classification without real network. The defaults below do the real work.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

import httpx

from app.config import Settings
from corpus.check_index_manifest import check_index_manifest

Kind = Literal["hard", "soft"]


@dataclass(frozen=True)
class DependencyResult:
    name: str
    kind: Kind
    ok: bool
    detail: str


Probe = Callable[[Settings], Awaitable[DependencyResult]]

_PROBE_KINDS: dict[str, Kind] = {
    "openemr_fhir": "hard",
    "anthropic": "hard",
    "session_store": "hard",
    "langfuse": "soft",
    "retrieval_index": "soft",
    "active_reranker": "soft",
    "document_runtime": "hard",
    "document_category_read": "hard",
}


def _probe_name(probe: Probe) -> str:
    return getattr(probe, "__name__", "dependency").removeprefix("probe_")


def _probe_kind(probe: Probe) -> Kind:
    return _PROBE_KINDS.get(_probe_name(probe), "hard")


# --- default real probes ---------------------------------------------------

async def probe_openemr_fhir(settings: Settings) -> DependencyResult:
    """The FHIR CapabilityStatement is public — a cheap real reachability check."""
    url = str(settings.openemr_fhir_base_url).rstrip("/") + "/metadata"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers={"Accept": "application/fhir+json"})
        ok = resp.status_code == 200
        return DependencyResult("openemr_fhir", "hard", ok, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001 - readiness must never raise
        return DependencyResult("openemr_fhir", "hard", False, type(exc).__name__)


async def probe_anthropic(settings: Settings) -> DependencyResult:
    """Validate provider reachability + credentials without spending tokens
    (GET /v1/models lists models, no completion)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": settings.anthropic_api_key.get_secret_value(),
                    "anthropic-version": "2023-06-01",
                },
            )
        ok = resp.status_code == 200
        return DependencyResult("anthropic", "hard", ok, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return DependencyResult("anthropic", "hard", False, type(exc).__name__)


async def probe_session_store(settings: Settings) -> DependencyResult:
    """Execute the same minimal query the durable stores depend on.

    A TCP handshake is not sufficient readiness evidence: a listener can be up while
    authentication, database selection, or query execution is broken.
    """
    dsn = settings.session_store_dsn.get_secret_value()
    connection = None
    try:
        import asyncpg

        connection = await asyncpg.connect(dsn, timeout=5.0)
        value = await asyncio.wait_for(connection.fetchval("SELECT 1"), timeout=5.0)
        return DependencyResult(
            "session_store", "hard", value == 1, "ok" if value == 1 else "query_failed"
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyResult("session_store", "hard", False, type(exc).__name__)
    finally:
        if connection is not None:
            try:
                await connection.close(timeout=2.0)
            except Exception:  # noqa: BLE001 - readiness cleanup is best effort
                pass


async def probe_langfuse(settings: Settings) -> DependencyResult:
    """Soft dependency. Unconfigured Langfuse is 'ok' (disabled, not failed);
    a configured-but-unreachable Langfuse is degraded, never fatal."""
    if settings.langfuse_host is None:
        return DependencyResult("langfuse", "soft", True, "disabled")
    url = str(settings.langfuse_host).rstrip("/") + "/api/public/health"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        ok = resp.status_code < 500
        return DependencyResult("langfuse", "soft", ok, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return DependencyResult("langfuse", "soft", False, type(exc).__name__)


async def probe_retrieval_index(settings: Settings) -> DependencyResult:
    """Hash-check the corpus and execute a deterministic static synthetic search.

    The static search deliberately does not initialize an ONNX model.  It proves the
    committed chunk store is readable and contains a known non-clinical guideline term;
    the selected reranker is exercised by its own soft probe below.
    """

    del settings  # the corpus location is an integration/deploy setting, not a secret
    default_corpus = Path(__file__).resolve().parents[1] / "corpus"
    corpus_dir = Path(os.getenv("EVIDENCE_CORPUS_DIR", str(default_corpus)))
    def check() -> tuple[bool, str]:
        integrity = check_index_manifest(corpus_dir)
        if not integrity.ok:
            return False, "integrity_check_failed"
        chunks_path = corpus_dir / "chunks.jsonl"
        hits = 0
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            quote = row.get("quote") if isinstance(row, dict) else None
            if isinstance(quote, str) and "hypertension" in quote.casefold():
                hits += 1
        return (hits > 0, "ok" if hits > 0 else "synthetic_search_miss")

    try:
        ok, detail = await asyncio.to_thread(check)
    except Exception:  # noqa: BLE001 - readiness must never raise
        return DependencyResult(
            "retrieval_index", "soft", False, "integrity_check_failed"
        )
    return DependencyResult("retrieval_index", "soft", ok, detail)


async def probe_active_reranker(settings: Settings) -> DependencyResult:
    """Run the configured retrieval/reranking path on a synthetic clinical-term pair."""

    del settings
    default_corpus = Path(__file__).resolve().parents[1] / "corpus"
    corpus_dir = Path(os.getenv("EVIDENCE_CORPUS_DIR", str(default_corpus)))

    def check() -> bool:
        from corpus.retrieval import HybridRetriever

        outcome = HybridRetriever(corpus_dir).search("hypertension", k=2)
        return bool(outcome.items)

    try:
        ok = await asyncio.to_thread(check)
    except Exception:  # noqa: BLE001 - a soft dependency never raises from readiness
        return DependencyResult(
            "active_reranker", "soft", False, "synthetic_pair_failed"
        )
    return DependencyResult(
        "active_reranker", "soft", ok, "ok" if ok else "synthetic_pair_miss"
    )


def default_readiness_checks() -> list[Probe]:
    return [
        probe_openemr_fhir,
        probe_anthropic,
        probe_session_store,
        probe_langfuse,
        probe_retrieval_index,
        probe_active_reranker,
    ]


# --- aggregation -----------------------------------------------------------

@dataclass(frozen=True)
class ReadinessReport:
    status: Literal["ready", "degraded", "not_ready"]
    results: list[DependencyResult]

    @property
    def http_status(self) -> int:
        # Hard failure => 503; otherwise 200 (degraded still serves).
        return 503 if self.status == "not_ready" else 200

    def to_body(self) -> dict:
        return {
            "status": self.status,
            "checks": [
                {"name": r.name, "kind": r.kind, "ok": r.ok, "detail": r.detail}
                for r in self.results
            ],
        }


async def run_readiness(settings: Settings, checks: list[Probe]) -> ReadinessReport:
    results = await asyncio.gather(*(probe(settings) for probe in checks))
    hard_down = any((not r.ok) and r.kind == "hard" for r in results)
    soft_down = any((not r.ok) and r.kind == "soft" for r in results)
    if hard_down:
        status: str = "not_ready"
    elif soft_down:
        status = "degraded"
    else:
        status = "ready"
    return ReadinessReport(status=status, results=list(results))  # type: ignore[arg-type]


class CachedReadinessRunner:
    """Timeout-bound, stampede-safe readiness aggregation with a short TTL cache."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 10.0,
        # R07/REL1 (2026-07-19): raised 8.0 -> 20.0 on measured production data. With
        # the retrieval weights pre-baked into the image (no downloads at probe time),
        # the active_reranker probe's real fresh-retriever rerank still measured
        # 8.5-10.7 s on Railway's shared vCPU, so 8 s flagged a WORKING reranker as
        # `timeout` on every TTL refresh. Hard probes self-bound at <=5 s via their
        # inner httpx/asyncpg timeouts, so this budget effectively bounds only the
        # reranker probe (plan §5 R07: budget raise with documented rationale).
        probe_timeout_seconds: float = 20.0,
    ) -> None:
        if ttl_seconds <= 0 or probe_timeout_seconds <= 0:
            raise ValueError("readiness cache and timeout bounds must be positive")
        self._ttl = ttl_seconds
        self._timeout = probe_timeout_seconds
        self._cached: ReadinessReport | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def run(self, settings: Settings, checks: list[Probe]) -> ReadinessReport:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self._ttl:
            return self._cached
        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < self._ttl:
                return self._cached

            async def bounded(probe: Probe) -> DependencyResult:
                try:
                    return await asyncio.wait_for(
                        probe(settings), timeout=self._timeout
                    )
                except TimeoutError:
                    return DependencyResult(
                        _probe_name(probe), _probe_kind(probe), False, "timeout"
                    )
                except Exception as exc:  # noqa: BLE001 - readiness is a boundary
                    return DependencyResult(
                        _probe_name(probe),
                        _probe_kind(probe),
                        False,
                        type(exc).__name__,
                    )

            results = await asyncio.gather(*(bounded(probe) for probe in checks))
            hard_down = any((not item.ok) and item.kind == "hard" for item in results)
            soft_down = any((not item.ok) and item.kind == "soft" for item in results)
            status: Literal["ready", "degraded", "not_ready"]
            if hard_down:
                status = "not_ready"
            elif soft_down:
                status = "degraded"
            else:
                status = "ready"
            self._cached = ReadinessReport(status=status, results=list(results))
            self._cached_at = time.monotonic()
            return self._cached
