# TDD-Swarm ledger — epic: E6 verification (swarm/e6-verification)
Baseline (main @ fe9af0f): 119 passed, 3 live deselected.
Posture: production-grade.

Ticket T-E6: §5 verification pipeline (typed claims → field-level verify → deterministic
templater re-render) + audit rules F-D.1/F-D.4/F-D.5/F-D.6 (+F-D.2, D12 deceased, treatment-verb).
Files: NEW app/verify/{claims,verifier,rules}.py; EXTEND app/verify/templater.py.
Tests own: agent/tests/ (frozen by Test Agent). Src own: agent/app/verify/ (Impl Agent).

## Phase 2 — tests frozen (RED)
- Test Agent committed 3 test files @ 40ad57e (test_claims/test_verifier/test_templater_verified, 600 lines). app/ untouched.
- Orchestrator re-ran: RED for the right reason (ModuleNotFoundError app.verify.*), prior 119 unbroken.
- Independent test-design review: APPROVE_FREEZE, 0 critical/important, all 11 DoD rules covered. Tests FROZEN.

## Phase 3–4 — implemented (GREEN) + verified
- Impl Agent committed app/verify/{claims,verifier,rules}.py + templater.py extension @ 6187b66 (app/verify only).
- Orchestrator re-ran gates: 166 passed / 3 live deselected. Frozen tests byte-identical since 40ad57e (git diff empty) — separation of powers held.
- Independent Reviewer (did not write code): VERDICT APPROVE. All 11 DoD items PASS. No critical/important findings.
- Minor findings recorded for owner wave review (non-blocking, per skill + reviewer follow-up recommendation):
  1. F-D.1 immunization declined-trap is latent — no Immunization tool/record in the six-tool set, so ImmunizationClaim blocks at citation-resolution (correct outcome: "declined" is never emitted), not via the declined branch. Forward-looking §5 vocabulary; wire the trap when/if an Immunization tool lands.
  2. TextClaim does not resolve its citations → an unresolvably-cited descriptive TextClaim → FLAGGED not BLOCKED (renders nothing, so no leak). Tighten to match "every claim must cite a resolvable record."
  3. render_from_verified surfaces all packet notices unconditionally when packet passed (cosmetic).
  4. Screens are demo-depth (§5-sanctioned): audited literal cases all caught; some tense/synonym variants not (documented extension path).
- Ticket T-E6: review-passed. Boundary reached — pausing for owner review before PR merge (no merge to main by the swarm).

## Finding #2 — CLOSED (fail-closed) via a second swarm micro-cycle
- Test Agent added frozen test @ 2b58dea (unresolvable-cited TextClaim → BLOCKED; additions-only, RED against FLAGGED).
- Impl Agent fixed verifier.py @ e5197da (fail-closed on unresolvable citation; no test edited).
- Ticket reviewer: APPROVE, 0 findings. Orchestrator re-ran: 167 passed. Separation of powers intact.

## Findings DEFERRED (recorded, with reasons — not this-week work)
- #1 F-D.1 immunization explicit declined-trap — FORWARD-LOOKING. The Early six-tool scope
  (Patient/Condition/MedicationRequest/Observation/Encounter/AllergyIntolerance) has NO
  Immunization tool, so an ImmunizationClaim already fails closed at citation-resolution
  (correct outcome: "declined" is never emitted). The dedicated declined-branch trap +
  its exercising test are wired only when/if an Immunization read tool is added (post-Early).
- #3 Cosmetic notice rendering — render_from_verified surfaces all packet notices when a
  packet is passed. Honest, not a correctness/safety issue; refine when the /chat SSE
  contract (§5a) pins who owns notice surfacing. Deferred to Final polish.
- #4 Demo-depth screens — §5 explicitly sanctions demo-depth rule tables ("rule tables are
  demo-depth, extension path documented"). Every AUDITED literal phrase/verb is caught
  case-insensitively; tense/synonym expansion is Final-scope (verification v2, E-Final).

