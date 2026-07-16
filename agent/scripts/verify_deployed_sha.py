#!/usr/bin/env python3
"""Content-free deployment identity, liveness, and readiness verifier."""

from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


_REQUIRED_HARD_CHECKS = frozenset(
    {
        "openemr_fhir",
        "anthropic",
        "session_store",
        "document_runtime",
        "document_category_read",
    }
)
_SYNTHETIC_SMOKE_CHECKS = frozenset({"retrieval_index", "active_reranker"})


def _get(base_url: str, path: str) -> Mapping[str, Any]:
    with urlopen(  # noqa: S310 - base URL is validated as HTTPS below
        Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"}),
        timeout=20,
    ) as response:
        if response.status != 200:
            raise RuntimeError("deployment_probe_failed")
        payload = json.loads(response.read())
    if not isinstance(payload, Mapping):
        raise RuntimeError("deployment_probe_invalid")
    return payload


def _verify_readiness_and_smoke(ready: Mapping[str, Any]) -> None:
    if ready.get("status") not in {"ready", "degraded"}:
        raise RuntimeError("deployment_not_ready")
    raw_checks = ready.get("checks")
    if not isinstance(raw_checks, list):
        raise RuntimeError("deployment_readiness_invalid")

    checks: dict[str, Mapping[str, Any]] = {}
    for raw_check in raw_checks:
        if not isinstance(raw_check, Mapping):
            raise RuntimeError("deployment_readiness_invalid")
        name = raw_check.get("name")
        if not isinstance(name, str) or not name or name in checks:
            raise RuntimeError("deployment_readiness_invalid")
        checks[name] = raw_check

    for name in _REQUIRED_HARD_CHECKS:
        check = checks.get(name)
        if check is None or check.get("kind") != "hard" or check.get("ok") is not True:
            raise RuntimeError("deployment_hard_probe_failed")
    if checks["document_runtime"].get("detail") != "ready":
        raise RuntimeError("worker_sha_attestation_failed")
    if checks["document_category_read"].get("detail") != "authorized_read_ok":
        raise RuntimeError("document_category_smoke_failed")

    for name in _SYNTHETIC_SMOKE_CHECKS:
        check = checks.get(name)
        if (
            check is None
            or check.get("kind") != "soft"
            or check.get("ok") is not True
            or check.get("detail") != "ok"
        ):
            raise RuntimeError("synthetic_retrieval_smoke_failed")

    if any(
        check.get("kind") == "hard" and check.get("ok") is not True
        for check in checks.values()
    ):
        raise RuntimeError("deployment_hard_probe_failed")


def verify(base_url: str, sha: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise ValueError("base_url_invalid")
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError("sha_invalid")
    health = _get(base_url, "/health")
    ready = _get(base_url, "/ready")
    if health.get("status") != "alive" or health.get("sha") != sha:
        raise RuntimeError("deployed_sha_mismatch")
    _verify_readiness_and_smoke(ready)

    openapi = _get(base_url, "/openapi.json")
    paths = openapi.get("paths")
    if not isinstance(paths, Mapping):
        raise RuntimeError("deployment_contract_missing")
    required = {"/chat", "/documents", "/documents/lab-trends"}
    if not required.issubset(paths):
        raise RuntimeError("deployment_contract_missing")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0 or args.timeout_seconds > 900:
        raise ValueError("timeout_invalid")
    deadline = time.monotonic() + args.timeout_seconds
    while True:
        try:
            verify(args.base_url, args.sha)
            break
        except Exception:  # noqa: BLE001 - deploy convergence is bounded below
            if time.monotonic() >= deadline:
                raise RuntimeError("deployment_verification_timeout") from None
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
    print("PASS:web_and_worker_identity_readiness_and_synthetic_smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
