"""Fail-closed tests for the exact-SHA Railway deployment verifier."""

from __future__ import annotations

from copy import deepcopy

import pytest

import scripts.verify_deployed_sha as verify_module


def _ready(*, status: str = "ready") -> dict[str, object]:
    return {
        "status": status,
        "checks": [
            {
                "name": "openemr_fhir",
                "kind": "hard",
                "ok": True,
                "detail": "HTTP 200",
            },
            {
                "name": "anthropic",
                "kind": "hard",
                "ok": True,
                "detail": "HTTP 200",
            },
            {
                "name": "session_store",
                "kind": "hard",
                "ok": True,
                "detail": "ok",
            },
            {
                "name": "document_runtime",
                "kind": "hard",
                "ok": True,
                "detail": "ready",
            },
            {
                "name": "document_category_read",
                "kind": "hard",
                "ok": True,
                "detail": "authorized_read_ok",
            },
            {
                "name": "retrieval_index",
                "kind": "soft",
                "ok": True,
                "detail": "ok",
            },
            {
                "name": "active_reranker",
                "kind": "soft",
                "ok": True,
                "detail": "ok",
            },
            {
                "name": "langfuse",
                "kind": "soft",
                "ok": status == "ready",
                "detail": "ok" if status == "ready" else "unreachable",
            },
        ],
    }


def _check(payload: dict[str, object], name: str) -> dict[str, object]:
    checks = payload["checks"]
    assert isinstance(checks, list)
    return next(item for item in checks if item["name"] == name)


def test_readiness_attests_worker_and_runs_bounded_synthetic_probes() -> None:
    verify_module._verify_readiness_and_smoke(_ready(status="degraded"))


@pytest.mark.parametrize(
    ("name", "update", "error"),
    [
        ("document_runtime", {"detail": "worker_heartbeat_missing"}, "worker_sha"),
        (
            "document_category_read",
            {"detail": "pending_first_pinned_job"},
            "document_category",
        ),
        ("retrieval_index", {"ok": False}, "synthetic_retrieval"),
        ("active_reranker", {"detail": "synthetic_pair_miss"}, "synthetic_retrieval"),
    ],
)
def test_readiness_rejects_missing_attestation_or_smoke_evidence(
    name: str, update: dict[str, object], error: str
) -> None:
    payload = deepcopy(_ready())
    _check(payload, name).update(update)

    with pytest.raises(RuntimeError, match=error):
        verify_module._verify_readiness_and_smoke(payload)


def test_verify_requires_web_sha_and_w2_route_contract(monkeypatch) -> None:
    sha = "a" * 40
    payloads = {
        "/health": {"status": "alive", "sha": sha},
        "/ready": _ready(),
        "/openapi.json": {
            "paths": {
                "/chat": {},
                "/documents": {},
                "/documents/lab-trends": {},
            }
        },
    }
    monkeypatch.setattr(verify_module, "_get", lambda _base, path: payloads[path])

    verify_module.verify("https://agent.example", sha)
