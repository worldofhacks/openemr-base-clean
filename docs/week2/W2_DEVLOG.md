# W2_DEVLOG — Week 2 (Multimodal Evidence Agent)

> Append-only chronological record for Week 2. Week 1's log is frozen at
> `docs/week1/DEVLOG.md`. Convention: every entry carries What / Why / Result / Stage.

## [2026-07-13] W2 kickoff — assignment received, defense prep built · type: milestone
- What: Week 2 PRD (Multimodal Evidence Agent) received ~4h before the Architecture
  Defense. Deep-read + presearch produced `docs/week2/W2_DEFENSE_PREP.md`: hard gates
  (graded CI regression injection; required PDF bounding-box overlay; prescribed
  citation shape), the two defense vulnerabilities (W1 read-only boundary vs required
  writes; D6 no-framework vs required orchestration framework), a W2-D1..D7 decision
  slate, W1 debt list, and a grill bank.
- Why: defend from a written position, not improvisation; W1 discipline carried over.
- Result: defense package on main before the defense.
- Stage: PRE-DEFENSE.

## [2026-07-13] Week-scoped convention locked · type: decision
- What: owner ruled all W2 artifacts are NEW files (W2_ prefix, W2-D#/W2-R# numbering);
  Week 1 documents are frozen history, never edited; DEVLOG and PROJECT_STORY are also
  week-scoped (this file starts fresh; W1's ends). Repo reorganized: `docs/week1/`
  holds frozen W1 planning/defense/demo/reviews/prompts/diagrams + DEVLOG + STORY +
  COST_ANALYSIS; `docs/week2/` holds all W2 docs; graded deliverables stay at repo root.
- Why: no confusion between graded weeks; W1 submission paths preserved at root.
- Result: this layout; W1 cross-references inside frozen docs accepted as stale history.
- Stage: PRE-DEFENSE.

## [2026-07-13] Pre-W2 repo cleanup — clean slate on main · type: milestone
- What: deleted 12 stale Codex worktrees (2 skeletons left for host-side Finder delete),
  19 stale local branches, Playwright CLI logs, 2 unreferenced root PNGs. Preserved the
  two unmerged docs-only branches by merging them (FINAL_BACKLOG.md,
  CODEX_GAP_REVIEW.md). Committed the untracked W1 defense/demo docs.
- Why: owner directive: zero tech debt entering W2; nothing functional removed.
- Result: single branch (main), single worktree, clean status. Host-side residuals:
  push to origin+gitlab, delete 2 skeleton folders, prune remote branches.
- Stage: PRE-DEFENSE.

## [2026-07-13] /arch-finalize complete — binding W2_ARCHITECTURE.md at root · type: milestone
- What: cold-eyes finalize (53-agent adversarial workflow: 7 dimension auditors +
  independent PRD coverage re-derivation, refuter-verified) over the v2 draft. 3
  critical (all in the eval gate: unbuildable thresholds, stub blindness, judge
  contradiction), 31 important, 29 minor, 1 refuted. Coverage: 99 PRD requirements,
  94 covered, 5 out-of-scope with PRD-sanctioned citations, 0 uncovered.
- Why: the draft was authored in this workstream; the binding contract required an
  unattached audit before /tasks-gen.
- Result: root W2_ARCHITECTURE.md (15 §-anchors, ~500-word summary, every capability
  cited to W2-D#/R#/F# + W1 refs); W2_gap-audit.md findings register; dated revisions
  W2-D1/D4/D7, W2-O1 resolved; W2-R6 added (PyMuPDF is AGPL — pypdfium2 default).
  Owner gates decided: two-tier gate w/ live-Anthropic Tier 2 (W2-D8), Cohere behind
  a RERANKER seam w/ Monday-EOD key trigger + mxbai fallback, front-desk actor
  demoted to narrative-only, durable Postgres job rows w/ delegated-token write
  principal (pulls W1 token-persistence debt into MVP).
- Stage: PLANNING COMPLETE → /tasks-gen next. Owner actions dated in the binding
  doc tail: Cohere production key in Railway (Mon EOD trigger), ANTHROPIC_API_KEY
  into GH Actions secrets (Tier 2), push main to origin + GitLab.

## [2026-07-13] W2 presearch + owner decision conversation · type: milestone
- What: `docs/week2/W2_PRESEARCH.md` completed against the 16-section Pre-Search
  Checklist; owner decisions recorded: LangGraph (W2-D2), Cohere rerank under a
  PHI-free-query contract (W2-D4), local Tesseract OCR for grounding + bboxes (W2-D3),
  production-grade default posture, strict core-first scope, machine-authored
  provenance on derived writes (W2-D1). Research topics W2-R1..R5 spawned.
- Why: same before-code process as W1; the conversation is the checklist's required
  reference artifact.
- Result: presearch committed pre-defense; W2_RESEARCH.md queued next.
- Stage: PRE-DEFENSE.

## [2026-07-13] W2-F1 live verification + post-verification consistency pass · type: milestone
- What: independent live verification of W2-F1 (local live stack; production read-only):
  verdict **CONFIRMED** — route-level 404s on FHIR POSTs even with maximal write scopes.
  New findings W2-F7..F11; W2-F4 **resolved** with the verified minimum scope set and a
  hard constraint: no supported persisted-scope edit path exists → **replacement SMART
  client** required at MVP (registered scope still is not an effective ceiling, W2-F12).
  Contract corrections: upload returns 200 `true` with no id
  (id via collection GET by content hash); byte-exact read-back is the FHIR
  DocumentReference→Binary projection (standard download 500s — ~~known CSRF-key defect~~
  **superseded 2026-07-13: raw bytes passed as filename**);
  vitals proven end-to-end through FHIR Observation reads. Binding doc gained an
  owner-approved "Verification errata" block + the Cohere-trigger date fix (Monday
  2026-07-13; 07-14 is Tuesday). Consistency pass then aligned the implementation plan
  (W2-OA3 → replacement-client task; W2-M2 marked verified-by-audit; M8/M11/M16 carry
  the id-discovery + FHIR read-back contracts), W2_RESEARCH R5 (verified-live pointer),
  W2_gap-audit (dated write-path note), and W2_DEFENSE_PREP (live-evidence addendum).
  One pre-authorized decision-level addition only: `writeback.skipped(unit_mismatch)`
  added to §6a + dated W2-D1 note (never convert units — a converted number is a
  derived value not on the page).
- Why: the tasks-gen plan (f55f046) predated part of the verification; docs must agree
  exactly with probed reality before build starts.
- Result: plan/research/gap-audit/defense/devlog aligned; **the leftover "W2-F1
  Verification Local" API client was found still ENABLED in the local dev stack and was
  disabled** (`is_enabled=0` confirmed) — the W1 E9 duplicate-launcher lesson applied;
  it was local-only, password-grant, never production.
- Stage: PLANNING COMPLETE → Wave 0 build next.

## [2026-07-13] Adversarial audit review integrated — W2-F12..F23, W2-D9 · type: milestone
- What: a separate read-only agent adversarially re-audited the whole write/upload surface
  (static analysis; `W2_AUDIT_REVIEW_RAW.md`). Result: W2-F1..F11 → 7 CONFIRMED, 4
  IMPRECISE, 0 false-positive; 12 new findings W2-F12..F23; 183 routes checked, 0 active
  module route listeners. Spot-verified 4 citations against code before integrating
  (ScopeEntity.php:156-161 legacy-scope escalation; EncounterService.php:580-595 caller
  pid/eid stamped, no user/author; :657-676 range validator absent from REST;
  ApiResponseLoggerListener.php:83-85 response logged into both columns). Distilled into
  W2_AUDIT.md (new findings + imprecise corrections + retired gate verdict) and recorded
  as ADR **W2-D9**; threaded the mandatory controls into W2_IMPLEMENTATION_PLAN.md
  (integration table + W2-OA3/M8/M11/W2-1).
- Why: the tasks-gen plan predated this review; several new HIGH findings show the OpenEMR
  write surface enforces no patient/encounter ownership, scope ceiling, category ACL,
  vital range, attribution, or idempotency on create — so those become mandatory
  agent-side controls, not defense-in-depth.
- Result: owner's two load-bearing calls recorded in W2-D9 — (1) the W2-D1 transport
  survives (no client-supplied FHIR CRUD; standard documents/vitals APIs stand); (2) the
  "no finding blocks the architecture" verdict is retired, and the blocking controls
  (W2-F12..F21) gate the write path. Precision fixes adopted: missing scope → 403 not 401
  (W2-F4); download 500 is raw-bytes-as-filename, not a CSRF defect (W2-F9); `api_log`
  logs the response, so inbound PDF/vital bodies are NOT logged (W2-F20, earlier leak
  hypothesis FALSE). Docs-only; no code, no system changes this pass.
- Stage: PLANNING — plan hardened pre-build.

## [2026-07-13] Post-review feasibility remediation locked — W2-D9/W2-D10 · type: decision
- What: a second adversarial pass challenged whether the documented controls were
  buildable and whether coverage prose had outrun implementation evidence. The owner
  kept the standard-REST transport and locked W2-D10: Final includes the source document,
  grounded extraction artifact, and eligible grounded intake vitals under one contained
  exactly-once contract. The following 20 closures are now required:
  1. PHI detection excludes canonical synthetic inputs and scans generated logs, traces,
     reports, recordings, results, and other outputs; a known-leak fixture must turn red.
  2. `schema_valid`, `citation_present`, `safe_refusal`, and `no_phi_in_logs` require
     100%; factual consistency retains its threshold and >5-point delta, with denominator
     arithmetic emitted and drills flipping enough applicable cases to cross it.
  3. Freeze the schemas: GroundedField-owned citation, complete FailureReason,
     result-level lab collection date, typed retrieval/job/write-intent/worker-lease/log
     models, and grounded intake vitals. The canonical vitals set is bps, bpd, weight,
     height, temperature, pulse, respiration, oxygen_saturation, plus measurement_date;
     each owns on-page value/unit/citation/bbox. Provenance note is generated, not extracted.
  4. The document API remains path-based: separately fixed source/artifact paths resolve
     to the provisioned expected category ID and ACL before POST; ambiguity fails closed.
  5. Source/artifact/vital creates use intents `{pending, unknown, complete}`, a remote
     marker and payload fingerprint; possible commit → unknown → reconcile, never blind retry.
  6. Permanent dedup/lineage key is patient-safe
     `(patient_id, document_id_or_content_hash, leg, version, field_id)`; 30-day attempts are
     separate; failed-job requeue is atomic.
  7. The exact replacement-client scope payload lives in W2_AUDIT.md; missing or extra
     grants refuse, and old access plus refresh tokens are retired at cutover.
  8. Golden negatives are named for W2-F12/F13/F14/F15/F16/F17/F19.
  9. Current-fact corrections are binding: missing scope is 403; download 500 is raw
     bytes passed as filename; disable-only is not revocation; numeric SLOs lock at Early.
  10. W2_USERS actor/auth language and W2-D8/D9/D10 traceability are reviewed together.
  11. Durable jobs use transactional claim, lease/heartbeat, bounded backoff,
      stale-lease recovery, explicit worker topology, graceful shutdown, and queue-age metrics.
  12. Jobs use a separate encrypted patient/principal-bound delegated credential whose
      refresh lifecycle is independent of interactive idle expiry.
  13. One E2E test reconstructs upload, queue, workers, provider calls, every EHR
      write, and every readback from one correlation ID.
  14. Agent Postgres joins backup/restore; source custody is explicit; job/dedup/ledger
      rows are PHI and inherit retention, access, encryption, and recovery obligations.
  15. One typed W1-compatible log envelope has an owner; migrations 002/003 are ordered,
      forward-safe, secret-safe, and have clean-upgrade plus rollback/recovery evidence.
  16. W2-F20 is an admin/config gate: record deployed non-DEBUG evidence and fail closed
      before Binary readback if the log level is unknown or DEBUG.
  17. Tier 2 gets a real cost/quota/runtime spike for roughly 50 ×
      (VLM + answer + judge), plus a safe fork-secret policy; untrusted fork code never
      receives repository secrets.
  18. Wave 0 concurrently loads bge-small, local reranker, and one OCR page and enforces
      the Railway RSS/cold-start ceiling before that fallback stack may ship.
  19. Only the PRD-sanctioned stretch tier is cut. Core, engineering requirements,
      D9/D10 containment, all three writes, both gate tiers, and GitLab submission-host
      enforcement (Tier 1 plus a same-SHA fail-closed bridge to the live Tier 2) are
      uncuttable through Final.
  20. `/health` remains process liveness; `/ready` includes worker heartbeat and oldest
      queue age, and a soft dependency is verified as 200 + degraded rather than outage.
- Why: the earlier plan incorrectly treated a local ledger transaction as remote
  exactly-once, described direct category-ID input the API does not accept, conflated
  interactive session expiry with background refresh, under-specified queue ownership,
  misclassified patient-linked Postgres rows as PHI-free, under-scoped backup/readiness,
  and could make its PHI scanner fail on canonical fixtures. Gate arithmetic and live-call
  cost were also overstated.
- Result: `W2_gap-audit.md` now treats every non-stretch PRD/core/engineering requirement
  as **scheduled with concrete Final evidence**, not already proven. Its 20-item table
  carries a STOP condition for each closure. W2-D9 remains the mandatory containment
  gate; W2-D10 owns the full three-leg write protocol. Historical 401/CSRF/disable-only,
  one-case-factual-flip, direct-category-ID, local-transaction-exactly-once, and
  PHI-free-job-row claims are superseded and may not be presented as current facts.
- STOP: do not enable a write if any D9/D10 precondition is unknown; do not ship a gate
  whose leak fixture or threshold-crossing drill stays green; do not cut a non-stretch
  requirement; do not report readiness with a stale worker or unknown F20 configuration.
- Stage: PLANNING REMEDIATED — implementation evidence required by Final 2026-07-19.

## [2026-07-15] Post-audit closeout reconciliation — two gap audits + Codex plan · type: milestone
- What: two independent gap audits (Claude + Codex) ran against canonical `4f644d9` (GitHub +
  GitLab `main` and `swarm/w2-wave0` in sync). Verdict: the deployed
  upload→extract→ground→write/readback→cite→answer pipeline is substantially built and live for
  both document types, but is **not yet a rubric-safe MVP**. Decisive blocker: the graded eval
  gate (§7) does not execute the 50 golden cases through the agent — CI runs the retired W1
  10-case `evals.runner`; there is no committed `w2_baseline.json`, no >5pp delta, no
  recorded/live executor; and 5 golden cases conflict with the scorer contract. Two answer-path
  contracts are incomplete: the answer model is not fed the reranked snippets (`composer.py`), and
  the HTTP boundary still permits legacy `str` citations (`chat.py`). Missing submission/
  engineering evidence: W2 cost/latency report, full OpenAPI + contract tests, full-flow Bruno,
  W1-vs-W2 baselines, backup/restore drill.
- Why: prove the hard gate the PRD says graders actively test (introduce a regression → CI must go
  red and block merge + deploy), close the two grounding/citation contracts, and produce the
  remaining graded evidence before Early (2026-07-16) and Final (2026-07-19).
- Result: additive, dated reconciliation across the binding docs — **no prior language rewritten**.
  `W2_IMPLEMENTATION_PLAN.md` gained the **MVP-to-Final closeout overlay** (lanes **W2-C1..C13**,
  per-lane branches/acceptance, MVP acceptance gate); `W2_DECISIONS.md` gained **W2-D11..D21** +
  open item **W2-O4** (W2-D1..D10 untouched); `W2_ARCHITECTURE.md` gained the **2026-07-15 closeout
  revision** (§2/§2a/§3/§4a/§6/§6a/§7/§8a: answer grounding top-5, CitationV2-only boundary,
  two-tier gate execution, observability/one-ID correlation, readiness hard/soft, SLO locking,
  CI/CD + exact-SHA deploy, backup RPO/RTO). Build model changed: Codex joins as independent
  auditor + second implementer under isolated worktrees with a lead integrator; `.github/` and
  golden cases 41–50 are in scope. The critic, `medication_list`, and lab-trend widget are
  reinstated from the Cut § as Milestone-2 conservative-final scope (W2-C11/C12/C13).
- STOP: the gate is not "done" until an introduced regression turns CI red and blocks merge +
  deploy; never derive eval observations from golden expectations; no PHI/secrets in CI artifacts;
  no OpenEMR PHP/schema edits or SMART-scope widening; no native lab FHIR Observation write
  (byte-attested artifacts only). Tier-2 `ANTHROPIC_API_KEY`, `RAILWAY_TOKEN`, the GitLab
  status/mirror credential, and Railway backups are blocking owner actions (W2-O4).
- Stage: PLANNING RECONCILED — closeout implementation (W2-C1..C13) pending against Early/Final.
- 2026-07-19 — **G-D2: PyMuPDF/AGPL ban removed (owner decision).** Traced the ban to the
  2026-07-13 /arch-finalize pass (W2_RESEARCH.md W2-R6) and verified the AgentForge Week 2
  PDF imposes no dependency-license requirement. Removed the ban from gates.md, the AC-5
  test (renamed `test_reader_deps_declare_license_metadata` — now asserts license-metadata
  completeness for the per-PR dep audit), pyproject comments, reader/ingestion docstrings,
  and TICKETS.md hard rules. W2-R6 library selection unchanged; plan §9 (G-tasks:
  tables/figures/image intake) unaffected in substance — G-D1's "PyMuPDF not adopted"
  rationale is now purely capability-based. Full record: W2_DECISIONS.md G-D2.
- 2026-07-19 — **G-fix: cursive/handwritten forms and PNG uploads no longer fail extraction.**
  Root cause: `vlm.py` source-completeness validators compared VLM output against OCR
  evidence that could not read the document (Tesseract cannot read handwriting; photo
  PNGs OCR to garbage) and raised `VlmResponseRejected` on the resulting mismatch —
  rejecting VALID extractions of exactly the imperfect documents the W2 PDF requires
  (W2-REQ-91). Pattern informed by the reference implementation analyzed today
  (VLM-first extraction, evidence used to verify only where it could actually read).
  Changes: (1) document-level evidence-quality gate (`reader.evidence_is_trustworthy`,
  W2-D3 heuristics) — garbage/unreadable evidence skips the veto entirely; (2) per-value
  mercy — printed-label/garbled-value rows and digit-free vital evidence no longer veto
  (`token_is_wordlike`); (3) lab row check is now an in-order subsequence match: VLM may
  report rows degraded OCR could not parse, may never drop or reorder OCR-readable rows;
  (4) `image_reader.py` hardened — undecodable bytes → typed unreadable page (never
  raises), OCR runs under the shared kill-safe subprocess budget
  (`reader._run_ocr_with_timeout`, dead-child fast-path) — parity with the PDF path.
  Anti-invention posture unchanged on trustworthy evidence (clean conflicting values,
  dropped rows, reordering, vital rescale/omission all still reject; grounding still
  decides visibility per W2-REQ-97). New frozen tests: `tests/test_vlm_evidence_gate.py`
  (12), `tests/test_image_intake_robustness.py` (4). Verified green alongside the
  untouched contracts: `test_vlm_provider.py` 17/17, `test_reader_geometry.py` 7/7
  (AC-4 kill path exercises the refactor; real Tesseract). Full-suite run in the repo
  venv (`cd agent && .venv/bin/pytest -q`, baseline 936+5) still owed before merge.

## [2026-07-19] R08 verification — full suite + recorded gate green · type: milestone
- What: the owed full-suite + recorded-gate verification of the G-fix (plan task R08, PR 0b),
  on `feat/g-d2-reader` rebased onto main @ `93ab760`. First run: 951 passed / 1 failed —
  `test_documents_b2.py::test_intake_image_reader_emits_canonical_ocr_boxes` defined its fake
  OCR runner as a test-local closure, which cannot pickle into the spawned OCR child the
  G-fix introduced for image intake (G-D3; the new robustness tests already use module-level
  fakes for exactly this reason). Fix: hoisted the fake to module level
  (`_intake_box_ocr_runner`) — every assertion byte-identical; the pinned box contract is
  unchanged. Second run: **952 passed / 5 skipped** (baseline 936+5 plus the 16 new frozen
  tests). Recorded 50-case gate (`make eval-tier1`): **gate=PASS, zero category delta** —
  schema 50/50, citation 50/50, factual 23/23, safe_refusal 10/10, no_phi 50/50;
  artifact-scan PASS (scanned=2, failing=0). Generated `evals/results-tier1.json` timing
  jitter reverted (E01 owns evidence commits).
- Why: plan §4d.3 — full suite ≥ baseline and the recorded gate must be green before any PR;
  R08 acceptance demands zero rubric-category movement.
- Result: R08 meets its acceptance; PR 0b ready for review pending C02 phase-1 protection.
- Stage: REMEDIATION — PR 0b verified.

## [2026-07-19] Remediation day — full code train delivered as PRs 24–33 · type: milestone
- What: executed W2_IMPLEMENTATION_PLAN.md end-to-end with parallel worktree sub-agents
  under §4d discipline. W00 tree hygiene (doc set on main; G-D2+R08 isolated); Track A
  salvage (C02-p1 runbooks, A01 draft, D01-lite banner, E01-lite exact-SHA evidence,
  O02-lite partial datapoints incl. deployed retrieval probe p50 4.94 s/p95 6.49 s,
  S01 dry run verifying all six PDF demo elements live incl. the bbox click-to-source
  overlay); then R08/R07/R01/R06/R04/C01/R03/R02/R05/R09 — every lane RED-first,
  full-suite ≥ baseline, recorded gate zero-delta, independently re-verified before
  push. Two golden-set changes (R02: 2 swaps, R09: 3 swaps) byte-equivalence-justified
  and flagged for V01. Per-lane evidence: docs/week2/evidence/W2_EVIDENCE_INDEX.md.
- Why: close every P0/P1 finding with exact-SHA evidence per the audit RTM.
- Result: ten green PRs; C02 phase-1 protection applied on both hosts (GitHub ruleset
  19180393, five required checks, bypass=never; GitLab main Maintainers-only +
  pipeline-required merges — found already protected at Maintainers, momentarily
  stricter, restored to preserve the mirror-push flow); merge train begun (#24 merged
  43605c2 through the protected flow). Owner resolved the six P2s (A01-RES).
- Stage: REMEDIATION — merge train in progress; REL1 + evidence ops next; Tier-2 mint,
  S01 recording, and V01 remain handed back.

## [2026-07-19] Tier-2 mint unblocked — stale baseline + guideline-lane root cause (G-D6) · type: incident+decision
- What: the owner-authorized Tier-2 mint dispatch at `293f18b` failed closed twice in
  ~1.4 s with aggregate-only `gate=FAIL error=ValueError`. Root-caused in three layers:
  (1) `w2_baseline.json` was stale for the manifest (bound to `c847a544…` minted at
  `6059703`; manifest changed to `74726474…` by 279d663/9d7980c without a live
  re-mint — the designed W2-O4 fail-closed posture); (2) on the first full local live
  run (50/50 executed, $2.68, 13 min) `citation_present` failed 49/50 on
  `lab-multi-lipid-panel`: the `submit_claims` guideline resolver invalidated all
  three valid chunk selections because the model narrated a schema-legal `text`
  member; (3) with resolution fixed, the model consistently omitted the top reranked
  chunk that AF-P0-02 pins as always-rendered. Diagnosed via 3× deterministic
  `diagnose-live` fails, then an instrumented in-process harness capturing the raw
  `submit_claims` payload. COHERE_API_KEY was ruled out (the tier2 job pins
  `RERANKER: local`; the crash predates any provider call).
- Why: each failed dispatch was undiagnosable from CI logs (exception class only) —
  fixed by adding a sanitized, length-capped `detail="…"` to the runner's stderr
  failure lines (artifact stays class-only; `artifact_scan` enforces that).
- Result: owner decision G-D6 (anchor-plus-selection; Option B weaken-the-eval
  rejected) recorded in W2_DECISIONS.md; resolver `text` tolerance (guideline lane
  only); composer anchors the top canonical snippet whenever the guideline lane
  renders; frozen composer test updated with rationale; failing case verified
  PASS 2/2 in-harness live; full offline suite 1166 passed / 5 skipped. Workflow
  tier2 env now consumes the provisioned `COHERE_API_KEY` secret (inert while
  `RERANKER: local`).
- Stage: REMEDIATION — full 50-case live re-run at branch head, baseline re-mint,
  PR through protected checks, exact-SHA redeploy of both services, then the single
  authorized `agent-eval-gate` dispatch at the new release SHA.

## [2026-07-19] Langfuse spans carry real recorded timestamps · type: fix (owner cycle 2)
- What: Langfuse's native Trace/Observation percentile widgets showed ~0/negative
  durations for the graph-turn family. Cause: both exporters replay the span tree
  post-hoc at turn end; the v4 SDK stamps every span's START at creation (emission
  time) and offers no public start_time parameter (only `create_event` takes a
  timestamp), while the exporters applied REAL recorded ends — so graph spans got
  start > end. Fix at the exporter: `_backdate_span_start` corrects the underlying
  OTel span's start attribute while recording (guarded; on any SDK-layout change the
  span keeps its emission-time start — exports never fail). Graph path backdates
  root/decision/hop/sub spans to their recorded `time.time_ns()` captures; the flat
  W1 path lays ordered steps out sequentially from the real request-boundary anchor
  (`utc_timestamp`) so durations stay exactly latency_ms with real absolute times
  (fallback: end at emission when the anchor is unparseable). Timestamps only — the
  D16 refs-only/masked content posture is untouched.
- Why: grader feedback requires latency visibly tracked in Langfuse's native
  percentile widgets; metadata.latency_ms alone cannot power them.
- Result: RED-first tests (`test_graph_spans_carry_exact_recorded_timestamps`,
  `test_sink_lays_out_steps_from_the_recorded_request_anchor`) with fakes extended
  to model the SDK's OTel span; full venv suite 1173 passed / 5 skipped (baseline
  1166); recorded 50-case gate PASS zero-delta; ruff + mypy-ratchet clean.
