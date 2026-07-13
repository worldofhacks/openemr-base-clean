# ARCHITECTURE_DRAFT.md — Clinical Co-Pilot (rough draft, Brain 1)

> Status: rough draft for adversarial finalization (`/arch-finalize` → repo-root `ARCHITECTURE.md`, which must open with a ~500-word summary per PRD).
> Posture: **production-grade**. Mode: Default. Decisions referenced as D# (DECISIONS.md).
> **Superseded note (2026-07-08):** this draft predates the D5 revision — Langfuse is now **Langfuse Cloud under an assumed BAA**, not self-hosted in the Railway project. Every "Langfuse (self-hosted)" reference below is historical; the binding text is repo-root `ARCHITECTURE.md` + DECISIONS.md D5 revision 2026-07-08.

## §1 System Overview

```
┌───────────────────── Railway project (managed TLS, deploy-on-push) ─────────────────┐
│                                                                                      │
│  ┌─────────────── OpenEMR ───────────────┐        ┌────────── Agent Service ───────┐ │
│  │  PHP/Laminas + Railway MySQL          │ SMART  │  FastAPI (Python, Pydantic)    │ │
│  │  • Patient charts (UI)                │ launch │  • /chat (SSE streaming)       │ │
│  │  • OAuth2/OIDC server                 │◄──────►│  • Orchestrator (tool loop)    │ │
│  │  • FHIR R4 API ◄ scope+compartment    │ FHIR   │  • Verification gate           │ │
│  │  • volume: sites/ state               │ + token│  • Session store (Postgres)    │ │
│  └───────────────────────────────────────┘        │  • /health /ready              │ │
│                                                   └───────┬──────────────┬─────────┘ │
│  ┌── Langfuse (self-hosted svc group) ──┐                 │              │           │
│  │ traces • dashboards • costs • evals  │◄──── traces ────┘              │           │
│  │ (pg + clickhouse + redis services)   │                        Claude API (BAA)   │
│  └──────────────────────────────────────┘                        Sonnet 4.6 + Haiku │
└──────────────────────────────────────────────────────────────────────────────────────┘
```
One Railway project (D8), three trust zones (§4). Agent is an OAuth2/SMART client of OpenEMR (D2); all patient reads via FHIR with the clinician's delegated token (D9); all LLM calls via provider abstraction (D4); every request fully traced to Langfuse deployed inside the same project (D5). Local dev = Docker Compose (dev-easy); prod = Railway services.

**Non-goals (D12, explicit):** no diagnosis, no treatment recommendations, no prescribing or ordering, no patient messaging, no chart writes, no cross-patient search, no write scopes. The agent is read-only by construction.

## §2 Components

- **OpenEMR (fork):** unmodified except (a) SMART app registration, (b) launch affordance on patient chart (link/button — smallest possible diff). It remains the source of truth for identity, authorization, clinical data, and its own audit log.
- **Agent service (FastAPI):** routes `/chat` (SSE), `/sessions`, `/health`, `/ready`, `/metrics-lite`. Internals: SMART/OAuth client (code flow w/ PKCE; token cache per session), tool registry (Pydantic-contracted FHIR tools: `get_patient_summary`, `get_active_medications`, `get_recent_labs`, `get_encounters`, `get_allergies`, `get_conditions`, plus deterministic composites like `get_changes_since_last_visit` — computed by code, narrated by the LLM), **EvidencePacket builder** (normalizes tool results into evidence records w/ stable IDs — the only thing the LLM and verifier see), orchestrator (direct Anthropic tool-use loop, D6; **concurrent fan-out of independent tool calls, D10**; per-session FHIR TTL cache; outbound `X-Copilot-Request-Id` header on every FHIR call), verification gate + **deterministic templater** (§5), session store (Postgres, D-O2; session pinned to clinician+patient at creation, D12).
- **Langfuse stack:** langfuse-web/worker + Postgres + ClickHouse + Redis as Railway services (template-based); dashboards + alert checker (§7). Fallback if service sprawl bites: Langfuse Cloud free tier — `scope simplification`, acceptable only under demo-data rule.
- **Edge/TLS:** Railway-managed domains + HTTPS per service (SMART/OAuth requires HTTPS — comes free here); no reverse proxy to maintain.

## §3 Request Lifecycle (happy path, F1)

