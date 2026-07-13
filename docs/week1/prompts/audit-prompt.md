# Claude Code Prompt — Stage 3: The Audit (hard gate before any AI work)

Run in the fork root, after deployment is live. This is the PRD's hard gate: AUDIT.md
must exist and be complete before any agent/AI code is written. Deliverable is
`./AUDIT.md` at repo root, opening with a ~500-word summary of key findings.

---

```
You are auditing my fork of OpenEMR before any AI/agent work begins. This is a
graded hard gate: the deliverable is ./AUDIT.md at the repo root, and it must open
with a ~500-word one-page summary of the MOST IMPACTFUL findings (not a dump — the
brevity is the point), followed by the full findings by section.

Context you already have in this repo:
- docs/planning/ — my architecture decisions (D1–D13), research (R#), and the draft.
  The audit must be honest even where it complicates those decisions.
- DEPLOYMENT.md — how the app is built and deployed (Railway, fork-source image).
- The live instance and local Synthea data (25 patients, ~1000 encounters).

SCOPE — this is READ-ONLY analysis. Do NOT write agent code, add features, or modify
OpenEMR application logic. Direct database inspection IS allowed here (audit-only) —
this is the one place raw SQL/schema reading is permitted, since the runtime agent
will never use it. Use demo data only.

Produce findings in these five sections (the PRD names all five). Tag every finding
with a stable ID (e.g. F-S.1 security, F-P.3 performance, F-A.2 architecture,
F-D.5 data-quality, F-C.4 compliance), a severity (critical/high/medium/low), the
evidence (file path, query result, endpoint response, or config), and the
architecture impact (what it forces the agent design to do — cite my D#/§ where it
changes or confirms a decision).

1. SECURITY AUDIT
   - Authn/authz: how OpenEMR issues and checks identity; how OAuth2/SMART scopes and
     the ACL (GACL) interact; what client_credentials vs authorization-code changes
     about provider attribution; whether the local-API shortcut can skip scope/audit.
   - Data exposure vectors: what endpoints leak, what the deployed surface exposes
     (confirm no phpMyAdmin/Xdebug/debug endpoints are public), token handling.
   - PHI handling + HIPAA-relevant gaps in the CURRENT system (before my agent).
   - Verify the credential rotation and secret posture from DEPLOYMENT.md actually holds.

2. PERFORMANCE AUDIT
   - Where the system is slow: per-request DB overhead (e.g. schema queries per
     service constructor), FHIR search fan-out behavior, N+1 patterns.
   - Data structure + volume: table sizes from the Synthea import, index presence on
     the resources the agent will read most (Observation, Condition, MedicationRequest,
     Encounter, AllergyIntolerance).
   - Constraints that will bound agent latency — measure a few real FHIR calls against
     the live instance and record wall-clock. This feeds my §9 cost/latency model.

3. ARCHITECTURE AUDIT
   - How the system is organized (modern src/ vs legacy interface/ + library/), where
     data lives, how the API pipeline (apis/dispatch.php → listeners → controllers →
     services → FHIR mappers) actually flows.
   - The integration points for adding capabilities: SMART launch surface, custom
     module system, event system. Confirm or challenge my D2 sidecar assumption with
     concrete file evidence.

4. DATA QUALITY AUDIT (this one most directly becomes agent failure modes)
   - Completeness: missing fields on the resources the agent depends on.
   - Consistency: hardcoded FHIR fields (e.g. encounter status/type), zero-date forms,
     binary-only medication status, resolved conditions hidden by the FHIR mapper.
   - The allergy semantics specifically: does an empty AllergyIntolerance query mean
     "no allergies" or "no records"? This is a patient-safety finding — verify against
     the actual data and mapper.
   - Duplicates, stale data, deceased-patient indicators and how the FHIR mapper
     represents them. Each data-quality finding should name the verifier rule or
     phrasing rule it forces in my §5 verification design.

5. COMPLIANCE & REGULATORY AUDIT (HIPAA its own pass, beyond security)
   - Audit-logging: what api_log captures and, critically, what it OMITS (OAuth
     client id, granted scopes) — and what that means for my correlation-ID design.
   - Data retention, breach-notification obligations, and the BAA implications of
     sending PHI to an LLM provider (the PRD says assume a signed BAA — state that
     assumption explicitly and what it does/doesn't cover).

METHOD:
- Prefer concrete evidence over assertion: run the query, hit the endpoint, cite the
  file+line. An unverified claim is a hypothesis — label it as one.
- Where a finding confirms or breaks one of my D1–D13 decisions, say so explicitly and
  note whether the decision needs revision (flag it; don't rewrite my decisions here).
- Keep the ~500-word summary ruthlessly prioritized: the handful of findings that most
  change what the agent must do. Everything else lives in the sections below it.

OUTPUT:
- Write ./AUDIT.md (root). Summary first, then the five sections, then a short
  "Audit → architecture impact" table mapping the top findings to the D#/§ they affect.
- Commit it. Do not start Stage 5 (agent) work — stop at the gate and report the top
  findings + any decision that now needs revisiting before I build.

Ask me before: modifying any OpenEMR application code (you shouldn't need to),
changing deployed config, or spending real money. Otherwise proceed and report.
```
