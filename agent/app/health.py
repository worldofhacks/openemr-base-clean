"""Liveness + readiness probes (ARCHITECTURE.md §2, §7, §6).

`/ready` runs a real probe per dependency — never an unconditional 200. Dependencies
are classified:
  - HARD  (OpenEMR FHIR metadata, Anthropic, session store): if any is down the
    agent cannot serve, so /ready returns 503 and Railway pulls it from rotation.
  - SOFT  (Langfuse): observability is off the critical path (D13/§6), so a failure
    is reported as `degraded` but /ready still returns 200.

Probes are injectable (create_app(readiness_checks=...)) so tests exercise the
hard/soft classification without real network. The defaults below do the real work.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal
from urllib.parse import urlsplit

import httpx

from app.config import Settings

Kind = Literal["hard", "soft"]


@dataclass(frozen=True)
class DependencyResult:
    name: str
    kind: Kind
    ok: bool
    detail: str


Probe = Callable[[Settings], Awaitable[DependencyResult]]


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
    """TCP reachability to the Postgres host:port (E2.2 upgrades this to SELECT 1
    once the store lands — no DB driver dependency at E1)."""
    dsn = settings.session_store_dsn.get_secret_value()
    parts = urlsplit(dsn)
    host, port = parts.hostname, parts.port or 5432
    if not host:
        return DependencyResult("session_store", "hard", False, "no host in DSN")
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=5.0)
        writer.close()
        await writer.wait_closed()
        return DependencyResult("session_store", "hard", True, f"tcp {host}:{port}")
    except Exception as exc:  # noqa: BLE001
        return DependencyResult("session_store", "hard", False, type(exc).__name__)


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


def default_readiness_checks() -> list[Probe]:
    return [probe_openemr_fhir, probe_anthropic, probe_session_store, probe_langfuse]


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