1. Launch: clinician clicks Co-Pilot on chart → SMART EHR launch → OAuth2 code+PKCE → agent receives token w/ granular scopes + `launch/patient` context. Session row created; **correlation ID minted here**, propagated to every log line, tool call, LLM call, trace span (engineering rqmt).
2. Brief generation: orchestrator runs the summary tool plan — **all independent FHIR reads fan out concurrently (D10): wall-clock ≈ slowest call, not the sum; per-call timeout + total turn budget; dependent chains sequential only where data requires** → prompt assembly (stable patient-context prefix → **prompt-cache hit on every subsequent turn**, D4) → Sonnet 4.6 streams structured response with inline citation tags.
3. Verification gate (§5) validates citations + constraints **before** flush to client; violations block/downgrade.
4. Response streams w/ citation chips; trace (steps, order, per-step latency, tokens, cost, verification verdict) lands in Langfuse.
Follow-ups (F2): same loop, session context + cache; new FHIR calls only on demand.

## §4 Trust Boundaries & AuthZ

- **Zone A — OpenEMR:** owns identity, ACL, clinical truth. Nothing bypasses it: agent has no credentials to OpenEMR's database (MariaDB locally / Railway MySQL in prod) (D9). *Authz reality (D2 rev. 2026-07-07, F-S.1/F-S.2):* for the agent's patient-scoped SMART tokens, enforcement is **granted scopes + single-patient compartment binding** (the FHIR service overwrites the `patient` param with the server-derived puuid → no cross-patient read), **not** scope∧GACL — the ACL loop is skipped on the patient branch, and OpenEMR's own `checkUserHasAccessToPatient()` is a stub returning `true`. The agent's ceiling is the *granted scope set*; the clinician↔patient guarantee is enforced by the agent-side session pin (Zone B / D12), not by an inherited server check.
- **Zone B — Agent service:** trusts only validated OAuth tokens; acts strictly *as* the clinician (no service super-user; never `client_credentials` — that attributes to the synthetic `oe-system` user, F-S.5). Cross-patient queries structurally refused (session pinned to launch patient — the real enforcement point). Never uses OpenEMR's same-session local-API shortcut (Bearer path only; the local-API path skips OAuth scope checks, F-S.3). Prompt-injection stance: retrieved chart text is data, not instructions (delimited, system-prompt hardened); tool surface is read-only so worst-case injection yields wrong words, not wrong writes; injection eval cases required.
- **Zone C — LLM provider:** receives PHI under assumed BAA (PRD-sanctioned); no training on data; provider abstraction limits coupling.
- Langfuse sits inside Zone B's boundary (self-hosted — the point of D5).
- Secrets: Railway environment variables (per-service, not in repo; local dev via non-committed `.env`), rotated post-cohort; API keys never in prompts/logs.

## §5 Verification Layer (D7 v2 — evidence-packet + structured-claims pipeline)

Position: between model output and user flush (streaming: verify per complete claim-block before flushing block).

```
tool results → EvidencePacket (normalized records, stable IDs: ResourceType:id:hash8)
            → LLM answers in TYPED CLAIMS (MedicationClaim{name,dose,status}, LabValueClaim{...})
              each claim carries evidence_ids into the packet
            → verifier: field-level match, claim vs cited record
              REJECT ON CONTRADICTION, NOT ABSENCE (10mg vs 5mg → reject; both silent on dose → pass)
            → deterministic templater RE-RENDERS display text from verified fields
              (LLM's own prose discarded if divergent — it cannot phrase its way past verification)
            → domain constraints (allergy-vs-prescription, dosage bounds, stale-lab flags)
              + forbidden-phrasing screen (empty allergy result ≠ "NKDA"; no "labs are normal" w/o support)
              + treatment-verb blocklist (start/stop/increase/prescribe/order/diagnose → refuse)
            → verdict: pass | flagged | blocked | refused(kind) → flush / honest refusal
```

Design rules: the LLM never computes deltas or arithmetic over clinical data — composite questions like "what changed since last visit" are answered by a **deterministic tool** whose output the LLM only narrates. All checks deterministic in the serving path (auditable, fast, testable as invariants); LLM-as-judge lives only in the eval suite. Verdicts + refusal kinds logged per response, dashboarded, alertable.

