from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import static_security_gate


def test_bandit_output_omits_source_and_message_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "results": [
            {
                "filename": "agent/app/example.py",
                "line_number": 17,
                "test_id": "B999",
                "issue_severity": "HIGH",
                "issue_text": "secret diagnostic text",
                "code": "sensitive prompt text",
            }
        ]
    }
    monkeypatch.setattr(
        static_security_gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=json.dumps(payload), stderr="provider secret"
        ),
    )

    assert static_security_gate._bandit() == 1
    output = capsys.readouterr().out
    assert output == (
        "bandit=FAIL path=agent/app/example.py line=17 rule=B999 severity=HIGH\n"
    )
    assert "secret" not in output
    assert "prompt" not in output


def test_semgrep_errors_are_reported_without_raw_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"results": [], "errors": [{"message": "sensitive source text"}]}
    monkeypatch.setattr(
        static_security_gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2, stdout=json.dumps(payload), stderr="raw error"
        ),
    )

    assert static_security_gate._semgrep() == 2
    output = capsys.readouterr().out
    assert output == "semgrep=INCONCLUSIVE exit=2 errors=1 details=suppressed\n"
    assert "sensitive" not in output
    assert "raw error" not in output


def test_safe_path_rejects_escape() -> None:
    outside = Path(static_security_gate.REPOSITORY_ROOT).parent / "not-in-repo.py"

    assert static_security_gate._safe_path(str(outside)) == "outside-project"


def test_both_scanners_cover_every_executable_python_surface(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commands: list[tuple[str, ...]] = []

    def _run(command: tuple[str, ...], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        payload = {"results": [], "errors": []} if "semgrep" in command[0] else {"results": []}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(static_security_gate.subprocess, "run", _run)

    assert static_security_gate._bandit() == 0
    assert static_security_gate._semgrep() == 0
    capsys.readouterr()
    assert len(commands) == 2
    for command in commands:
        for target in static_security_gate.SCAN_TARGETS:
            assert target in command
