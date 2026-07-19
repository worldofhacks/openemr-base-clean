# AgentForge Week 2 audit remediation plan

**Source of scope:** `docs/week2/W2_gap-audit.md` (final-submission audit) and
`docs/week2/Week_2_AgentForge.pdf` pp.1–7

**Audit baseline:** branch `fix/w2-demo-bugs` @ `658307936f0396d292c94fff3f9ef8089f1697e7`
(= `origin/main` tip = deployed `/health` SHA, cache-busted 2026-07-19)

**Revalidated:** 2026-07-19 ~11:40 CDT, against the real tree, workflows, golden set, live
deployment, and both hosting remotes. This revision corrects the prior draft's baseline SHA,
branch name, repository path map, citation-source counts, migration inventory, test-suite
baseline, and verification commands, and adds the deadline model both reviews found missing.

**Deadline model (owner decision 2026-07-19):** The PDF (p.3) sets Final = Sunday @ Noon
Central; the owner has **accepted a late submission** in exchange for a complete, defensible
project. Quality therefore outranks speed, and nothing on the Ready bar may be cut for
schedule. Two tracks remain, reframed:

- **Track A — early-visible deliverables (start immediately, no scramble):** tree hygiene,
  branch protection, reranker stabilization, first-pass video, committed eval evidence,
  partial cost report, known-gaps banner. These make the repository presentable at any moment
  while Track B completes. Track A never claims a Ready verdict.
- **Track B — Ready:** close every P0/P1 with exact-SHA evidence per the closure checklist
  (§8). The verdict flips only through V01, an independent final verification pass on the
  release SHA.

## 1. Scope and outcome

This plan contains only work required to close:

- AF-P0-01 through AF-P0-03;
- AF-P1-01 through AF-P1-11; and
- AF-P2-01 through AF-P2-06, solely to resolve the ambiguities identified by the audit.

It does not add features, cleanup, refactors, or process work unless explicitly necessary to
close one of those findings, with three owner-directed additions: R07 (stabilizes an observed
production degradation that blocks O01/O02/S01 evidence quality) and — added 2026-07-19 by
owner direction so that NOTHING required for submission lives outside this plan — R08 (lands
the already-implemented extraction-robustness fix for cursive/handwritten forms and image
intake, required by W2-REQ-91/92) and R09 (makes the third-document-type deliverable,
`medication_list`, demonstrable through the eval gate — code is complete; golden coverage is
zero). Non-audit feature work ("Change request G") lives in
`docs/week2/W2_BACKLOG_CHANGE_REQUEST_G.md` and must not compete with submission blockers.
Everything else that remains is an owner action, enumerated in §4c. The release moves from **Not Ready** to **Ready** only after every P0 and P1 row in
§3 is closed with the named evidence. Until an owner/grader answers a P2 question, the
conservative current behavior described in Task A01 stays in place.

**PDF coverage assertion:** every one of the 103 RTM rows in `docs/week2/W2_gap-audit.md` whose
status is not Met maps to a task in this plan (cross-walk in §3); Met rows are regression-guarded
by the existing CI suites. Two operational tasks (W00 tree hygiene, REL1 release/activation) and
the sub-agent dispatch protocol (§4a) were added in this revision because a plan cannot be
"ready to push" while the working tree holds unreviewed mixed changes and no task owns the final
deploy/flag-activation step.

## 2. Revalidation and drift from the audited commit (corrected)

| Audit area | Current evidence (verified 2026-07-19) | Plan consequence |
|---|---|---|
| Repository | Branch `fix/w2-demo-bugs` @ `6583079…` = `origin/main` tip. Worktree carries unrelated concurrent doc edits from parallel sessions. | Each PR must contain only its own diff; adopt single-writer discipline per file (§7). |
| Test suite | 936 passed, 5 skipped, 1 warning (matches gap-audit T1; independently rerun). The prior draft's 951/2 figure was not reproducible. | Passing tests do not close audit findings. |
| Deployment | `/health` (cache-busted) = exact HEAD SHA; live app is current. `/ready` (cache-busted, 11:36 CDT) = **degraded — `active_reranker: timeout`**; all hard checks green incl. `document_runtime: ready`. Plain URLs may serve stale cached responses (pre-`8a21edc` class) — always cache-bust. | No SHA drift. The reranker readiness flap is real and recurring → R07. Record demo/profiles only against an all-green `/ready`. |
| AF-P0-01 / AF-P1-11 | GitHub `main`: no branch protection (404), rulesets `[]`. GitLab mirror matches HEAD exactly; protection/required-pipeline state unverified. | C02 (two phases). Phase 1 is minutes-to-hours with admin access — do it in Track A. |
| AF-P0-02 | `agent/evals/execution.py:843-907` still uses the local term-overlap path; **all 449 golden expected citations are `uploaded_document`** (not patient-record — prior draft had this inverted). No guideline-citation cases exist. | R02. Must also solve offline model mechanics (recorded embedding/rerank adapters or pre-populated `FASTEMBED_CACHE_DIR`) because Tier 1 runs under `network_disabled()`. |
| AF-P0-03 | `agent/app/routes/chat.py:124-133` (there is no `routes/answer.py`) returns one brief + flat `citations` list; per-claim structure exists internally (`VerifiedComposition`) and in W2 composition SSE events. | R01 is a serialization/contract task — 0.5–1 day, not 1–2 days. |
| AF-P1-01 | Public shell, SMART sign-in, `/openapi.json`, and attested document runtime verified; authenticated identity-linked journey not evidenced. | O01. |
| AF-P1-02 | `agent/app/orchestrator/graph.py:208-229` routes state-sequentially; graph nesting exports only to the Langfuse sink; deployed `W2_GRAPH_ENABLED` not externally observable. | R03. |
| AF-P1-03 | PostgreSQL is already the *declared* durable artifact authority (`agent/app/ingestion/artifacts.py:105` — "Durable artifact authority backed by migration 004"); OpenEMR holds a second durable copy; weak `object`/open-string facades remain. Migration inventory is 001, 003–007, **all present in the audited SHA** (006/007 are not post-audit drift). | R04 is an authority-declaration/divergence-test/typing task, not a data migration (1 day, not 3–5). |
| AF-P1-04 | Root causes verified: W2 event emitter defaults `NullEventSink` (`agent/app/service.py:204`); `RETRIEVAL_COMPLETED` registered but never emitted (`events.py:246-250`); encounter-summary emitters zero-fill each other (`ingestion/telemetry.py:207`, `observability/langfuse.py:486-487`); `agent/ops/alert_checker.py:481-515` evaluates a four-signal Week-1 set, never reads `w2_alerts.json`, and is unscheduled. | R05 rewritten around these exact sites. |
| AF-P1-05 | CI type-checks a curated file list via CLI flags (`agent-quality.yml:52-73`; no `[tool.mypy]` config); no image build/start/readiness job. | C01, narrowed to image smoke + bounded mypy ratchet. |
| AF-P1-06 | Committed `results-tier2.json` INCONCLUSIVE; green live result exists as CI artifact (GitHub artifacts expire ~14 days) bound to `6059703…`. | E01 simplified: commit sanitized exact-SHA results + run URLs + digests. No signing infra. |
| AF-P1-07 | No video; script exists (`docs/week2/evidence/W2_DEMO_SCRIPT.md`). | S01 — Track A item; the PDF's six elements are demonstrable on the current deployed SHA with owner credentials. |
| AF-P1-08 | No four-flow profile. Existing live-gate artifact (50 calls, ≈$3.07, p50 5.61 s, p95 12.27 s) is an eval aggregate — **not** closure. k6 harness `agent/load/k6/w2_profiles.js` is committed. | O02; O02-lite in Track A is a labeled partial, never claimed as closure. |
| AF-P1-09 | Plan/script/tests exist (`agent/scripts/restore_drill.py`); no backups/restore executed. | O03; start immediately (calendar wait risk). |
| AF-P1-10 | Cohere: 4 s timeout + breaker + local fallback, **no bounded retry** (`agent/corpus/retrieval.py:261-333`). | R06. |
| Eval-gate nuance | Recorded Tier-1 passes `baseline=None` (`agent/evals/w2_runner.py:341-347`): the >5pp delta rule runs only live-tier; PRs enforce the ≥0.90 floor + 100% invariants. | Documented in C02/E01 evidence; optionally close by loading the baseline in Tier 1 (small, R02-adjacent). |

