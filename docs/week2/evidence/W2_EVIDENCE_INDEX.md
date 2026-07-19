# W2 Evidence Index

Closure evidence for `docs/week2/W2_IMPLEMENTATION_PLAN.md` (§8 checklist). Every entry
records the task, the exact SHA(s), and the verification performed. Created by W00; all
subsequent tasks append here. A §8 box may be checked only with a link recorded in this
file or directly in the checklist.

## W00 — Working-tree hygiene and push readiness (2026-07-19)

- **Doc set committed on `main`:** `6301e2f` `docs(w2): final-submission audit +
  remediation plan` (W2_ARCHITECTURE.md, W2_gap-audit.md, W2_IMPLEMENTATION_PLAN.md,
  new W2_BACKLOG_CHANGE_REQUEST_G.md, new prompts/W2_EXECUTION_PROMPT.md) +
  `a9e5b75` `chore: ignore personal Claude Code settings overrides`.
- **Both remote HEADs verified equal after push** (`git ls-remote`, 2026-07-19):
  - `origin` (github.com/worldofhacks/openemr-base-clean) `main` =
    `a9e5b756209ab2e0fcd919d5ca061f4c63153681`
  - `gitlab` (labs.gauntletai.com/alexander.miller/openemr-base-clean) `main` =
    `a9e5b756209ab2e0fcd919d5ca061f4c63153681`
- **G-D2 + R08 code isolated** on review branch `feat/g-d2-reader` (based on `6583079`),
  two commits per plan §6 PR 0/0b:
  - `f9b8fc4` `chore(w2): record owner decisions G-D1..G-D3; remove license-family ban`
  - `2dab3e0` `fix(agent): stop vetoing valid extractions on unreadable OCR evidence (R08)`
  - Branch pushed only after the R08 full-suite + recorded-gate verification (plan §4d.3).
- **Local tool state left uncommitted** (`.claude/settings.local.json` modification,
  `.agents/`, `AGENTS.md`); `.gitignore` now ignores the settings override (the file
  itself remains tracked — untracking is an owner call).
- Mid-run drift note: `W2_DECISIONS.md` gained owner entries G-D1/G-D3 (and G-D2's
  stale §9.2 pointer fix) at 13:52 local, confirmed deliberate by the owner; content
  rides `f9b8fc4`.

## R08 / PR 0b — extraction-robustness fix verified (2026-07-19)

- **Branch:** `feat/g-d2-reader` @ `a48987f` (rebased onto main @ `93ab760`); commits
  `7c99b75` (G-D1..G-D3 decision records + license-gate removal), `8881ed2` (R08 fix,
  16 new frozen tests), `a48987f` (picklable intake OCR fake — assertions unchanged).
- **Full suite:** `cd agent && .venv/bin/pytest -q` → **952 passed, 5 skipped**
  (≥ 936+5 baseline; +16 new frozen tests). First run caught one test-local OCR fake
  that could not pickle into the G-D3 spawned OCR child — hoisted to module level,
  assertions byte-identical (see W2_DEVLOG.md R08 verification entry).
- **Recorded 50-case gate:** `make eval-tier1` → gate=PASS, **zero category delta**
  (schema 50/50, citation 50/50, factual 23/23, safe_refusal 10/10, no_phi 50/50);
  artifact-scan PASS. Generated timing jitter in `evals/results-tier1.json` reverted.
- **PR 0b:** https://github.com/worldofhacks/openemr-base-clean/pull/24 — merge waits on
  C02 phase-1 protection (owner admin) + review; §8 R08 box stays unchecked until merged
  through the protected flow.

## Track A batch — C02-p1 prep, A01 draft, D01-lite, E01-lite, O02-lite, R07 (2026-07-19)

