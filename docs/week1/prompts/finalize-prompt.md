# Claude Code Prompt — Stages 4 + 5: USERS.md + finalized ARCHITECTURE.md (MVP hard gates)

Run in the fork root, after AUDIT.md exists. Closes the two remaining MVP planning
hard gates. Three phases in order — the audit revisions must land before finalize.

---

```
The audit (./AUDIT.md) is complete. It flagged specific decision revisions and
produced concrete verifier rules. Close the two remaining MVP planning hard gates —
USERS.md and the finalized root ARCHITECTURE.md — incorporating the audit. Work in
this order; do not skip ahead.

READ FIRST: ./AUDIT.md (esp. its "Decisions that need revisiting" section and the
"Audit → Architecture Impact" table), docs/planning/DECISIONS.md,
docs/planning/ARCHITECTURE_DRAFT.md, docs/planning/PRESEARCH.md. Scope is planning
docs only — no agent/application code.

PHASE 1 — Apply the audit's flagged decision revisions to docs/planning/.
The audit did the analysis; apply exactly these, revision-dated, never silently
rewriting history (keep the prior wording noted):
  1. D10 / §7 — REQUIRED. Remove the claim that the correlation ID "joins OpenEMR's
     api_log via a shared id." Restate: Langfuse (D5) is the authoritative agent-side
     trace and the system of record for client_id + granted scopes + correlation id;
     api_log correlation is best-effort/fuzzy on (user_id, patient_id, request_url,
     utc_timestamp), weakened because every agent call logs the same delegated user_id.
     Evidence: F-C.1, F-C.2, F-A.5, F-P.6.
  2. D2 — reword. "Authorization inherited from OAuth2/SMART scopes + ACL" → for
     patient-scoped tokens it is granted scopes + single-patient compartment binding;
     GACL adds nothing for that bound patient. Keep D2's substance (external sidecar,
     inherited authz) — the audit CONFIRMED it (F-A.2: real SMART/OAuth2 surface,
     S256-enforced PKCE, delegated attribution). Evidence: F-S.1.
  3. D5 — elevate. Self-hosted Langfuse is now also a HIPAA §164.312(b) accountability
     control (the system of record for client_id + exercised scopes), because
     OpenEMR's api_log omits them — not merely observability. Evidence: F-C.1.
     [Archive note 2026-07-08: hosting later revised to Langfuse Cloud under an
     assumed BAA (DECISIONS.md D5 rev 2026-07-08); the elevated role stands.]
  4. D12 — close the test gap. The deceased hard-stop and the empty-allergy path are
     untestable on demo data (zero deceased Synthea patients). Add a requirement that
     the eval suite injects synthetic fixtures for both. Evidence: F-S.7, F-D.5.
  5. §5 verifier rules — make concrete. Encode the audit's specific rules:
     - FHIR status fields are unreliable — never render verbatim (F-D.1 immunization
       inversion; F-D.6 hardcoded Encounter.status=finished).
     - Allergy criticality is null dataset-wide — reject any criticality-based claim;
       never infer/deprioritize allergy risk from it (F-D.4).
     - Empty allergy result → "no allergy records returned; confirm with patient,"
       never "NKDA"/"no known allergies" (F-D.5).
     - Consume ALL conditions; never send clinical-status=active (broken filter returns
       nothing); reject "no history of X" if an inactive/resolved match exists (F-D.6).
     - Flag decade-stale lab dates rather than imply currency; reject valueless
       observations (F-D.6).
     - Empty medication dose → "dose not specified — confirm before dosing," never
       invent; de-dup order+plan to one stable ID per drug (F-D.2).
  Also add two new items surfaced by the audit:
     - Deployment runbook: user-scoped OAuth apps register DISABLED — a one-time
       "enable app in Administration" step is required before the agent can mint
       tokens (F-S.6).
     - Compliance: inventory api_log as a second in-boundary PHI store (Full-Logging
       default writes plaintext FHIR bodies); note the api_log_option/retention
       decision for the deployment (F-S.4, F-C.3).

PHASE 2 — Write ./USERS.md (root, Stage 4 hard gate).
Source the persona + use cases from docs/planning/PRESEARCH.md; this is the source of
truth ARCHITECTURE.md traces back to. Include:
  - The ONE narrow target user (PCP, 20-patient day, 90 sec between rooms) with the
    evidence the choice rests on (OpenEMR is outpatient; Synthea models primary-care
    encounters — the only persona serveable end-to-end with real data).
  - A concrete workflow: the moment the agent enters the physician's day.
  - Specific use cases (pre-visit brief; what-changed-since-last-visit; source-cited
    chart Q&A; evidence-backed attention flags) — each with an explicit "why is a
    conversational agent the right shape here" answer (PRD rule: no use-case trace →
    the capability doesn't get built).
  - Non-goals (no diagnosis/prescribing/orders/messaging/chart-writes/cross-patient).

PHASE 3 — Produce root ./ARCHITECTURE.md (Stage 5 hard gate).
Invoke the /arch-finalize skill (.claude/skills/arch-finalize) against the PRD,
./AUDIT.md, ./USERS.md, and the now-revised docs/planning/*. It must:
  - Run the gap audit; every capability must trace to a USERS.md use case.
  - Produce ./ARCHITECTURE.md OPENING WITH A ~500-WORD ONE-PAGE SUMMARY (graded hard
    gate) — key decisions, considerations, tradeoffs; write it last from the finished
    body.
  - Keep §N anchors; cite D#/R#/F# inline so a reviewer follows the trail.
  - Fold in the audit's confirmations (D2/D9 validated by F-A.2/F-S.5) AND its
    challenges (the revised D10/§7) — honest about both. The immunization-inversion
    finding (F-D.1) should appear as the concrete justification for §5.
  - Preserve explicit non-goals and owned tradeoffs; do not soften them.

Commit after each phase. Do not start agent/AI code — MVP is the foundation + plan,
not a working agent. Report: the decisions revised, that USERS.md + ARCHITECTURE.md
exist, and anything still open.
```