Revalidation commands (corrected to real paths):

    git rev-parse HEAD && git branch --show-current
    cd agent && .venv/bin/pytest -q
    rg -n "HybridRetriever|_local_retrieve" agent/evals agent/app agent/corpus
    python3 -c "import json,collections;cs=json.load(open('agent/evals/golden/cases.json'));print(collections.Counter(c['source_type'] for x in cs for c in (x.get('expected_citations') or [])))"
    rg -n "NullEventSink" agent/app/service.py
    rg -n "w2_alerts" agent/ops
    curl -fsS "https://agent-production-9f62.up.railway.app/ready?cb=$(date +%s)"

## 3. Finding-to-task matrix

| Finding | Related requirement IDs | Closing task | Required closing evidence |
|---|---|---|---|
| AF-P0-01 | W2-REQ-01, 05, 31, 36, 41, 51 | C02 | Required, unbypassable gates plus negative and positive merge proof on both hosts. |
| AF-P0-02 | W2-REQ-01, 05, 12, 36, 39, 40, 50 | R02 | Full evaluator traverses production `HybridRetriever`; guideline/irrelevant/unavailable cases; mutation drills go red. |
| AF-P0-03 | W2-REQ-27, 28, 98 | R01 | Every externally returned clinical claim carries its own CitationV2 set in JSON, SSE, and fallback UI. |
| AF-P1-01 | W2-REQ-04, 06, 07, 13, 16, 18, 25, 29, 38, 41, 54, 85, 96, 99 | O01 | Identity-linked production end-to-end run, one correlation ID, stored artifacts and traces, exact SHA. |
| AF-P1-02 | W2-REQ-04, 11, 74, 85 | R03 | Four route combinations in tests + deployed graph traces with nested sub-spans. |
| AF-P1-03 | W2-REQ-14, 18, 55, 56, 57, 79, 89 | R04 | Declared single authority + divergence test + typed facades + corrected migration notes (001, 003–007). |
| AF-P1-04 | W2-REQ-13, 34, 58, 62, 63, 64, 73, 76, 89 | R05 | Live sink wired, dormant events emitted, fused encounter record, `w2_alerts.json` evaluated on a schedule, three alerts exercised. |
| AF-P1-05 | W2-REQ-65 | C01 | Image build/start/readiness job + bounded mypy ratchet, both required. |
| AF-P1-06 | W2-REQ-50 | E01 | Durable committed evaluator evidence bound to the accepted SHA. |
| AF-P1-07 | W2-REQ-06, 13, 52 | S01 | 3–5 minute video with the PDF's six elements. |
| AF-P1-08 | W2-REQ-06, 53, 59, 71 | O02 | Four-path latency/CPU/memory/throughput/cost report, p50/p95, W1 comparison, spend + forecast. |
| AF-P1-09 | W2-REQ-81, 89 | O03 | Backup configuration + timed isolated restore meeting RPO ≤24 h / RTO ≤60 m. |
| AF-P1-10 | W2-REQ-60 | R06 | Cohere bounded-retry classification tests + telemetry. |
| AF-P1-11 | W2-REQ-47 + AF-P0-01 gate IDs | C02 | GitLab protected-branch and required-pipeline proof. |
| Deployment degradation (new, observed) | W2-REQ-54, 75; prerequisites for O01/O02/S01 | R07 | Cache-busted `/ready` consistently all-green; reranker probe no longer flaps. |
| AF-P2-01 – AF-P2-06 | W2-REQ-42–46, 80, 100–103 | A01 | Written grader/owner answers; any resulting work mapped back to its P2 finding. |
| Documentation sync (both reviews) | W2-REQ-48, 67, 90 | D01 | Architecture/gap-audit/README/evidence index updated to final statuses and verdict. |

## 4. Execution order

### Track A — immediate parallel actions (submission salvage, hours)

| # | Action | Task | Time |
|---|---|---|---|
| A-0 | Tree hygiene: commit the audit-doc set; isolate the uncommitted G-D2 application edits (`reader.py`, `pyproject.toml`, `test_reader_geometry.py`, `gates.md`, `TICKETS.md`, decision docs) into their own reviewed branch — they must not ride a submission push unreviewed. | W00 | 0.5 h |
| A-1 | Verify GitLab mirror holds HEAD (already confirmed matching); push any final doc commits to both remotes. | C02-p1 | minutes |
| A-2 | Enable GitHub branch protection + ruleset requiring the existing `agent-eval-gate` (quality + eval-tier1) checks; enable GitLab protected branch + required pipeline. | C02-p1 | minutes–hours (admin) |
| A-3 | Stabilize reranker readiness; confirm cache-busted `/ready` all-green. | R07 | 0.25–0.5 d |
| A-4 | Record the demo video against the current deployed SHA with owner credentials: upload, extraction, evidence retrieval, citations, eval results, observability. Re-record only if the accepted SHA later changes materially. | S01 | 0.5 d |
| A-5 | Commit sanitized current eval evidence + run URLs + digests (14-day artifact expiry makes this urgent). | E01-lite | 0.5–1 h |
| A-6 | O02-lite: report scaffold with the eval-aggregate numbers + one k6 smoke, explicitly labeled "partial — not AF-P1-08 closure". | O02-lite | 1–2 h |
| A-7 | D01-lite: add a dated "known gaps at submission" banner to the gap audit; verdict stays Not Ready with Track B pointer. | D01-lite | 0.5 h |
| A-8 | Secure access needed by Track B: tester/SMART demo credentials, Langfuse tenant, Railway billing export, backup admin, alert channel. | O-prep | parallel |

### Track B — Ready (order and gates)

| Order | Tasks | Gate to leave the stage |
|---|---|---|
| 0 | A01 (grader questions out early; answers may arrive any time) | All six answers recorded; new work mapped to its P2 finding. |
| 1 | R01 → R02; R03, R04, R05, R06, R07 in parallel | Finding-specific tests pass; no public contract, authority, or observability gap remains in code. |
| 2 | C01 | Complete image build/start/readiness + mypy ratchet green in CI. |
| 3 | C02 phase 2 | Both hosts enforce required gates; red candidate blocked, green candidate merges (drill evidence archived). |
| 4 | E01 | Accepted SHA has durable committed evaluator evidence. |
| 5 | REL1 → O01 | Accepted SHA deployed, W2 flags attested, then the production journey proven on it. |
| 6 | R05 production checks; O02; O03; S01 final (re-record only if needed) | Observability, performance/cost, backup/restore, demo evidence complete. |
| 7 | D01, V01 | Independent verification re-scores the RTM against the release SHA; only V01 flips the verdict to Ready. |

Critical path: **R01 → R02 → C02-p2 → E01 → REL1 → O01 → O02/O03 → D01.** O03 starts
immediately (retention/calendar wait). A01 runs in parallel; only an AF-P2-04 answer can block
R04's authority wording or O01's trend verification.

### 4a. Sub-agent dispatch protocol

