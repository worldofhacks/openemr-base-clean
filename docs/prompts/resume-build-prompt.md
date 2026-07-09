# Claude Code Prompt — Resume Early Build (HYBRID: lean sequential + tdd-swarm for the safety-critical layer)

Run in the fork root. The agent service is partway built (E1 exists). This resumes it
through to a live agent: lean single-threaded test-first for the plumbing, and the
tdd-swarm skill's separation-of-powers reserved for the highest-stakes layers (E6
verification, E8 evals) where independent test authorship matters most.

---

```
We're mid-build on the Early Submission agent. E1 (the agent/ FastAPI skeleton — config,
logging, /health + /ready, correlation-ID middleware, tests) already exists. Do NOT start
over. Resume the E-path in ./IMPLEMENTATION_PLAN.md. ARCHITECTURE.md is the binding
contract; AUDIT.md (F-*) and DECISIONS.md (D1–D15) are the rationale. Agent is read-only,
demo data only, no OpenEMR app-code changes. Early Submission is due Thursday 11:59 PM CT —
optimize for shipping a live, verified UC1 agent; protect that critical path over breadth.

PHASE 0 — ORIENT + GREEN BASELINE:
1. Read IMPLEMENTATION_PLAN.md and the existing agent/ tree.
2. `cd agent && pip install -e ".[dev]" && pytest -q`. Report pass/fail; confirm the app
   boots and /health + /ready respond. If anything is red, fix to green FIRST (use the
   bug-hunt skill if the cause isn't obvious). Don't stack features on red.
3. Reconcile the plan: tick the checkboxes for tasks genuinely done + verified green, so
   the plan tells the truth. Report the true next-unbuilt task.

PHASE 1 — LEAN SEQUENTIAL for the plumbing (E2, E3, E4, E5, E7):
Single-threaded, test-first. Per task: write the failing test from the Accept criteria
FIRST (include the edge/error case the architecture names, not just happy path) →
implement the smallest code to pass → task test + full suite green → tick the checkbox →
commit citing the task id + § anchor. PAUSE and report at each phase boundary.
- E2 (verify carefully — first live integration): authorization_code + PKCE(S256) against
  the LIVE provisioned OAuth client on prod (works post crypto-fix, DEPLOYMENT.md §8). Test
  token exchange + launch-context binding against live OpenEMR; report exact granted scopes.
  Session pinned to (clinician, patient) at creation (D12).
- E3 → E4: freeze Pydantic tool contracts first (source of truth), then the 6 read tools
  with parallel fan-out + per-call timeouts (D10), explicit Observation category (F-P.2),
  then the EvidencePacket builder with stable IDs.
- E5: direct Anthropic tool-use loop + prompt-cached patient prefix; D13 deterministic
  fallback on LLM failure.
- E7: Langfuse traces carry correlation_id + client_id + exercised scopes (D5 = HIPAA
  system-of-record) + token/cost.

PHASE 2 — TDD-SWARM for the safety-critical layer (E6, then E8):
Invoke the tdd-swarm skill (.claude/skills/tdd-swarm) SCOPED to these tasks only — this is
where independent, frozen tests earn their cost. Do NOT swarm the whole build.

  E6 — verification layer (highest stakes):
  - Run tdd-swarm's flow on E6.1 + E6.2 as its ticket set on a swarm/e6-verification
    branch. The Test Agent writes and FREEZES the tests from the audit's concrete rules
    BEFORE any implementation exists; the Implementation Agent (separate, cannot edit the
    tests) loops to green; an independent Reviewer verifies against the ticket DoD.
  - Frozen tests must encode: a completed-vaccine fixture that returns not-done and asserts
    the agent does NOT say "declined" (F-D.1); reject any criticality claim (F-D.4); empty
    allergy → "no allergy records returned; confirm with patient", never NKDA (F-D.5);
    consume all conditions, never clinical-status=active (F-D.6); typed claims → field-level
    verify (reject on contradiction, not absence) → deterministic templater re-renders
    display text from verified fields.
  - Deterministic vs eval split: verification LOGIC (field match, phrasing rules, templater)
    → frozen deterministic tests. LLM-behavior grounding claims → eval cases with thresholds,
    marked `eval` (handled in E8).

  E8 — eval suite (deploy gate):
  - Run tdd-swarm scoped to E8 on a swarm/e8-evals branch. Cases tagged
    boundary/invariant/regression + the synthetic deceased + empty-allergy fixtures the
    demo data can't provide. Wire as the CI deploy-gate — must be green before E9.
  - When an eval goes red, use the eval-triage skill (NOT bug-hunt — evals are LLM-behavior).

  For E6/E8: the swarm never merges to main — I review and approve the swarm PR before it
  lands. Keep the swarm scoped and fast; escalate a blocked ticket to me rather than looping
  silently past the cap.

PHASE 3 — E9 DEPLOY (sequential, after E8 gate is green on merged main):
Deploy the agent as a NEW service in the existing Railway project; verify UC1 (pre-visit
brief) end-to-end on the live URL before calling it done.

CROSS-CUTTING:
- If a task needs architecture ARCHITECTURE.md doesn't cover, STOP and route the change
  through the arch-finalize skill (don't invent it in code, don't silently amend the doc).
- Append a devlog entry (skills/devlog) at each phase boundary: what shipped, why, evidence.

Start with PHASE 0. Report before moving past the green baseline, and pause at each phase
boundary.
```
