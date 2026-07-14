#!/usr/bin/env bash
# Spec-lint: every acceptance criterion AC-n in the ticket file maps to >=1 test
# tagged spec(<ticket-id>:AC-n) under the ticket's declared test paths.
# Usage: .tdd-swarm/spec-lint.sh tickets/W2-M1.md
set -u
TICKET_FILE="$1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TID=$(basename "$TICKET_FILE" .md)
FAIL=0
ACS=$(grep -oE '\bAC-[0-9]+' "$TICKET_FILE" | sort -u)
[ -n "$ACS" ] || { echo "spec-lint: no AC-n ids found in $TICKET_FILE"; exit 1; }
for AC in $ACS; do
  # eval-tagged criteria are graded in the eval harness, not frozen tests
  if grep -qE "\b$AC\b.*\[eval\]|\[eval\].*\b$AC\b" "$TICKET_FILE"; then
    echo "spec-lint: $TID:$AC -> eval-harness case (exempt from frozen-test mapping)"
    continue
  fi
  # live-measure criteria are recorded operational evidence (deployed/live runs), not frozen tests
  if grep -qE "\b$AC\b.*\[live-measure\]|\[live-measure\].*\b$AC\b" "$TICKET_FILE"; then
    echo "spec-lint: $TID:$AC -> live-measure evidence row (exempt from frozen-test mapping)"
    continue
  fi
  if ! grep -rqE "spec\($TID:$AC\)" agent/tests/ agent/ops/ agent/evals/ 2>/dev/null; then
    echo "spec-lint: MISSING test tagged spec($TID:$AC)"
    FAIL=1
  fi
done
exit $FAIL
