#!/usr/bin/env python3
"""Reuse one already-green exact-SHA Tier-2 result without another live spend.

Only fixed status text is emitted. GitHub response bodies, artifact contents,
identifiers, URLs, and validation exceptions are deliberately never logged.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)
import zipfile


_ROOT = Path(__file__).resolve().parents[2]
_AGENT_ROOT = _ROOT / "agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from evals.golden_loader import DEFAULT_MANIFEST  # noqa: E402
from evals.w2_models import Rubric  # noqa: E402
from evals.w2_runner import baseline_from_result  # noqa: E402


API_BASE = "https://api.github.com"
EXPECTED_REPOSITORY = "worldofhacks/openemr-base-clean"
EXPECTED_WORKFLOW_FILE = "agent-eval-gate.yml"
EXPECTED_WORKFLOW_NAME = "agent-eval-gate"
EXPECTED_WORKFLOW_PATH = ".github/workflows/agent-eval-gate.yml"
EXPECTED_JOB_NAME = "eval-tier2-live"
EXPECTED_ARTIFACT_NAME = "eval-results-tier2-live"
EXPECTED_RESULT_NAME = "results-tier2.json"
EXPECTED_CASE_COUNT = 50
MAX_API_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_PAGES = 10
PAGE_SIZE = 100

_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:([0-9a-f]{64})")


class ReuseUnavailable(RuntimeError):
    """The available evidence does not prove a reusable exact-SHA result."""


class RestClient(Protocol):
    api_base: str

    def get_json(self, path: str) -> Mapping[str, Any]: ...

    def get_bytes(self, path: str) -> bytes: ...


@dataclass(frozen=True)
class ReuseExpectation:
    repository: str
    workflow: str
    sha: str
    current_run_id: int

    def __post_init__(self) -> None:
        if self.repository != EXPECTED_REPOSITORY:
            raise ReuseUnavailable("repository_mismatch")
        if self.workflow != EXPECTED_WORKFLOW_FILE:
            raise ReuseUnavailable("workflow_mismatch")
        if _SHA.fullmatch(self.sha) is None:
            raise ReuseUnavailable("sha_invalid")
        if type(self.current_run_id) is not int or self.current_run_id <= 0:
            raise ReuseUnavailable("current_run_invalid")

    @property
    def branch(self) -> str:
        return f"tier2/{self.sha}"


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Never forward the GitHub token to an artifact storage origin."""

    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        destination = urlsplit(newurl)
        if destination.scheme != "https":
            raise ReuseUnavailable("redirect_rejected")
        if destination.netloc != urlsplit(API_BASE).netloc:
            redirected.remove_header("Authorization")
            redirected.remove_header("authorization")
        return redirected


class GitHubRestClient:
    """Small fixed-host GitHub REST client with bounded response reads."""

    api_base = API_BASE

    def __init__(self, token: str) -> None:
        if not token:
            raise ReuseUnavailable("token_unavailable")
        self._token = token
        self._opener = build_opener(_SafeRedirectHandler())

    def _request(self, path: str, *, accept: str) -> Request:
        if not path.startswith("/repos/") or "\r" in path or "\n" in path:
            raise ReuseUnavailable("api_path_invalid")
        return Request(
            self.api_base + path,
            headers={
                "Accept": accept,
                "Authorization": "Bearer " + self._token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "openemr-w2-live-eval-reuse/1",
            },
        )

    def get_json(self, path: str) -> Mapping[str, Any]:
        request = self._request(path, accept="application/vnd.github+json")
        with self._opener.open(request, timeout=20) as response:
            payload = response.read(MAX_API_BYTES + 1)
        if len(payload) > MAX_API_BYTES:
            raise ReuseUnavailable("api_response_too_large")
        value = _load_json(payload)
        if not isinstance(value, Mapping):
            raise ReuseUnavailable("api_response_invalid")
        return value

    def get_bytes(self, path: str) -> bytes:
        request = self._request(path, accept="application/vnd.github+json")
        with self._opener.open(request, timeout=30) as response:
            payload = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise ReuseUnavailable("archive_too_large")
        return payload


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReuseUnavailable("json_duplicate_key")
        result[key] = value
    return result


