"""Exact-SHA GitHub-to-GitLab bridge verification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/verify_github_gate.py"
_SPEC = importlib.util.spec_from_file_location("verify_github_gate", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
bridge = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bridge
_SPEC.loader.exec_module(bridge)


def _payloads(sha: str, digest: str):
    run = {
        "id": 42,
        "name": "agent-eval-gate",
        "head_sha": sha,
        "conclusion": "success",
        "event": "push",
    }
    return {
        "repository_payload": {"full_name": "worldofhacks/openemr-base-clean"},
        "workflow_payload": {"workflow_runs": [run]},
        "checks_payload": {
            "check_runs": [
                {
                    "name": "eval-tier2-live",
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
        "artifacts_payload": {
            "artifacts": [
                {
                    "name": "eval-results-tier2-live",
                    "expired": False,
                    "digest": digest,
                    "workflow_run": {"id": 42, "head_sha": sha},
                }
            ]
        },
    }


def _expectation(sha: str, digest: str):
    return bridge.BridgeExpectation(
        repository="worldofhacks/openemr-base-clean",
        sha=sha,
        workflow_name="agent-eval-gate",
        check_name="eval-tier2-live",
        artifact_name="eval-results-tier2-live",
        artifact_digest=digest,
    )


def test_bridge_accepts_one_exact_green_sha_and_digest():
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    bridge.validate_bridge_payloads(_expectation(sha, digest), **_payloads(sha, digest))


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("repo", "repository_mismatch"),
        ("sha", "workflow_not_green"),
        ("check", "check_not_green"),
        ("digest", "artifact_not_attested"),
    ],
)
def test_bridge_rejects_every_identity_or_result_mismatch(mutation: str, reason: str):
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    if mutation == "repo":
        payloads["repository_payload"]["full_name"] = "attacker/fork"
    elif mutation == "sha":
        payloads["workflow_payload"]["workflow_runs"][0]["head_sha"] = "c" * 40
    elif mutation == "check":
        payloads["checks_payload"]["check_runs"][0]["conclusion"] = "failure"
    else:
        payloads["artifacts_payload"]["artifacts"][0]["digest"] = (
            "sha256:" + "d" * 64
        )
    with pytest.raises(bridge.BridgeVerificationError, match=reason):
        bridge.validate_bridge_payloads(_expectation(sha, digest), **payloads)
