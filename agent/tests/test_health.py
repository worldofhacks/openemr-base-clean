"""E1.2 — /health (liveness) and a REAL /ready (ARCHITECTURE.md §2, §7, §5a, §6).

/ready must actually probe dependencies — no unconditional 200. Hard dependencies
(OpenEMR FHIR metadata, Anthropic, session store) failing => 503 with a per-dep
body. The soft dependency (Langfuse) failing => still 200 but flagged `degraded`,
because D13 + §6 keep the agent serving without observability.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.health import CachedReadinessRunner, DependencyResult
from app.routes.openapi_contract import NO_STORE_CACHE_CONTROL


def _app_with_checks(checks):
    from app.main import create_app

    return create_app(readiness_checks=checks)


def _ok(name, kind):
    async def probe(_settings):
        return DependencyResult(name=name, kind=kind, ok=True, detail="ok")

    return probe


def _down(name, kind):
    async def probe(_settings):
        return DependencyResult(name=name, kind=kind, ok=False, detail="unreachable")

    return probe


ALL_OK = [
    _ok("openemr_fhir", "hard"),
    _ok("anthropic", "hard"),
    _ok("session_store", "hard"),
    _ok("langfuse", "soft"),
]


def test_health_is_liveness_only_200(complete_env):
    with TestClient(_app_with_checks(ALL_OK)) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"
    assert resp.json()["sha"] == "unknown"
    assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL


def test_ready_200_when_all_dependencies_ok(complete_env):
    with TestClient(_app_with_checks(ALL_OK)) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert {c["name"]: c["ok"] for c in body["checks"]}["openemr_fhir"] is True
    assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL


def test_ready_503_when_hard_dependency_openemr_down(complete_env):
    checks = [_down("openemr_fhir", "hard"), _ok("anthropic", "hard"),
              _ok("session_store", "hard"), _ok("langfuse", "soft")]
    with TestClient(_app_with_checks(checks)) as client:
        resp = client.get("/ready")
    assert resp.status_code == 503  # pulled from rotation — cannot serve
    body = resp.json()
    assert body["status"] == "not_ready"
    failed = [c for c in body["checks"] if not c["ok"]]
    assert any(c["name"] == "openemr_fhir" and c["kind"] == "hard" for c in failed)
    assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL


def test_ready_200_degraded_when_soft_dependency_langfuse_down(complete_env):
    checks = [_ok("openemr_fhir", "hard"), _ok("anthropic", "hard"),
              _ok("session_store", "hard"), _down("langfuse", "soft")]
    with TestClient(_app_with_checks(checks)) as client:
        resp = client.get("/ready")
    # Soft dep down must NOT pull the instance from rotation (§6/§7).
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    langfuse = next(c for c in body["checks"] if c["name"] == "langfuse")
    assert langfuse["ok"] is False and langfuse["kind"] == "soft"
    assert resp.headers["cache-control"] == NO_STORE_CACHE_CONTROL


def test_ready_503_when_only_soft_ok_but_hard_down(complete_env):
    # A single hard failure => 503 even if everything else is fine (no unconditional 200).
    checks = [_ok("openemr_fhir", "hard"), _ok("anthropic", "hard"),
              _down("session_store", "hard"), _ok("langfuse", "soft")]
    with TestClient(_app_with_checks(checks)) as client:
        resp = client.get("/ready")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_readiness_runner_caches_successful_probe(complete_env):
    calls = 0

    async def probe(_settings):
        nonlocal calls
        calls += 1
        return DependencyResult("postgres", "hard", True, "ok")

    runner = CachedReadinessRunner(ttl_seconds=60, probe_timeout_seconds=1)
    settings = Settings()
    first = await runner.run(settings, [probe])
    second = await runner.run(settings, [probe])
    assert first is second
    assert calls == 1


@pytest.mark.asyncio
async def test_readiness_runner_bounds_soft_reranker_timeout(complete_env):
    async def probe_active_reranker(_settings):
        await asyncio.sleep(1)
        return DependencyResult("active_reranker", "soft", True, "ok")

    runner = CachedReadinessRunner(ttl_seconds=60, probe_timeout_seconds=0.01)
    report = await runner.run(Settings(), [probe_active_reranker])
    assert report.status == "degraded"
    assert report.http_status == 200
    assert report.results == [
        DependencyResult("active_reranker", "soft", False, "timeout")
    ]