# ============================================================================
# Ticket T-E6a — wire E6 verification into the serving loop (verify-then-flush, §5)
# Branch swarm/e6a-verify-in-loop off main @ 411108a (182 passed baseline).
# Traces to: ARCHITECTURE §5 (verify-then-flush), §3 UC1 step 3, D7, D12, F-C.1 (verdicts→trace).
# Design: LLM answers via a `submit_claims` tool (typed E6 claims + evidence_ids) → Verifier.verify
#   each vs the EvidencePacket → render_from_verified (BLOCKED/REFUSED dropped) → served text.
#   D12 deceased pre-flight before the LLM. Trace.verdicts populated. source stays "llm" so the
#   frozen E5/E7 orchestrator tests (which assert source+structure, not served text) stay green.
# Test Agent owns tests/ (freezes the end-to-end invariant); Impl Agent owns app/ (no test edits).

## TEST_DISPUTE (adjudicated) — verify-then-flush vs an E7 test pinning served prose
- Impl Agent returned BLOCKED(TEST_DISPUTE), refusing to weaken §5: `test_orchestrator_trace.py::
  test_tracing_failure_never_breaks_the_brief` asserts `res.text == "brief"` on the end_turn-no-
  submit_claims path. Under verify-then-flush, uncited prose is BLOCKED and never served → that
  assertion encodes the superseded "serve raw prose" contract.
- Orchestrator adjudication: dispute VALID. The test's real intent is the SOFT-DEPENDENCY property
  (source=="llm" + tracer.dropped==1); the served-text pin was incidental to the old behavior.
  Resolution: a Test Agent updates that test to assert the soft-dep intent (drop the res.text pin).
  Separation of powers held — the Impl Agent did not touch it; a Test-role agent does.

## T-E6a — review-passed (verify-then-flush wired + findings closed)
- Verify-then-flush impl @ 9c10488; TEST_DISPUTE adjudicated (test updated); Finding #1/#2 fixed
  fail-closed @ 200b39b (Test froze 939040a → Impl fixed → Reviewer APPROVE). Suite 190 passed.
- Orchestrator re-ran gates itself (trust nothing): 190 passed; frozen invariant + finding-1 tests
  byte-identical since their freezes; impl commits app/ only. Reviewer: no safety/behavioral findings.

## Findings DEFERRED (recorded, reasons)
- E6-verifier label fallback (out-of-scope note from the T-E6a review): `_verify_medication`/`_verify_lab`
  set the verified LABEL as `record.fields.get("name"/"display") or claim.X`, so when a CITED record's
  label is empty (absence, not contradiction — §5 passes) the claim's own label renders. Assessed NOT a
  critical bypass: (1) it's the documented §5 limitation ("field-level match proves provenance, not
  synthesis"); (2) the SENSITIVE fields (dose, lab value) are ALWAYS record-sourced, never the claim's
  (F-D.2 holds); (3) real records carry labels. Deferred to E6-verifier hardening (drop/annotate a label
  the record lacks) — needs its own Test-Agent-frozen test; flagged to owner. Lives in verifier.py (E6),
  not the T-E6a diff.

# ============================================================================
# Ticket T-E6b — verifier leniency + 3 review-flagged gaps (branch swarm/e6b-verifier-leniency off main)
# Traces: §5 rule 1 (identity match), D7, D9/D5 (provider attribution), F-D.2.
# Scope (4): (1) LENIENT label identity match (name/display) but STRICT exact dose+lab value;
#   (2) all-claims-blocked → honest D13 grounded/"couldn't verify" render, NEVER empty source=llm;
#   (3) clinician_sub from the token id_token fhirUser/sub (currently hardcoded in service.py) — D9/D5;
#   (4) implement F-D.2 order/plan medication de-dup (NOT implemented; E6.2 checkbox is wrong → correct + add).
# Orchestrated as a Workflow (ultracode): Test-freeze → RED → Impl → adversarial Review panel.

## T-E6b workflow (Test→RED→Impl→adversarial Review) — 212 passed
- Test froze 9e8326c (14 new invariants + 7 reconciled all-blocked→D13 tests). Impl d44df40 (app-only,
  frozen tests untouched): lenient label match, all-blocked→D13 grounded, clinician_sub from id_token
  fhirUser/sub, F-D.2 order/plan dedup. Both reviewers APPROVE.