Rules: one sub-agent per task, one branch per task, touch **only** the owned files below, rebase
on `main` before opening the PR, never regenerate `agent/evals/recordings/` except where noted
(single regeneration owner: R02, after R01 merges). Doc-status updates (D01) ride each PR for
the finding it closes. A task is *done* only when its verification command passes locally, its
PR is green in `agent-eval-gate`, and its closing evidence is linked in §8.

| Task | Branch | Owned files (primary) | Merge after | Verification (done signal) |
|---|---|---|---|---|
| W00 | `chore/w2-tree-hygiene` | commits only; no content edits | — (first) | `git status` clean on main; G-D2 edits isolated on `feat/g-d2-reader` |
| A01 | n/a (message + docs append) | `docs/week2/W2_DECISIONS.md` (append answers) | — | six recorded answers |
| R01 | `fix/w2-claim-citation-contract` | `app/routes/chat.py`, `app/routes/ui.py`, `app/schemas/answers.py`, `ops/openapi.yaml`, `bruno/`, `tests/test_chat_route.py`, `tests/test_answer_closeout.py`, `tests/test_week1_ui_citations.py` | W00 | R01 pytest set + `pytest -q evals` green |
| R02 | `fix/w2-eval-production-retrieval` | `evals/execution.py`, `evals/w2_runner.py`, `evals/golden/cases.json`, `evals/recordings/` (sole owner), `evals/refresh_recordings.py`, eval-gate workflow env | R01 | corpus+evals suites green; both mutation drills red; recorded tier network-free |
| R03 | `fix/w2-conditional-routing` | `app/orchestrator/graph.py`, `state.py`, `workers/`, `tests/test_graph_skeleton.py`, `tests/test_orchestrator_trace.py` | W00 | four-route matrix + trace-tree tests green |
| R04 | `fix/w2-authority-typing` | `app/ingestion/artifacts.py` (docs), `app/service.py` + `app/writeback/` facade types, `agent/migrations/` notes, divergence test files | W00 | mypy-pinned invocation + R04 pytest set green |
| R05 | `fix/w2-observability-wiring` | `app/observability/`, `app/service.py` (sink wiring only — coordinate the one shared file with R04 via merge order R04→R05), `app/ingestion/telemetry.py`, `ops/alert_checker.py`, `ops/w2_alerts.json` schedule, dashboards | R04 | observability pytest set green; three synthetic alerts fire |
| R06 | `fix/w2-cohere-retry` | `corpus/retrieval.py` (retry seam only), `corpus/tests/` | W00 | fake-clock retry/breaker tests green |
| R07 | `fix/w2-reranker-warmup` | `agent/Dockerfile`, startup warmup, `app/health.py` budget (only if needed) | W00 | 3× consecutive cache-busted `/ready` all-green after restart |
| C01 | `ci/w2-image-smoke-mypy-ratchet` | `.github/workflows/agent-quality.yml`, ratchet file, `agent/Dockerfile` smoke hooks | R01–R07 branches defined | negative fixtures red, clean run green |
| C02 | `ci/w2-required-gates` + host admin config | `.github/workflows/`, `.github/scripts/verify_github_gate.py` wiring, `.gitlab-ci.yml` | C01 | rule exports + red/green merge drills on both hosts |
| E01 | `chore/w2-eval-evidence` | `evals/results-tier*.json`, `docs/week2/evidence/W2_CI_EVIDENCE.md`, submission check | C02 | fresh-clone verification of SHA-bound green results |
| REL1 | release operation (no branch) | merge train, deploy, activation | E01 | deployed `/health` = accepted SHA; `/ready` all-green; graph attested |
| O01/O02/O03/S01 | evidence operations | `docs/week2/evidence/` outputs only | REL1 | §5 acceptance blocks |
| D01 | rides each PR + `docs/w2-final-sync` | root `W2_ARCHITECTURE.md`, `docs/week2/W2_gap-audit.md`, `README.md` | continuous | no stale statuses at verdict flip |
| V01 | independent verification (fresh session/agent, read-only + docs) | re-scores RTM; may edit only gap-audit verdict + evidence index | all of the above | fresh-clone grader simulation passes; verdict flip authorized |

### 4b. Timeline estimate (parallel sub-agents)

Critical path: W00 (0.5 h) → R01 (0.5–1 d) → R02 (2–3 d) → C02-p2 (1 d) → E01+REL1 (1 d) →
O01 (1 d) → O02/S01-final/V01 (1 d) ≈ **6–8 working days end-to-end**; R03/R04/R05/R06/R07/C01
and O03's calendar wait are absorbed in parallel lanes, so **~4–5 calendar days with 3–4
concurrent sub-agents**. The long pole is R02; staff it first and keep it single-owner.

File-conflict notes: `app/service.py` is shared by R04 (types) and R05 (sink) — strict merge
order R04 → R05. `agent/Dockerfile` is shared by R07 and C01 — R07 merges first.
`evals/recordings/` is owned exclusively by R02; the G-backlog regeneration (if ever executed)
must rebase onto R02's recordings, never regenerate in parallel. `agent/app/llm/vlm.py`,
`agent/app/ingestion/reader.py`, and `agent/app/ingestion/image_reader.py` are owned by R08
until its PR merges (reader.py also carries G-D2 docstring edits — one reviewed branch may
carry both, as two commits). R09's golden cases land only after R02 owns the recordings.

### 4c. Owner-action ledger (everything that is NOT a plan task)

Per owner direction 2026-07-19, all remaining work is either a §5 task or one of these
owner-only actions — nothing else exists outside this document:

1. **Host admin access for C02 phase 1:** enable GitHub branch protection/rulesets and GitLab
   protected-branch + required-pipeline settings (agents prepare configs; only the owner holds
   admin).
2. **Tier-2 credential provisioning (W2-O4, W2_DECISIONS.md):** protected `eval-tier2-live`
   `ANTHROPIC_API_KEY`, `RAILWAY_TOKEN`, masked GitLab status/mirror credential, Railway
   backups authorization. Until provisioned, the live gate, exact-SHA deploy evidence, GitLab
   bridge, and O03 restore drill fail closed.
3. **Grader/P2 communication (A01):** send the six questions, receive and record answers.
4. **S01 publishing decision:** approve the recorded demo link's audience/access before it goes
   into the README.
5. **REL1 go/no-go:** final owner approval to deploy and activate the accepted SHA.
6. **Late-submission message to graders:** the deadline model (§ header) means the Final lands
   late; the owner sends the submission note stating current state + honest known-gaps banner
   (D01-lite provides the text).

### 4d. Execution protocol — PDF conformance + TDD discipline (binding on every implementing agent)

Owner direction (2026-07-19): agents executing this plan continuously verify against the
AgentForge PDF and work test-first — completing a task is not the goal; conforming is. The
binding loop for EVERY §5 task:

1. **Before writing code:** re-read the task's cited PDF sections
   (`docs/week2/Week_2_AgentForge.pdf`) and its RTM rows in `docs/week2/W2_gap-audit.md`;
   restate the acceptance criteria in the PR description in the PDF's own words. If task text
   and PDF conflict, the PDF wins — stop, record the discrepancy in this plan, then proceed.
2. **RED first:** write or extend the failing test that pins the acceptance criterion BEFORE
   the implementation (`.tdd-swarm/gates.md` discipline). Frozen tests are contracts: never
   weaken, never delete, never go green by editing an assertion. A genuine behavior change
   requires an owner-recorded decision first (the G-D2 / G-fix entries in `W2_DECISIONS.md` and
   `W2_DEVLOG.md` are the precedent for how to do this correctly).
3. **Green + gates before any PR:** implement to green; run the full suite
   (`cd agent && .venv/bin/pytest -q`, ≥ 936-passed baseline) and the recorded 50-case gate
   locally. Any rubric-category delta is stop-and-diagnose — never a threshold judgment call.
