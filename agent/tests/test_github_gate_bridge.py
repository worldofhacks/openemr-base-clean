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


def _run(
    sha: str,
    *,
    run_id: int = 42,
    check_suite_id: int = 420,
    name: str = "agent-eval-gate",
    path: str = ".github/workflows/agent-eval-gate.yml",
    head_branch: str = "main",
    event: str = "push",
    status: str = "completed",
    conclusion: str = "success",
):
    return {
        "id": run_id,
        "check_suite_id": check_suite_id,
        "name": name,
        "path": path,
        "head_sha": sha,
        "head_branch": head_branch,
        "event": event,
        "status": status,
        "conclusion": conclusion,
    }


def _check(
    sha: str,
    *,
    check_id: int = 84,
    check_suite_id: int = 420,
    name: str = "eval-tier2-live",
    status: str = "completed",
    conclusion: str = "success",
):
    return {
        "id": check_id,
        "name": name,
        "head_sha": sha,
        "status": status,
        "conclusion": conclusion,
        "check_suite": {"id": check_suite_id},
    }


def _artifact(
    sha: str,
    digest: str,
    *,
    artifact_id: int = 126,
    run_id: int = 42,
    name: str = "eval-results-tier2-live",
    head_branch: str = "main",
    expired: bool = False,
):
    return {
        "id": artifact_id,
        "name": name,
        "expired": expired,
        "digest": digest,
        "workflow_run": {
            "id": run_id,
            "head_branch": head_branch,
            "head_sha": sha,
        },
    }


def _payloads(sha: str, digest: str):
    return {
        "repository_payload": {"full_name": "worldofhacks/openemr-base-clean"},
        "workflow_payload": {"total_count": 1, "workflow_runs": [_run(sha)]},
        "checks_payload": {"total_count": 1, "check_runs": [_check(sha)]},
        "artifacts_payload": {
            "total_count": 1,
            "artifacts": [_artifact(sha, digest)],
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


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_bridge_accepts_one_exact_green_main_run(event: str):
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    payloads["workflow_payload"]["workflow_runs"][0]["event"] = event
    bridge.validate_bridge_payloads(_expectation(sha, digest), **payloads)


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


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("check_name", "eval-tier2-live-subset", "check_name_mismatch"),
        (
            "artifact_name",
            "eval-results-tier2-live-subset",
            "artifact_name_mismatch",
        ),
    ],
)
def test_bridge_expectation_pins_production_names(
    field: str, value: str, reason: str
):
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    values = {
        "repository": "worldofhacks/openemr-base-clean",
        "sha": sha,
        "workflow_name": "agent-eval-gate",
        "check_name": "eval-tier2-live",
        "artifact_name": "eval-results-tier2-live",
        "artifact_digest": digest,
    }
    values[field] = value
    with pytest.raises(bridge.BridgeVerificationError, match=reason):
        bridge.BridgeExpectation(**values)


def test_bridge_ignores_pr_and_diagnostic_runs_when_main_run_is_unique():
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    payloads["workflow_payload"]["workflow_runs"].extend(
        [
            _run(
                sha,
                run_id=43,
                check_suite_id=421,
                head_branch="feature/citation-probe",
                event="pull_request",
            ),
            _run(
                sha,
                run_id=44,
                check_suite_id=422,
                name="agent-eval-live-subset",
                path=".github/workflows/agent-eval-live-subset.yml",
                event="workflow_dispatch",
            ),
        ]
    )
    payloads["workflow_payload"]["total_count"] = 3
    bridge.validate_bridge_payloads(_expectation(sha, digest), **payloads)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("branch", "workflow_not_green"),
        ("path", "workflow_not_green"),
        ("check_suite", "check_not_green"),
        ("artifact_run", "artifact_not_attested"),
        ("artifact_branch", "artifact_not_attested"),
        ("diagnostic_check", "check_not_green"),
        ("diagnostic_artifact", "artifact_not_attested"),
    ],
)
def test_bridge_rejects_non_main_diagnostic_or_cross_run_mixing(
    mutation: str, reason: str
):
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    if mutation == "branch":
        payloads["workflow_payload"]["workflow_runs"][0]["head_branch"] = "feature"
    elif mutation == "path":
        payloads["workflow_payload"]["workflow_runs"][0]["path"] = (
            ".github/workflows/agent-eval-live-subset.yml"
        )
    elif mutation == "check_suite":
        payloads["checks_payload"]["check_runs"][0]["check_suite"]["id"] = 999
    elif mutation == "artifact_run":
        payloads["artifacts_payload"]["artifacts"][0]["workflow_run"]["id"] = 999
    elif mutation == "artifact_branch":
        payloads["artifacts_payload"]["artifacts"][0]["workflow_run"][
            "head_branch"
        ] = "feature"
    elif mutation == "diagnostic_check":
        payloads["checks_payload"]["check_runs"][0]["name"] = (
            "eval-tier2-live-subset"
        )
    else:
        payloads["artifacts_payload"]["artifacts"][0]["name"] = (
            "eval-results-tier2-live-subset"
        )
    with pytest.raises(bridge.BridgeVerificationError, match=reason):
        bridge.validate_bridge_payloads(_expectation(sha, digest), **payloads)