- Stage: REMEDIATION — supersedes 89a2b86 as release candidate (its dispatch mint
  DID go green end-to-end: run 29711288074, eval-tier2-live success — chain proven).
  Next: PR, merge, exact-SHA redeploy of both services, single authorized mint at
  the new merge SHA, synthetic-turn Langfuse verification.

## [2026-07-19] FINAL CLOSEOUT at release SHA b31207c · type: milestone
- What: PR #41 (Langfuse real span timestamps) merged through the six protected checks →
  release SHA `b31207ce33ebe0706b2dc9fa13816b73fb08d4fc`. Both services redeployed
  exact-SHA via Railway CLI (bad repo-root-context deploy on `agent` diagnosed and
  superseded; junk auto-created project deleted; deploy pattern hardened and recorded);
  `verify_deployed_sha.py` PASS (web + worker identity, readiness, synthetic smoke).
  Single authorized Tier-2 mint dispatched at the release SHA: run 29713267431 GREEN —
  fresh live 50-case gate, all five categories met. E01 final committed (durable copies +
  digests). Owner drove dry-run graph turns as Daron260 Windler79; Langfuse shows real
  non-zero trace durations with a chronological span cascade (percentile widgets
  populate). D01 full sync: rubric walkthrough live links, §8 closure boxes checked with
  evidence, README grader quickstart, gap-audit banner refresh (verdict stays Not
  Ready), G07 phrasing-limitation backlog item, straggler evidence committed
  (S01 fixtures, O01 bundle, R05 verification, O02 re-measure).
- Why: code freeze closeout — main fully pushed, eval evidence finalized, demo-ready
  handback so the owner can record immediately.
- Stage: CLOSEOUT — owner-only items remain: record + publish the S01 video, then
  launch V01 cold-eyes in a fresh session; owner-gated O02 full profile and O03
  restore drill stay open and stated.
