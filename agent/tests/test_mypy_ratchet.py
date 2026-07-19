"""C01 / AF-P1-05 (W2-REQ-65) — the mypy coverage ratchet only grows.

PDF p.6 Engineering Requirements: "CI pipeline: build, lint/typecheck, tests,
coverage, dependency audit, security scan... Dependency audit and security scan
must run on every PR."

`.github/workflows/agent-quality.yml` feeds `agent/mypy-ratchet.txt` to the
pinned strict mypy invocation (flags unchanged from the pre-C01 curated list).
This frozen test pins the ratchet contract:

  * the ratchet file exists, is non-empty, and every listed module is a real
    tracked `.py` file under `agent/` — no dangling or unowned entries;
  * the list only grows: every module in ``RATCHET_BASELINE`` must stay
    listed. Removing a module from `mypy-ratchet.txt` turns this test red.
    Shrinking the baseline itself is a weakening edit to a frozen test and
    requires an owner-recorded decision (W2_DECISIONS.md) first, per the
    §4d execution protocol;
  * every listed module is pinned: growing the ratchet requires appending the
    new module to ``RATCHET_BASELINE`` too (an additive, strengthening edit),
    so newly covered modules immediately enjoy the same removal protection.

There is no exclusion syntax: an entry is either listed (strict-checked in CI)
or it is not. "No unowned exclusions" (gap audit AF-P1-05) is enforced by
construction.
"""

from __future__ import annotations

from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
RATCHET_FILE = AGENT_DIR / "mypy-ratchet.txt"

# Frozen growth-only baseline. Append when adding a module to mypy-ratchet.txt;
# never remove without an owner-recorded decision (frozen-test discipline, §4d).
RATCHET_BASELINE: tuple[str, ...] = (
    "scripts/dependency_audit.py",
    "scripts/restore_drill.py",
    "scripts/verify_deployed_sha.py",
    "scripts/static_security_gate.py",
    "evals/artifact_scan.py",
    "evals/w2_models.py",
    "app/schemas/answers.py",
    "app/schemas/lab_trends.py",
    "app/orchestrator/critic.py",
    "app/observability/events.py",
)


def _ratchet_entries() -> list[str]:
    lines = RATCHET_FILE.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def test_ratchet_file_exists_and_lists_at_least_the_baseline_count() -> None:
    assert RATCHET_FILE.is_file(), (
        "agent/mypy-ratchet.txt is missing — the CI mypy job reads its module "
        "list from this tracked file"
    )
    entries = _ratchet_entries()
    assert len(entries) >= len(RATCHET_BASELINE), (
        f"mypy-ratchet.txt lists {len(entries)} modules; the ratchet floor is "
        f"{len(RATCHET_BASELINE)} — the list only grows"
    )


def test_every_listed_module_is_an_existing_python_file_under_agent() -> None:
    problems: list[str] = []
    for entry in _ratchet_entries():
        if entry.startswith(("/", "..")) or "\\" in entry:
            problems.append(f"{entry!r}: must be a forward-slash path relative to agent/")
            continue
        if not entry.endswith(".py"):
            problems.append(f"{entry!r}: must be a .py module path")
            continue
        candidate = (AGENT_DIR / entry).resolve()
        if not candidate.is_relative_to(AGENT_DIR):
            problems.append(f"{entry!r}: escapes the agent/ tree")
        elif not candidate.is_file():
            problems.append(f"{entry!r}: file does not exist")
    assert not problems, "invalid mypy-ratchet.txt entries:\n" + "\n".join(problems)


def test_ratchet_entries_are_unique() -> None:
    entries = _ratchet_entries()
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    assert not duplicates, f"duplicate mypy-ratchet.txt entries: {duplicates}"


def test_ratchet_only_grows_every_baseline_module_stays_listed() -> None:
    removed = sorted(set(RATCHET_BASELINE) - set(_ratchet_entries()))
    assert not removed, (
        "modules removed from mypy-ratchet.txt — the ratchet only grows; "
        "removal requires an owner-recorded decision (W2_DECISIONS.md): "
        f"{removed}"
    )


def test_every_ratchet_entry_is_pinned_in_the_frozen_baseline() -> None:
    unpinned = sorted(set(_ratchet_entries()) - set(RATCHET_BASELINE))
    assert not unpinned, (
        "modules listed in mypy-ratchet.txt but not pinned in RATCHET_BASELINE "
        "— append them to RATCHET_BASELINE in this test so the ratchet "
        f"protects them from future removal: {unpinned}"
    )