**Concrete verifier & phrasing rules (D7 rev. 2026-07-07, audit-derived — the abstract rules made specific).** These are not hypothetical; each was proven against the live data:
1. **FHIR `status` fields are unreliable — never render verbatim.** The Immunization mapper's case-sensitive `"completed" == "Completed"` bug returns all 67/67 completed vaccines as `not-done` + `patient objection` (F-D.1); Encounter.status is hardcoded `finished` (F-D.6). The templater must never say "patient declined/refused [X]" from these fields. **F-D.1 is the concrete justification for this entire layer** — a naïve agent tells the physician the patient refused every vaccine.
2. **Allergy criticality is null dataset-wide (F-D.4) — reject any criticality claim; never infer/rank/deprioritize allergy risk from it. Constant fields (type/category/status) not asserted.**
3. **Empty allergy result → "no allergy records returned; confirm with patient," never "NKDA"/"no known allergies" (F-D.5).** OpenEMR has no NKDA record type; absence is the hazard.
4. **Consume ALL conditions; never send `clinical-status=active` (broken filter returns nothing); reject "no history of X" if an inactive/resolved match exists (F-D.6).**
5. **Flag decade-stale lab dates rather than imply currency; reject valueless observations (F-D.6).**
6. **Empty medication dose → "dose not specified — confirm before dosing," never invent; de-dup MedicationRequest order+plan to one stable ID per drug (F-D.2).**

**Hard-stops & refusals (D12):** deceased-indicator pre-flight before any summarization → deterministic refusal; canonical refusal messages for ambiguous resolution, wrong-patient, treatment-advice, expired session. Refusal is a feature: the confidently-wrong answer is the failure mode that kills clinical trust.

**Known limitations (documented honestly):** field-level match proves *provenance and consistency*, not perfect *synthesis* — a claim can cite and match a real record while emphasizing the wrong thing (covered by golden-answer evals, not the serving path); rule tables are demo-depth, not a clinical knowledge base — extension path documented.

## §6 Failure Modes (F3)

