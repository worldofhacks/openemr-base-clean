from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import dependency_audit
from scripts.dependency_audit import ExceptionPolicyError, load_active_exceptions


def _write(path: Path, exceptions: list[dict[str, str]]) -> Path:
    path.write_text(
        json.dumps({"version": 1, "exceptions": exceptions}), encoding="utf-8"
    )
    return path


def test_empty_exception_policy_is_valid(tmp_path: Path) -> None:
    policy = _write(tmp_path / "exceptions.json", [])

    assert load_active_exceptions(policy, today=date(2026, 7, 15)) == []


def test_exception_requires_specific_owner_and_future_expiry(tmp_path: Path) -> None:
    policy = _write(
        tmp_path / "exceptions.json",
        [
            {
                "id": "CVE-2026-12345",
                "owner": "@security-owner",
                "justification": "A narrowly scoped transitive issue with no reachable sink.",
                "expires": "2026-07-14",
            }
        ],
    )

    with pytest.raises(ExceptionPolicyError, match="expired"):
        load_active_exceptions(policy, today=date(2026, 7, 15))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("id", "not-a-cve", "id is invalid"),
        ("owner", "?", "owner is invalid"),
        ("justification", "too short", "must be specific"),
    ],
)
def test_exception_closed_fields_are_validated(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    entry = {
        "id": "CVE-2026-12345",
        "owner": "@security-owner",
        "justification": "A narrowly scoped transitive issue with no reachable sink.",
        "expires": "2026-08-15",
    }
    entry[field] = value
    policy = _write(tmp_path / "exceptions.json", [entry])

    with pytest.raises(ExceptionPolicyError, match=match):
        load_active_exceptions(policy, today=date(2026, 7, 15))


def test_audit_resolves_the_project_instead_of_the_editable_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _write(tmp_path / "exceptions.json", [])
    observed: list[str] = []

    def _run(command: list[str], **kwargs: object):
        observed.extend(command)
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"dependencies": [], "fixes": []}),
            stderr="",
        )

    monkeypatch.setattr(dependency_audit.subprocess, "run", _run)

    assert dependency_audit.main(("--exceptions", str(policy))) == 0
    assert observed[-1] == str(dependency_audit.PROJECT_ROOT)
    assert "--strict" in observed
    assert "--progress-spinner" in observed
    assert "--skip-editable" not in observed


def test_audit_report_never_forwards_description_or_resolver_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    completed = SimpleNamespace(
        returncode=1,
        stdout=json.dumps(
            {
                "dependencies": [
                    {
                        "name": "cryptography",
                        "version": "46.0.7",
                        "vulns": [
                            {
                                "id": "GHSA-537c-gmf6-5ccf",
                                "fix_versions": ["48.0.1"],
                                "description": "secret resolver or prompt text",
                            }
                        ],
                    }
                ]
            }
        ),
        stderr="private-index-token",
    )

    assert dependency_audit._report_audit(completed) == 1
    output = capsys.readouterr().out
    assert output == (
        "dependency-audit=FAIL package=cryptography version=46.0.7 "
        "vulnerability=GHSA-537c-gmf6-5ccf fixes=48.0.1\n"
    )
    assert "secret" not in output
    assert "token" not in output