- Adversarial reviewer IMPORTANT finding: the "share one significant token" leniency over-collapses
  distinct token-adjacent entities (insulin glargine≈insulin lispro, metoprolol tartrate≈succinate).
  NOT a served-falsehood (strict dose/value + render uses the record's true identity), but the entity
  gate is weaker than intended → micro-cycle to tighten (token-SUBSET, not shared-one-token).

# ============================================================================
# EPIC: W2 Wave 0 — de-risking spikes (swarm/w2-wave0), started 2026-07-14
# Baseline (main @ c3e0804): 238 passed, 5 skipped (agent/ pytest). Posture: production-grade (carried).
# Tickets (from docs/week2/W2_IMPLEMENTATION_PLAN.md Phase 0 — binding source; owner pre-approved
# exactly this set in the dispatch prompt, satisfying the Phase-1 human checkpoint):
#   W2-M1 (container spike, Railway + RSS gate)  -> W2-M4 (PDF words+boxes reader spike)
#   W2-M3 (LangGraph skeleton + SSE spike)       || W2-M24 (Tier-2 timing/cost/quota + fork-PR secret policy)
# Sub-waves: 0a = {W2-M1, W2-M3, W2-M24}; 0b = {W2-M4}. STOP after Wave 0 for owner go/no-go.
# Constraints: writes ONLY under agent/ (+ devlog aggregate, .tdd-swarm/, tickets/); no OpenEMR
# PHP/routes/schema (W2-D2/D9); no OpenEMR write enablement (W2-OA3 pending); PyMuPDF banned (W2-R6);
# synthetic data only; secrets from env only. GH Issues mirroring skipped (W1 precedent: ledger is
# the record). Facts at start: Railway CLI logged in (agent service Online); fork GH repo secrets
# EMPTY (W2-OA2 pending — M24 measures on the local agent key and notes it); host tesseract installed
# for the M4 local loop; per-ticket isolation via `openemr-cmd worktree add <branch> -b --base
# swarm/w2-wave0` (no stack started; no git hooks in this clone).
- Orchestrator pre-staged langgraph>=1.2,<2 in agent/pyproject.toml on swarm/w2-wave0 (W2-R1 binding; latest 1.2.9) so W2-M1 solely owns pyproject/Dockerfile within wave 0a and W2-M3 never touches the dep manifest — same-wave file-scope exclusivity preserved.

## Phase 1 — tickets written + adversarially reviewed
- Planner commit 4249dea (4 tickets + TICKETS.md). Adversarial review r1: FIX_NEEDED — 1 critical
  (M24 tests in agent/ops/tests/ never collected by the binding pytest gate → moved to agent/tests/),
  3 important (M4 fixtures dir moved test_scopes→file_scopes; M24 image-gen path pre-authorized
  stdlib-only; M1/gates.md license clause aligned to permissive-family + explicit HPND allowlist).
  Fix commit da47e36. Review r2: APPROVE (0 critical/important; 3 minor).
- Minor findings APPLIED by orchestrator (tickets are planning artifacts, pre-freeze): M1 AC-1 now
  smokes pdfplumber; M3 AC-6 pins the SSE opt-in to the §2a contract (test author must not invent);
  M4 AC-7 single deterministic pass-branch + tesseract-version-tolerant assertion rule.
- Owner checkpoint: satisfied by the dispatch prompt ("build exactly these" — W2-M1→M4, M3 ∥ M24).

## Phase 2 — wave 0a worktrees + environment-normalized baseline
- Ticket worktrees created via openemr-cmd (no stacks): openemr-wt-ticket-w2-m1-container-spike,
  -w2-m3-graph-skeleton, -w2-m24-tier2-spike; fresh python3.12 venvs, pip install -e '.[dev]'.
- Baseline normalization (verified by collect-diff): fresh [dev] venv = 236 passed / 6 skipped,
  which is EXACTLY the primary's 238/5 minus the opt-in [ui] playwright extra (test_ui_smoke:
  2 passing params there -> 1 module skip here). Per-worktree gate number: 236 passed / 6 skipped.