4. **Evidence at completion:** a §8 box may be checked only with a link to the commit, CI run,
   or production probe at the exact SHA. Unverifiable means unchecked.
5. **Single-writer + scope:** own only your task's files (§4b conflict table); PRs contain only
   their own diff; re-read any shared doc immediately before writing (parallel-session drift
   was observed twice on 2026-07-19).
6. **Stop conditions:** an unexpected frozen-test failure, any golden-category delta, any
   PDF-conformance question, or any file outside your scope → stop, record, ask the owner.
   The grader's HARD GATE (PDF p.5) introduces a regression deliberately; this discipline is
   what makes it bounce.

## 5. Audit remediation tasks

### W00 — Working-tree hygiene and push readiness (blocking first step)

- **Why:** As of 2026-07-19 ~11:58 CDT the tree mixes three concerns: (a) the audit/plan doc
  set (`W2_ARCHITECTURE.md`, `W2_gap-audit.md`, this plan, the new backlog file); (b)
  **unreviewed G-D2 application-code edits** (`agent/app/ingestion/reader.py`,
  `agent/app/ingestion/__init__.py`, `agent/pyproject.toml`,
  `agent/tests/test_reader_geometry.py`, `.tdd-swarm/gates.md`, `TICKETS.md`,
  `docs/week2/W2_DECISIONS.md`, `W2_DEVLOG.md`); (c) local tool state (`.claude/`,
  `.gitignore`, `.agents/`, `AGENTS.md` — never push). Nothing is "ready to push" until these
  are separated.
- **Implementation:** (1) commit the doc set as `docs(w2): final-submission audit + remediation
  plan`; (2) move the G-D2 code edits to branch `feat/g-d2-reader` for review — they alter the
  reader stack and test assertions and must pass the full gate before any release train; (3)
  review the uncommitted `.gitignore` diff (+3 lines): commit it only if the entries belong in
  the repository (e.g., ignoring local tool dirs), otherwise revert it; (4) leave local tool
  state (`.claude/`, `.agents/`, `AGENTS.md`) uncommitted; (5) push `main` + doc commit to
  `origin` and `gitlab` remotes; (6) record both remote HEADs in the evidence index.
- **Acceptance:** `git status` on `main` shows only intentionally-unpushed local tool state;
  both remotes at the same doc-commit SHA; G-D2 edits isolated with their own PR description.
- **Effort:** 0.5 h.

### A01 — Resolve the six P2 ambiguities with the grader (corrected questions)

- **Findings:** AF-P2-01…06; W2-REQ-42–46, 80, 100–103.
- **Objective:** Written grader/instructor answers to exactly the six audit ambiguities. These
  are grading-scope questions — direct them to the course/grader channel, not repo owners.
