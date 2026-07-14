#!/usr/bin/env bash
# Tier-1 local gates for epic W2 Wave 0 (see .tdd-swarm/gates.md).
# Usage: .tdd-swarm/run-local-gates.sh [<ticket-file>] [<freeze-sha>]
#   Run from the worktree root. Implementers and the orchestrator run this
#   identical script so results cannot diverge by construction.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TICKET="${1:-}"
FREEZE_SHA="${2:-}"
PY="agent/.venv/bin/python"
FAIL=0

say() { printf '%s\n' "$*"; }
gate() { # gate <name> <status 0|1>
  if [ "$2" -eq 0 ]; then say "GATE $1: PASS"; else say "GATE $1: FAIL"; FAIL=1; fi
}

[ -x "$PY" ] || { say "GATE venv: FAIL (agent/.venv missing — create it first)"; exit 1; }

# 1. Syntax
"$PY" -m compileall -q agent/app agent/tests agent/ops agent/evals >/dev/null 2>&1
gate syntax $?

# 2. Unit tests (live/ui self-deselect)
( cd agent && ../"$PY" -m pytest -q 2>&1 | tail -3 ) > /tmp/gates-pytest-$$.out
grep -qE '(^| )[0-9]+ passed' /tmp/gates-pytest-$$.out && ! grep -qE '[0-9]+ (failed|error)' /tmp/gates-pytest-$$.out
gate unit-tests $?
cat /tmp/gates-pytest-$$.out; rm -f /tmp/gates-pytest-$$.out

# 3. Frozen-test integrity (impl phase only — needs freeze sha)
if [ -n "$FREEZE_SHA" ]; then
  CHANGED=$(git diff --name-only "$FREEZE_SHA"..HEAD -- 'agent/tests/' 'agent/evals/test_*' 'agent/ops/tests/' | wc -l | tr -d ' ')
  [ "$CHANGED" = "0" ]
  gate frozen-tests $?
else
  say "GATE frozen-tests: SKIP (no freeze sha supplied — RED phase)"
fi

# 4. Spec-lint
if [ -n "$TICKET" ] && [ -f "$TICKET" ]; then
  bash .tdd-swarm/spec-lint.sh "$TICKET"
  gate spec-lint $?
else
  say "GATE spec-lint: SKIP (no ticket file supplied)"
fi

# 5. No new TODO/FIXME/HACK, no skip/only markers, no bare print in app/ (diff vs merge-base with swarm branch)
BASE=$(git merge-base HEAD swarm/w2-wave0 2>/dev/null || git merge-base HEAD main)
DIFF=$(git diff "$BASE"..HEAD -- agent/ | grep '^+' | grep -v '^+++' || true)
echo "$DIFF" | grep -qE '\b(TODO|FIXME|HACK)\b' && gate no-todos 1 || gate no-todos 0
APPDIFF=$(git diff "$BASE"..HEAD -- agent/app/ | grep '^+' | grep -v '^+++' || true)
echo "$APPDIFF" | grep -qE '^\+[^#]*\b(print\(|breakpoint\(\))' && gate no-debug 1 || gate no-debug 0
echo "$DIFF" | grep -qE '@pytest\.mark\.skip\b|\.only\b' && gate no-skip-markers 1 || gate no-skip-markers 0

say "----"
if [ "$FAIL" -eq 0 ]; then say "ALL GATES PASS"; else say "GATES FAILED"; fi
exit "$FAIL"
