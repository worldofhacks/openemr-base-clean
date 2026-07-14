# Gate command mapping — epic W2 Wave 0 (swarm/w2-wave0)

Scope: this epic writes ONLY under `agent/` (+ the named new-doc exception
`docs/week2/W2_TIER2_CI_POLICY.md` — a W2-M24 file scope, per TICKETS.md —
`.tdd-swarm/`, `tickets/`). `docs/week2/W2_DEVLOG.md` is ORCHESTRATOR-ONLY, written at
wave integration from ticket reports — it is in NO ticket's file_scopes and no ticket
may touch it. The OpenEMR PHP toolchain
gates (phpstan/phpcs/rector) are OUT of scope for this epic — no PHP file may be
touched (W2-D2/W2-D9); the integration gate greps the wave diff to enforce that.

Baseline (main @ c3e0804, recorded 2026-07-14): `238 passed, 5 skipped` via
`cd agent && .venv/bin/python -m pytest -q` (live/ui tests self-deselect without
RUN_LIVE/[ui] extra).

## Tier 1 — local gates (per ticket worktree; wrapped in run-local-gates.sh)

| Gate | Command / check | Status |
|---|---|---|
| Syntax | `agent/.venv/bin/python -m compileall -q agent/app agent/tests agent/ops agent/evals` | ACTIVE |
| Unit tests | `cd agent && .venv/bin/python -m pytest -q` — all pass incl. ticket's frozen tests | ACTIVE |
| New tests present | ticket's test files exist, collected, and not skipped | ACTIVE |
| Frozen-test integrity | `git diff <freeze-sha>..HEAD -- <test paths>` is empty on impl commits | ACTIVE |
| Spec-lint | `.tdd-swarm/spec-lint.sh tickets/<id>.md` — every AC-n has a `spec(<id>:AC-n)` tag in tests; every new test cites one | ACTIVE |
| No TODOs | no new `TODO\|FIXME\|HACK` in the ticket diff | ACTIVE |
| No debug logging | no new bare `print(`/`breakpoint()` in `agent/app/` diff (scripts under `agent/ops/` may print — they are operator CLIs, consistent with W1 `alert_checker.py`) | ACTIVE |
| Docs updated | public behavior changed → README/doc updated (reviewer-verified) | ACTIVE (judgment) |
| Reachability | new module wired to an entrypoint (route/flag/CLI) — reviewer-verified | ACTIVE (judgment) |
| Formatting | no formatter configured in `agent/` (no black/ruff config; matches W1 baseline) | SKIPPED — no tool in repo; adding one is out-of-scope for a spike wave |
| Linting | no linter configured in `agent/` | SKIPPED — same reason; reviewer covers quality |
| Type checking | no mypy/pyright config in `agent/` | SKIPPED — same reason; Pydantic v2 models validate at runtime |
| Coverage | pytest-cov not installed; W1 baseline never recorded coverage % | SKIPPED — "new tests present + frozen" carries the intent; no `skip`/`only` markers added is ACTIVE (grep) |

## Tier 2 — repo gates (wave review, integration branch)

