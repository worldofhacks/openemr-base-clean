# ARCHITECTURE.md — Clinical Co-Pilot for OpenEMR

> Binding architecture for the AI Clinical Co-Pilot (Stage 5 hard gate). Traces to `USERS.md` (use cases UC1–UC4) and is informed by `AUDIT.md` (findings F-#). Decisions are ADRs in `docs/planning/DECISIONS.md` (D#); external facts are `docs/planning/RESEARCH.md` (R#). This document is the contract the build phase implements against — no agent code exists yet; MVP is the foundation + plan.

---

## One-page summary

The Clinical Co-Pilot is a **read-only, multi-turn conversational agent** that gives a primary-care physician a **verified, cited pre-visit brief** about one patient in the 90 seconds between exam rooms (`USERS.md` UC1), then answers follow-ups with a per-claim citation to the chart (UC2–UC4). It is deliberately *not* a diagnostic tool, a chatbot, or a dashboard.

**Placement (D2, confirmed by audit F-A.2).** The agent is an **external SMART-on-FHIR sidecar** — an OAuth2 client of OpenEMR, not code inside it. This is the single most defensible decision: authorization is *inherited* from OpenEMR's own OAuth2/SMART surface rather than re-implemented, the blast radius on failure is the agent (never the EHR), and the language/tooling choice is free (Python/FastAPI, D3). The audit verified the surface is real — S256-enforced PKCE, EHR-launch, delegated-token attribution — while correcting one belief: for the agent's patient-scoped tokens, enforcement is **granted scopes + single-patient compartment binding, not scope∧ACL** (F-S.1), and OpenEMR's own patient-access check is a stub (F-S.2), so the **agent-side session pin (D12) is the real clinician↔patient guarantee**. It is certification-*capable* upstream code, not fork-certified.

**Data access (D9).** All reads go through the FHIR R4 API with the clinician's delegated `authorization_code`+PKCE token — never SQL, never `client_credentials` (which would attribute access to a synthetic system user, F-S.5), never OpenEMR's same-session local-API shortcut (F-S.3). The six independent baseline reads fan out in parallel (D10); the network hop is ~0.4s/read live (F-P.5), small next to the LLM.

**Verification (D7 / §5) is the load-bearing trust mechanism**, and the audit proved it is not optional theater: the stock FHIR Immunization mapper inverts status so **every completed vaccine reports as "patient refused"** (F-D.1). The agent therefore never surfaces a FHIR field verbatim — every tool result is normalized into an **EvidencePacket** of typed evidence records, the model answers in **typed claims carrying evidence_ids**, a deterministic verifier rejects on field-level contradiction, and a **deterministic templater re-renders the displayed text from verified fields** so the model cannot phrase its way past a check. Concrete rules the audit forced: empty allergy → "confirm with patient," never "NKDA" (F-D.5); never infer allergy risk from the (dataset-wide null) criticality field (F-D.4); consume all conditions (F-D.6); flag stale labs.

**Observability (D5, elevated; hosting revised 2026-07-08).** **Langfuse Cloud** — an external processor under an **assumed BAA, the same PRD-sanctioned posture as the LLM provider (D4)** — is also the **HIPAA §164.312(b) accountability record**: the system of record for OAuth client_id + exercised scopes + correlation id per call, because OpenEMR's `api_log` omits them (F-C.1) and cannot be joined by a shared id (F-C.2, D10 revised). Every request is fully traced; the dashboard and ≥3 alerts read from Langfuse. Demo runs on the cloud free tier under the demo-data-only rule; the production path is Langfuse's dedicated HIPAA data region (`hipaa.cloud.langfuse.com`, Pro plan+, signed BAA before real PHI).

**Owned tradeoffs (not hidden):** the sidecar spends latency on an OAuth hop (mitigated by caching + parallel calls); observability adds a **second external PHI processor** — Langfuse Cloud under an assumed BAA, owned via PHI-minimized traces (hashes, not identifiers), project-level retention, and a documented MIT self-host exit if vendor terms change (D5 rev); Railway has an outage history (mitigated by one-click rollback + local-compose demo continuity); and the "seconds not minutes" story is defended at *perceived* latency (streaming), with the 28s prior-art anchor an unverified assumption to be re-measured at Early (R12). Deployment must also close two audit items: pin `https://` (edge-only TLS, F-S.9) and set an `api_log` retention posture (Full-Logging default stores PHI at rest, F-S.4/D15).

---

## §1 System Overview

```
┌───────────────────── Railway project (managed TLS, deploy-on-push) ─────────────────┐
│                                                                                      │
│  ┌─────────────── OpenEMR ───────────────┐        ┌────────── Agent Service ───────┐ │
│  │  PHP/Laminas + Railway MySQL          │ SMART  │  FastAPI (Python, Pydantic)    │ │
│  │  • Patient charts (UI)                │ launch │  • /chat (SSE streaming)       │ │
│  │  • OAuth2/OIDC server (S256 PKCE)     │◄──────►│  • Orchestrator (tool loop)    │ │
│  │  • FHIR R4 API ◄ scope+compartment    │ FHIR   │  • Verification gate           │ │
│  │  • volume: sites/ state               │ + token│  • Session store (Postgres)    │ │
│  └───────────────────────────────────────┘        │  • /health /ready              │ │
│                                                   └───────┬──────────────┬─────────┘ │
└───────────────────────────────────────────────────────────┼──────────────┼───────────┘
                                                     traces │              │ prompts
                        (both egress points under assumed BAAs)            │
   ┌── Langfuse Cloud (assumed BAA, D5) ──┐                 │              ▼
   │ traces • dashboards • costs • evals  │◄────────────────┘      Claude API (BAA)
   │ = HIPAA accountability record        │                        Sonnet 4.6 + Haiku
   └──────────────────────────────────────┘
```

One Railway project (D8), three trust zones (§4). The agent is an OAuth2/SMART client of OpenEMR (D2); all patient reads via FHIR with the clinician's delegated token (D9); all LLM calls via a provider abstraction (D4); every request fully traced to Langfuse Cloud under an assumed BAA (D5 rev 2026-07-08). Local dev = Docker Compose (dev-easy); prod = Railway services.

**Non-goals (D12, explicit — do not soften).** No diagnosis, no treatment recommendations, no prescribing or ordering, no patient messaging, no chart writes, no cross-patient search, no write scopes. The agent is **read-only by construction**, not by policy.

## §2 Components

- **OpenEMR (fork):** unmodified except (a) SMART app registration, (b) a launch affordance on the patient chart. The affordance already exists as a near-zero-diff attach point — `SMARTLaunchController` on the `PatientDemographics` render event (F-A.3) — so the diff is registration + opt-in, not a core patch. OpenEMR remains the source of truth for identity, authorization, clinical data, and its own audit log.
- **Agent service (FastAPI):** routes `/chat` (SSE), `/sessions`, `/health`, `/ready` (contracts in §5a). Internals: SMART/OAuth client (auth-code + PKCE-S256; per-session token cache); tool registry (Pydantic-contracted FHIR tools — `get_patient_summary`, `get_active_medications`, `get_recent_labs`, `get_encounters`, `get_allergies`, `get_conditions` — plus deterministic composites like `get_changes_since_last_visit`, computed by code and only narrated by the LLM); **EvidencePacket builder** (normalizes tool results into typed evidence records with stable IDs — the only thing the LLM and verifier see, and the **input-side injection enforcer**, §4); orchestrator (direct Anthropic tool-use loop, D6; concurrent fan-out of independent calls, D10; per-session FHIR TTL cache; outbound `X-Copilot-Request-Id` on every FHIR call); verification gate + deterministic templater (§5); session store (Postgres, pinned to clinician+patient at creation, D12).
- **Langfuse Cloud (external, D5 rev 2026-07-08):** no Langfuse services in the Railway project — traces export to Langfuse Cloud (demo: free tier, US region, assumed BAA under the demo-data-only rule; production: HIPAA data region `hipaa.cloud.langfuse.com`, Pro plan+, signed BAA before real PHI). Hosts the dashboards, eval datasets, and the HIPAA accountability record (D5); the alert checker (§7) reads its API. Exit: MIT-licensed self-host migration path if vendor terms change.
- **Edge/TLS:** Railway-managed domains + HTTPS per service (SMART/OAuth requires HTTPS — free here). TLS is **edge-only** (F-S.9) → the agent pins `https://` and rejects downgrade (§4).

**Scope-trace note (gap-audit G9-1):** the observability/ops surfaces (dashboard, `/health`, `/ready`, alerts, load tests) trace to the PRD's **engineering requirements**, not to a USERS.md use case — they are graded infrastructure, not user-facing capability, and so are exempt from the "every capability traces to a UC" rule (which governs *agent capabilities*). Every *agent capability* does trace to UC1–UC4 (`USERS.md` traceability table).

## §3 Request Lifecycle

**§3.1 — Correlation ID minted at launch**, propagated to every log line, tool call, LLM call, and Langfuse span. "Full trace reconstructable from logs alone" holds **agent-side** (D10 rev — not via a hard OpenEMR api_log join, which is impossible: F-C.2).

**UC1 — Pre-visit brief (happy path, F1).**
1. Clinician clicks Co-Pilot on the chart → SMART EHR launch → OAuth2 code+PKCE(S256) → agent receives token with granular scopes + `launch/patient` context. Session row created, pinned to (clinician, patient); correlation ID minted.
2. Orchestrator runs the summary plan — **all six independent FHIR reads fan out concurrently (D10): wall-clock ≈ slowest call**, per-call timeout + total turn budget; dependent chains sequential only where data requires → prompt assembly (stable patient-context prefix → prompt-cache hit on later turns, D4) → Sonnet 4.6 streams typed claims.
3. Verification gate (§5) validates claims + constraints **before** each claim-block flushes; violations block/downgrade/refuse.
4. Response streams with citation chips; the trace (steps, order, per-step latency, tokens, cost, verdict) lands in Langfuse.

**UC2 — What changed since last visit (dependent chain, gap-audit FLOWS-1).** First read **Encounter** to bound "since last visit"; if no reliable prior encounter → deterministic refusal ("could not identify a prior visit reliably — review the chart manually," D12), never silently answer a different question. Otherwise a **deterministic delta tool** (code computes the diff over conditions/meds/labs — the LLM never does arithmetic, §5) → verify → narrate.

**UC3 — Cited chart Q&A (F2).** Turn arrives in the existing session → context = prior turns + cached FHIR (TTL) → additional tool calls only on demand → verify → respond. Treatment-verb requests refused (D12); every answer carries an evidence_id.

**UC4 — Attention flags (gap-audit FLOWS-1).** Flags are produced in the UC1 pass over the already-cached EvidencePacket (no extra fan-out); "why is this flagged?" is answered from the same cached evidence. A flag is a prompt to look, never a judgment (§5 rules; never rank on null criticality, F-D.4).

**§3a — Lifecycles & retention (gap-audit LIFE-1).**
| Entity | Created | Expiry / invalidation | Retention |
|--------|---------|-----------------------|-----------|
| OAuth access token | SMART launch | SMART ~1h; refreshed via refresh_token grant if a turn outlives it (else re-launch) | not stored beyond session |
| Session | launch | `MIN(token exp, idle timeout, turn cap)`; patient switch → new launch | Postgres; purged on expiry per retention rule |
| FHIR TTL cache | first read in a turn | short per-session TTL (staleness bound); dropped at session end | in-memory, never persisted |
| Langfuse trace | per request | — | retention policy set on the Langfuse Cloud project (PHI-minimized but PHI-bearing; external under assumed BAA, D5 rev) |
| Session store (Postgres) | session | row deleted on expiry | **a PHI store** — inventoried in §6a; retention + encryption-at-rest owned by deployment |
| OpenEMR `api_log` | every FHIR call | — | **second PHI store** (Full-Logging default, F-S.4/D15) — deployment sets `api_log_option`/retention |

## §4 Trust Boundaries & AuthZ

- **Zone A — OpenEMR:** owns identity, ACL, clinical truth. Nothing bypasses it: the agent has no DB credentials (D9). *Authz reality (D2 rev, F-S.1/F-S.2):* for the agent's patient-scoped tokens, enforcement is **granted scopes + single-patient compartment binding** (the FHIR service overwrites the `patient` param with the server-derived puuid → no cross-patient read), **not** scope∧GACL; OpenEMR's own `checkUserHasAccessToPatient()` is a stub — so the **agent-side session pin (Zone B / D12) is the real clinician↔patient enforcer.**
- **Zone B — Agent service:** trusts only validated OAuth tokens; acts strictly *as* the clinician (no super-user; **never `client_credentials`**, F-S.5; **never the local-API `APICSRFTOKEN` shortcut**, F-S.3 — Bearer path only). Cross-patient queries structurally refused (session pin = the enforcement point). **Prompt-injection enforcement (gap-audit T1):** the crossing where untrusted chart text reaches the LLM is owned input-side by the **EvidencePacket builder** (chart data becomes typed, delimited evidence records — data, not instructions) and backstopped output-side by the **templater + treatment-verb blocklist** (§5); the read-only scope set means worst-case injection yields wrong words, never wrong writes. Injection eval cases required (§8).
- **Zone C — external BAA-covered processors:** the **LLM provider** receives PHI under an assumed BAA (PRD-sanctioned, D4); no training on data; provider abstraction limits coupling. **Langfuse Cloud** (D5 rev 2026-07-08) is the second Zone-C processor: traces egress under the same assumed-BAA posture, PHI-minimized (hashes, not identifiers), project-level retention set; production path = HIPAA data region + signed BAA. Both egress points are inventoried in §6a/D15.
- **Transport (F-S.9):** TLS is Railway-edge-only → the agent **pins `https://` in the FHIR base URL and rejects downgrade.** **Deployment action:** close the Railway MySQL TCP proxy (DEPLOYMENT.md §4.5) before Final — it is a direct-DB path bypassing the entire Zone A FHIR/ACL boundary; nothing but OpenEMR should reach MySQL.
- Secrets: Railway per-service env vars (not in repo; local via non-committed `.env`); rotated post-cohort; API keys never in prompts/logs.

## §5 Verification Layer (D7 v2 — evidence-packet + structured-claims pipeline)

Position: between model output and user flush (streaming: verify each complete claim-block before flushing it).

```
tool results → EvidencePacket (normalized typed records, stable IDs ResourceType:id:hash8)
            → LLM answers in TYPED CLAIMS (MedicationClaim{name,dose,status}, LabValueClaim{...})
              each claim carries evidence_ids into the packet
            → verifier: field-level match, claim vs cited record
              REJECT ON CONTRADICTION, NOT ABSENCE (10mg vs 5mg → reject; both silent → pass)
            → deterministic templater RE-RENDERS display text from verified fields
              (LLM prose discarded if divergent — it cannot phrase past verification)
            → domain constraints + forbidden-phrasing screen + treatment-verb blocklist
            → verdict: pass | flagged | blocked | refused(kind) → flush / honest refusal
```

**Why this is load-bearing, not theater (F-D.1).** The stock FHIR Immunization mapper compares `"completed" == "Completed"` (case-sensitive) and returns **all 67/67 completed vaccines as `status: not-done` + "patient objection"** — verified live. A naïve agent tells the physician the patient refused every vaccine. The layer exists because *the FHIR source is not field-correct*, so display text is re-rendered from verified evidence, never echoed from the model or the raw field.

**Concrete rules the audit forced (D7 rev; each cites its finding):**
1. **FHIR `status` fields are unreliable — never render verbatim** (Immunization inversion F-D.1; Encounter.status hardcoded `finished` F-D.6). Never say "patient declined/refused [X]" or assert encounter state from these fields.
2. **Allergy criticality is null dataset-wide (F-D.4)** — reject any criticality claim; never infer/rank/deprioritize allergy risk from it; constant fields (type/category/status) not asserted.
3. **Empty allergy result → "no allergy records returned; confirm with patient," never "NKDA" (F-D.5).** Absence is the hazard.
4. **Consume ALL conditions; never filter `clinical-status=active` (broken filter returns nothing); reject "no history of X" if an inactive/resolved match exists (F-D.6).**
5. **Flag decade-stale lab dates rather than imply currency; reject valueless observations (F-D.6).**
6. **Empty medication dose → "dose not specified — confirm before dosing," never invent; de-dup MedicationRequest order+plan to one stable ID per drug (F-D.2).**

Design rule: the LLM never computes deltas/arithmetic over clinical data (composites are deterministic tools it only narrates). All serving-path checks are deterministic (auditable, fast, testable as invariants); LLM-as-judge lives only in the eval suite. **Hard-stops (D12):** deceased-indicator pre-flight (keys on `Patient.deceasedDateTime` OR `deceasedBoolean==true`, F-S.7) → deterministic refusal, before any summarization; canonical refusals for ambiguous resolution, wrong-patient, treatment-advice, expired session.

**Known limitations (honest):** field-level match proves *provenance and consistency*, not perfect *synthesis* — a claim can cite and match a real record while emphasizing the wrong thing (covered by golden-answer evals, not the serving path); rule tables are demo-depth, extension path documented. **Label-fallback on absence (D7 addendum 2026-07-09):** when a claim cites a *real* record whose **label** field is empty — a `MedicationRequest` with no `name`, an `Observation` with no `display` — the verifier passes on *absence* (not contradiction, per the core rule) and the templater renders the claim's own label. The sensitive fields (medication `dose`, lab `value`) stay record-sourced regardless (F-D.2), so this never invents a dose or a number; it is the provenance-not-synthesis limitation made concrete for the label only. It is pinned by a regression test (so a change is a conscious decision) and deferred to E6-verifier hardening (render or annotate only a label the cited record actually carries).

## §5a Interface Contracts (gap-audit I1 — strict schemas are the source of truth, PRD)

Every tool and endpoint boundary has a typed Pydantic contract. Worked example:

```
get_recent_labs(input: {patient_id: PatientId, category="laboratory", lookback_days: int}) ->
  EvidencePacket{ records: list[EvidenceRecord{ id: "Observation:{uuid}:{hash8}",
     loinc: str|None, value: float|None, unit: str|None, effective: date|None,
     abnormal_flag: str|None, category: "laboratory"|"vital-signs"|... }] }
```

- **Tools (one line each, all → EvidencePacket record shapes):** `get_patient_summary`→demographics+active problems; `get_active_medications`→MedicationClaim-shaped (dose may be null → rule 6); `get_recent_labs`→LabValueClaim-shaped (category-scoped to prune the 10-way fan-out, F-P.2); `get_encounters`→Encounter records (status non-asserted, rule 1); `get_allergies`→Allergy records (criticality never trusted, rule 2; empty → rule 3); `get_conditions`→all conditions incl. inactive (rule 4); `get_changes_since_last_visit`→deterministic delta over the above.
- **Endpoints:** `POST /chat` → SSE stream of `{claim_block, citations[], verdict}` events; `POST /sessions` → `{session_id}` (pins clinician+patient); `GET /health` → process 200; `GET /ready` → per-dependency body + 503/200 (§7).
- **SMART token exchange:** auth-code + **PKCE S256** (plain refused, F-A.2), scopes `openid launch launch/patient user/Patient.read user/Condition.read user/MedicationRequest.read user/Observation.read user/AllergyIntolerance.read user/Encounter.read`, `grant_type=authorization_code`. The registered app is **disabled until an admin enables it** (D14, F-S.6) — a one-time provisioning step.

## §6 Failure Modes (F3, expanded per gap-audit LIFE-1)

| Failure | Behavior |
|---|---|
| FHIR call fails/times out | Bounded retries → partial answer that *names* what's missing; never silent omission |
| FHIR 401 mid-session (token expired, F-P.5) | Distinguish from a data error → refresh_token grant, else prompt re-launch; do **not** render as a partial data result |
| SMART launch / handshake fails (app disabled, D14) | Explicit "co-pilot not enabled — enable the app in Administration" message; no silent hang |
| LLM down / retries exhausted | Deterministic fallback (D13): EvidencePacket rendered via the templater — grouped, grounded, state-aware, explicit "generated without LLM assistance — records present, synthesis is not" banner; verifier still runs; fallback rate traced + alertable |
| LLM 429 / rate-limited | Backoff within the turn budget **before** falling to D13 |
| Stream interrupted mid-SSE | Mark the response incomplete; UI shows a cut-off marker (verify-then-flush must not present a truncated answer as complete) |
| Deceased indicator on record | Hard-stop before any summarization (D12); deterministic refusal; keyed on `deceasedDateTime`/`deceasedBoolean` (F-S.7 fixture) |
| Ambiguous data resolution | Canonical refusal — "review the chart manually"; never silently answer a different question |
| Verification blocks | Honest "couldn't verify" message; incident logged + metered |
| Session-store unreachable | **Fail-closed** — refuse new/continuing sessions (the pin is the cross-patient guard, F-S.2); never serve unpinned |
| Very large chart (pid=7-class, F-P.3) | Bounded evidence selection + turn cap; note the truncation; size D10 per-call timeout for this worst case |
| Langfuse down | Serving unaffected (observability off the critical path; §7 soft dependency); export buffered/dropped with a counter |
| OpenEMR down | `/ready` fails (hard dependency); agent refuses new sessions with explicit status |
| Railway platform outage | Rollback lever (§7) + documented risk (D8): demo continuity via local compose; production = multi-region/self-host (§9) |

## §6a Source-of-truth ledger (gap-audit S4)

| Datum | Single authority |
|-------|------------------|
| Identity + ACL + clinical truth | **OpenEMR** (Zone A, D9) |
| App-level attribution (client_id + exercised scopes + correlation id) | **Langfuse Cloud** (external, assumed BAA; D5 rev — api_log omits it, F-C.1) |
| Patient id + session pin | **Agent session store** (D12), seeded **once** from the untrusted SMART launch context, never re-derived from client input |
| Verified display text | **Deterministic templater** (§5) — not the LLM's prose |
| Verifier ground truth | **EvidencePacket** (typed records) — not raw FHIR JSON |
| Session validity | `MIN(OAuth token exp, idle timeout, turn cap)` (§3a) |

## §7 Observability & Ops

**Correlation ID everywhere** (§3.1). **Dashboard (Langfuse):** request count, error rate, p50/p95 latency, tool-call counts, retry counts, verification pass/fail rate, token cost per request, + agent-specific (LLM-fallback rate, refusal-kind breakdown).

**Cross-system correlation (D10 rev).** Langfuse is the authoritative agent-side trace and system of record for `{client_id, exercised scopes, correlation_id, user, patient, request_url, utc_timestamp}` per FHIR call. OpenEMR `api_log` correlation is **best-effort/fuzzy** on `(user_id, patient_id, request_url, utc_timestamp)` and weak (every agent call logs the same delegated user_id; api_log omits client_id/scopes, F-C.1/F-C.2). The `X-Copilot-Request-Id` header is still sent (cheap, forward-compatible). **No hard cross-system join is promised.**

**Alerts (≥3, each with runbook — gap-audit G2).** Delivered by the checker script → a **Slack/webhook** channel (stated, not deferred):
| Alert | Threshold | Likely cause | First on-call action | Escalate |
|-------|-----------|--------------|----------------------|----------|
| p95 latency | >15s (R12 — re-baselined at Early from measured data) | LLM latency spike or FHIR N+1 on a pid=7-class chart (F-P.3) | Check Langfuse per-stage spans to localize LLM vs FHIR | Anthropic status + D13 fallback rate; page if sustained >15m |
| Error rate | >5% | FHIR/LLM/dependency failures | Group errors by stage in Langfuse; check `/ready` | Roll back (below) if tied to a deploy |
| Tool-failure rate | >10% | OpenEMR FHIR instability or token expiry | Check OpenEMR health + token refresh path | Investigate api_log; consider fallback |
| LLM-fallback rate (D13) | >X% | Anthropic degraded | Confirm provider status; verify fallback banner is showing | Provider-tier escalation |

**`/health` vs `/ready` (gap-audit G3).** `/health` = process liveness (200). `/ready` classifies dependencies: **HARD** (OpenEMR FHIR metadata, Anthropic API, session store — down ⇒ cannot serve ⇒ **503**) vs **SOFT** (Langfuse — down ⇒ report `degraded` in the body but still **200**, because D13 + §6 keep serving without observability). This satisfies the PRD "ready checks the observability backend" requirement without contradicting §6.

**Deploy & Rollback (gap-audit G1, D8).** Deploy = push to `main` → GitHub Actions runs evals → Railway deploys **only on green** (eval-gate-before-deploy). Rollback = (a) Railway one-click redeploy of any prior deployment, or (b) `git revert` → auto-redeploy. Bad-deploy detection = `/ready` failure + the p95/error-rate alerts above.

**Runnable API collection (Bruno, gap-audit G4).** Repo-committed, env-parameterized, covering `/chat`, `/health`, `/ready`, and sample tool flows. Because SMART EHR-launch is a browser OAuth flow Bruno cannot script unattended, the collection ships a **documented dev-only token-mint helper** (scripted auth-code exchange against the enabled test client, D14) that populates a Bruno environment variable — so a grader can run the authenticated flows end-to-end, not just the health subset.

**Baselines & load (F-P.5).** CPU/mem per service from Railway metrics; latency/throughput under k6 @ **10 and 50 VUs**, p50/p95/p99 + error rate recorded pre-Final. Audit floor: ~0.39s/read live, parallel fan-out ~0.4–0.6s (D10 worth ~1.7–1.9s/brief).

## §8 Evaluation Strategy (production-grade — happy-path-only fails)

pytest + a golden dataset built from the demo patients (location: `agent/evals/`). **EvalCase schema (gap-audit T1):** `{id, category: boundary|invariant|regression|adversarial, input, fixture?, expected, guards: "<failure mode this prevents>", pass_criteria}` — every case names the failure mode it guards (PRD).

- **Boundary:** empty record, missing labs, malformed query, huge chart (pid=7), **tool-failure** (F3 partial-answer).
- **Invariant (one per §5 rule, gap-audit T2, each cites its F-#):** every claim cites (D7); no cross-patient leakage (D12); immunization status never rendered "refused" (F-D.1); empty allergy → "confirm with patient," never NKDA (F-D.5); never rank on null criticality (F-D.4); "no history of X" rejected when an inactive match exists (F-D.6); stale-lab flagged (F-D.6); empty dose → "confirm before dosing," order+plan de-duped (F-D.2).
- **Regression:** pinned outputs for canonical queries.
- **Adversarial:** prompt-injection; unauthorized-data extraction; **guardrail assertions (gap-audit T4)** — the agent never negotiates `client_credentials` (F-S.5) and never sends `APICSRFTOKEN` (F-S.3).
- **Required synthetic fixtures (D12 rev, gap-audit T3):** a **deceased-patient** fixture (audit-only `deceased_date` or mocked `Patient.deceasedDateTime`) exercising the D12 hard-stop (F-S.7); a **no-allergy** fixture exercising rule 3 (F-D.5); an **LLM-failure** fixture exercising the D13 fallback banner; a **FHIR-failure** fixture exercising the F3 partial-answer.

Correctness measured vs FHIR ground truth (deterministic where possible; LLM-judge only where not, pinned). **CI:** evals run per push; deploy gated on green by Early (D8).

## §9 Cost Model (AI Cost Analysis deliverable)

Per-brief ≈ (patient-context tokens × cache economics) + output; **measured from real dev traces** (Langfuse cost tracking) + Railway usage billing — not cost-per-token × n. Prompt caching (90% off cached input, R1) dominates because the stable patient-context prefix is re-sent every turn (D4); two-model split (Sonnet reasoning / Haiku utility) anchors the curve. Scale narrative: **100 users** (one Railway project, as-is) → **1K** (agent replicas, Redis session store, watch usage-based cost) → **10K** (queue-based tool execution, read replicas, **likely exit managed PaaS for dedicated/multi-region** — Railway's outage record + cost curve both argue for it) → **100K** (multi-region, **self-hosted open-weight inference flips economics**, R4; batch pre-computation of morning briefs off-peak). Latency assumption (R12) re-baselined from measured data at Early; the FHIR block is <1s parallel (F-P.5), so the LLM dominates and streaming is the perceived-latency lever.

## §10 Build Order (roadmap → downstream `/tasks-gen`)

1. **MVP (done/now):** local run + Synthea data → OpenEMR on Railway (D8) → AUDIT.md (gate) → USERS.md → this ARCHITECTURE.md. *No agent code — the audit gates it.*
2. **Early:** SMART registration + OAuth flow (enable the app, D14) → tool layer w/ Pydantic contracts (§5a) → orchestrator loop → verification v1 (citations + §5 rules) → Langfuse wired (cloud project created — US region demo posture, D5 rev; traces, correlation IDs, dashboard) → live agent → eval framework v1 with the required fixtures (§8), GH Actions eval-gate → **re-measure latency (R12)** → demo video.
3. **Final:** verification v2 (full constraint rules) → dashboard + alerts + runbooks (§7) → Bruno collection + token-mint helper → k6 load tests + baselines → cost analysis from real traces (§9) → close the audit deploy items (https-pin, MySQL proxy, api_log retention) → eval suite full → demo video + social post.

## §11 Submission checklist (gap-audit G5 — runtime deliverables owned)

Per checkpoint (so no graded artifact is dropped): **deployed URL string** in the submission; **demo video (3–5 min)** built around the 2–3 key decisions (D2 sidecar, §5 verification justified by F-D.1, D5 accountability); **README setup guide** (links DEPLOYMENT.md); **eval results export**; **AI cost analysis** (§9); **(Final) GauntletAI social post**. Each build wave (§10) ends by ticking its submission artifacts.

## Open items (carried to `CLAUDE_CODE_HANDOFF.md`; next step = /tasks-gen)

- **O1** UI embed: SMART launch in new tab (default) vs iframe polish — resolve during Early build.
- **O2** Session store Postgres vs Redis — default Postgres; revisit if latency demands.
- **R12** latency anchor is an unverified assumption — **replace with measured Langfuse data at Early** (the one number in this doc awaiting real measurement).
- **Known tension (revised with D5 rev 2026-07-08)** Langfuse Cloud is a vendor dependency for the HIPAA accountability record — owned via the MIT self-host migration exit and project-level retention; alert delivery stays checker→webhook (§7) against the cloud API.
- **Deploy actions before Final** (from the audit): pin `https://` (F-S.9), close the Railway MySQL TCP proxy (F-S.9), set `api_log_option`/retention (F-S.4/D15).
