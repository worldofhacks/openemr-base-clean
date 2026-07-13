# PRESEARCH.md — product understanding & planning baseline

> arch-draft playbook, Default mode. Posture: **production-grade** (confirmed). PRD: `Week_1_AgentForge.pdf`.

## Phase 0 — PRD Intake

### Product in One Sentence
An AI Clinical Co-Pilot embedded in OpenEMR that gives a primary care physician verified, patient-specific context in the 90 seconds between patient rooms, via a multi-turn conversational interface.

### What the Product Is
Tool-calling agent over one patient's record (history, meds, labs, encounters); every response passes a verification layer (source attribution + clinical constraint checks); authorization inherited from the logged-in clinician; fully observable; eval-gated.

### What the Product Is Not
A generic medical chatbot, a search bar, a dashboard widget, a report generator, or a diagnostic/decision-making authority.

### Primary Problem
Pre-visit context recall under time pressure: who am I seeing, why, what changed since last visit, what's on file, what matters today — currently requires scanning dense EHR notes across multiple screens.

### Primary User
**Locked:** Primary care physician with a ~20-patient day (see DECISIONS.md D1).

### Core Workflow
Between rooms, physician opens the co-pilot from the patient's chart → receives a pre-visit brief (what changed, active meds, recent/abnormal labs, today's reason) → asks follow-ups → every claim carries a citation to a record in the chart.

### Explicit PRD Requirements
Agentic multi-turn chatbot w/ tool use; verification layer (source attribution + domain constraint enforcement); observability from the start (per-request trace: steps, order, timing, tool failures, tokens, cost); eval suite (boundary/invariant/regression — happy-path-only fails); correlation IDs across all boundaries; strict schemas for all tool I/O; real-time dashboard (requests, error rate, p50/p95, tool calls, retries, verification pass/fail); runnable API collection; separate /health and /ready (real dependency checks); ≥3 documented alerts; baseline CPU/mem/latency/throughput profiles; load tests @ 10 & 50 concurrent users (p50/p95/p99 + error rate); AI cost analysis at 100/1K/10K/100K users; deployed publicly; demo videos; AUDIT.md before any AI work.

### Implied Requirements
Prompt-injection defense (eval case "inputs that attempt to extract unauthorized info" implies it); graceful degradation when OpenEMR/LLM/observability is down; conversation state management scoped to (clinician, patient) pair; audit logging of agent access to PHI (HIPAA audit-controls implication); latency budget "seconds, not minutes" → streaming + caching; demo-data-only guard.

### External Dependencies
OpenEMR fork (PHP/Laminas + MariaDB), Anthropic API (assumed BAA), Langfuse Cloud *(assumed BAA — revised 2026-07-08 from self-hosted, see DECISIONS.md D5 revision)*, Railway (D8), GitHub (+ optional GitLab mirror).

### Ambiguities / Open Questions
See OPEN QUESTIONS section below.

### Initial Risk Areas
Technical: OAuth2/SMART plumbing time-sink; FHIR data completeness in demo set; latency of multi-tool chains. Product: brief that isn't actually trusted/used; agent shaped by what's buildable instead of user need. Acceptance: audit gate skipped or thin → hard fail; happy-path-only evals → explicit fail; missing live deployed agent at Early/Final.

### Planning Mode / Build Posture
Default mode; production-grade posture (both confirmed by Alex 2026-07-06). MVP-checkpoint deferrals are allowed but must be flagged `scope simplification` and defensible.

## Phase 1 — Product Mechanics