def _load_json(payload: bytes) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except ReuseUnavailable:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReuseUnavailable("json_invalid") from exc


def _positive_id(value: Any) -> bool:
    return type(value) is int and value > 0


def _mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReuseUnavailable(reason)
    return value


def _objects(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    values = payload.get(key)
    if not isinstance(values, list) or any(
        not isinstance(item, Mapping) for item in values
    ):
        raise ReuseUnavailable("api_response_invalid")
    return list(values)


def _paged_objects(
    client: RestClient,
    path: str,
    key: str,
) -> list[Mapping[str, Any]]:
    objects: list[Mapping[str, Any]] = []
    total_count: int | None = None
    separator = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        payload = client.get_json(f"{path}{separator}page={page}")
        raw_total = payload.get("total_count")
        if type(raw_total) is not int or raw_total < 0:
            raise ReuseUnavailable("api_response_invalid")
        if total_count is None:
            total_count = raw_total
        elif raw_total != total_count:
            raise ReuseUnavailable("api_pagination_changed")
        page_objects = _objects(payload, key)
        objects.extend(page_objects)
        if len(page_objects) < PAGE_SIZE:
            break
    else:
        raise ReuseUnavailable("api_pagination_exceeded")
    if total_count != len(objects):
        raise ReuseUnavailable("api_pagination_incomplete")
    return objects


def _same_repository(value: Any, repository: str) -> bool:
    return isinstance(value, Mapping) and value.get("full_name") == repository


def _full_api_url(client: RestClient, path: str) -> str:
    return client.api_base.rstrip("/") + path


def _check_run_path(
    client: RestClient,
    expectation: ReuseExpectation,
    value: Any,
) -> tuple[str, int]:
    if not isinstance(value, str):
        raise ReuseUnavailable("check_run_binding_invalid")
    repo = quote(expectation.repository, safe="/")
    prefix = f"{client.api_base.rstrip('/')}/repos/{repo}/check-runs/"
    if not value.startswith(prefix):
        raise ReuseUnavailable("check_run_binding_invalid")
    suffix = value[len(prefix) :]
    if not suffix.isascii() or not suffix.isdigit() or int(suffix) <= 0:
        raise ReuseUnavailable("check_run_binding_invalid")
    return f"/repos/{repo}/check-runs/{suffix}", int(suffix)


def _validate_workflow(
    workflow: Mapping[str, Any], expectation: ReuseExpectation
) -> int:
    workflow_id = workflow.get("id")
    if (
        not _positive_id(workflow_id)
        or workflow.get("name") != EXPECTED_WORKFLOW_NAME
        or workflow.get("path") != EXPECTED_WORKFLOW_PATH
        or workflow.get("state") != "active"
    ):
        raise ReuseUnavailable("workflow_invalid")
    return workflow_id


def _select_run(
    runs: list[Mapping[str, Any]],
    expectation: ReuseExpectation,
    workflow_id: int,
) -> Mapping[str, Any]:
    accepted_paths = {
        EXPECTED_WORKFLOW_PATH,
        f"{EXPECTED_WORKFLOW_PATH}@{expectation.branch}",
    }
    candidates = [
        run
        for run in runs
        if run.get("id") != expectation.current_run_id
        and run.get("workflow_id") == workflow_id
        and run.get("name") == EXPECTED_WORKFLOW_NAME
        and run.get("path") in accepted_paths
        and run.get("head_sha") == expectation.sha
        and run.get("head_branch") == expectation.branch
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and _same_repository(run.get("repository"), expectation.repository)
        and _same_repository(run.get("head_repository"), expectation.repository)
    ]
    if len(candidates) != 1:
        raise ReuseUnavailable("run_unavailable")
    run = candidates[0]
    if (
        not _positive_id(run.get("id"))
        or not _positive_id(run.get("check_suite_id"))
        or not _positive_id(run.get("run_attempt"))
    ):
        raise ReuseUnavailable("run_invalid")
    return run


def _validate_job_and_check(
    client: RestClient,
    expectation: ReuseExpectation,
    run: Mapping[str, Any],
    jobs: list[Mapping[str, Any]],
) -> None:
    matching = [job for job in jobs if job.get("name") == EXPECTED_JOB_NAME]
    if len(matching) != 1:
        raise ReuseUnavailable("job_ambiguous")
    job = matching[0]
    run_id = run["id"]
    repo = quote(expectation.repository, safe="/")
    run_path = f"/repos/{repo}/actions/runs/{run_id}"
    if (
        not _positive_id(job.get("id"))
        or job.get("run_id") != run_id
        or job.get("head_sha") != expectation.sha
        or job.get("head_branch") != expectation.branch
        or job.get("workflow_name") != EXPECTED_WORKFLOW_NAME
        or job.get("status") != "completed"
        or job.get("conclusion") != "success"
        or job.get("run_url") != _full_api_url(client, run_path)
    ):
        raise ReuseUnavailable("job_not_green")

    check_path, check_id = _check_run_path(
        client, expectation, job.get("check_run_url")
    )
    if check_id != job["id"]:
        raise ReuseUnavailable("check_run_binding_invalid")
    check = client.get_json(check_path)
    suite = _mapping(check.get("check_suite"), "check_suite_invalid")
    app = _mapping(check.get("app"), "check_app_invalid")
    if (
        check.get("id") != check_id
        or check.get("name") != EXPECTED_JOB_NAME
        or check.get("head_sha") != expectation.sha
        or check.get("status") != "completed"
        or check.get("conclusion") != "success"
        or suite.get("id") != run["check_suite_id"]
        or app.get("slug") != "github-actions"
    ):
        raise ReuseUnavailable("check_run_not_green")


def _select_artifact(
    artifacts: list[Mapping[str, Any]],
    client: RestClient,
    expectation: ReuseExpectation,
    run: Mapping[str, Any],
) -> Mapping[str, Any]:
    matching = [
        artifact
        for artifact in artifacts
        if artifact.get("name") == EXPECTED_ARTIFACT_NAME
    ]
    if len(matching) != 1:
        raise ReuseUnavailable("artifact_ambiguous")
    artifact = matching[0]
    artifact_id = artifact.get("id")
    workflow_run = _mapping(artifact.get("workflow_run"), "artifact_binding_invalid")
    digest = artifact.get("digest")
    repo = quote(expectation.repository, safe="/")
    download_path = f"/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    if (
        not _positive_id(artifact_id)
        or artifact.get("expired") is not False
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or artifact.get("archive_download_url") != _full_api_url(client, download_path)
        or workflow_run.get("id") != run["id"]
        or workflow_run.get("head_sha") != expectation.sha
        or workflow_run.get("head_branch") != expectation.branch
    ):
        raise ReuseUnavailable("artifact_invalid")
    return artifact


