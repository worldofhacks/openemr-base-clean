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

## R05 — code complete, stacked PR open (2026-07-19)

- **R05 (AF-P1-04):** branch `fix/w2-observability-wiring` @ `ddfacf4`, PR #32
  (https://github.com/worldofhacks/openemr-base-clean/pull/32; stacked on R04's branch
  per §4b merge order, retargets when #28 merges). All five verified root-cause sites
  fixed: StructuredLogEventSink default, RETRIEVAL_COMPLETED emission, fused encounter
  summary (PDF p.5 field-complete), scheduled nine-rule w2_alerts evaluator (new
  agent-w2-alerts.yml cron) with fire/dedupe/clear drills for the three required
  alerts, ten dashboard panels documented. Suite 952/5 (939+13); gate PASS zero delta;
  mypy/ruff green. Production verification (one-ID reconstruction, panel data, webhook
  delivery) lands with O01/post-REL1.

## R09 — code complete, stacked PR open (2026-07-19) — CODE TRAIN COMPLETE

- **R09 (third document type):** branch `feat/w2-medication-golden-cases` @ `06a82b9`
  (2 commits), PR #33 (https://github.com/worldofhacks/openemr-base-clean/pull/33;
  stacked on R02's branch — sequential recordings ownership, retargets when #31
  merges). Three medication-list golden cases through the production replay path
  (clean grounded / wrapped-frequency visible-unverified / missing-date honesty);
  three byte-equivalence-justified replacements; manifest exactly 50; denominators
  unchanged (50/50/22/9/50); recorded baseline regenerated with provenance;
  RED→GREEN transcript in PR. CI suite 1207/6; gate PASS 0.0pp deltas.
- **Queued decision notes for the post-#24 docs sync** (W2_DECISIONS.md is contended
  by PR #24's EOF appends — apply immediately after it merges): (1) **G-D5** —
  medication_list grounding-only posture (text in PR #33 report); (2) R04 authority
  ledger pointer (ledger recorded in artifacts.py + agent/migrations/README.md);
  (3) R01 response-versioning note (additive presence-conditional `claims[]` lane,
  not a versioned endpoint).
- **S01 final-pass fixtures (post-merge):** `evals/fixtures/golden/
  med-list-clean-grounded.pdf` (clean beat) + `med-list-wrapped-frequency-unverified.pdf`
  (degraded beat) — supersede the kit's junk_layer.pdf medication beat.

## Merge train complete (2026-07-19, owner-authorized finishing run)

All ten code PRs merged into protected `main` in the ordered train, each after its
required checks passed (one exception noted below, mitigated and root-caused):

| PR | Task | Merge commit |
|---|---|---|
| #24 | R08 + G-D1..G-D3 | `43605c2` |
| #34 | docs: A01-RES + decision notes + C02-p1 exports | (docs) |
| #26 | R01 (P0) | `e26bd95` |
| #31 | R02 (P0) | `090cac5` |
| #35 | R09 (recreation of #33) | `1da141c` |
| #30 | R03 | `943c142` |
| #28 | R04 | `1b1591e` |
| #27 | R06 | `103d059` |
| #25 | R07 | `7b58b72` |
| #36 | R05 (recreation of #32) | `4b7f5f2` |
| #29 | C01 | `ae667aa` |

- **Stacked-PR mishaps, both recovered:** #33 was auto-closed when its base branch was
  deleted at #31's merge → branch replayed onto main, re-verified, merged as #35.
  #32's `--base main` retarget silently failed (GraphQL projectCards deprecation bug in
  `gh pr edit`) and the PR merged into its old UNPROTECTED base branch — main never
  received it and no protection was bypassed; branch replayed onto main (full suite
  1000/5 locally), merged as #36 after checks; the orphaned base branches
  (`fix/w2-authority-typing`, `fix/w2-reranker-warmup`) were deleted. Root cause noted:
  retargets must use the REST API (`gh api pulls/N -X PATCH -f base=main`) and the base
  must be verified before merging (#29 followed this and merged cleanly).
- Final suite on merged main (C01 tree): **1005 passed / 5 skipped**
  (936 baseline + 69 new frozen tests across the train).
- Post-merge main pushes: eval-tier1 + quality green; `eval-tier2-live` fails closed
  pending the owner's live-gate mint (stale live baseline after the R02/R09 manifest
  change — designed W2-O4 posture).