## Phase 2 — wave 0a tests FROZEN (RED verified by orchestrator, trust-nothing)
- W2-M1: freeze cdeed28 (test_app_boot.py extended append-only; 5 AC-1 import tests RED for right
  reason, 2 AC-2 guards green-by-construction as documented; suite 5F/238P/6S). Review: APPROVE_FREEZE,
  3 minor (recorded): stale 238/5 baseline wording in ticket vs real fresh-venv 236/6; pip-in-venv
  assumption; host-tesseract dependency is by-design of AC-1.
- W2-M3: freeze d88b234 (test_graph_skeleton.py NEW; 16 RED; suite 16F/236P/6S). Review:
  APPROVE_FREEZE, 4 minor (recorded): span capture via existing sink seam outside M3 scopes (impl
  calls the seam, doesn't edit it); fake propagate_attributes order-sensitivity; step budget bounded
  not pinned-exact; per-decision reason_code sets approximated by single closed enum.
- W2-M24: freeze 849cbcc (test_tier2_spike.py NEW; 30 RED; suite 30F/236P/6S). Review: APPROVE_FREEZE,
  3 minor (recorded): AC-5 ablation missing no-secrets leg; stale baseline wording; nearest-rank
  percentile definition frozen (documented choice).
- Orchestrator re-ran suites + spec-lint in all three worktrees and verified changed-file sets are
  exactly the declared test files. Statuses -> tests-written. Impl agents may NOT touch agent/tests/**.

## Phase 3 — wave 0a GREEN (all three DONE; orchestrator re-verified, trust-nothing)
- W2-M1 @ 497a7ed: gates ALL PASS re-run by orchestrator (243P/6S). Frozen tests byte-identical since
  cdeed28. Scope clean (Dockerfile, pyproject, ops/spike_rss.py, report; railway.json not needed).
  MEASURED: Railway plan limit 32 GB -> W2_WAVE0_RSS_CEILING_MB=24414; concurrent peak RSS 2494 MB
  (cold 22 MB) -> PASS ~9.8x headroom, ladder NOT invoked; local-reranker stack UNBLOCKED.
  Image 369->809 MB (models fetched at startup, not baked; bge 2.4s + fp32 reranker 7.4s load);
  deploy-to-healthy ~61s; Railway builder GREEN first try; /health 200 throughout (orchestrator
  re-checked live: 200 alive). SPIKE FINDINGS: mxbai-rerank-base-v1 absent from fastembed builtins but
  loads torch-free via TextCrossEncoder.add_custom_model() (HF repo Apache-2.0, own ONNX artifacts) —
  feature tracks must add the registration call at composition root (W2-D4 rev impact); fp32 reranker
  ~2.3 GB resident (quantized 244 MB on disk / 2068 MB peak) — fine at 32 GB, would need ladder step 2
  if plan ever <=2 GB (W2-O1 note). tqdm MPL-2.0-AND-MIT allowlist -> handed to Reviewer.
  INFRA STATE CHANGE: Railway SSH key 'w2m1-spike-key' (host id_ed25519.pub) registered for
  railway ssh, left registered — Security agent to assess; surfaced for owner go/no-go.
- W2-M3 @ f1765c7: gates ALL PASS re-run (252P/6S). Frozen tests intact since d88b234. Scope clean
  (orchestrator/{graph,state,workers/*}, chat.py, report). loop.py untouched. AC-7 REAL Langfuse
  nesting VERIFIED via API readback (trace 52c7bfaf75d3b116d5fe080e7b417cb4, supervisor⊃worker via
  parentObservationId chains). AC-8 SSE VERDICT: §2a fallback INVOKED — stream final composer stage
  only; cause is the §5 verify-then-flush contract + non-streaming provider.complete(), NOT a LangGraph
  limit; TTFE/total=1.00 (no perceived-latency win today; graph overhead ~3.2ms/turn). SPIKE FINDINGS
  for feature tracks: late-bind graph entrypoints (import-order tripwire hazard); LangfuseSink needs a
  public nested-trace API (spike drives sink._get_client() read-only — reviewer to adjudicate);
  LangGraph recursion_limit must exceed the semantic step budget (used 2*8+4).
- W2-M24 @ 81b044c: gates ALL PASS re-run (266P/6S). Frozen tests intact since 849cbcc. Scope clean
  (ops/spike_tier2.py, docs/week2/W2_TIER2_CI_POLICY.md, report). MEASURED (claude-sonnet-4-6, LOCAL
  key — W2-OA2 substitution noted): 50-case Tier-2 run = $0.345 and ~9.3 min (160 provider calls,
  50,250 in + 12,950 out tokens @ $3/$15 per MTok) — VIABLE as a required PR gate; ~12x below the
  retired $4 claim. Policy doc with all six frozen clauses committed.
- Secret scan on all three diffs: clean; no .env committed anywhere. Live /health re-verified 200.
- Review + Security panel (6 independent agents) dispatched.

## Phase 4 — verification panel verdicts (6 independent agents; re-run after session-limit interrupt)
- W2-M1: Reviewer APPROVE (every DoD/AC met with file:line evidence; AC-5 concurrency verified in code,
  all arithmetic re-checked; tqdm MPL-2.0-AND-MIT allowlist CONFIRMED acceptable — unmodified wheel,
  file-level weak copyleft, explicit-allowlist mechanism is exactly the DoD's). Security PASS.
  Minors recorded: probe measures probe-process RSS not container-wide (tesseract subprocess +
  uvicorn app excluded — headroom 9.8x dwarfs it); NO-VERDICT exits 0; tqdm "dual-licensed" wording;
  quick-mode rerank_top1 key; SSH key left registered (also security minor — owner decision at
  go/no-go: keep for ops or remove); no HF revision pin for runtime model fetch; pytesseract no upper
  bound. -> REVIEW-PASSED.
- W2-M3: Reviewer APPROVE; Security PASS. Minors recorded: graph stack eagerly imported on flag-OFF
  path (import cost only, behavior identical — frozen AC-4 proves it); sink private-accessor use
  adjudicated ACCEPTABLE for the spike (promotion request documented for observability owner);
  span replay omits start_time (durations distorted in Langfuse view); REFUSE recorded as
  step_budget_exceeded reason; _DEFAULT_REFUSAL_TEXT private import; Accept-header substring match
  (also security minor; hardening note for W2-M9/M14). -> REVIEW-PASSED.
- W2-M24: Reviewer APPROVE (extrapolation/percentile math verified; near-miss passes for the right
  reason; arithmetic re-checked). Security: 1 IMPORTANT — lint clause-2 evadable via equivalent
  PR-ref spellings (refs/pull/N/head|/merge, merge_commit_sha) + implicit github.token counts as
  secret access under pull_request_target. FIX MICRO-CYCLE dispatched (frozen evasion tests first ->
  lint fix + policy-doc clause-2 inversion rider -> security re-review). Minors recorded: .yaml
  extension out of documented scope; keyword-presence policy-doc lint; percentile p-range; DSN
  over-redaction; non-dict YAML silently passes; PyYAML transitive dep.

## Phase 4b — W2-M24 fix micro-cycle CLOSED (separation of powers held)
- Test-role froze 5 evasion cases @ 405c894 (additions-only, 136+/0-; 4 RED for the right reason +
  near-miss guard green). Impl fixed lint @ 519dfed (refs/pull/N/{head,merge}, merge_commit_sha,
  implicit github.token counts as secret access; policy-doc clause-2 inversion rephrased; frozen tests
  untouched). Security re-review: PASS — adversarially verified (pre-fix module evades, post-fix
  flags; dependabot near-miss passes for the right reason: no checkout leg at all). Suite 271P/6S.
- Orchestrator re-ran gates: ALL PASS; frozen-drift none. -> W2-M24 REVIEW-PASSED.
- Residual minors -> W2-M20 follow-ups (recorded): manual git-fetch PR-head in run: bodies is a
  distinct undetected evasion family (recommend run:-body scan); bare pull/N/head regex relaxation.
