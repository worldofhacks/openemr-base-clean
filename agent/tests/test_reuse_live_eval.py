"""Exact-SHA, same-run attestation for paid Tier-2 result reuse."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
import zipfile

import pytest

from evals.golden_loader import load_golden_cases


_SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/reuse_live_eval.py"
_SPEC = importlib.util.spec_from_file_location("reuse_live_eval", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
reuse = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = reuse
_SPEC.loader.exec_module(reuse)


SHA = "a" * 40
OTHER_SHA = "b" * 40
REPOSITORY = "worldofhacks/openemr-base-clean"


def _green_result(sha: str = SHA) -> dict[str, Any]:
    denominators = {
        "schema_valid": 50,
        "citation_present": 50,
        "factually_consistent": 23,
        "safe_refusal": 10,
        "no_phi_in_logs": 50,
    }
    categories = [
        {
            "rubric": rubric,
            "numerator": denominator,
            "denominator": denominator,
            "inconclusive": 0,
            "current_score": 1.0,
            "baseline_score": 1.0,
            "percentage_point_delta": 0.0,
            "threshold": 1.0 if rubric != "factually_consistent" else 0.9,
            "passed": True,
            "trigger": "green",
        }
        for rubric, denominator in denominators.items()
    ]
    case_ids = [case.case_id for case in load_golden_cases(reuse.DEFAULT_MANIFEST)]
    assert len(case_ids) == 50
    cases = [
        {
            "case_id": case_id,
            "status": "PASS",
            "rubrics": {rubric: True for rubric in denominators},
        }
        for case_id in case_ids
    ]
    return {
        "schema_version": 1,
        "status": "PASS",
        "tier": "live",
        "source_sha": sha,
        "manifest_sha256": hashlib.sha256(
            reuse.DEFAULT_MANIFEST.read_bytes()
        ).hexdigest(),
        "recordings_sha256": None,
        "case_count": 50,
        "executor_call_count": 50,
        "inconclusive_reason": None,
        "limits": {"max_cost_usd": 10.0, "max_seconds": 1800.0},
        "categories": categories,
        "cases": cases,
        "metrics": {"cost_usd": 3.0, "elapsed_seconds": 120.0},
    }


def _archive_result(
    result: dict[str, Any],
    *,
    name: str = "results-tier2.json",
) -> tuple[bytes, bytes]:
    result_bytes = (json.dumps(result, sort_keys=True) + "\n").encode()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(name, result_bytes)
    return stream.getvalue(), result_bytes


class FakeClient:
    api_base = "https://api.github.com"

    def __init__(self, sha: str = SHA) -> None:
        self.sha = sha
        self.workflow = {
            "id": 11,
            "name": "agent-eval-gate",
            "path": ".github/workflows/agent-eval-gate.yml",
            "state": "active",
        }
        self.run = {
            "id": 42,
            "workflow_id": 11,
            "name": "agent-eval-gate",
            "path": (f".github/workflows/agent-eval-gate.yml@tier2/{sha}"),
            "head_sha": sha,
            "head_branch": f"tier2/{sha}",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "check_suite_id": 88,
            "run_attempt": 1,
            "repository": {"full_name": REPOSITORY},
            "head_repository": {"full_name": REPOSITORY},
        }
        self.runs = [self.run]
        self.jobs = [
            {
                "id": 77,
                "name": "eval-tier2-live",
                "run_id": 42,
                "head_sha": sha,
                "head_branch": f"tier2/{sha}",
                "workflow_name": "agent-eval-gate",
                "status": "completed",
                "conclusion": "success",
                "run_url": f"{self.api_base}/repos/{REPOSITORY}/actions/runs/42",
                "check_run_url": (f"{self.api_base}/repos/{REPOSITORY}/check-runs/77"),
            }
        ]
        self.check = {
            "id": 77,
            "name": "eval-tier2-live",
            "head_sha": sha,
            "status": "completed",
            "conclusion": "success",
            "check_suite": {"id": 88},
            "app": {"slug": "github-actions"},
        }
        self.archive, self.result_bytes = _archive_result(_green_result(sha))
        self.artifact = {
            "id": 66,
            "name": "eval-results-tier2-live",
            "expired": False,
            "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest(),
            "archive_download_url": (
                f"{self.api_base}/repos/{REPOSITORY}/actions/artifacts/66/zip"
            ),
            "workflow_run": {
                "id": 42,
                "head_sha": sha,
                "head_branch": f"tier2/{sha}",
            },
        }
        self.artifacts = [self.artifact]
        self.calls: list[tuple[str, str]] = []

    def replace_archive(self, archive: bytes) -> None:
        self.archive = archive
        self.artifact["digest"] = "sha256:" + hashlib.sha256(archive).hexdigest()

    def get_json(self, path: str) -> dict[str, Any]:
        self.calls.append(("json", path))
        if path.endswith("/actions/workflows/agent-eval-gate.yml"):
            return self.workflow
        if "/actions/workflows/agent-eval-gate.yml/runs?" in path:
            return {"total_count": len(self.runs), "workflow_runs": self.runs}
        if "/actions/runs/42/attempts/1/jobs?" in path:
            return {"total_count": len(self.jobs), "jobs": self.jobs}
        if path.endswith("/check-runs/77"):
            return self.check
        if "/actions/runs/42/artifacts?" in path:
            return {"total_count": len(self.artifacts), "artifacts": self.artifacts}
        raise AssertionError("unexpected mocked API route")

    def get_bytes(self, path: str) -> bytes:
        self.calls.append(("bytes", path))
        assert path == f"/repos/{REPOSITORY}/actions/artifacts/66/zip"
        return self.archive


def _expectation(*, current_run_id: int = 999) -> Any:
    return reuse.ReuseExpectation(
        repository=REPOSITORY,
        workflow="agent-eval-gate.yml",
        sha=SHA,
        current_run_id=current_run_id,
    )


def test_reuses_only_prior_exact_tier2_push_and_ignores_main_and_current_run():
    client = FakeClient()
    current = deepcopy(client.run)
    current["id"] = 999
    main = deepcopy(client.run)
    main["id"] = 1000
    main["head_branch"] = "main"
    client.runs = [current, main, client.run]

    result = reuse.fetch_reusable_result(client, _expectation())

    assert result == client.result_bytes
    runs_call = next(path for kind, path in client.calls if "/runs?" in path)
    assert "event=push" in runs_call
    assert "status=completed" in runs_call
    assert f"head_sha={SHA}" in runs_call
    assert f"branch=tier2%2F{SHA}" in runs_call
    assert ("bytes", f"/repos/{REPOSITORY}/actions/artifacts/66/zip") in client.calls


@pytest.mark.parametrize(
    "mutation",
    [
        "pull_request_run",
        "main_run",
        "current_run",
        "duplicate_run",
        "job_other_run",
        "job_failed",
        "other_check_suite",
    ],
)
def test_rejects_untrusted_run_or_job_check_binding(mutation: str):
    client = FakeClient()
    current_run_id = 999
    if mutation == "pull_request_run":
        client.run["event"] = "pull_request"
    elif mutation == "main_run":
        client.run["head_branch"] = "main"
    elif mutation == "current_run":
        current_run_id = 42
    elif mutation == "duplicate_run":
        duplicate = deepcopy(client.run)
        duplicate["id"] = 43
        client.runs.append(duplicate)
    elif mutation == "job_other_run":
        client.jobs[0]["run_id"] = 41
    elif mutation == "job_failed":
        client.jobs[0]["conclusion"] = "failure"
    else:
        client.check["check_suite"]["id"] = 89

    with pytest.raises(reuse.ReuseUnavailable):
        reuse.fetch_reusable_result(client, _expectation(current_run_id=current_run_id))


@pytest.mark.parametrize(
    "mutation",
    ["expired", "other_run", "ambiguous", "digest_mismatch"],
)
def test_rejects_untrusted_or_ambiguous_artifact(mutation: str):
    client = FakeClient()
    if mutation == "expired":
        client.artifact["expired"] = True
    elif mutation == "other_run":
        client.artifact["workflow_run"]["id"] = 41
    elif mutation == "ambiguous":
        client.artifacts.append(deepcopy(client.artifact))
    else:
        client.artifact["digest"] = "sha256:" + "0" * 64

    with pytest.raises(reuse.ReuseUnavailable):
        reuse.fetch_reusable_result(client, _expectation())


@pytest.mark.parametrize(
    "unsafe_name", ["../results-tier2.json", "/results-tier2.json"]
)
def test_rejects_unsafe_archive_path(unsafe_name: str):
    client = FakeClient()
    archive, _ = _archive_result(_green_result(), name=unsafe_name)
    client.replace_archive(archive)

    with pytest.raises(reuse.ReuseUnavailable):
        reuse.fetch_reusable_result(client, _expectation())


def test_rejects_archive_with_more_than_one_result():
    client = FakeClient()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("results-tier2.json", client.result_bytes)
        bundle.writestr("other.json", b"{}")
    client.replace_archive(stream.getvalue())

    with pytest.raises(reuse.ReuseUnavailable):
        reuse.fetch_reusable_result(client, _expectation())


@pytest.mark.parametrize(
    "mutation",
    ["wrong_sha", "recorded", "49_cases", "failing_category", "stale_manifest"],
)
def test_result_must_be_exact_sha_live_50_case_and_fully_green(mutation: str):
    result = _green_result()
    if mutation == "wrong_sha":
        result["source_sha"] = OTHER_SHA
    elif mutation == "recorded":
        result["tier"] = "recorded"
    elif mutation == "49_cases":
        result["case_count"] = 49
        result["executor_call_count"] = 49
        result["cases"] = result["cases"][:49]
    elif mutation == "failing_category":
        category = next(
            item
            for item in result["categories"]
            if item["rubric"] == "factually_consistent"
        )
        category["numerator"] -= 1
        category["current_score"] = category["numerator"] / category["denominator"]
    else:
        result["manifest_sha256"] = "0" * 64
    payload = json.dumps(result).encode()

    with pytest.raises(reuse.ReuseUnavailable):
        reuse.validate_live_result(payload, SHA)


def test_result_json_with_duplicate_identity_key_fails_closed():
    payload = (
        b'{"source_sha":"'
        + SHA.encode()
        + b'","source_sha":"'
        + OTHER_SHA.encode()
        + b'"}'
    )

    with pytest.raises(reuse.ReuseUnavailable, match="json_duplicate_key"):
        reuse.validate_live_result(payload, SHA)


def test_main_copies_valid_result_and_emits_only_safe_true_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    client = FakeClient()
    output = tmp_path / "results-tier2.json"
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    assert (
        reuse.main(
            ["--sha", SHA, "--output", str(output)],
            client_factory=lambda token: client if token == "secret-token" else None,
        )
        == 0
    )

    assert output.read_bytes() == client.result_bytes
    assert github_output.read_text() == "reuse=true\n"
    assert capsys.readouterr().out == "reuse=true\n"


def test_main_miss_is_nonfatal_removes_stale_output_and_leaks_no_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    output = tmp_path / "results-tier2.json"
    output.write_text("case-secret-identifier")
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    def unavailable(_: str) -> Any:
        raise reuse.ReuseUnavailable("response-containing-case-secret-identifier")

    assert (
        reuse.main(
            ["--sha", SHA, "--output", str(output)],
            client_factory=unavailable,
        )
        == 0
    )

    captured = capsys.readouterr()
    assert not output.exists()
    assert github_output.read_text() == "reuse=false\n"
    assert captured.out == "reuse=false\n"
    assert captured.err == ""
    assert "case-secret-identifier" not in captured.out