| Failure | Behavior |
|---|---|
| FHIR call fails/times out | Bounded retries → partial answer that *names* what's missing; never silent omission |
| LLM down | Deterministic fallback (D13): EvidencePacket rendered via the templater — grouped, grounded, state-aware phrasings, explicit "no LLM synthesis" banner; verifier still runs; fallback rate traced + alertable |
| Deceased indicator on record | Hard-stop before any summarization (D12): deterministic refusal, all use cases |
| Ambiguous data resolution (e.g., can't identify prior visit) | Canonical refusal — "review the chart manually"; never silently answer a different question |
| Verification blocks | User sees honest "couldn't verify" message; incident logged + metered |
| Langfuse down | Serving unaffected (observability off critical path; export buffered/dropped w/ counter) |
| Empty/sparse record | Say exactly that (boundary eval case) |
| OpenEMR down | /ready fails; Railway healthcheck surfaces it; agent refuses new sessions with explicit status |
| Railway platform outage | Documented risk (D8): demo continuity via local compose; production answer = multi-region/self-host (cost analysis) |

## §7 Observability & Ops (engineering rqmts mapped)

Correlation ID everywhere (§3.1) • Langfuse dashboard: request count, error rate, p50/p95 latency, tool-call counts, retry counts, verification pass/fail rate, token cost per request • **Alerts (≥3, documented w/ on-call response):** p95 latency > 15s initial threshold — *re-baselined from measured data at Early; prior-art measured p50 ≈28s end-to-end with LLM ≈85% of wall-clock, so the honest latency story is streamed perceived latency (first tokens ~2–3s) + per-stage budgets, not raw completion time* — error rate > 5%, tool-failure rate > 10%, plus LLM-fallback rate (D13) — checker script queries Langfuse API on interval → notification; runbook entries in repo • /health (process) vs /ready (checks OpenEMR FHIR metadata endpoint, Anthropic API, Langfuse, session-store — real checks, no unconditional 200; wired to Railway healthchecks) • Baselines: CPU/mem per service from Railway metrics + latency/throughput under k6 scenarios @ 10 & 50 VUs, p50/p95/p99 + error rate recorded pre-Final • API collection: Bruno (repo-committed, env-parameterized) covering launch-token exchange, /chat, /health, /ready, sample tool flows.

**Cross-system correlation (D10 rev. 2026-07-07, audit F-C.1/F-C.2/F-A.5/F-P.6 — REQUIRED correction).** The earlier draft assumed the correlation ID would *join* OpenEMR's `api_log`. It cannot: `api_log` has no correlation column and no code path persists an inbound header, and it omits `client_id` + granted scopes entirely. Corrected design: **Langfuse (D5) is the authoritative agent-side trace and the system of record** for `{client_id, exercised scopes, correlation_id, user, patient, request_url, utc_timestamp}` per FHIR call — this is now also a HIPAA §164.312(b) accountability control, since OpenEMR's audit trail can't attribute *which app under which grant* accessed PHI. api_log correlation is **best-effort/fuzzy** on `(user_id, patient_id, request_url, utc_timestamp)` and weak (every agent call logs the same delegated `user_id`, and `patient_id` was `0` for user-role tokens). The agent still emits `X-Copilot-Request-Id` (cheap, forward-compatible). §7/dashboard must not promise a hard cross-system join.

**PHI-store inventory (D15, audit F-S.4/F-C.3).** OpenEMR's `api_log` defaults to Full Logging (`api_log_option=2`), persisting full FHIR response bodies unencrypted — a **second in-boundary PHI store** the D10 fan-out writes to every turn. The deployment makes an explicit `api_log_option`/retention decision; the compliance section enumerates api_log alongside Langfuse and the LLM channel with named incident-response owners.

**Deployment provisioning (D14, audit F-S.6).** The agent's `user/*`-scoped SMART client registers **disabled** — the runbook includes a one-time "enable app in Administration → API Clients" step before any token flow, or first-run fails with a silent client-disabled 401.

## §8 Evaluation Strategy

pytest + eval dataset (golden set built from demo patients). Every case tagged **boundary** (empty record, missing labs, malformed query, huge chart) / **invariant** (every claim cites; no cross-patient leakage; allergy-contradiction always flagged; refusal on out-of-scope) / **regression** (pinned outputs for canonical queries) — each documents the failure mode it guards (PRD: happy-path-only fails). Adversarial set: prompt-injection attempts, unauthorized-data extraction attempts. Correctness measured vs FHIR ground truth (deterministic where possible, LLM-judge where not). CI: evals run per push; gate deploy by Early submission (D8).

**Synthetic-fixture requirement (D12 rev. 2026-07-07, audit F-S.7/F-D.5).** Two safety-critical paths are untestable against the alive-only, allergy-bearing Synthea set and MUST get injected fixtures or they ship unverified: (a) a **deceased-patient fixture** (audit-only `deceased_date`, or a mocked `Patient.deceasedDateTime`) exercising the D12 deterministic-refusal hard-stop; (b) a **no-allergy-record fixture** exercising the empty-allergy → "confirm with patient" forbidden-phrasing rule (D7 rev. rule 3). Also invariant-test the D7 status rules: an immunization case asserting the agent never renders "patient refused" from the inverted FHIR status (F-D.1), and a resolved-condition case asserting "no history of X" is rejected when an inactive match exists (F-D.6).

## §9 Cost Model (skeleton for AI Cost Analysis deliverable)

Per-brief ≈ (patient-context tokens × cache economics) + output; measure real numbers from dev traces (Langfuse cost tracking = actual dev spend) + Railway usage billing. Scale narrative: 100 users (one Railway project, as-is) → 1K (agent replicas, Redis session store, watch usage-based cost curve vs dedicated infra) → 10K (queue-based tool execution, read replicas, **likely exit managed PaaS for dedicated/multi-region infra — Railway's outage record and cost curve both argue for it at this tier**, negotiated provider tier) → 100K (multi-region, dedicated inference — self-hosted open-weight models flip economics, R4; batch pre-computation of morning briefs off-peak). Explicitly not cost-per-token × n: caching rates, model mix, pre-computation, and infra step-changes dominate.

## §10 Build Order (roadmap → IMPLEMENTATION_PLAN.md downstream)

1. **MVP (Tue):** local run + sample data → deploy OpenEMR to Railway (image + volume + managed MySQL; pathfinding timeboxed per D8) → AUDIT.md (hard gate) → USERS.md → finalize ARCHITECTURE.md from this draft. *No agent code yet — the audit gates it.*
2. **Early (Thu):** SMART registration + OAuth flow → tool layer w/ Pydantic contracts → orchestrator loop → verification v1 (citations) → Langfuse services on Railway wired (traces, correlation IDs) → deployed live agent → eval framework v1 (GH Actions; Railway waits on checks) → demo video.
3. **Final (Sun):** verification v2 (constraint rules) → dashboard + 3 alerts + runbooks → Bruno collection → k6 load tests + baselines → cost analysis from real traces → eval suite full → polish, demo video, social post.

## Open items for finalize pass
O1 (UI embed detail), O2 (session store), O3 (submission portal/GitLab), demo-data richness (PRESEARCH OQ4), Railway cost trajectory + OpenEMR pathfinding timebox (PRESEARCH OQ5–6), streaming-vs-verification interaction (verify-then-flush granularity), Langfuse alert delivery channel.
