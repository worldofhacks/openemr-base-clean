#!/usr/bin/env python3
"""Verify one exact GitHub W2 gate before a GitLab bridge accepts it.

The script emits only a one-line PASS/FAIL code.  It never prints API payloads,
headers, tokens, artifact URLs, or commit metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote
from urllib.request import Request, urlopen


_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_REPOSITORY = "worldofhacks/openemr-base-clean"
_WORKFLOW_NAME = "agent-eval-gate"
_WORKFLOW_PATH = ".github/workflows/agent-eval-gate.yml"
_CHECK_NAME = "eval-tier2-live"
_ARTIFACT_NAME = "eval-results-tier2-live"
_MAIN_BRANCH = "main"
_TRUSTED_EVENTS = frozenset({"push", "workflow_dispatch"})


class BridgeVerificationError(RuntimeError):
    """The remote status does not prove the requested exact-SHA gate."""


@dataclass(frozen=True)
class BridgeExpectation:
    repository: str
    sha: str
    workflow_name: str
    check_name: str
    artifact_name: str
    artifact_digest: str

    def __post_init__(self) -> None:
        if self.repository != _REPOSITORY:
            raise BridgeVerificationError("repository_mismatch")
        if _SHA.fullmatch(self.sha) is None:
            raise BridgeVerificationError("sha_invalid")
        if self.workflow_name != _WORKFLOW_NAME:
            raise BridgeVerificationError("workflow_mismatch")
        if self.check_name != _CHECK_NAME:
            raise BridgeVerificationError("check_name_mismatch")
        if self.artifact_name != _ARTIFACT_NAME:
            raise BridgeVerificationError("artifact_name_mismatch")
        if _DIGEST.fullmatch(self.artifact_digest) is None:
            raise BridgeVerificationError("artifact_digest_invalid")


def _objects(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    values = payload.get(key)
    if not isinstance(values, list) or any(
        not isinstance(item, Mapping) for item in values
    ):
        raise BridgeVerificationError("response_shape_invalid")
    total_count = payload.get("total_count")
    if total_count is not None:
        if (
            not isinstance(total_count, int)
            or isinstance(total_count, bool)
            or total_count < 0
        ):
            raise BridgeVerificationError("response_shape_invalid")
        if total_count != len(values):
            raise BridgeVerificationError("response_incomplete")
    return values


def _positive_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _select_workflow_run(
    expectation: BridgeExpectation,
    workflow_payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any], int, int]:
    """Select the only complete trusted main run and return its run/suite ids."""

    runs = [
        run
        for run in _objects(workflow_payload, "workflow_runs")
        if run.get("name") == expectation.workflow_name
        and run.get("path") == _WORKFLOW_PATH
        and run.get("head_sha") == expectation.sha
        and run.get("head_branch") == _MAIN_BRANCH
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") in _TRUSTED_EVENTS
    ]
    if not runs:
        raise BridgeVerificationError("workflow_not_green")
    if len(runs) != 1:
        raise BridgeVerificationError("workflow_not_unique")
    selected = runs[0]
    run_id = selected.get("id")
    check_suite_id = selected.get("check_suite_id")
    if not _positive_id(run_id) or not _positive_id(check_suite_id):
        raise BridgeVerificationError("workflow_identity_invalid")
    return selected, run_id, check_suite_id


def validate_bridge_payloads(
    expectation: BridgeExpectation,
    *,
    repository_payload: Mapping[str, Any],
    workflow_payload: Mapping[str, Any],
    checks_payload: Mapping[str, Any],
    artifacts_payload: Mapping[str, Any],
) -> None:
    if repository_payload.get("full_name") != expectation.repository:
        raise BridgeVerificationError("repository_mismatch")

    _run, run_id, check_suite_id = _select_workflow_run(
        expectation, workflow_payload
    )

    checks = [
        check
        for check in _objects(checks_payload, "check_runs")
        if check.get("name") == expectation.check_name
        and check.get("head_sha") == expectation.sha
        and check.get("status") == "completed"
        and check.get("conclusion") == "success"
        and isinstance(check.get("check_suite"), Mapping)
        and check["check_suite"].get("id") == check_suite_id
    ]
    if not checks:
        raise BridgeVerificationError("check_not_green")
    if len(checks) != 1:
        raise BridgeVerificationError("check_not_unique")
    if not _positive_id(checks[0].get("id")):
        raise BridgeVerificationError("check_identity_invalid")

    artifacts = [
        artifact
        for artifact in _objects(artifacts_payload, "artifacts")
        if artifact.get("name") == expectation.artifact_name
        and artifact.get("expired") is False
        and artifact.get("digest") == expectation.artifact_digest
        and isinstance(artifact.get("workflow_run"), Mapping)
        and artifact["workflow_run"].get("id") == run_id
        and artifact["workflow_run"].get("head_sha") == expectation.sha
        and artifact["workflow_run"].get("head_branch") == _MAIN_BRANCH
    ]
    if not artifacts:
        raise BridgeVerificationError("artifact_not_attested")
    if len(artifacts) != 1:
        raise BridgeVerificationError("artifact_not_unique")
    if not _positive_id(artifacts[0].get("id")):
        raise BridgeVerificationError("artifact_identity_invalid")


def _get_json(path: str, token: str) -> Mapping[str, Any]:
    request = Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "openemr-w2-gitlab-bridge/1",
        },
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed GitHub host
        payload = json.loads(response.read())
    if not isinstance(payload, Mapping):
        raise BridgeVerificationError("response_shape_invalid")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--check-name", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--artifact-digest", required=True)
    args = parser.parse_args(argv)
    try:
        expected = BridgeExpectation(
            repository=args.repository,
            sha=args.sha,
            workflow_name=args.workflow_name,
            check_name=args.check_name,
            artifact_name=args.artifact_name,
            artifact_digest=args.artifact_digest,
        )
        token = os.environ.get("GITHUB_STATUS_TOKEN", "")
        if not token:
            raise BridgeVerificationError("token_unavailable")
        repo = quote(expected.repository, safe="/")
        repository_payload = _get_json(f"/repos/{repo}", token)
        workflow_payload = _get_json(
            f"/repos/{repo}/actions/workflows/agent-eval-gate.yml/runs"
            f"?head_sha={expected.sha}&branch=main&status=completed&per_page=100",
            token,
        )
        if repository_payload.get("full_name") != expected.repository:
            raise BridgeVerificationError("repository_mismatch")
        _run, run_id, check_suite_id = _select_workflow_run(
            expected, workflow_payload
        )
        checks_payload = _get_json(
            f"/repos/{repo}/check-suites/{check_suite_id}/check-runs"
            f"?check_name={quote(expected.check_name, safe='')}"
            "&filter=all&per_page=100",
            token,
        )
        artifacts_payload = _get_json(
            f"/repos/{repo}/actions/runs/{run_id}/artifacts?name="
            f"{quote(expected.artifact_name, safe='')}&per_page=100",
            token,
        )
        validate_bridge_payloads(
            expected,
            repository_payload=repository_payload,
            workflow_payload=workflow_payload,
            checks_payload=checks_payload,
            artifacts_payload=artifacts_payload,
        )
    except Exception as exc:  # noqa: BLE001 - expose only the closed reason code
        reason = (
            str(exc)
            if isinstance(exc, BridgeVerificationError)
            else "bridge_request_failed"
        )
        print(f"FAIL:{reason}", file=sys.stderr)
        return 1
    print("PASS:exact_sha_gate_attested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