def _extract_result(archive: bytes) -> bytes:
    if not archive or len(archive) > MAX_ARCHIVE_BYTES:
        raise ReuseUnavailable("archive_invalid")
    try:
        with zipfile.ZipFile(BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) != 1:
                raise ReuseUnavailable("archive_ambiguous")
            member = members[0]
            name = member.filename
            path = PurePosixPath(name)
            mode = (member.external_attr >> 16) & 0o170000
            if (
                member.is_dir()
                or member.flag_bits & 0x1
                or not name
                or "\\" in name
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
                or path.name != EXPECTED_RESULT_NAME
                or (mode not in {0, stat.S_IFREG})
                or member.file_size <= 0
                or member.file_size > MAX_RESULT_BYTES
            ):
                raise ReuseUnavailable("archive_member_invalid")
            with bundle.open(member) as source:
                result = source.read(MAX_RESULT_BYTES + 1)
            if len(result) != member.file_size or len(result) > MAX_RESULT_BYTES:
                raise ReuseUnavailable("result_size_invalid")
            return result
    except ReuseUnavailable:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ReuseUnavailable("archive_invalid") from exc


def validate_live_result(result_bytes: bytes, expected_sha: str) -> dict[str, Any]:
    """Apply the canonical baseline validator plus exact reuse constraints."""

    result = _load_json(result_bytes)
    if not isinstance(result, dict):
        raise ReuseUnavailable("result_invalid")
    try:
        baseline = baseline_from_result(result)
    except Exception as exc:  # noqa: BLE001 - convert to a non-sensitive closed code
        raise ReuseUnavailable("result_baseline_invalid") from exc
    manifest_sha = hashlib.sha256(DEFAULT_MANIFEST.read_bytes()).hexdigest()
    if (
        type(result.get("schema_version")) is not int
        or result["schema_version"] != 1
        or result.get("tier") != "live"
        or result.get("source_sha") != expected_sha
        or baseline.source_sha != expected_sha
        or baseline.case_count != EXPECTED_CASE_COUNT
        or baseline.manifest_sha256 != manifest_sha
        or len(baseline.categories) != len(Rubric)
        or any(
            category.numerator != category.denominator or category.score != 1.0
            for category in baseline.categories
        )
    ):
        raise ReuseUnavailable("result_constraints_invalid")
    return result


