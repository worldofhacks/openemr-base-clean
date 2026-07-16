"""Run pip-audit with only reviewed, owner-bound, unexpired CVE exceptions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Sequence


DEFAULT_EXCEPTIONS = Path(__file__).parents[1] / "security" / "cve_exceptions.json"
PROJECT_ROOT = Path(__file__).parents[1]
_VULN_ID = re.compile(r"^(?:CVE-\d{4}-\d{4,}|GHSA-[23456789cfghjmpqrvwx]{4}-[23456789cfghjmpqrvwx]{4}-[23456789cfghjmpqrvwx]{4})$")
_OWNER = re.compile(r"^[A-Za-z0-9_.@/-]{2,80}$")
_PACKAGE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_VERSION = re.compile(r"^[A-Za-z0-9_.+!-]{1,100}$")


class ExceptionPolicyError(ValueError):
    """The committed exception file is malformed or contains an expired waiver."""


def load_active_exceptions(path: Path, *, today: date | None = None) -> list[str]:
    effective_today = today or date.today()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExceptionPolicyError("CVE exception policy is unreadable") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ExceptionPolicyError("CVE exception policy version must equal 1")
    entries = raw.get("exceptions")
    if not isinstance(entries, list):
        raise ExceptionPolicyError("CVE exceptions must be a list")

    active: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "justification",
            "owner",
            "expires",
        }:
            raise ExceptionPolicyError("each CVE exception must use the closed schema")
        vuln_id = entry["id"]
        owner = entry["owner"]
        justification = entry["justification"]
        try:
            expiry = date.fromisoformat(entry["expires"])
        except (TypeError, ValueError) as exc:
            raise ExceptionPolicyError("CVE exception expiry must be ISO-8601") from exc
        if not isinstance(vuln_id, str) or not _VULN_ID.fullmatch(vuln_id):
            raise ExceptionPolicyError("CVE exception id is invalid")
        if vuln_id in seen:
            raise ExceptionPolicyError("duplicate CVE exception id")
        if not isinstance(owner, str) or not _OWNER.fullmatch(owner):
            raise ExceptionPolicyError("CVE exception owner is invalid")
        if not isinstance(justification, str) or not 20 <= len(justification) <= 500:
            raise ExceptionPolicyError("CVE exception justification must be specific")
        if "http" in justification.lower():
            raise ExceptionPolicyError("CVE exception justification must not embed URLs")
        if expiry <= effective_today:
            raise ExceptionPolicyError("CVE exception is expired")
        seen.add(vuln_id)
        active.append(vuln_id)
    return active


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.dependency_audit")
    parser.add_argument("--exceptions", type=Path, default=DEFAULT_EXCEPTIONS)
    parser.add_argument("--check-only", action="store_true")
    return parser


def _safe(value: object, pattern: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else "unknown"


def _report_audit(completed: subprocess.CompletedProcess[str]) -> int:
    """Print aggregate vulnerability identifiers without forwarding resolver logs."""

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        print(
            f"dependency-audit=INCONCLUSIVE exit={completed.returncode} output=suppressed",
            file=sys.stderr,
        )
        return 2
    dependencies = payload.get("dependencies") if isinstance(payload, dict) else None
    if not isinstance(dependencies, list):
        print("dependency-audit=INCONCLUSIVE schema=invalid", file=sys.stderr)
        return 2

    finding_count = 0
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        vulnerabilities = dependency.get("vulns")
        if not isinstance(vulnerabilities, list):
            continue
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                continue
            fixes = vulnerability.get("fix_versions")
            safe_fixes = (
                ",".join(_safe(fix, _VERSION) for fix in fixes[:10])
                if isinstance(fixes, list)
                else "unknown"
            )
            print(
                "dependency-audit=FAIL"
                f" package={_safe(dependency.get('name'), _PACKAGE)}"
                f" version={_safe(dependency.get('version'), _VERSION)}"
                f" vulnerability={_safe(vulnerability.get('id'), _VULN_ID)}"
                f" fixes={safe_fixes or 'none'}"
            )
            finding_count += 1
    if finding_count:
        return 1
    if completed.returncode != 0:
        print(
            f"dependency-audit=INCONCLUSIVE exit={completed.returncode} details=suppressed",
            file=sys.stderr,
        )
        return 2
    print("dependency-audit=PASS vulnerabilities=0")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        exceptions = load_active_exceptions(args.exceptions)
    except ExceptionPolicyError as exc:
        print(f"dependency-audit=FAIL policy_error={exc}", file=sys.stderr)
        return 2
    if args.check_only:
        print(f"dependency-audit-policy=PASS active_exceptions={len(exceptions)}")
        return 0
    # Audit the project dependency graph, not the current environment.  The latter
    # includes this editable, unpublished package and makes ``--strict`` fail before
    # pip-audit reaches its third-party dependencies.
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--strict",
        "--progress-spinner",
        "off",
        "--format",
        "json",
        "--desc",
        "off",
        "--aliases",
        "off",
    ]
    for vuln_id in exceptions:
        command.extend(("--ignore-vuln", vuln_id))
    command.append(str(PROJECT_ROOT))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("dependency-audit=INCONCLUSIVE execution=failed output=suppressed")
        return 2
    return _report_audit(completed)


if __name__ == "__main__":
    raise SystemExit(main())
