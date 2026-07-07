# Quality Gates

Two tiers. Local gates run inside every implementation loop iteration (ticket
scope, fast). Repo gates run at wave review (whole system, slower). A ticket
cannot report DONE with any local gate red; a wave cannot unlock the next wave
with any repo gate red. The orchestrator re-runs gates itself before trusting
any agent's PASS claim.

## Tier 1 — Local correctness (per ticket, every loop iteration)

| Gate | Pass condition |
|---|---|
| Formatting | Formatter produces zero diff |
| Linting | Zero errors (warnings per repo policy) |
| Type checking | Zero errors |
| Unit tests | All pass, including the ticket's frozen tests |
| New tests present | Ticket's test files exist and execute (Test Agent wrote them; gate catches deletion/skips) |
| Coverage maintained | Coverage % ≥ baseline recorded at Phase 0; no `skip`/`only` markers added |
| No TODOs | No new TODO/FIXME/HACK in the diff |
| No debug logging | No new print/console.log/var_dump/dd in the diff |
| Docs updated | Public behavior changed → relevant doc/README section changed |
| Reachability | New code is wired to a real entrypoint (route, tool registration, DI container, export used somewhere) — no orphaned modules that tests import but the app never runs |
| Spec-lint | Every acceptance criterion id (`AC-n`) in the ticket maps to ≥1 test tagged `spec(<ticket>:<AC>)`, and every new test cites a criterion. Script it (`.tdd-swarm/spec-lint.sh <ticket-file>`) so it's mechanical, not judgment |

## Tier 2 — Repository correctness (per wave, on the integration branch)

| Gate | Pass condition |
|---|---|
| Full build | Clean build from scratch |
| Integration tests | Affected integration + e2e suites pass |
| API compatibility | Contract tests / schema diff shows no breaking change (or a migration ticket exists) |
| Dependency graph | No cycles introduced; lockfile consistent; no unapproved new deps |
| Migration validation | Migrations apply cleanly on a copy of baseline data, and roll back |
| Security scan | SAST/audit (e.g. `npm audit`, `composer audit`, semgrep) — no new Critical/High |
| Secret scan | gitleaks/trufflehog clean on the wave's commit range |
| Regression suite | Full test suite ≥ baseline pass count |
| Performance smoke † | p50/p95 latency, memory, throughput within threshold of `.tdd-swarm/baselines.md` (default: fail on >10% regression) |
| Architecture drift | Wave's changes respect the architecture doc: no undeclared cross-subsystem deps, no contract changes without a migration ticket |

† = posture-gated: under `mvp` posture (`.tdd-swarm/posture.md`) this gate may be deferred, but the deferral is recorded in posture.md and re-enabled before the final PR. A deferred gate is a written decision, never a silent skip.

## Per-repo command mapping

At Phase 0, write `.tdd-swarm/gates.md` mapping each gate to a concrete command
for this repo, and verify each runs. Gates without a runnable command are
listed as SKIPPED with a reason — silent gate skips are a red flag.

Example (Node/TS):

```
format:    npx prettier --check .
lint:      npx eslint . --max-warnings 0
typecheck: npx tsc --noEmit
unit:      npx vitest run --coverage
build:     npm run build
secrets:   npx gitleaks detect --no-banner
audit:     npm audit --audit-level=high
```

Example (PHP / OpenEMR-style):

```
format:    ./vendor/bin/php-cs-fixer fix --dry-run --diff
lint:      ./vendor/bin/phpcs
typecheck: ./vendor/bin/phpstan analyse
unit:      ./vendor/bin/phpunit --testsuite unit
build:     composer install --no-dev && npm run build
audit:     composer audit
```

Wrap Tier 1 in one script (`.tdd-swarm/run-local-gates.sh <worktree>`) so
implementers, and the orchestrator's re-verification, run the identical
command and diverging results are impossible by construction.