def fetch_reusable_result(client: RestClient, expectation: ReuseExpectation) -> bytes:
    """Return validated result bytes or raise a closed reuse-unavailable signal."""

    repo = quote(expectation.repository, safe="/")
    workflow = quote(expectation.workflow, safe="")
    workflow_path = f"/repos/{repo}/actions/workflows/{workflow}"
    workflow_id = _validate_workflow(client.get_json(workflow_path), expectation)

    query = urlencode(
        {
            "branch": expectation.branch,
            "event": "push",
            "status": "completed",
            "head_sha": expectation.sha,
            "per_page": PAGE_SIZE,
        }
    )
    runs = _paged_objects(
        client,
        f"{workflow_path}/runs?{query}",
        "workflow_runs",
    )
    run = _select_run(runs, expectation, workflow_id)
    run_id = run["id"]

    jobs = _paged_objects(
        client,
        f"/repos/{repo}/actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs"
        f"?per_page={PAGE_SIZE}",
        "jobs",
    )
    _validate_job_and_check(client, expectation, run, jobs)

    artifacts = _paged_objects(
        client,
        f"/repos/{repo}/actions/runs/{run_id}/artifacts"
        f"?name={quote(EXPECTED_ARTIFACT_NAME, safe='')}&per_page={PAGE_SIZE}",
        "artifacts",
    )
    artifact = _select_artifact(artifacts, client, expectation, run)
    download_path = f"/repos/{repo}/actions/artifacts/{artifact['id']}/zip"
    archive = client.get_bytes(download_path)
    match = _DIGEST.fullmatch(str(artifact["digest"]))
    if match is None or hashlib.sha256(archive).hexdigest() != match.group(1):
        raise ReuseUnavailable("artifact_digest_invalid")
    result = _extract_result(archive)
    validate_live_result(result, expectation.sha)
    return result


def _clear_output(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        raise ReuseUnavailable("output_invalid")


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _emit_reuse_flag(reused: bool) -> None:
    value = "true" if reused else "false"
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with Path(github_output).open("a", encoding="utf-8") as output:
                output.write(f"reuse={value}\n")
        except OSError:
            pass
    print(f"reuse={value}")


def _current_run_id(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdigit():
        raise ReuseUnavailable("current_run_invalid")
    run_id = int(value)
    if run_id <= 0:
        raise ReuseUnavailable("current_run_invalid")
    return run_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository",
        "--repo",
        dest="repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
    )
    parser.add_argument("--workflow", default=EXPECTED_WORKFLOW_FILE)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--current-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[str], RestClient] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    reused = False
    try:
        _clear_output(args.output)
        expectation = ReuseExpectation(
            repository=args.repository or "",
            workflow=args.workflow,
            sha=args.sha,
            current_run_id=_current_run_id(args.current_run_id),
        )
        token = os.environ.get("GITHUB_TOKEN", "")
        factory = client_factory or GitHubRestClient
        result = fetch_reusable_result(factory(token), expectation)
        _write_atomic(args.output, result)
        reused = True
    except Exception:  # noqa: BLE001 - every miss emits only the closed false signal
        try:
            _clear_output(args.output)
        except Exception:  # noqa: BLE001 - status remains safely false
            pass
    _emit_reuse_flag(reused)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
