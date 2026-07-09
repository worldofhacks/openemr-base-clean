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
