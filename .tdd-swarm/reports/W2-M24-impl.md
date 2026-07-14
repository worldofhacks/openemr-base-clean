# W2-M24 Implementation Report — Tier-2 timing/cost/quota spike + fork-PR secret policy

Ticket: `tickets/W2-M24.md` · Branch: `ticket/w2-m24-tier2-spike` · Freeze SHA: `849cbcc`

## What changed

Two new files, exactly the ticket's file scopes:

1. **`agent/ops/spike_tier2.py`** (NEW) — operator measurement CLI + policy lints:
   - `extrapolate(units)` — 50× the mean per-unit aggregate of the three-call
     shape (VLM extraction + answer + judge); multi-page VLM extraction counted
     explicitly (one provider call per page, never hidden in "50 turns");
     `retry_amplification = total attempts / total base calls`; empty sample →
     `ValueError` (never a silent zero projection).
   - `percentile(values, p)` — nearest-rank (`rank = ceil(p/100·n)`, 1-indexed,
     min rank 1); observed-value discipline, no interpolation; empty → `ValueError`.
   - `build_report(units, *, rate_limit_headroom, daily_quota_statement,
     max_cost_usd, max_seconds)` — full report shape; verdict ∈
     {`viable`, `stop_escalate`} **computed by the module** against the 50-case
     projection on both budget axes (cost AND runtime); the W2-OA2 local-key
     substitution note is module-computed, never caller-supplied.
   - `render_report(report)` — whitelisted-fields-only text surface, plus a
     defense-in-depth scrub (secret-named env values incl. DSN password
     segments, `sk-ant-*` tokens, `Bearer` values → `[REDACTED]`).
   - `lint_workflows(paths)` — read-only; fires **only** on the three-way
     conjunction `pull_request_target` trigger + `actions/checkout` of PR-head
     code (`head.sha`/`head.ref`/`github.head_ref`) + explicit secrets usage
     (`${{ secrets.* }}` or `secrets: inherit`). Structural YAML parse
     (PyYAML — already installed via the declared `langgraph` →
     `langchain-core` chain; `pyproject.toml` untouched; handles the PyYAML
     `on:`→`True` key quirk). Passes all 55 real workflows including the
     `dependabot-auto-merge.yml` near-miss (trigger + secrets, no PR-code
     checkout); a checkout with no `ref` override is base-repo code and passes.
   - `lint_policy_doc(path)` — asserts the six frozen clauses; missing file →
     `FileNotFoundError`; a stub doc yields ≥ 6 findings.
   - Stdlib-only synthetic image generation: 5×7 bitmap font rasterized to an
     8-bit grayscale PNG via `zlib` + `struct` (pre-authorized; pillow not used
     — W2-M1 not merged at run time). NO new dependencies.
   - CLI (`python -m ops.spike_tier2`): loads `agent/.env` (values opaque —
     names only ever surfaced), runs N units (default 5; unit 1 uses 2 VLM
     pages to exercise the multi-page multiplier), measures per call via
     `client.messages.with_raw_response.create` (wall time, `usage` tokens,
     `retries_taken`, `anthropic-ratelimit-*` headers), prices from the
     published per-MTok table, prints aggregates only.

2. **`docs/week2/W2_TIER2_CI_POLICY.md`** (NEW — the allowed new-doc exception)
   — freezes all six clauses (see AC-6 below). No binding doc, no
   `.github/workflows/` file, no `W2_DEVLOG.md` touched.

## Gate evidence

`bash .tdd-swarm/run-local-gates.sh tickets/W2-M24.md 849cbcc`:

```
GATE syntax: PASS
GATE unit-tests: PASS
266 passed, 6 skipped, 1 warning in 1.23s
GATE frozen-tests: PASS
spec-lint: W2-M24:AC-7 -> live-measure evidence row (exempt from frozen-test mapping)
GATE spec-lint: PASS
GATE no-todos: PASS
GATE no-debug: PASS
GATE no-skip-markers: PASS
----
ALL GATES PASS
```

- 266 passed = 236 prior + 30 new frozen W2-M24 tests; 6 skips are the
  standing env-based self-deselects (RUN_LIVE live tests ×5, playwright ui ×1).
  Note: the recorded main baseline (238 passed, 5 skipped) differs by
  environment only — `openemr-base-clean`'s venv runs the ui smoke test
  (playwright installed there); no test was removed or weakened.
- Frozen-test integrity: `git diff 849cbcc..HEAD -- agent/tests/` is empty.

## AC coverage

