"""Run static security scanners without echoing source or prompt text to CI logs."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SCAN_TARGETS = (
    "agent/app",
    "agent/evals",
    "agent/scripts",
    ".github/scripts",
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")


def _tool(name: str) -> str:
    """Resolve a scanner installed beside the active Python interpreter."""

    candidate = Path(sys.executable).parent / name
    return str(candidate)


def _safe_path(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    try:
        resolved = (REPOSITORY_ROOT / value).resolve()
        relative = resolved.relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return "outside-project"
    return relative if _SAFE_IDENTIFIER.fullmatch(relative) else "invalid-path"


def _safe_identifier(value: object) -> str:
    return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else "unknown"


def _safe_line(value: object) -> int:
    return value if isinstance(value, int) and 0 < value < 10_000_000 else 0


def _run_json(command: Sequence[str], *, scanner: str) -> tuple[int, dict[str, Any] | None]:
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        print(f"{scanner}=INCONCLUSIVE execution=failed output=suppressed")
        return 2, None
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        print(f"{scanner}=INCONCLUSIVE exit={completed.returncode} output=suppressed")
        return 2, None
    if not isinstance(payload, dict):
        print(f"{scanner}=INCONCLUSIVE exit={completed.returncode} output=suppressed")
        return 2, None
    return completed.returncode, payload


def _bandit() -> int:
    status, payload = _run_json(
        (_tool("bandit"), "-q", "-lll", "-f", "json", "-r", *SCAN_TARGETS),
        scanner="bandit",
    )
    if payload is None:
        return status
    results = payload.get("results")
    if not isinstance(results, list):
        print("bandit=INCONCLUSIVE schema=invalid")
        return 2
    for finding in results:
        if not isinstance(finding, dict):
            continue
        print(
            "bandit=FAIL"
            f" path={_safe_path(finding.get('filename'))}"
            f" line={_safe_line(finding.get('line_number'))}"
            f" rule={_safe_identifier(finding.get('test_id'))}"
            f" severity={_safe_identifier(finding.get('issue_severity'))}"
        )
    if results:
        return 1
    if status != 0:
        print(f"bandit=INCONCLUSIVE exit={status} output=suppressed")
        return 2
    print("bandit=PASS findings=0")
    return 0


def _semgrep() -> int:
    status, payload = _run_json(
        (
            _tool("semgrep"),
            "scan",
            "--config",
            "p/python",
            "--error",
            "--severity",
            "ERROR",
            "--json",
            "--quiet",
            *SCAN_TARGETS,
        ),
        scanner="semgrep",
    )
    if payload is None:
        return status
    results = payload.get("results")
    errors = payload.get("errors")
    if not isinstance(results, list) or not isinstance(errors, list):
        print("semgrep=INCONCLUSIVE schema=invalid")
        return 2
    for finding in results:
        if not isinstance(finding, dict):
            continue
        start = finding.get("start")
        line = start.get("line") if isinstance(start, dict) else None
        print(
            "semgrep=FAIL"
            f" path={_safe_path(finding.get('path'))}"
            f" line={_safe_line(line)}"
            f" rule={_safe_identifier(finding.get('check_id'))}"
        )
    if results:
        return 1
    if errors or status != 0:
        print(f"semgrep=INCONCLUSIVE exit={status} errors={len(errors)} details=suppressed")
        return 2
    print("semgrep=PASS findings=0")
    return 0


def main() -> int:
    bandit_status = _bandit()
    semgrep_status = _semgrep()
    if 2 in (bandit_status, semgrep_status):
        return 2
    return 1 if 1 in (bandit_status, semgrep_status) else 0


if __name__ == "__main__":
    raise SystemExit(main())
