# Claude Code Prompt — Bridge to Build: IMPLEMENTATION_PLAN.md

Run in the fork root, after ARCHITECTURE.md is finalized. Produces the spec-anchored
build plan for the Early Submission agent. No application code yet — this is the plan.

---

```
Read .claude/skills/tasks-gen/SKILL.md and execute its process (invoke the skill if
it's callable; otherwise follow the SKILL.md steps directly — same as arch-finalize).

Inputs:
- ./ARCHITECTURE.md (binding contract — read fully, especially §10 build order and
  the §5 verification pipeline; it is read-only here)
- docs/planning/DECISIONS.md (D1–D15, post-audit revisions), ./AUDIT.md, ./USERS.md
- The checkpoint deadlines: MVP done; EARLY SUBMISSION = Thursday 11:59 PM CT
  (deployed agent + eval framework + observability wired in + demo video);
  FINAL = Sunday 11:59 AM CT.

Produce ./IMPLEMENTATION_PLAN.md phased against those real deadlines. Scope the EARLY
phase tightly to what Thursday actually requires — a live, verified, observable agent
that does the core pre-visit use case (UC1) end-to-end — and defer polish to FINAL.

Ordering must respect the hard dependencies (these are non-negotiable sequence):
  1. Agent service skeleton — FastAPI, config, /health + REAL /ready (checks OpenEMR
     FHIR metadata, LLM provider, Langfuse, session store), structured logging,
     correlation-ID middleware. (Observability scaffold lands FIRST, per §7 — it is
     not retrofitted.)
  2. SMART/OAuth client — authorization_code + PKCE against the now-provisioned,
     working OAuth client (D9; the crypto-key fix means this is live). Session pinned
     to (clinician, patient) at creation (D12).
  3. FHIR tool layer — the ~6 read-only tools with Pydantic contracts, parallel
     fan-out (D10), per-call timeouts + total turn budget; pass explicit Observation
     category to prune fan-out (audit F-P.2).
  4. EvidencePacket builder — normalized records, stable evidence IDs (§5). This is
     the only thing the LLM and verifier see.
  5. Orchestrator — direct Anthropic tool-use loop (D6), prompt-cached patient prefix.
  6. Verification v1 — typed claims → field-level match (reject on contradiction) →
     deterministic templater. Encode the audit's concrete rules from §5/D7: FHIR
     status unreliable (never render verbatim — F-D.1 immunization inversion),
     criticality-null (reject criticality claims — F-D.4), empty-allergy phrasing
     (F-D.5), consume-all-conditions / never clinical-status=active (F-D.6).
  7. Langfuse wired — traces carry the correlation ID + client_id + exercised scopes
     (D5 is now the HIPAA system-of-record per the audit); token/cost tracking.
  8. Eval framework v1 — pytest, cases tagged boundary/invariant/regression, INCLUDING
     the synthetic fixtures the demo data can't provide: a deceased-patient fixture
     (D12 hard-stop) and an empty-allergy fixture (F-S.7 test gap).
  9. Deploy the agent as a new Railway service in the existing project; verify the
     live agent serves UC1 end-to-end.

Every task carries: Files (NEW/extended), Anchors (§/D#/F#/UC#), Accept (2–5 bullets
INCLUDING the edge/error behavior the architecture names — not happy-path only), and
Test (the unit/integration/eval that proves it). Mark parallelizable tracks. Add a
Deliverables map (each Early-graded item → the task that produces it) and a dated
Cut/deferred section. Flag — never invent — any task lacking architecture backing.

Do not write application code. Output ./IMPLEMENTATION_PLAN.md, commit it, and report
the Early-phase critical path + anything still open before I start building.
```