- **Core object of value:** the **verified pre-visit brief** (and subsequent verified answers) about exactly one patient.
- **Unit of work:** one agent invocation (question → tool calls → verified, cited answer). Unit of value: one saved context-switch (target: brief < 30s, follow-ups < 10s perceived).
- **State-changing actions:** conversation turns append to session; agent **never writes clinical data** (read-only by design, wk1 scope — tagged `locked decision`).
- **Lifecycle:** SMART launch from patient chart (carries user + patient context) → session created → turns (each: retrieve → reason → verify → cite → respond) → session ends on navigation/timeout; trace persisted to Langfuse; PHI-bearing session store expires per retention rule.
- **Who creates/resolves:** clinician creates queries; verification layer resolves whether a response ships, gets flagged, or gets refused.
- **Hidden mechanics (PRD-implied):** citation integrity (a claim without a resolvable source must not be stated as fact); scope inheritance (agent can never exceed the clinician's own access); uncertainty must be communicated, not papered over.
- **Actions that must be impossible:** answering about a patient other than the launch context; answering with data the clinician's role can't access; stating unverifiable clinical facts as facts; writing to the EHR.

## Phase 2 — Users, Actors, Permissions

### Primary User
- Role: PCP, outpatient clinic, ~20 patients/day
- Context: 90-second gaps between rooms; interrupted constantly; EHR fatigue
- Pain: reconstructing "what changed & what matters" from dense chart data under pressure
- Success: walks in already oriented; failure: brief is wrong/slow/unverifiable → trust lost permanently

### Secondary Users (documented, out of wk1 scope — `scope simplification`)
Nurse (different ACL profile), resident (supervised access), practice admin (no clinical data). Architecture must not preclude them: authz model is role-based via OpenEMR scopes, so adding them is config, not redesign.

### Non-Human Actors
Agent service (OAuth2 client, acts *as* the launching clinician — never as a super-user); Langfuse Cloud trace export *(revised 2026-07-08: no self-hosted Langfuse worker)*; health/readiness probes; load-test harness; CI deploy job.

### Permission Matrix
| Actor | Can | Cannot | Risk if violated |
|---|---|---|---|
| PCP (via agent) | Read own patients' charts via FHIR w/ their token | Write EHR; read other clinicians' patients beyond ACL | PHI breach |
| Agent service | Call FHIR w/ user-delegated token; call LLM; write traces | Hold standing super-user creds; bypass OpenEMR ACL; train on data (BAA) | Authz bypass — worst failure mode |
| Nurse/resident (future) | Role-scoped subset | Physician-only data | Compliance |
| Unauthenticated | /health, /ready, SMART launch redirect | Any PHI | Exposure |

## Phase 3 — Stakeholders (who judges this)

| Stakeholder | Cares about | Rejects if | Evidence needed |
|---|---|---|---|
| Gauntlet grader | All hard gates, engineering rqmts, defensibility | Missing AUDIT.md-before-AI, happy-path evals, dead deployment | Repo docs, live URL, dashboard, eval results |
| Hypothetical hospital CTO (PRD's stated bar) | Patient safety, PHI handling, failure behavior | Hallucination reaching a clinician; authz hole | Verification design + eval evidence, trust-boundary map |
| Interviewer (Tue/Thu/Sun) | Depth of reasoning, tradeoff honesty | Choices that can't be defended ("framework X because tutorial") | DECISIONS.md ADRs |
| Alex (maintainer) | Wk2–3 compounding, velocity | Architecture that fights the remaining weeks | Clean boundaries, no tech debt traps |

## Phase 4 — Flows

### F1: Pre-visit brief (happy path)
Physician on patient chart → launches co-pilot (SMART EHR launch: OAuth2 code flow w/ patient context) → agent service validates token, creates session+correlation ID → orchestrator fires patient-summary tool plan (demographics, active problems, active meds, recent labs w/ abnormal flags, last + today's encounter) via FHIR, parallel where independent → synthesis w/ per-claim citations → verification layer checks citations + constraints → streamed brief w/ citation chips. Data touched: FHIR Patient, Condition, MedicationRequest, Observation, Encounter, AllergyIntolerance. Failure branches → F3.

### F2: Follow-up question
Turn arrives in existing session → context (prior turns + cached FHIR data w/ TTL) → additional tool calls only if needed → verify → respond. Constraint: session pinned to launch patient; cross-patient asks are refused by design.

### F3: Degraded modes (system flow)
- FHIR 4xx/5xx or timeout → retry w/ backoff (budgeted) → on exhaustion: explicit "couldn't retrieve X — here's what I have" partial with what's missing named. Never silent omission.
- LLM provider down → fail loud, fast; offer deterministic (non-LLM) chart summary from cached structured data — `proposed recommendation`.
- Verification failure → response blocked/edited to flagged form, incident logged w/ correlation ID, visible in dashboard metric.
- Langfuse down → agent keeps serving (observability is not on the request critical path; buffered/dropped-with-counter export) — `proposed recommendation`.
- Empty/sparse patient record → brief says exactly that (boundary eval case).

### F4: Ops flows
Deploy (push→CI→compose up→health check→evals gate), rollback (git revert + redeploy), load test (k6 @ 10/50 vus), alert response (3 documented runbook entries).

## Domain Model (wk1 slice)
Session (id, clinician_sub, patient_id, created_at) → Turns (role, content, citations[]) → ToolCalls (name, schema-validated input/output, FHIR resource refs, latency, status) → Citations (claim_span → FHIR resource type/id/element) → VerificationResults (per-response: pass/flag/block + reasons). All rows carry correlation_id.

## Constraints
HIPAA/PHI (demo data only; assumed BAA; PHI-in-traces stays within our deployment); audit-before-AI hard gate; latency seconds-level; one week; solo dev + AI tooling; Railway managed platform (D8 — local dev via Docker Compose); budget: Railway usage-based (~$5–20/mo small projects, monitored) + LLM API usage.

## Evaluation Criteria (acceptance)
Every PRD hard gate + engineering requirement (see WEEK1_CHECKLIST.md); eval suite where every case is boundary/invariant/regression-tagged with the failure mode it guards; verification pass/fail visible on dashboard; live agent at Early/Final.

## OPEN QUESTIONS
1. Submission mechanism/portal (not in PRD) — **ask staff today**; GitLab mirror requirement — ask.
2. Final deadline minute (11:59 AM vs noon Sunday) — assume 11:59 AM.
3. Architecture Defense clock start + format (live? slides? doc walkthrough?) — **ask staff today**.
4. Demo-data richness for labs trending (affects brief depth) — resolve during Stage 1 local run.
5. Railway usage cost trajectory (ClickHouse memory) — monitor from day one; fallback plan in D8.
6. OpenEMR-on-Railway pathfinding risk — no prior art found; timebox to Tue morning (D8). Contingency is Railway-native: previous-deployment rollback + local compose for demo continuity.

## Early Decision Candidates
Promoted to DECISIONS.md D1–D9.