| Gate | Command / check |
|---|---|
| Full suite ≥ baseline | fresh venv in integration worktree; `pytest -q` count ≥ 238 passed |
| Container build | `docker build agent/` succeeds locally (W2-M1 adds native deps); Railway build green (M1 evidence) |
| Dependency check | `pip check` clean; **two-tier license gate (amended 2026-07-14, W2 Wave 0 — see note below):** FIRST-PARTY + DIRECT deps strict-permissive (Apache/BSD/MIT family; permissive-equivalent identifiers e.g. pillow HPND only via explicit allowlist + justification; no GPL/LGPL/MPL identifier on a direct dep); documented non-infecting TRANSITIVE runtime deps accepted by owner exception per a **criterion** (MPL-2.0 file-level weak copyleft on unmodified wheels — tqdm/certifi/orjson; and libgfortran in the Linux numpy wheel under `GCC-exception-3.1`); **AGPL hard-banned at every level (W2-R6 — PyMuPDF)**; no torch anywhere (W2-M1 AC-2) |
| Secret scan | `gitleaks detect` (if installed) else `git diff main...HEAD` grep for key patterns (sk-ant-, api key literals, Bearer); `.env` files never committed |
| PHI check | wave diff + fixtures are synthetic/non-clinical only; no PHI in logs/traces/fixtures |
| Write-surface freeze | wave diff contains NO OpenEMR PHP/routes/schema file and NO OpenEMR write-path enablement (W2-D2/D9; W2-M8/M11 out of scope) |
| Architecture drift | wave-built shape vs W2_ARCHITECTURE.md §2/§2a/§6/§9 — undeclared deps/boundary crossings are findings |
| Regression on main-merge readiness | integration branch merges clean onto current main |

### Dependency-license gate — refined criterion (amended 2026-07-14, W2 Wave 0)

Owner-granted documented exception (W2-M1 license resolution). This is an owner-directed
acceptance-criteria change, re-frozen with rationale; it weakens no frozen test — the
license gate is a DoD/doc clause, not a pytest. The gate is two-tier:

- **First-party + direct deps — strict.** Every dependency declared directly in
  `agent/pyproject.toml` must carry a permissive OSI license in the Apache-2.0 / BSD / MIT
  family. A permissive-equivalent identifier (e.g. pillow's `MIT-CMU`/HPND) is admitted
  only via an explicit allowlist entry with a justification comment. No GPL/LGPL/MPL/AGPL
  identifier on any direct dep. PyMuPDF is banned (AGPL, W2-R6). No `torch` anywhere
  (frozen AC-2 test enforces torch absence).
- **Transitive runtime deps — documented accepted exceptions for non-infecting licenses,
  by criterion (non-exhaustive).** ACCEPTANCE RULE: a transitive dep whose only
  non-permissive identifier is file-level weak copyleft (MPL-2.0) on an unmodified wheel,
  or a runtime-library GPL exception, is accepted; AGPL and viral/strong copyleft
  (GPL/LGPL without a runtime exception) are never accepted. Full `importlib.metadata`
  scan of the installed environment (2026-07-14, 94 dists — the only MPL/copyleft
  identifiers found):
  - **MPL-2.0, file-level weak copyleft, unmodified wheels** — its obligations attach only
    to modified MPL-covered files that are redistributed, never to the combined/larger
    work: **tqdm** `MPL-2.0 AND MIT` (via fastembed), **certifi** `MPL-2.0` (via httpx),
    **orjson** `MPL-2.0 AND (Apache-2.0 OR MIT)` (via langgraph → langgraph-sdk). We
    neither modify nor redistribute modified source of any of them. Non-viral, non-GPL.
  - **libgfortran** — bundled inside the Linux `numpy` binary wheel;
    `GPL-3.0-or-later WITH GCC-exception-3.1`. The GCC Runtime Library Exception exists
    specifically so linking against the GCC runtime does not impose GPL on the resulting
    work. The identifier contains "GPL-3.0" but the exception means our use/distribution
    triggers no GPL copyleft. (A Linux-wheel binary artifact; absent on the macOS dev
    venv, present on the Railway/Linux deploy wheel.)
  - *Scan hygiene for the future automated gate (W2-M20):* a naive substring scan
    false-positives on `mmh3` (its MIT license text contains "IMPLIED" ⊃ "MPL"); mmh3 is
    MIT, not MPL.
- **AGPL is hard-banned at every level** (direct or transitive) — the one license no
  exception covers (PyMuPDF, W2-R6).

Posture: production-grade (carried from W1 — `.tdd-swarm/posture.md`). Performance
smoke: Wave 0 IS the performance measurement (M1 RSS ceiling, M24 timing/cost);
numbers recorded in ticket reports + devlog become the baselines for later waves.