- **The six questions (corrected framing):**
  1. Critic (AF-P2-01): p.4 calls the critic "extension work, not core"; p.5 lists it as a Core
     Deliverable. Is it graded core? (It already exists post-composition and is flag-gated —
     the question is scope, **not** "before or after composition".)
  2. Third document type (AF-P2-02): p.3 MVP says two types; p.5 requires a third. Is
     `medication_list` (source + grounded artifact, no clinical write) sufficient?
  3. Click-to-source UI + contextual retrieval improvements (AF-P2-03): are the p.5 bullets
     graded core, and do section-aware chunking + deterministic query building qualify as
     "contextual retrieval improvements"?
  4. Lab trend chart (AF-P2-04): does an artifact-backed trend qualify, or is a discrete FHIR
     Observation write required? (Also determines R04's projection wording.)
  5. Eval-data wording (AF-P2-05): p.7 forbids extracted values/raw documents in analytics/eval
     artifacts while Stage 4 requires fixture documents with expected values — do synthetic
     fixtures satisfy the intent?
  6. Checkpoints (AF-P2-06): confirm the calendar dates/timezone for MVP Tue 11:59 PM, Early
     Thu 11:59 PM, Final Sun noon (Central per p.3), and how schedule conformance is graded.
- **Acceptance:** Six recorded answers (verbatim or approved paraphrase, author + timestamp);
  no ambiguity silently treated as passed; conservative current behavior retained meanwhile.
- **Effort:** 1 hour to send/record; external response time excluded.

### R01 — Per-claim citation contract at every public boundary (P0)

- **Findings:** AF-P0-03; W2-REQ-27, 28, 98.
- **Current evidence:** `VerifiedComposition` already carries one-claim/one-citation internally
  and in W2 composition SSE events (`chat.py:192-215`); the JSON `ChatResponse` and initial
  SSE/fallback block flatten to `brief` + `citations[]` (`chat.py:124-133`).
- **Files:** `agent/app/routes/chat.py`, `agent/app/schemas/answers.py`,
  `agent/app/schemas/citations.py`, `agent/app/orchestrator/composer.py`, `agent/app/verify/`,
  `agent/app/routes/ui.py` (fallback UI), `agent/ops/openapi.yaml`, `agent/bruno/`,
  `agent/tests/test_chat_route.py`, `agent/tests/test_answer_closeout.py`,
  `agent/tests/test_week1_ui_citations.py`.
- **Implementation:** (1) closed `claims[]` response model — claim text, source class, verdict,
  CitationV2 list, optional overlay ref; (2) serialize identically in JSON, initial SSE block,
  and fallback UI; (3) keep `brief`/`citations` as derived, documented-non-authoritative
  compatibility fields; (4) fail closed when a claim has zero evidence or ambiguous assignment;
  (5) update OpenAPI + Bruno + contract tests.
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_chat_route.py tests/test_answer_closeout.py tests/test_w2_serving_integration.py tests/test_week1_ui_citations.py
      cd agent && .venv/bin/pytest -q evals

- **Acceptance:** Every returned clinical claim owns exactly its CitationV2 set in all response
  modes; deliberately uncited output fails closed; OpenAPI/Bruno/contract tests green.
- **Effort:** 0.5–1 day.

### R02 — Full evaluator through production retrieval (P0)

- **Findings:** AF-P0-02; W2-REQ-01, 05, 12, 36, 39, 40, 50.
- **Current evidence:** `_local_retrieve` term-overlap path (`agent/evals/execution.py:843-907`)
  serves both tiers; **all 449 expected citations are `uploaded_document`** — zero guideline
  cases; Tier 1 runs under `network_disabled()` (`recorded_executor.py:154-189`) while
  `HybridRetriever` lazily downloads FastEmbed/reranker weights (`corpus/retrieval.py:440-445,556-561`).
- **Files:** `agent/evals/execution.py`, `agent/evals/w2_runner.py`, `agent/evals/harness.py`,
  `agent/corpus/retrieval.py`, `agent/evals/golden/cases.json`,
  `agent/evals/refresh_recordings.py`, `agent/evals/recordings/index.json`, workflow env in
  `.github/workflows/agent-eval-gate.yml`.
- **Implementation:** (1) inject production `HybridRetriever` into evaluator execution over the
  committed corpus/index; (2) solve offline determinism explicitly — either recorded
  embedding/rerank adapters keyed like existing recordings, or a verified pre-populated
  `FASTEMBED_CACHE_DIR` cached in CI, compatible with `network_disabled()`; (3) retire the
  term-overlap path as the accepted evaluator route (unit tests may keep it); (4) add golden
  cases: relevant guideline citation, irrelevant, no-result, retrieval-unavailable, tie/rank,
  claim/citation association; (5) pin corpus version/config hashes in output; (6) two mutation
  drills (break ranking; break availability) must turn the gate red; (7) load the committed
  baseline in the recorded tier as well (`w2_runner.py:341-347`), so the PDF's "any category
  regresses by more than 5%" rule (p.5, req 6) binds at PR time, not only live-tier.
- **Verification:**

      cd agent && .venv/bin/pytest -q corpus/tests evals
      cd agent && make -C . -f agent/Makefile eval-tier1   # or: cd agent && .venv/bin/python -m evals.w2_runner run --tier recorded

- **Acceptance:** All applicable cases traverse `HybridRetriever`; guideline citations exist in
  expected and observed output; both mutation drills fail the gate; **the recorded (PR) tier
  loads the committed baseline and fails on any rubric category regressing > 5 pp or dropping
  below its floor (PDF p.5 Core Req 6 — binding at PR time, not only live-tier)**; recorded
  tier remains network-free and reproducible; `artifact_scan` passes over the regenerated
  recordings and results (metadata-only discipline preserved).
- **Effort:** 2–3 days.

### R03 — Conditional graph routing and nested worker tracing

- **Findings:** AF-P1-02; W2-REQ-04, 11, 74, 85.
- **Files:** `agent/app/orchestrator/graph.py`, `state.py`, `workers/`, `agent/app/service.py`,
  `agent/app/config.py`, `agent/tests/test_graph_skeleton.py`,
  `agent/tests/test_orchestrator_trace.py`, `agent/tests/test_w2_serving_integration.py`.
- **Implementation:** (1) derive `needs_intake`/`needs_retrieval` from validated request state;
  route to neither/either/both without executing unneeded workers; (2) deterministic merge when
  both run; (3) nest OCR/VLM/schema/write and BM25/dense/rerank sub-spans inside their worker
  span (closes the REQ-74 "Not Met"); emit route-decision spans with correlation IDs to the
  event lane, not only the Langfuse sink; (4) readiness reports graph state; fail readiness only
  where the deployment *declares* the graph required (preserve the fail-closed W1 fallback mode).
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_graph_skeleton.py tests/test_orchestrator_trace.py tests/test_w2_serving_integration.py

- **Acceptance:** Four route combinations pass with worker call-count assertions; trace tree
  asserts full parentage; deployed trace (via O01) shows the selected branch.
- **Effort:** 2 days.

### R04 — Declare authority, prove divergence detection, close typed-facade gaps (rewritten)

- **Findings:** AF-P1-03; W2-REQ-14, 18, 55, 56, 57, 79, 89.
- **Current evidence:** PostgreSQL is already the declared durable artifact authority
  (`agent/app/ingestion/artifacts.py:105`); OpenEMR holds a verified byte-digest copy; report
  and trend reads use the Postgres path. This is an authority-*documentation* and
  *enforcement-proof* task — **not** a data migration or cutover.
- **Implementation:** (1) record the authority decision (subject to AF-P2-04's answer):
  Agent PostgreSQL = authoritative for extraction artifacts/refs; OpenEMR copy = verified
  projection with digest readback; OpenEMR remains authoritative for source documents and
  written vitals; (2) add a divergence test: mutate one copy in a fixture and assert detection
  fails closed rather than silently serving either; (3) replace the actual weak `object`/open-
  string facades in `agent/app/service.py` and `agent/app/writeback/` seams with typed
  protocols/DTOs; (4) synchronize migration notes to the real inventory 001, 003–007 (006/007
  were in the audited SHA); no new migrations unless a concrete schema gap appears.
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_schemas.py tests/test_composite_refs.py tests/test_w2_runtime_migrations.py tests/test_document_readback_verification.py
      # mypy via the CI-pinned invocation in .github/workflows/agent-quality.yml:52-73 (no bare `mypy app`)

- **Acceptance:** Authority ledger documented and matching every read/write path; divergence
  test fails closed; named facades typed; migration notes accurate.
- **Effort:** 1 day.

### R05 — Wire observability to its verified root causes (rewritten)

- **Findings:** AF-P1-04; W2-REQ-13, 34, 58, 62, 63, 64, 73, 76, 89.
- **Verified root causes to fix, by site:** (1) production composition passes no event sink →
  `EventEmitter(NullEventSink())` (`agent/app/service.py:204`): wire a PHI-safe production sink;
  (2) `RETRIEVAL_COMPLETED` defined/registered but never emitted
  (`agent/app/observability/events.py:246-250`): emit at retrieval completion; (3) encounter
  summary split across zero-filling emitters (`agent/app/ingestion/telemetry.py:207`,
  `agent/app/observability/langfuse.py:486-487`): fuse or join into one per-turn record carrying
  tool sequence, step latencies, tokens, cost, retrieval hits, grounding rate, routing, and
  verification outcomes; (4) `agent/ops/alert_checker.py:481-515` evaluates the Week-1 signal
  set, never reads `agent/ops/w2_alerts.json`, and is unscheduled: point a scheduled evaluator
  (workflow cron or Railway job) at `w2_alerts.json`; (5) import/provision the dashboard
  (`agent/ops/w2_dashboard.json`) or map its ten panels onto the Langfuse UI with documented
  queries; (6) exercise the three required alerts (extraction failure rate, retrieval p95,
  eval-regression >5pp) with safe synthetic conditions and documented runbook links
  (`docs/week2/evidence/W2_RUNBOOKS.md`).
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_observability_sink.py tests/test_observability_trace.py tests/test_ingestion_observability.py ops/tests

- **Acceptance:** One correlation ID reconstructs a full production request; no protected value
  captured; all three alerts fire, deliver, and clear; panels show data for the accepted SHA.
- **Effort:** 1.5–2 days + production verification.

### R06 — Bounded retry and fallback classification for Cohere

- **Findings:** AF-P1-10; W2-REQ-60.
- **Current evidence:** 4 s timeout, 2-failure/30 s breaker, PHI screen, local fallback exist
  (`agent/corpus/retrieval.py:261-333,519-530`); no bounded retry of retryable failures.
- **Implementation:** classify retryable (timeout, connect, 429, eligible 5xx) vs permanent
  (4xx/validation); bounded attempts + jittered backoff + overall deadline; breaker updated per
  logical attempt; fallback after exhaustion/open circuit; attempt/exhaustion/fallback
  telemetry without request content. Fake-clock tests only; no live provider needed.
- **Verification:**

      cd agent && .venv/bin/pytest -q corpus/tests/test_retrieval.py corpus/tests/test_evidence_route.py

- **Acceptance:** Retry budget honored; permanent failures not retried; fallback output remains
  R01/R02-compatible; no real sleeps in tests.
- **Effort:** 1 day.

### R07 — Stabilize deployed reranker readiness (new; observed degradation)

- **Findings:** supports W2-REQ-54/75 deployment evidence and O01/O02/S01 quality; observed
  cache-busted `/ready` `active_reranker: timeout` at 2026-07-19 11:36 CDT (recurring; earlier
  cached snapshot showed the same).
- **Likely cause:** local mxbai ONNX weights lazily download on first use
  (`corpus/retrieval.py:440-445`); a cold container exceeds the 5 s probe budget
  (`agent/app/health.py:69-128`).
- **Implementation (pick smallest sufficient):** pre-bake reranker + embedding weights into the
  image (`FASTEMBED_CACHE_DIR` populated at build) and/or warm the reranker at startup; only if
  neither suffices, raise the soft-probe budget with a documented rationale. Verify Railway
  restart → all-green cache-busted `/ready` three consecutive probes.
- **Acceptance:** `/ready?cb=<unique>` consistently `ready` with `active_reranker: ok` across
  restarts; demo/profile runs recorded against an all-green state.
- **Effort:** 0.25–0.5 day.

### R08 — Land the extraction-robustness fix: cursive/handwritten forms + image intake (owner-directed; code implemented 2026-07-19)

- **Findings:** W2-REQ-91/92 (PDF p.2: "useful even if the document scan is imperfect");
  W2-REQ-97 posture preserved (unsupported facts visible, never invented). Root cause and full
  change record: `W2_DEVLOG.md` G-fix entry (2026-07-19).
- **State:** ALREADY IMPLEMENTED in the working tree and verified on the four affected suites
  (new `tests/test_vlm_evidence_gate.py` 12/12 + `tests/test_image_intake_robustness.py` 4/4;
  frozen `tests/test_vlm_provider.py` 17/17 and `tests/test_reader_geometry.py` 7/7 unchanged).
  NOT yet committed, full-suite-verified, or merged. W00's tree inventory predates these files —
  the fix adds: `agent/app/llm/vlm.py` (evidence-quality gate; per-value mercy; in-order
  subsequence row check), `agent/app/ingestion/image_reader.py` (never raises; kill-safe
  subprocess budget), further `agent/app/ingestion/reader.py` edits
  (`_run_ocr_with_timeout`, `evidence_is_trustworthy`, `token_is_wordlike`), the two new test
  files, and the `W2_DEVLOG.md` entry.
- **Implementation:** (1) isolate exactly those files on `fix/extraction-evidence-gate`
  (extends W00's separation; reader.py also carries G-D2 docstring edits — one reviewed branch
  may carry both concerns as two commits); (2) full venv suite: `cd agent && .venv/bin/pytest
  -q` ≥ the 936-passed baseline; (3) recorded 50-case gate run — golden fixtures use
  trustworthy evidence, so the expected delta is ZERO; any category movement is a stop-and-
  diagnose signal, not a threshold judgment call; (4) merge through the protected-branch flow
  once C02 phase 1 is live; (5) D01 records the behavior change (degraded-evidence extractions
  now persist as honestly-ungrounded; grounded-only vitals writes unchanged).
- **Acceptance:** suite ≥ baseline; gate green with no category regression; a cursive/photo
  upload produces a persisted, honestly-ungrounded extraction artifact and zero OpenEMR
  clinical writes from ungrounded fields (pinned by the 16 new tests + existing
  `test_medication_list.py` persistence rule).
- **Dependencies:** W00 (separation), C02 phase 1 (merge path). File-scope independent of
  R01 (`routes/chat.py`) and R02 (`evals/execution.py`).
- **Effort:** 0.25 day (verification + PR mechanics; the code exists).

### R09 — Medication-list deliverable demonstrability (third document type; owner-directed 2026-07-19)

- **Findings:** PDF p.5 Core Deliverables — "A third document type such as referral fax or
  medication list" — and Core Req 6 (behaviors proven through the golden set + boolean
  rubrics). Current state verified 2026-07-19: implementation is COMPLETE and healthy end-to-
  end (frozen schema, `extract_medication_list` VLM tool, upload validation, repository doc
  type, migration `agent/migrations/007_medication_list.sql`, routes + OpenAPI/Bruno + UI
  coverage, `tests/test_medication_list.py` 5/5, artifact-only persistence — never vitals),
  but **zero golden/eval cases reference `medication_list`** (`evals/golden/cases.json`: 0
  hits). The deliverable exists and cannot currently be demonstrated through the gate.
- **Implementation:** (1) add 2–3 golden cases: a clean medication-list extraction (rows +
  citations grounded), a degraded/ungrounded case (fields visible-and-unverified — R08
  semantics), and a refusal/missing-data case if the rubric taxonomy expects one per doc type;
  fixtures authored with the existing deterministic-generator discipline (synthetic markers,
  no clinical tripwires); (2) wire through the SAME evaluator harness — lands after R02 merges
  and rebases onto R02's recordings (never parallel regeneration, §4b); (3) S01's final pass
  includes one medication-list upload beat; (4) D01 records the grounding-only posture for
  this doc type (no completeness validator — by design) in `W2_DECISIONS.md`.
- **Acceptance:** gate green including the new cases with rubric categories unchanged; demo
  shows the third document type end-to-end; decision note recorded.
- **Dependencies:** R02 (evaluator + recordings ownership), then S01-final and D01.
- **Effort:** 0.5 day.

### C01 — Image build/start/readiness gate and bounded mypy ratchet (narrowed)

- **Findings:** AF-P1-05; W2-REQ-65 (PDF p.6 requires build + typecheck on every PR).
- **Implementation:** (1) CI job: `docker build` the agent image, start with deterministic test
  config, bounded readiness poll on `/health` + `/ready`, always collect logs; (2) mypy: keep
  the pinned strict invocation, convert the curated list into a tracked ratchet (checked-in
  covered-modules file + a test asserting the list only grows; no unowned exclusions); do not
  launch a full-package typing cleanup campaign; (3) both jobs feed C02's required checks; no
  `continue-on-error`.
- **Verification:**

      docker build -t agentforge:w2-test agent/
      # negative fixtures: one type error, one startup crash, one not-ready response must each fail CI

- **Acceptance:** Clean run green; each negative fixture red; ratchet file enforced.
- **Effort:** 1 day.

### C02 — Enforce the gates on GitHub and GitLab (two phases)

- **Findings:** AF-P0-01, AF-P1-11; W2-REQ-01, 05, 31, 36, 41, 47, 51.
- **Phase 1 (Track A, minutes–hours, admin):** GitHub branch protection/ruleset on `main`
  requiring the existing `agent-eval-gate` (quality + eval-tier1) checks; disallow force-push/
  deletion; restrict bypass. GitLab protected branch + required pipeline (`.gitlab-ci.yml`
  eval + bridge stages). Export both configurations as evidence.
- **Phase 2 (Track B, ~1 day):** add C01's checks and the Tier-2 exact-SHA requirement via the
  existing fail-closed bridge (`.github/scripts/verify_github_gate.py`,
  `agent/tests/test_github_gate_bridge.py`); stale-result and fork-secret handling documented
  (`docs/week2/W2_TIER2_CI_POLICY.md`); run one red and one green merge drill per host and
  archive run URLs (red drill branches `drill/w2-red-*` already exist as templates).
- **Acceptance:** Rule exports show required checks; red candidate cannot merge, green can, on
  both hosts; drill evidence archived in `docs/week2/evidence/W2_CI_EVIDENCE.md`.
- **Effort:** Phase 1 minutes–hours; phase 2 ≤1 day plus admin availability.

### E01 — Durable committed evaluator evidence for the accepted SHA (simplified)

- **Findings:** AF-P1-06; W2-REQ-50.
- **Implementation:** (1) commit the sanitized exact-SHA Tier-1 result and the aggregate-only
  live Tier-2 result for the accepted SHA (replacing the INCONCLUSIVE placeholder), with run
  URLs and SHA-256 digests recorded in `docs/week2/evidence/W2_CI_EVIDENCE.md`; (2) GitHub
  artifacts expire (~14 days) — repository copies are the durable record; (3) a submission check
  fails if committed evidence SHA ≠ release SHA. No signing infrastructure, no new artifact
  store.
- **Acceptance:** Fresh clone resolves a green 50-case result bound to the release SHA; PHI/
  artifact scan passes over the committed evidence.
- **Effort:** 0.5 day (E01-lite in Track A: commit current evidence now).

### REL1 — Final release, deployment, and Week-2 activation (new; previously implicit)

- **Why:** No task owned the step between "PRs merged" and "production journey": deploying the
  accepted SHA and attesting the Week-2 flags. The PDF requires the deployed app with the core
  flow *working* (p.5), which depends on `W2_DOCUMENT_RUNTIME_ENABLED=true` **and**
  `W2_GRAPH_ENABLED=1` in the deployed environment.
- **Implementation:** (1) merge the §6 train in order; (2) let `agent-deploy.yml` deploy web +
  document-worker with `DEPLOYMENT_SHA` = evaluated SHA; (3) run
  `agent/scripts/activate_w2_write_path.py` (attested var set incl. `W2_GRAPH_ENABLED=1`) and
  `agent/scripts/verify_w2_write_path.py`; (4) verify with
  `agent/scripts/verify_deployed_sha.py` and three cache-busted `/ready` probes (all-green,
  R07); (5) record deployed SHA + readiness JSON in the evidence index; (6) confirm both
  remotes hold the released SHA.
- **Acceptance:** `/health` (cache-busted) = accepted SHA; `/ready` all-green including
  `document_runtime` and reranker; graph flag attested by the activation script's recorded
  output; O01 may begin.
- **Effort:** 0.5 day including checks.

### O01 — Prove the production journey on the accepted SHA

- **Findings:** AF-P1-01; W2-REQ list per §3.
- **Implementation:** deploy the accepted SHA (existing exact-SHA `agent-deploy.yml` path) with
  graph + telemetry enabled and R07 green; SMART-launch with demo credentials and a synthetic
  patient; execute upload (lab + intake), association, extraction, OpenEMR write + readback,
  evidence retrieval, grounded answer with per-claim citations (R01), citation click + page
  preview/bbox, follow-up question, duplicate-upload idempotency, and missing-data behavior;
  capture one correlation ID end-to-end, the Langfuse trace (supervisor → worker → critic hops
  visible, closing the routing-inspectability evidence), `/health` SHA, and sanitized
  screenshots; run the Bruno `Deployed` environment collection including negative
  identity/wrong-patient cases. For REQ-96 (exactly-once), evidence = duplicate upload returns
  200 with no second write, the `readback-verification` endpoint confirms byte digests, and the
  crash-safe timeout/reconcile behavior is cited from the intent-ledger test suite — do **not**
  induce write timeouts against production OpenEMR; record that justification in the bundle.
- **Acceptance:** One sanitized evidence bundle ties identity, patient, inputs, trace,
  citations, writes/readback, idempotency, and SHA; negative cases fail safely.
- **Effort:** 1 day including access coordination.

### O02 — Four-path performance, resource, and cost report

- **Findings:** AF-P1-08; W2-REQ-06, 53, 59, 71.
- **Implementation:** run the four existing k6 modes in `agent/load/k6/w2_profiles.js`
  (retrieval, ingestion/extraction, full graph, W1 chat) against the accepted SHA with pinned
  dataset/concurrency/duration; capture request counts, errors, **p50/p95** (p99 not required
  by the PDF), throughput, CPU/memory (Railway metrics), provider usage and cost; compare
  against the W1 anchors in `docs/week2/evidence/W2_BASELINES.md:19-25`; reconcile actual dev
  spend and projected production cost with Anthropic/Cohere/Railway exports; analyze
  bottlenecks; lock the SLO ceilings (retrieval p95 ≤2 s, ingestion p95 ≤30 s) from measured
  data. Publish into `docs/week2/evidence/W2_COST_LATENCY.md` + `W2_BASELINES.md`.
- **Track A (O02-lite):** scaffold + eval-aggregate datapoint (≈$3.07, p50 5.61 s, p95 12.27 s)
  + one smoke profile, labeled "partial — not AF-P1-08 closure."
- **Acceptance:** All four paths reported with every required measure, method, and sample size;
  regressions explained; inputs + SHA preserved.
- **Effort:** 1 day.

### O03 — Backup protection and timed isolated restore

- **Findings:** AF-P1-09; W2-REQ-81, 89. Start immediately — retention/calendar wait applies.
- **Implementation:** enable/verify automatic backups for Agent PostgreSQL and OpenEMR
  MySQL + document volume with owner/schedule/retention/encryption; restore a point into an
  isolated target; run `agent/scripts/restore_drill.py` checks (schema/version, migrations
  001–007, credentials, readback digests, dedup) plus the O01 read path; measure RPO/RTO vs
  ≤24 h/≤60 m; preserve sanitized logs. Golden set needs no separate backup (repo-reproducible).
- **Acceptance:** ≥ required restore points exist; timed drill meets targets; post-restore
  verifier green; production restore prohibited by explicit target guards.
- **Effort:** 1 day execution + calendar wait.

### S01 — Record the 3–5 minute demonstration (Track A first pass)

- **Findings:** AF-P1-07; W2-REQ-06, 13, 52.
- **Implementation:** follow `docs/week2/evidence/W2_DEMO_SCRIPT.md`; show exactly the PDF p.5
  elements — document upload, extraction, evidence retrieval, citations (the citations beat
  must show click-to-source: an authenticated click opening the correct page with its visible
  bounding-box overlay, W2-REQ-29 — the PDF makes the overlay REQUIRED, p.5 Core Req 5), eval
  results, observability — on the deployed app (R07 green first), with the deployed SHA and a
  correlation ID visible; synthetic data only; frame/transcript PHI scan before publishing;
  stable shareable link added to README + evidence index. Track A records against the current
  SHA today; re-record only if the accepted SHA changes the demonstrated behavior.
- **Acceptance:** 3–5 minutes, all six elements, reviewer-accessible link, scan clean, SHA
  stated.
- **Effort:** 0.5 day.

### D01 — Documentation and status synchronization (new)

- **Findings:** closure hygiene for W2-REQ-48, 67, 90; both reviews found no owner for this.
- **Implementation:** after each closing task merges — and finally at verdict flip — update:
  root `W2_ARCHITECTURE.md` status labels and §12 table; `docs/week2/W2_gap-audit.md` finding
  statuses, RTM rows, and verdict; `docs/week2/evidence/` index; README setup/env deltas; keep
  the two-tier gate and known-gap wording accurate. Grader-runnability items (PDF p.3 "no
  guessing"): README gains a short "Grader quickstart" section stating the branch (`main`), the
  hook installer (`make -C agent hooks`), the demo video link, the deployed URLs, demo
  credentials pointer, and the cache-busting note for `/health`/`/ready` probes. Also refresh
  `docs/week2/W2_DEFENSE_PREP.md` and `W2_USERS.md` so the capability→Week-1-user mapping (PDF
  p.4 Stage 5) reflects the final system. Verdict may read **Ready** only when §8 is fully
  checked and V01 has signed off.
- **Acceptance:** No stale "open" finding that evidence closes; no "Ready" claim without
  evidence links; docs match the release SHA.
- **Effort:** 0.5 day cumulative.

### V01 — Independent final verification and verdict authority (new)

- **Why:** Every prior audit cycle in this project found real gaps that the implementing agents
  had marked closed. The verdict flip must not be self-graded.
- **Implementation:** a fresh session/agent (cold eyes, read-only except the gap-audit verdict
  block and evidence index) performs, against the release SHA: (1) fresh clone from the GitLab
  mirror; (2) offline grader simulation — install, `make -C agent hooks`, run the recorded
  50-case gate and full pytest suite without live keys; (3) re-score every RTM row and §8
  checkbox against its linked evidence; (4) live probes — cache-busted `/health`/`/ready`,
  `/openapi.json`, evidence search, and spot-check the O01 bundle (trace resolves from its
  correlation ID alone); (5) verify the video link, cost report, backup drill record, and CI
  red/green drill URLs resolve; (6) confirm no unreviewed diff rode the release (tree clean,
  G-branch separate); (7) only then update the gap-audit verdict to **Ready** with a dated
  sign-off entry.
- **Acceptance:** A signed verification entry listing what was re-executed, what was inspected,
  and zero open P0/P1; any failure reopens the owning task instead of being waived.
- **Effort:** 0.5 day.

## 6. Pull-request sequence

| PR | Contents | Findings |
|---|---|---|
| 0 | W00 doc-set commit; G-D2 code isolated to `feat/g-d2-reader` (own reviewed PR, not in this train) | push readiness |
| 0b | R08 extraction-evidence-gate fix (`vlm.py`, `reader.py`, `image_reader.py` + 16 tests; full suite + recorded gate rerun; may share the reader review branch with G-D2 as two commits) | W2-REQ-91/92 (owner-directed) |
| 1 | R01 claim-level response contract + OpenAPI/Bruno/tests | AF-P0-03 |
| 2 | R02 production retrieval in evaluator + guideline cases + offline adapters + mutation drills | AF-P0-02 |
| 2b | R09 medication-list golden cases + S01 demo beat (rebases onto PR 2's recordings) | Third-document-type deliverable, PDF p.5 (owner-directed) |
| 3 | R03 conditional routing + nested trace tests | AF-P1-02 |
| 4 | R04 authority declaration, divergence test, typed facades, migration-note sync | AF-P1-03 |
| 5 | R05 sink wiring, event emission, encounter fusion, scheduled `w2_alerts` evaluation, alert drills | AF-P1-04 |
| 6 | R06 Cohere bounded retry + telemetry | AF-P1-10 |
| 7 | R07 reranker weight pre-bake/warmup | deployment evidence prerequisite |
| 8 | C01 image smoke + mypy ratchet | AF-P1-05 |
| 9 | C02 workflow/bridge changes (host configuration applied by admin alongside) | AF-P0-01, AF-P1-11 |
| 10 | E01 committed evidence + submission check | AF-P1-06 |
| 11 | D01 documentation/status sync (may ride with each PR; final pass at verdict) | closure |

After PR 10, REL1 deploys and activates the accepted SHA; O01, O02, O03, S01 are
evidence-producing operations against it. G-backlog work
(`W2_BACKLOG_CHANGE_REQUEST_G.md`) and the isolated `feat/g-d2-reader` PR must not enter this
train before the P0s merge; any G recordings regeneration rebases onto PR 2's.

## 7. Risks and controls

| Risk | Control |
|---|---|
| Concurrent doc/plan edits by parallel sessions (observed twice on 2026-07-19) | Single-writer per file; re-read before write; PRs contain only their own diff. |
| R01 client breakage | Derived compatibility fields + contract tests. |
| R02 nondeterminism/network in Tier 1 | Recorded adapters or pre-populated model cache; `network_disabled()` stays; pinned hashes. |
| R04 over-reach into data migration | Scope locked to declaration/divergence/typing; migrations only for proven schema gaps. |
| R05 PHI leakage/alert noise | Existing masks + redaction tests; bounded labels; synthetic alert drills. |
| R07 misdiagnosis (probe budget vs cold load) | Verify weights presence in image first; only then adjust budget with rationale. |
| C02 merge outage / secret exposure | Temporary-branch rule testing; least-privilege; documented emergency role; Tier-2 policy (`W2_TIER2_CI_POLICY.md`). |
| O01 wrong-patient write | Dedicated synthetic patient + pinned-session preflight asserts. |
| O02 load on shared services | Approved concurrency, stop conditions, test tenant. |
| O03 restore into production | Hard-coded isolated targets in `restore_drill.py`; disposal after evidence. |
| S01 sensitive capture | Synthetic-only, frame/transcript scan, access check before publish. |
| Deadline overrun | Track A ships regardless; gap audit publishes remaining opens honestly (verdict stays Not Ready until §8 completes). |

## 8. Closure checklist

### Track A (submission salvage — may all complete today)

- [x] W00: doc set committed; G-D2 code edits isolated; local tool state excluded.
  *(2026-07-19: commits `6301e2f`+`a9e5b75` on main; G-D2+R08 on `feat/g-d2-reader`;
  evidence: W2_EVIDENCE_INDEX.md §W00.)*
- [x] GitLab mirror at HEAD; final docs pushed to both remotes. *(Both remotes verified
  equal after every docs push — `git ls-remote` records in W2_EVIDENCE_INDEX.md.)*
- [ ] C02 phase-1 protection enabled on GitHub and GitLab (config exports archived).
  *(OWNER: ready-to-apply runbook + ruleset JSON at docs/week2/evidence/c02/.)*
- [ ] R07: cache-busted `/ready` all-green, three consecutive probes. *(Code merged-ready
  on PR #25 with offline 3× probe proof; production probes land post-deploy.)*
- [ ] S01 first-pass video recorded, scanned, linked (six PDF elements). *(OWNER records:
  all six elements verified working live-authenticated 2026-07-19 — kit + 8 dry-run
  screenshots at docs/week2/evidence/W2_S01_RECORDING_KIT.md.)*
- [x] E01-lite: current sanitized eval evidence + run URLs + digests committed.
  *(2026-07-19: `7bbf079` — exact-SHA Tier-1 + live Tier-2 copies + digests,
  W2_CI_EVIDENCE.md §E01-lite.)*
- [x] O02-lite partial report committed, labeled not-closure. *(2026-07-19: `36ec240` —
  W2_COST_LATENCY.md §O02-lite incl. deployed retrieval probe p50 4.94 s / p95 6.49 s.)*
- [x] D01-lite: dated known-gaps banner in the gap audit; verdict remains Not Ready.
  *(2026-07-19: `72f9792`.)*

### P0

- [ ] AF-P0-01: required, unbypassable gates on both hosts; red candidate blocked, green merges
  (drill URLs archived).
- [ ] AF-P0-02: complete evaluator traverses `HybridRetriever`; guideline/irrelevant/unavailable
  cases present; both mutation drills red; PR-tier baseline delta rule (>5 pp category
  regression fails) enforced per PDF p.5 Core Req 6.
- [ ] AF-P0-03: every externally returned clinical claim owns its CitationV2 set in JSON, SSE,
  and fallback UI; uncited output fails closed.

### P1

- [ ] AF-P1-01 (O01) · [ ] AF-P1-02 (R03) · [ ] AF-P1-03 (R04) · [ ] AF-P1-04 (R05) ·
  [ ] AF-P1-05 (C01) · [ ] AF-P1-06 (E01) · [ ] AF-P1-07 (S01 final) · [ ] AF-P1-08 (O02) ·
  [ ] AF-P1-09 (O03) · [ ] AF-P1-10 (R06) · [ ] AF-P1-11 (C02 GitLab evidence)

### P2 and docs

- [ ] AF-P2-01…06: six grader answers recorded; consequent work mapped and completed.
- [ ] D01: architecture, gap audit, README, and evidence index match the release SHA.

### Owner-directed additions (2026-07-19)

- [ ] R08: extraction-robustness fix isolated in its own PR; full venv suite ≥ 936-passed
  baseline; recorded 50-case gate green with zero category delta; merged through the protected
  flow; behavior change recorded by D01.
- [ ] R09: ≥ 2 medication-list golden cases green in the gate; S01 final includes a
  medication-list upload beat; grounding-only posture recorded in `W2_DECISIONS.md`.

### Final verdict rule

- [ ] Every box above links to repository, CI, production, or grader evidence.
- [ ] No P0 or P1 remains open.
- [ ] V01's independent verification entry is recorded (fresh clone, offline gate, RTM
  re-score, live probes, evidence spot-checks all pass).
- [ ] Only then does `docs/week2/W2_gap-audit.md` flip to **Ready** — signed by V01, not by an
  implementing agent.

## 9. Change request G (relocated)

The non-audit document-understanding program (tables, figures/graphs, image intake; G01–G06,
decisions G-D1/G-D2, PyMuPDF appendix) now lives in
`docs/week2/W2_BACKLOG_CHANGE_REQUEST_G.md`. It is deferred behind the submission tracks above;
its two factual errors (951/2 test baseline; `evals.runner` named as the complete evaluator)
were corrected on relocation. G-D2 remains recorded in `docs/week2/W2_DECISIONS.md:450`.