- **AC-1..AC-6**: green via the 30 frozen tests in
  `agent/tests/test_tier2_spike.py` (offline, provider calls faked/synthetic).
- **AC-6 doc clauses** (all six, machine-linted + independent term floor):
  1. No repository secrets to forks.
  2. Never `pull_request_target` checkout of fork code (three-way conjunction
     defined; dependabot-auto-merge near-miss explicitly documented compliant).
  3. Forks run Tier 1 only.
  4. Maintainer reproduces the exact fork commit on a trusted same-repository
     branch for the required Tier-2 result before merge; any new commit
     invalidates the approval/status.
  5. Same-repo PRs: least-privilege environments with approval; no secret
     echo; no artifact retention of secret material.
  6. STOP escalation — quota/runtime/cost failure never reduces the 50 cases.
- **AC-7**: live-measure evidence below.

## [live-measure] AC-7 — real 5-unit run against the Anthropic API

**W2-OA2 SUBSTITUTION NOTE (required):** measured with the **local agent key
from `agent/.env`** (env var name `ANTHROPIC_API_KEY`) because the fork repo
`worldofhacks/openemr-base-clean` GitHub Actions secret is absent — owner
action **W2-OA2 pending** (noted, not blocking). No secret value was read into
this report, printed, or committed; `agent/.env` is gitignored.

Run: 2026-07-14, `cd agent && .venv/bin/python -m ops.spike_tier2`
(5 units of the real three-call shape; unit 1 = 2-page VLM extraction, so the
multi-page multiplier was exercised; runtime-generated synthetic non-clinical
PNGs — "SYNTHETIC INTAKE FORM - NOT A REAL PATIENT / NAME: TESTY MCTESTFACE /
DOB: 2099-01-01 ..."; raw provider outputs discarded, aggregates only).

- **Model id:** `claude-sonnet-4-6` (from `LLM_MODEL`)
- **Pricing source:** platform.claude.com published per-MTok pricing
  (docs/en/pricing.md; claude-api reference cached 2026-05-26):
  input **$3.00/MTok**, output **$15.00/MTok**
- **Answer/judge max_tokens:** 1024 / 512; VLM extraction 512 (representative sizes)

### Measured sample (5 units, 16 provider calls total)

| unit | pages | vlm calls | vlm s | vlm in/out tok | answer s | answer in/out | judge s | judge in/out | unit cost USD |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 2 | 8.18 | 816/228 | 2.14 | 289/82 | 6.59 | 373/33 | 0.009579 |
| 2 | 1 | 1 | 3.82 | 427/125 | 2.61 | 188/91 | 1.53 | 281/30 | 0.006378 |
| 3 | 1 | 1 | 9.20 | 427/125 | 3.17 | 188/89 | 1.70 | 279/32 | 0.006372 |
| 4 | 1 | 1 | 3.87 | 427/125 | 2.76 | 188/89 | 2.59 | 279/30 | 0.006342 |
| 5 | 1 | 1 | 3.67 | 427/125 | 2.26 | 188/58 | 1.96 | 248/33 | 0.005829 |

### Per-call-class aggregates (nearest-rank percentiles over per-unit-class seconds)

| class | p50 s | p95 s | input tok (Σ) | output tok (Σ) | cost USD (Σ) |
|---|---|---|---|---|---|
| vlm | 3.873 | 9.200 | 2,524 | 728 | 0.018492 |
| answer | 2.608 | 3.169 | 1,041 | 409 | 0.009258 |
| judge | 1.958 | 6.589 | 1,460 | 158 | 0.006750 |

- **Retry amplification:** **1.0** — 0 SDK retries across all 16 calls
  (`retries_taken` from the raw response on every call).
- **Sample totals:** 56.05 s wall, 5,025 input tok, 1,295 output tok, **$0.0345**.

### Rate-limit headroom (anthropic-ratelimit-* headers, final response)

- requests: **9,999 / 10,000 per min remaining**
- input-tokens: **10,000,000 / 10,000,000 per min remaining**
- output-tokens: **2,000,000 / 2,000,000 per min remaining**
- combined tokens: **12,000,000 / 12,000,000 per min remaining**

### Daily-quota statement (derived from observed limits)

No daily-cap header is exposed by the API; all observed limits are per-minute.
The 50-case projection consumes at minimum **~0.02 minute(s)** of quota at the
observed per-minute limits (160 calls vs 10,000 req/min; 50,250 input tok vs
10M/min; 12,950 output tok vs 2M/min) — a single required run, and even many
PR-gate runs per day, fit with enormous margin.

### 50-case extrapolation (50 × mean per-unit aggregate, formula per W2-D8/§7)

| metric | projected |
|---|---|
| provider calls (retry-amplified; multi-page VLM counted explicitly) | **160** |
| runtime (sequential) | **560.5 s (~9.3 min)** |
| input tokens | **50,250** |
| output tokens | **12,950** |
| cost | **$0.345** |

### Named max per-run budget + verdict

- **Budget (named):** `MAX_RUN_COST_USD = $5.00`, `MAX_RUN_SECONDS = 1200`
  (20 min — PR-blocking CI ceiling).
- **Verdict: `viable`** — projection fits both axes with ~14× cost margin and
  ~2.1× runtime margin. (Had it not fit, the locked rule applies: STOP
  escalation to the owner as a dependency problem — never a reduction of the
  50 cases or a gate bypass.)

## Spike findings (should feed W2-M20 planning)

1. **The retired "$4/run" figure was ~12× too high for this shape/model** —
   the measured bound is **$0.345/run** on `claude-sonnet-4-6` at
   representative token sizes. Even a 10× richer real-eval token profile
   (longer forms, fuller rubrics) stays under the $5 budget.
2. **Runtime, not cost or quota, is the binding axis** — ~9.3 min sequential
   projection against a 20-min ceiling leaves only ~2.1× margin, and per-call
   latency variance is real (VLM p95 9.2 s vs p50 3.9 s — a slow-tail run
   drifts toward 12–15 min). Recommendation for W2-M20: run the 50 cases with
   modest concurrency (even 4-way brings the wall time to ~2.5 min and the
   per-minute quota supports far more), and set the CI job timeout to the
   named 20-min budget, not to the mean.
3. **Quota is a non-issue at this key's tier** — 10,000 req/min and 10M input
   tok/min observed; the full gate uses <1% of one minute's quota. No daily
   cap surfaced in headers. Caveat: this is the LOCAL key's tier (W2-OA2
   pending); if the org key placed in repo secrets is a different tier, the
   quota reading must be re-checked — same CLI re-runs in minutes.