def test_bridge_rejects_duplicate_successful_main_runs_for_same_sha():
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    payloads["workflow_payload"]["workflow_runs"].append(
        _run(sha, run_id=43, check_suite_id=421, event="workflow_dispatch")
    )
    payloads["workflow_payload"]["total_count"] = 2
    with pytest.raises(bridge.BridgeVerificationError, match="workflow_not_unique"):
        bridge.validate_bridge_payloads(_expectation(sha, digest), **payloads)


@pytest.mark.parametrize(
    ("surface", "reason"),
    [
        ("check", "check_not_unique"),
        ("artifact", "artifact_not_unique"),
    ],
)
def test_bridge_rejects_duplicate_attestations_in_selected_run(
    surface: str, reason: str
):
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    if surface == "check":
        payloads["checks_payload"]["check_runs"].append(_check(sha, check_id=85))
        payloads["checks_payload"]["total_count"] = 2
    else:
        payloads["artifacts_payload"]["artifacts"].append(
            _artifact(sha, digest, artifact_id=127)
        )
        payloads["artifacts_payload"]["total_count"] = 2
    with pytest.raises(bridge.BridgeVerificationError, match=reason):
        bridge.validate_bridge_payloads(_expectation(sha, digest), **payloads)


def test_bridge_fails_closed_when_api_page_is_incomplete():
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    payloads["workflow_payload"]["total_count"] = 2
    with pytest.raises(bridge.BridgeVerificationError, match="response_incomplete"):
        bridge.validate_bridge_payloads(_expectation(sha, digest), **payloads)


def test_main_fetches_check_and_artifact_from_selected_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    sha = "a" * 40
    digest = "sha256:" + "b" * 64
    payloads = _payloads(sha, digest)
    requests: list[str] = []

    def fake_get_json(path: str, token: str):
        assert token == "masked-test-token"
        requests.append(path)
        if path == "/repos/worldofhacks/openemr-base-clean":
            return payloads["repository_payload"]
        if "/actions/workflows/agent-eval-gate.yml/runs?" in path:
            return payloads["workflow_payload"]
        if "/check-suites/420/check-runs?" in path:
            return payloads["checks_payload"]
        if "/actions/runs/42/artifacts?" in path:
            return payloads["artifacts_payload"]
        raise AssertionError(f"unexpected request path: {path}")

    monkeypatch.setattr(bridge, "_get_json", fake_get_json)
    monkeypatch.setenv("GITHUB_STATUS_TOKEN", "masked-test-token")
    result = bridge.main(
        [
            "--repository",
            "worldofhacks/openemr-base-clean",
            "--sha",
            sha,
            "--workflow-name",
            "agent-eval-gate",
            "--check-name",
            "eval-tier2-live",
            "--artifact-name",
            "eval-results-tier2-live",
            "--artifact-digest",
            digest,
        ]
    )

    assert result == 0
    assert capsys.readouterr().out == "PASS:exact_sha_gate_attested\n"
    assert any("/check-suites/420/check-runs?" in path for path in requests)
    assert any("/actions/runs/42/artifacts?" in path for path in requests)
    assert all("/commits/" not in path for path in requests)
