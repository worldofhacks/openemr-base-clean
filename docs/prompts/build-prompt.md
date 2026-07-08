# Claude Code Prompt — Early Build (agent implementation)

Run in the fork root. This is the first application code. Work the Early-phase
critical path (E1→E9) from IMPLEMENTATION_PLAN.md, in order, test-first.

---

```
Begin implementing the Early Submission agent per ./IMPLEMENTATION_PLAN.md. Work the
Early critical path E1→E9 IN ORDER — the sequence encodes hard dependencies, do not
reorder. ARCHITECTURE.md is the binding contract; DECISIONS.md (D1–D15) and AUDIT.md
(F-*) are the rationale. Demo/synthetic data only. The agent is READ-ONLY: no write
tools, no chart mutations, no DB credentials in the agent.

CADENCE (every task):
1. Write the failing test FIRST from the task's Accept criteria — including the
   edge/error case the architecture names, not just the happy path. Watch it fail.
2. Implement the smallest code that makes it pass.
3. Run the task's test + the suite. Green before moving on.
4. Commit per task with a message citing the task id and its § anchor.
PAUSE and report at each PHASE boundary (end of E1, E2, …) — state what's green,
what you verified, and anything that surprised you. Do not blow past a phase boundary.

PHASE-SPECIFIC NOTES:
- E1 (do this now): FastAPI skeleton, config, structured JSON logging, correlation-ID
  middleware, /health (liveness) and a REAL /ready that actually checks OpenEMR FHIR
  metadata + LLM provider + Langfuse + session store (no unconditional 200).
  Observability scaffold lands here, FIRST — it is not retrofitted (§7).
- E2 (verify carefully — first external integration): authorization_code + PKCE
  against the provisioned OAuth client (now live post crypto-fix). Session pinned to
  (clinician, patient) at creation (D12). This flow is fiddly — test the token
  exchange and the launch-context binding against the LIVE deployed OpenEMR, and
  report the exact scopes granted.
- E3–E4: Pydantic tool contracts frozen first, then the 6 read tools with parallel
  fan-out + per-call timeouts (D10); pass explicit Observation category (F-P.2). Then
  the EvidencePacket builder with stable evidence IDs — the only thing the LLM/verifier see.
- E6 (the crown jewel — spend your care here): verification v1. Typed claims →
  field-level match (reject on contradiction, not absence) → deterministic templater
  that re-renders display text from verified fields (the model's own prose is
  discarded). Encode AND test the audit's concrete rules: FHIR status never rendered
  verbatim (F-D.1 immunization inversion — write a test with a completed-vaccine
  fixture that returns not-done, assert the agent does NOT say "declined"); reject
  criticality claims (F-D.4); empty allergy → "no allergy records returned; confirm
  with patient", never NKDA (F-D.5); consume all conditions, never clinical-status=active (F-D.6).
- E7: Langfuse traces carry correlation_id + client_id + exercised scopes (D5 is the
  HIPAA system-of-record now) + token/cost.
- E8 (gate — must be green before E9): eval suite, cases tagged
  boundary/invariant/regression, INCLUDING the synthetic fixtures demo data lacks:
  a deceased-patient fixture (D12 hard-stop) and an empty-allergy fixture (F-S.7).
  Wire it as the CI deploy gate.
- E9: deploy the agent as a NEW service in the existing Railway project; verify UC1
  (pre-visit brief) end-to-end on the live URL before calling it done.

If a task needs something ARCHITECTURE.md doesn't cover, STOP and flag it (route
through /arch-finalize) — do not invent architecture in code.

Start with E1.1. Report at the end of E1.
```