4. **Zero SDK retries observed (amplification 1.0)** — but the sample is
   small; W2-M20 should keep the amplification term in its budget math rather
   than assuming 1.0 (the extrapolator applies it automatically when retries
   occur).
5. **First-call warmup skews small-sample p95** — unit 1's judge call took
   6.6 s vs ~1.5–2.6 s for every later judge call. For CI budgeting, treat
   p95 from n=5 as the observed max (nearest-rank does exactly this), not as
   a stable tail estimate.
6. **Multi-page counting matters exactly as W2-D8 warned** — the 2-page unit
   produced 4 attempts vs 3 for single-page units; the projected 160 calls
   (not a lazy 150 = 50×3) is the number W2-M20 must budget.

## Decisions

- **PyYAML for the workflow lint** — genuine structural parse (trigger map,
  jobs→steps→checkout `ref`) instead of brittle regex; it is already installed
  via the declared `langgraph` → `langchain-core` chain, so no pyproject
  change (W2-M1-owned) and no new dependency.
- **Verdict + substitution note computed in-module** — a caller-supplied
  verdict would be a self-grading report (pinned by the frozen tests).
- **Render surface is whitelist-only + scrubbed** — headers/env values never
  enter the report dict; the scrub is belt-and-braces for future edits.
- **Budget defaults named as module constants** (`MAX_RUN_COST_USD`,
  `MAX_RUN_SECONDS`) so W2-M20 consumes a named number, not folklore.
- **Stdlib PNG (zlib+struct 5×7 font raster)** over pillow: W2-M1 had not
  merged, and the ticket pre-authorizes exactly this path.

## Secrets / PHI hygiene

- Secret values never read into output: the CLI loads `.env` into the process
  environment and surfaces env var NAMES only; report text is scrubbed
  (AC-4 property-tested with adversarial fake keys, multi-var env, DSN
  password segment).
- `agent/.env` confirmed gitignored (`agent/.gitignore:6`) before copying;
  never committed; working tree checked clean of it at commit time.
- All sample inputs synthetic and non-clinical, generated at runtime; raw
  provider outputs discarded (only aggregates above).

## Out of scope honored

- No `.github/workflows/` writes (lint is read-only); no CI jobs/branch
  protection (W2-M19/M20); no eval cases/rubrics/judge config; the trusted
  dry run + fork simulation remains reassigned to W2-M20; no binding-doc or
  `W2_DEVLOG.md` edits; no OpenEMR PHP; W2-OA2 remains an owner action.
