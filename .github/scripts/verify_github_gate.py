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
        if self.repository != "worldofhacks/openemr-base-clean":
            raise BridgeVerificationError("repository_mismatch")
        if _SHA.fullmatch(self.sha) is None:
            raise BridgeVerificationError("sha_invalid")
        if self.workflow_name != "agent-eval-gate":
            raise BridgeVerificationError("workflow_mismatch")
        if not self.check_name or not self.artifact_name:
            raise BridgeVerificationError("name_invalid")
        if _DIGEST.fullmatch(self.artifact_digest) is None:
            raise BridgeVerificationError("artifact_digest_invalid")


def _objects(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    values = payload.get(key)
    if not isinstance(values, list):
        raise BridgeVerificationError("response_shape_invalid")
    return [item for item in values if isinstance(item, Mapping)]


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

    runs = [
        run
        for run in _objects(workflow_payload, "workflow_runs")
        if run.get("name") == expectation.workflow_name
        and run.get("head_sha") == expectation.sha
        and run.get("conclusion") == "success"
        and run.get("event") in {"push", "workflow_dispatch"}
    ]
    if len(runs) != 1:
        raise BridgeVerificationError("workflow_not_green")
    run_id = runs[0].get("id")

    checks = [
        check
        for check in _objects(checks_payload, "check_runs")
        if check.get("name") == expectation.check_name
        and check.get("head_sha") == expectation.sha
        and check.get("status") == "completed"
        and check.get("conclusion") == "success"
    ]
    if len(checks) != 1:
        raise BridgeVerificationError("check_not_green")

    artifacts = [
        artifact
        for artifact in _objects(artifacts_payload, "artifacts")
        if artifact.get("name") == expectation.artifact_name
        and artifact.get("expired") is False
        and artifact.get("digest") == expectation.artifact_digest
        and isinstance(artifact.get("workflow_run"), Mapping)
        and artifact["workflow_run"].get("id") == run_id
        and artifact["workflow_run"].get("head_sha") == expectation.sha
    ]
    if len(artifacts) != 1:
        raise BridgeVerificationError("artifact_not_attested")


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
            f"?head_sha={expected.sha}&status=completed&per_page=10",
            token,
        )
        checks_payload = _get_json(
            f"/repos/{repo}/commits/{expected.sha}/check-runs?per_page=100",
            token,
        )
        artifacts_payload = _get_json(
            f"/repos/{repo}/actions/artifacts?name="
            f"{quote(expected.artifact_name, safe='')}&per_page=20",
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