- **C02 phase-1 prep (owner applies):** `docs/week2/evidence/c02/W2_C02_PHASE1_RUNBOOK.md`
  + ready-to-POST `github-ruleset-main.json` (five required checks verified live on PR
  #24's head). Committed `0f33d75`. GitHub protection/rulesets confirmed absent at prep
  time (404 / `[]`).
- **A01 draft (owner sends):** `docs/week2/evidence/W2_A01_GRADER_QUESTIONS.md`
  (`0f33d75`). Answers append to `W2_DECISIONS.md`.
- **D01-lite:** dated known-gaps banner in `docs/week2/W2_gap-audit.md` (`72f9792`);
  verdict remains Not Ready.
- **E01-lite:** exact-SHA green Tier-1 + live Tier-2 results committed with digests +
  run URL (`7bbf079`); see `W2_CI_EVIDENCE.md` E01-lite section.
- **O02-lite:** partial datapoints + deployed retrieval probe recorded in
  `W2_COST_LATENCY.md` (labeled not-closure). Deployed `/evidence/search`
  p50 4.94 s / p95 6.49 s (n=30) — retrieval SLO risk flagged for O02/R07 follow-up.
- **R07 (code complete; production probes pending merge+deploy):** branch
  `fix/w2-reranker-warmup` @ `3e828a0`, PR #25
  (https://github.com/worldofhacks/openemr-base-clean/pull/25). Offline proofs: both
  pinned snapshots baked (306 MB); 3 consecutive fresh reranker probes ok at
  4.47/4.67/4.35 s with `--network none`; suite 941 passed / 5 skipped; recorded gate
  PASS zero delta. §8 box stays unchecked until 3× cache-busted all-green `/ready`
  post-deploy.
- **DEVLOG note:** Track-A DEVLOG entries are batched until PR 0b merges — PR 0b's
  branch already appends to `W2_DEVLOG.md` and parallel EOF appends on main would
  guarantee a merge conflict (single-writer discipline).

## R01 + R06 — code complete, PRs open (2026-07-19)

- **R01 (AF-P0-03):** branch `fix/w2-claim-citation-contract` @ `fdbe789`, PR #26
  (https://github.com/worldofhacks/openemr-base-clean/pull/26). Per-claim
  `ResponseClaim` lane in JSON + initial SSE + fallback UI; fail-closed 503 with zero
  clinical bytes on uncited/ambiguous claims; OpenAPI/Bruno synced. Suite 943/5
  (independently re-run); gate PASS zero delta. Frozen W1-envelope pins honored via a
  presence-conditional lane (decision note to ride docs sync). §8 box unchecked until
  merged + deployed click-to-source smoke (O01).
- **R06 (AF-P1-10):** branch `fix/w2-cohere-retry` @ `6cedec6`, PR #27
  (https://github.com/worldofhacks/openemr-base-clean/pull/27). Bounded jittered retry
  (max 2 attempts, 8 s deadline), permanent-vs-retryable classification, breaker per
  attempt, content-free telemetry; 31 fake-clock tests in 0.04 s. Suite 936/5 (=
  baseline; corpus dir 106 passed explicitly); gate PASS zero delta.
- Dispatched next: R03 (`fix/w2-conditional-routing`), R04 (`fix/w2-authority-typing`).
  R02 (long pole) and the S01 dry-run kit still in flight.

## S01 dry run + recording kit (2026-07-19)

- **Kit:** `docs/week2/evidence/W2_S01_RECORDING_KIT.md` + 8 sanitized dry-run
  screenshots in `docs/week2/evidence/s01/` (synthetic data only; no URL bars/tokens).
- **Verified live-authenticated on SHA `6583079` (2026-07-19):** `/health` SHA match;
  `/ready` all eight probes green (no reranker flap at probe time); SMART launch →
  sign-in → authorize → workbench; lab upload → bounded status → grounded extraction →
  **bbox click-to-source opens the correct page with visible overlay (W2-REQ-29)**;
  intake double-upload idempotency (same doc id, fresh readback digests, UNSUPPORTED
  fields redacted); medication list source+artifact-only; cited answer rendering all
  three CitationV2 source classes with chip click → page/bbox preview; committed
  exact-SHA eval aggregates; red-gate via committed drill URLs.
- **All six PDF p.5 elements demonstrable today.** Degraded-until: per-claim inline
  citations + critic badge (R01, PR #26), medication-list gate cases (R09), dashboard
  beat (R05), Langfuse correlation walk (owner access; O01).
- Demo patient: Daron260 Windler79 (Synthea synthetic). UI notes: one garbled guideline
  snippet (corpus PDF-extraction artifact — avoid via question choice); intake OCR path
  ~60–90 s (narrate over poll).
- **Owner actions remaining for the §8 S01 box:** record (kit makes it one sitting),
  frame/transcript PHI scan, publishing decision, link into README + this index.

## R04 — code complete, PR open (2026-07-19)

- **R04 (AF-P1-03):** branch `fix/w2-authority-typing` @ `11e7c16`, PR #28
  (https://github.com/worldofhacks/openemr-base-clean/pull/28). Authority ledger in
  `artifacts.py` + new `agent/migrations/README.md` (inventory 001, 003–007; 002 never
  existed); typed `WritePayload` union across the write facades; service.py seams
  typed; 3 fail-closed divergence tests. Suite 939/5; mypy CI invocation + typed files
  Success; gate PASS zero delta. Authority-ledger decision note deferred to the docs
  sync batch (W2_DECISIONS.md contended by PR #24 EOF appends). R05 rebases on this
  (§4b order R04 → R05).

## C01 — code complete, stacked PR open (2026-07-19)

- **C01 (AF-P1-05):** branch `ci/w2-image-smoke-mypy-ratchet` @ `1e7b13d`, PR #29
  (https://github.com/worldofhacks/openemr-base-clean/pull/29; stacked on R07's branch,
  retargets to main when #25 merges). New required-check candidates:
  `quality-security-contracts / image-build-smoke` + ratchet-backed
  `ruff-mypy-coverage`. Three red drills proven locally (type error, startup crash,
  malformed readiness) then restored green; smoke proves R07's offline weight
  resolution in-image. Suite 946/5 (941 base + 5 ratchet tests); gate PASS zero delta.
  Smoke boots with RETRIEVAL_WARMUP=0 (documented memory-bound rationale).

## R03 — code complete, PR open (2026-07-19)

- **R03 (AF-P1-02):** branch `fix/w2-conditional-routing` @ `724d0f4` (f0039fa + the
  orchestrator-applied 5-line readiness wiring in app/main.py), PR #30
  (https://github.com/worldofhacks/openemr-base-clean/pull/30). Need-sensitive routing
  (four-route matrix, deterministic merge), nested sub-span tree + event-lane route
  decisions, `probe_graph_state` readiness (soft; hard only under W2_GRAPH_REQUIRED).
  Suite 956/5 (936+20, re-verified post-wiring); gate PASS zero delta. Honest scope
  notes in the PR: single hybrid-search sub-span (corpus instrumentation deferred),
  dynamic-pipeline path uses INGESTION_STAGE events + per-document sub-span. Deployed
  trace lands with O01.

## R02 — code complete, PR open (2026-07-19) — P0 long pole

- **R02 (AF-P0-02):** branch `fix/w2-eval-production-retrieval` @ `c447ec6` (3 commits),
  PR #31 (https://github.com/worldofhacks/openemr-base-clean/pull/31). Production
  HybridRetriever in the accepted route (both tiers); recorded embedding/rerank
  adapters, byte-replay, network-free, fail-closed; term-overlap retired (poisoned-path
  proof); retrieval pins enforced by artifact_scan; **recorded tier now loads the
  committed baseline — >5pp rule binds at PR time**; two mutation drills red
  (transcripts in PR; permanent pytest drills; drill branches
  `drill/w2-red-retrieval-{ranking,availability}` pushed).
- **Golden-set change flagged for owner/V01:** two byte-equivalent cases replaced
  (no-query behavior; embedder-outage replay), three strengthened with
  expected_retrieval blocks; manifest exactly 50; denominators now factual 22 /
  safe_refusal 9. Justifications in the PR body.
- **Critical-path consequence:** committed live `w2_baseline.json` +
  `results-tier2.json` are stale for the new manifest — live tier fails closed on main
  post-merge until a green live run at the merged SHA regenerates them (C02-p2/E01;
  owner Tier-2 credentials).
- Verification (agent + orchestrator re-run): corpus+evals 237/1; full suite 936/5;
  CI suite 1207/6; gate PASS with `baseline=1.0 delta_pp=0.0` per category;
  artifact-scan PASS scanned=3.