## C02 phase 2 — required gates extended + red/green merge drills (2026-07-19)

- **Phase-2 ruleset applied:** ruleset 19180393 PUT-updated to six required contexts
  (adds `quality-security-contracts / image-build-smoke`). Export:
  `gh api repos/worldofhacks/openemr-base-clean/rulesets/19180393`.
- **GitHub red drill:** PR #37 (`drill/w2-red-c02p2-merge-block` — the documented
  ranking-inversion mutation cherry-picked onto merged main). Result: `eval-tier1: fail`
  (1m11s), `mergeStateStatus: BLOCKED`, merge attempt refused — *"Pull request #37 is
  not mergeable: the base branch policy prohibits the merge."* `image-build-smoke`
  passed on the same run (proving the red came from the eval gate, not infrastructure).
  Bypass: `current_user_can_bypass: never` (API-attested). PR closed, branch deleted.
- **GitHub green drill:** the ten train merges above — each merged only after all
  required checks passed on the exact head SHA.
- **GitLab red MR:** MR !2 on the mirror (same drill branch) — merge blocked
  (`ci_still_running` → pipeline must succeed; terminal state recorded in
  W2_CI_EVIDENCE.md when the shared runner completes). Protected-branch + merge-check
  settings exports recorded in W2_CI_EVIDENCE.md §C02 phase 1.

## FINAL CLOSEOUT — release SHA b31207ce33ebe0706b2dc9fa13816b73fb08d4fc (2026-07-19)

- **Tier-2 mint (green, exact-SHA, protected environment):**
  https://github.com/worldofhacks/openemr-base-clean/actions/runs/29713267431 — all seven
  jobs green including `eval-tier2-live` (fresh full live 50-case gate at the release
  SHA). Durable copies + sha256 digests: W2_CI_EVIDENCE.md §E01 final
  (`results-tier2-live-b31207c.json` `132b3635…99fb6`;
  `results-tier1-b31207c.json` `c1255e0e…88e9`).
- **Chain-proof mint at the superseded candidate `89a2b86`:**
  https://github.com/worldofhacks/openemr-base-clean/actions/runs/29711288074 — green;
  retained as evidence, superseded-not-deleted by the Langfuse real-timestamp cycle
  (PR #41).
- **Root-cause chain closed this cycle:** stale reviewed baseline (fail-closed W2-O4
  posture) → G-D6 guideline-lane fixes (PR #40) → baseline re-mint → Langfuse real span
  timestamps (PR #41). Decisions: `W2_DECISIONS.md` G-D6; incident narrative:
  `W2_DEVLOG.md` 2026-07-19 entries.
- **Deployment at the release SHA (both services, exact-SHA):** `/health` returns
  `{"status":"alive","sha":"b31207ce…"}`; `scripts/verify_deployed_sha.py` →
  `PASS:web_and_worker_identity_readiness_and_synthetic_smoke`; 3× cache-busted
  `/ready` → `status: ready`, hard checks (openemr_fhir, anthropic, session_store) and
  soft checks (langfuse, retrieval_index, active_reranker) all ok. OpenEMR EHR public
  URL healthy (302 login redirect, 0.26 s); MySQL service deployment SUCCESS.
- **Langfuse real-latency confirmation (owner dry-run turns, post-deploy):** graph-turn
  traces at 03:02–03:08Z carry real non-zero durations (root spans 19.9–96.5 s);
  span cascade chronological (e.g. correlation `w2.74551dee…`: intake worker 3.26 s
  with sequential extract sub-calls, retrieval 5.79 s incl. rerank leg, composer
  67.2 s, critic 1 ms). Sub-millisecond routing-decision spans display as 0 ms at the
  API's millisecond precision — never negative. Trace/Observation percentile widgets
  now populate for the graph-turn family.
- **Push record:** `main` pushed to both remotes (origin GitHub + gitlab mirror) at this
  closeout; HEAD equality verified via `git ls-remote` at push time (the recording
  commit itself is the only delta after this line's content HEAD).
