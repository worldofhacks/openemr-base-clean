# Architecture Proposal

I designed this system around a narrow user: a primary care physician with a 20-patient day who often has only 90 seconds between rooms to understand the next patient. The agent is not a generic medical chatbot and not an AI doctor. It is a source-grounded Clinical Co-Pilot embedded into OpenEMR to help the physician answer four practical pre-visit questions: who is this patient, why are they here, what changed since the last visit, and what should I review before entering the room? The user choice is verified, not assumed: OpenEMR is an outpatient practice-management system at its core, and its own sanctioned demo-data path is Synthea — which is explicitly modeled on primary-care encounters. This is the only persona I can serve end-to-end with real data instead of fabricated data.

The architecture has three parts. First, OpenEMR remains the system of record and the authorization authority. Second, a minimal launch surface inside the patient chart starts the Co-Pilot via SMART-on-FHIR EHR launch. Third, a separate agent service — the SMART sidecar — handles orchestration, FHIR retrieval, LLM calls, verification, observability, and evals.

I intentionally kept OpenEMR changes minimal: a SMART app registration and a launch button. I am not rewriting OpenEMR, replacing its workflows, or giving the model database access. The integration pattern is not just clean — it is the one US regulation mandates: ONC certification § 170.315(g)(10) requires certified EHRs to expose FHIR R4 with SMART launch, which means this agent is portable to any certified EHR, not just OpenEMR.

For the agent service, my stack is Python FastAPI with Pydantic schemas and a direct Anthropic SDK tool-use loop — no agent framework. The brief requires a multi-turn agentic chatbot that invokes tools, so the loop is a requirement, not a style choice; what I refused was hiding it inside a framework. One agent, six read-only tools, a loop I can explain line by line. Pydantic contracts on every tool input and output satisfy the schema requirement and feed the verifier.

For data access, the agent uses whitelisted, read-only, typed tools backed by OpenEMR's FHIR R4 API, called with the clinician's own delegated token. Standard REST is a documented fallback only if FHIR lacks a workflow field. Direct database access is allowed during the audit as research — never as the runtime path, because it would bypass OpenEMR's ACL and forfeit the entire trust-boundary story.

For the LLM, I chose Claude Sonnet 4.6 as primary with Haiku 4.5 for cheap utility calls, behind a thin provider seam. This is a researched decision, not a default: Sonnet is roughly 40–50% cheaper than the equivalent-capability alternative, and its 90%-off prompt caching matches my exact traffic shape — the same patient-context prefix re-sent on every turn of a session. The provider seam keeps the swap-out story real. Regardless of provider, the LLM is never trusted as a source of truth.

The most important layer is deterministic verification. Tool results are normalized into an evidence packet with stable evidence IDs — the packet is the only thing the model sees. The model must answer in typed, structured claims — a medication claim is fields: name, dose, status — each carrying evidence IDs. The verifier does field-level comparison against the cited record: reject on contradiction, not absence. Then the text the physician reads is re-rendered deterministically from the verified fields; the model's own prose is discarded if it diverges. The model drafts; the verifier decides; the model cannot phrase its way past verification.

Observability and evals are built in from day one. Every request gets a correlation ID at birth that rides every log line, tool call, and LLM span — and travels to OpenEMR as an `X-Copilot-Request-Id` header so traces join across systems. Traces land in Langfuse Cloud under an assumed BAA — the same PRD-sanctioned posture as the LLM provider — and are PHI-minimized to hashes, not identifiers; the named production path is Langfuse's dedicated HIPAA data region with a signed BAA before any real PHI. Evals are pytest-based and deterministic; every case is tagged boundary, invariant, or regression — happy-path-only suites explicitly fail this brief — and an adversarial set is hand-built to try to beat my own verifier.

For deployment, I chose Railway: one project containing OpenEMR (Docker image plus volume), managed MySQL, and the agent service — observability is Langfuse Cloud, so no observability services to operate. In a one-week solo sprint, engineering hours are the scarcest resource, and Railway converts ops — TLS, domains, deploys, per-service metrics — to zero. The tradeoffs are documented, not hidden: no known prior art for OpenEMR on Railway (timeboxed pathfinding, with local-compose parity for continuity), a real 2025–26 outage record (the production answer is exiting managed PaaS at scale, and it's in my cost analysis), and usage-based cost monitored from day one. Local dev remains Docker Compose for parity. This is demo-data-only; I would not represent it as production-ready for real PHI.

The architecture is intentionally conservative: keep OpenEMR authoritative, add AI as an isolated read-only sidecar, retrieve minimum necessary data with the user's own authority, verify every clinical claim field-by-field, fail closed on authorization and patient-context errors, refuse rather than guess when data is ambiguous, and stay transparent when data is missing or tools fail.

---

# 1. Clinical Co-Pilot for OpenEMR — Architecture

**Project:** AgentForge Clinical Co-Pilot
**Foundation:** OpenEMR open-source EHR (Gauntlet-HQ fork)
**Target User:** Outpatient primary care physician, 20-patient day, 90 seconds between rooms
**Workflow:** Pre-visit chart review between back-to-back appointments
**MVP Posture:** Read-only, source-grounded, least-privilege, physician-facing
**Core Architecture:** OpenEMR + SMART EHR launch + separate FastAPI agent service + FHIR read-only tools + evidence-packet verification pipeline + self-hosted observability

---

# 2. Architecture at a Glance

```
OpenEMR Patient Chart
        ↓  (Co-Pilot launch button)
SMART EHR Launch — OAuth2 code + PKCE
        ↓  (who is asking + which patient is open)
FastAPI Agent Service  ← correlation ID minted here
        ↓
Token / Patient-Context Validation → deceased hard-stop pre-flight
        ↓
Typed Read-Only FHIR Tools — PARALLEL fan-out (cost = slowest call)
        ↓
Normalized EvidencePacket (stable evidence IDs)
        ↓
Claude Sonnet 4.6 (cached patient prefix) → TYPED CLAIMS + evidence_ids
        ↓
Deterministic Verifier — field-level match, reject on contradiction
        ↓
Templater re-renders display text from verified fields
        ↓
Constraint + phrasing rules → pass | flag | block | refuse(kind)
        ↓
Source-cited streamed response → Physician in OpenEMR
        ↓
Trace → Langfuse Cloud, assumed BAA (tokens, cost, latency, verdicts)
```

**Core principle:** OpenEMR is the source of truth. The LLM is a drafting component, not the authority. The verifier decides.

---

# 3. Selected Stack

| Layer | Selected Choice | Why |
| --- | --- | --- |
| EHR foundation | OpenEMR fork | Required; real brownfield EHR |
| Integration | SMART-on-FHIR EHR launch (sidecar) | Federally mandated pattern — ONC §170.315(g)(10); authz inherited from OpenEMR |
| Agent backend | Python FastAPI | Async parallel tool calls, typed routes, trivial /health //ready, testable |
| Schemas | Pydantic (v2) | PRD's own named contracts tool; validates tool I/O, claims, evidence |
| Agent layer | Direct Anthropic SDK tool-use loop | PRD requires an agentic multi-turn chatbot; loop stays explicit, explainable line-by-line |
| Data access | OpenEMR FHIR R4 API w/ user-delegated token | Preserves ACL + audit; Standard REST documented fallback only |
| DB access | None at runtime | Direct SQL would bypass authorization; audit-research only |
| LLM | Claude Sonnet 4.6 + Haiku 4.5, thin provider seam | ~40–50% cheaper than equivalent class; 90% prompt-cache discount matches traffic shape |
| Verification | EvidencePacket → typed claims → field-match → templater | Deterministic safety gate; model prose never reaches the screen |
| Observability | Correlation IDs + Langfuse Cloud (assumed BAA) | Same BAA posture as the LLM provider; PHI-minimized traces; dashboards, cost, verdicts |
| Evals | pytest, boundary/invariant/regression-tagged + adversarial | Happy-path-only fails the brief; evals gate deploys |
| API collection | Bruno (repo-committed) | Graders run workflows without reading source |
| Load testing | k6 @ 10/50 concurrent users | p50/p95/p99 + error rate baselines required |
| Deployment | Railway (one project, all services) | Ops → zero in a one-week sprint; deploy-on-green; one-click rollback |
| Local dev | Docker Compose (dev-easy) | Parity with upstream OpenEMR tooling |
| Secrets | Railway env vars; non-committed .env locally | Never in repo, prompts, or logs |

---

# 4. Stack Decisions and Alternatives

## 4.1 OpenEMR Integration Strategy

**Chosen: SMART-on-FHIR sidecar — separate agent service, launch from the chart.**
Why: authorization is enforced by OpenEMR's own certified OAuth2/SMART scopes and ACL — the agent physically cannot read more than the launching clinician; blast radius is contained (agent crashes, EHR doesn't); the pattern is the ONC-mandated standard, so the agent ports to any certified EHR; and the agent's language/tooling is decoupled from legacy PHP.

**Not chosen: embedded PHP module with internal data access.** Fastest data path, but I would own authorization correctness myself inside a legacy codebase, the blast radius becomes the EHR, and I'd be locked to the weakest agent-tooling ecosystem. Its single advantage (latency) is the one thing I can mitigate — caching and parallel calls.

**Not chosen: standalone external app.** The brief requires embedding into OpenEMR; physicians shouldn't leave the EHR for pre-visit context.

## 4.2 Agent Service Framework

**Chosen: Python + FastAPI + Pydantic.** ASGI-native async is precisely the parallel fan-out mechanism; Pydantic v2 (Rust-core validation) is the PRD's own named schema tool; production-proven at scale, common in healthcare backends.
**Considered: TypeScript (Hono/Express + Zod).** Equally valid; marginally thinner eval tooling; no fluency advantage here.
**Not chosen: PHP inside OpenEMR.** See 4.1.

## 4.3 Agent Framework

**Chosen: direct Anthropic SDK tool-use loop, no framework.** The 2026 framing is "who owns the loop." This is a single agent, single provider, ~6 read-only tools, linear retrieve→draft→verify. Every abstraction between user and model is one I must defend; a plain loop keeps retrieval, ordering, and failure handling as explicit application code. Two deliberate determinism rules on top: composite questions ("what changed since last visit") are computed by deterministic tools the LLM only narrates, and the verifier — not the prompt — is the safety boundary.
**Not chosen: CrewAI / multi-agent.** The workflow is short-lived and focused; agent teams add hidden reasoning paths and verification surface without user value.
**Not chosen: LangChain agent loop.** The highest-risk parts — authorization, evidence construction, verification — must be explicit code, not framework internals.
**Deferred: LangGraph.** Right tool for durable stateful multi-step workflows; this MVP isn't one. The tool-registry/orchestrator seam is kept clean so weeks 2–3 can migrate if requirements change.
**Not chosen: vector index / RAG over PHI.** Structured FHIR data serves the use cases; vectorizing PHI adds retention, deletion, and access-control burden with no MVP payoff.

## 4.4 LLM Provider and Model Strategy

**Chosen: Claude Sonnet 4.6 primary, Haiku 4.5 utility, thin provider seam.** Researched, not defaulted: Sonnet 4.6 at $3/$15 per million tokens vs GPT-5.5 at $5/$30 — equivalent capability class for structured tool use at ~40–50% less; 90%-off prompt caching matches the exact traffic shape (stable patient-context prefix re-sent every turn); 200K-class context fits full patient summaries. Haiku ($1/$5) takes cheap structured utility calls. The PRD sanctions an assumed BAA with any provider, so this is capability + cost, not compliance.
**Considered: GPT-5.5.** Equally capable; loses on price and cache economics for this shape.
**Considered: self-hosted open-weight.** Maximum data control, but no GPU in this deployment, unproven clinical tool-use reliability inside one week. Revisited at the 100K-user tier in the cost analysis, where dedicated inference flips the economics — that's the "architectural change at scale," not a per-token multiplication.
**Provider seam:** one internal `llm.complete()` interface so the swap is a config change; verification is provider-independent by design.

## 4.5 Data Access Strategy

**Chosen: FHIR R4 first (and only, at runtime), with the clinician's delegated token.** Preserves OpenEMR's authorization on every read; healthcare-standard resources (Patient, Appointment, Encounter, Condition, MedicationRequest, AllergyIntolerance, Observation, DiagnosticReport, DocumentReference); portable beyond OpenEMR.
**Documented fallback: OpenEMR Standard REST** — only where FHIR lacks a workflow field; still behind OpenEMR's API/auth layer; each use recorded as a decision.
**Not chosen at runtime: direct database access.** Bypasses ACL and business logic, needs broad credentials, couples to internal schema, and forfeits the entire §4.1 defense. Allowed during audit as read-only research only.
**Never: `client_credentials` for clinical reads.** A system token collapses provider attribution — every read must be traceable to the clinician who asked.

## 4.6 Tool Design

**Chosen: whitelisted, read-only, typed tools.** Pydantic contracts on every input/output; least privilege; testable; observable.

| Tool | Purpose |
| --- | --- |
| `get_patient_summary` | Identity + demographics; runs deceased hard-stop pre-flight |
| `get_active_conditions` | Active problem list |
| `get_active_medications` | Active medication records |
| `get_allergies` | Documented allergies — empty result ≠ "no known allergies" |
| `get_recent_labs` / vitals | Observations with category filters + lookback windows |
| `get_encounters` | Recent visits, last completed encounter |
| `get_changes_since_last_visit` | Deterministic composite — computed by code, narrated by LLM |
| `build_previsit_evidence_packet` | Composite for the core use case |

**Intentionally excluded:** write/update tools, prescribing, ordering, messaging, diagnosis, treatment recommendation, arbitrary SQL/HTTP/shell, cross-patient search, web search. They expand clinical and security risk without serving pre-visit review.

## 4.7 Evidence and Verification Stack

**Chosen: EvidencePacket → typed claims → field-level verify → deterministic templater.**
- EvidencePacket: every tool result normalized to evidence records with stable IDs (`ResourceType:id:hash8`); the packet is the only model input and the only verification referent.
- Typed claims: `MedicationClaim{name, dose, status}`, `LabValueClaim{loinc, value, unit, date}`, etc., each with `evidence_ids`.
- Field-level verification: **reject on contradiction, not absence** — silent-on-dose both sides passes; 10mg-vs-5mg dies.
- Templater: physician-visible text re-rendered from verified fields; divergent LLM prose discarded. Missing fields render "not specified in record," never invented.
- Constraint + phrasing rules: allergy-vs-prescription contradiction, dosage bounds, stale-lab flags; empty allergy result must render "no allergy records returned — confirm with patient," never "NKDA"; treatment verbs (start/stop/increase/prescribe/order/diagnose) trigger refusal.

**Not chosen: freeform prose with citation tags (my own v1).** Citations attach incorrectly, subclaims can't be individually blocked, and text matching is weaker than field matching. Upgraded after prior-art review.
**Not chosen: LLM-as-judge in the serving path.** A second probabilistic system can agree with fluent hallucinations; judges live in the eval suite only.

## 4.8 Observability Stack

**Chosen: correlation IDs everywhere + Langfuse Cloud under an assumed BAA.**
Every request: ID minted at session start, on every log line, tool call, LLM span — and propagated to OpenEMR as `X-Copilot-Request-Id`, so agent traces join the EHR's own API log. Langfuse offers a BAA — a dedicated HIPAA data region (Pro plan+, signed BAA) — so the observability vendor sits in the same assumed-BAA posture as the LLM provider; demo runs on the cloud free tier under the demo-data-only rule, and the MIT self-host migration path is the documented exit if vendor terms change. Langfuse dashboards: request count, error rate, p50/p95 latency, tool calls, retries, verification pass/fail, token cost per request. **Alerts (≥3, runbook-documented):** p95 latency (threshold re-baselined from measured data), error rate > 5%, tool-failure rate > 10%, plus LLM-fallback rate.
**Not chosen: self-hosted Langfuse (my own v1 of this decision).** Once the vendor offers a BAA, self-hosting buys no compliance advantage and costs a four-service stack (web/worker + Postgres + ClickHouse + Redis) plus its ops in a one-week sprint.
**Not chosen: LangSmith.** Per-trace pricing scales badly (~$2.5K/mo at 1M traces) and it's LangChain-shaped.
**Not chosen: Braintrust.** Best-in-class evals but closed SaaS at $249/mo with no equivalent BAA story at that tier; our evals are pytest + Langfuse datasets.
**Not logged by default:** raw FHIR payloads, full prompts/responses, tokens/keys/secrets, patient identifiers in traces beyond hashes.

## 4.9 Eval and Testing Stack

**Chosen: pytest + fixture-based deterministic harness, three classes.**
- Mock-tool evals (fast, every push): LLM + verifier behavior over deterministic tool outputs.
- Live-fixture evals (against seeded demo patients): catch tool-level regressions — wrong filter, wrong field.
- Adversarial verifier evals: hand-built DraftAnswers designed to beat the verifier — claim text contradicting structured fields, valid-ID-wrong-category, treatment advice wrapped in "consider reviewing whether to increase," wrong-patient evidence, prompt-injection payloads in notes. A safety boundary you haven't tried to break is one you don't trust.
Every case is tagged **boundary** (empty record, malformed query, huge chart), **invariant** (every claim cites; no cross-patient leakage; allergy contradiction always flagged), or **regression** — each documents the failure mode it guards. Happy-path-only suites explicitly fail this brief.
**CI:** tests + eval subset gate every deploy (Railway waits on green); secret scan + dependency audit included.

## 4.10 Deployment Stack

**Chosen: Railway — one project: OpenEMR (image + volume), managed MySQL, agent service. (Langfuse is cloud-hosted — no observability services to run.)**
Why: in a one-week solo sprint the scarcest resource is engineering hours; Railway zeroes out TLS, domains, deploy pipeline, DB provisioning, per-service metrics — and SMART/OAuth requires HTTPS everywhere, which comes free. Deploy-on-push waits on CI checks → the eval gate survives. Rollback is one click to a previous deployment.
**Tradeoffs owned out loud:** no known OpenEMR-on-Railway prior art (timeboxed pathfinding; local-compose parity keeps the demo alive during any migration hiccup); a real 2025–26 platform outage record (contingency: one-click redeploy of previous deployments; production answer: exit managed PaaS at the ~10K-user tier — it's in the cost analysis); usage-based cost variance (monitored day one — and smaller since the D5 revision removed the memory-hungry self-hosted ClickHouse from the project).
**Not chosen: VPS + Compose (my own v1).** Strongest control/cost story and compose parity, but spends the week's scarcest resource on undifferentiated ops.
**Not chosen: AWS ECS/Fargate, Kubernetes.** Production-plausible, deadline-implausible; the risk this week is safe brownfield AI integration, not orchestration.
**Production evolution (before real PHI):** BAA-covered vendors end-to-end, private networking, secrets manager, managed DB with backups/restore tests, SIEM/audit-log review, MFA/session policy, incident response, clinical validation.

---

# 5. Main Data Flow

1. Physician opens a patient chart in OpenEMR and clicks the Co-Pilot button.
2. SMART EHR launch → OAuth2 code + PKCE → agent receives a provider-attributed token with read-only scopes and the launch patient context.
3. Session created, pinned to (clinician, patient); correlation ID minted; deceased hard-stop pre-flight runs.
4. Orchestrator fires the whitelisted FHIR tools — independent reads fan out in parallel; wall-clock ≈ slowest call.
5. Tool results normalize into the EvidencePacket (stable evidence IDs, missing-data notices, tool-error states — empty ≠ missing ≠ failed).
6. Sonnet 4.6 (cached patient prefix) drafts typed claims with evidence IDs.
7. Verifier: field-level match per claim (reject on contradiction), constraint + phrasing rules, treatment-verb screen.
8. Templater re-renders display text from verified fields; unsupported claims removed or downgraded to explicit uncertainty; violations → honest refusal with canonical message.
9. Source-cited answer streams to the physician with citation chips.
10. Full trace (steps, latencies, tokens, cost, verdicts, refusal kinds) lands in Langfuse under the correlation ID.

---

# 6. Verification Strategy

**Verified (strict evidence required):** patient identity, appointment context, active medications and status, allergies, conditions, lab values and abnormality labels, encounters, changes since last visit, missing-data claims, tool-failure claims.

**Blocked in MVP:** diagnosis generation, treatment recommendations, prescribing advice, lab/order recommendations, patient messaging, chart writing, generic medical advice not grounded in this chart, cross-patient access.

**Verdicts:** `pass | flagged | blocked | refused(kind)` — refusal kinds are typed (wrong-patient, ambiguous-resolution, treatment-language, deceased, session-expired), logged, and dashboarded.

Unsupported clinical claims are never displayed with a weak confidence score. They are removed or rewritten as explicit uncertainty. An abnormality label requires an interpretation flag, reference range, or configured rule — never model judgment alone.

**Known limitation, stated before you ask:** field-level match proves provenance and consistency, not perfect synthesis — a claim can cite and match a real record while emphasizing the wrong thing. That residual gap is covered by golden-answer evals, and it is why the physician sees citations, not just answers.

---

# 7. Failure Handling

**Fail closed:** unauthorized access; patient-context mismatch; evidence belonging to another patient; malformed packet; repeated invalid model output with fallback unavailable; diagnosis/treatment/write requests; deceased indicator present; ambiguous prior-visit resolution.

**Degrade gracefully, loudly:** tool failure → bounded retries → partial answer that *names* what's missing; allergy retrieval failure → mandatory warning ("could not be retrieved — do not treat as confirmation of no allergies"); LLM hard-fail → deterministic fallback: the templater renders the EvidencePacket directly — grouped, grounded, state-aware phrasings, explicit "generated without LLM assistance" banner; verifier still runs; fallback rate alerts.

**The distinction that must never collapse:** empty result (tool succeeded, zero records) vs missing data (expected, unavailable) vs tool failure (could not check). Three different answers to the physician. Collapsing them is how "allergy lookup failed" becomes "no allergies" — the exact failure that kills trust.

**Refusal is a feature:** when the system cannot reliably answer the question asked ("what changed?" with no resolvable prior visit), it says so and points to the chart — it never silently answers a different question.

---

# 8. Security Posture

- Least-privilege, read-only SMART scopes; provider-attributed tokens only; no `client_credentials` for clinical reads; no write scopes.
- Session pinned to (clinician, patient) at creation; patient switch requires a fresh launch; turn cap bounds cost and exposure.
- FHIR/REST behind OpenEMR's authorization — no runtime DB credentials anywhere in the agent.
- Tool whitelist; no arbitrary SQL/HTTP/shell/web tools; no cross-patient search.
- Secrets in Railway env vars / non-committed .env; never in code, prompts, or logs; tokens backend-held, never in browser storage; nothing token-like ever logged.
- PHI-minimized traces (hashes, not identifiers); exactly **two** PHI egress points, both under the PRD's assumed BAAs — the LLM provider and Langfuse Cloud — each inventoried with an incident-response owner; production path for observability is Langfuse's HIPAA data region + signed BAA.
- **Prompt injection:** chart notes and documents are untrusted *data*, never instructions — delimited and hardened in the system prompt; the read-only tool surface means worst-case injection yields wrong words, not wrong writes; the verifier catches unsupported words; injection fixtures live in the eval suite.
- Model output is untrusted until verified and sanitized before render.

---

# 9. Deployment and Operations

**MVP deployment (Railway project):** OpenEMR (Docker image + `sites/` volume) + managed MySQL + FastAPI agent service; observability exports to Langfuse Cloud (external, assumed BAA — D5 rev). Railway-managed TLS and domains. Local dev = Docker Compose (dev-easy) for parity.

**CI/CD gates:** unit tests → mocked integration tests → security/adversarial tests → deterministic eval subset → secret scan + dependency audit → Railway deploys only on green → `/ready` healthcheck validates OpenEMR FHIR metadata, LLM provider, Langfuse, and session store — real checks, no unconditional 200.

**Rollback:** one-click redeploy of any previous Railway deployment; `git revert` → auto-redeploy as the code path. OpenEMR changes minimized; no schema changes in MVP.

**Baselines:** per-service CPU/memory from Railway metrics + k6 load tests at 10 and 50 concurrent users; p50/p95/p99 and error rates recorded pre-Final so future changes measure against them.

---

# 10. What I Would Emphasize in Q&A

**Why FastAPI/Pydantic?** Typed evidence, typed claims, validated tool contracts, async parallel fan-out, trivial health endpoints — and Pydantic is the brief's own named contracts tool.

**Why a direct SDK loop instead of PydanticAI/LangChain/LangGraph?** The brief requires an agentic multi-turn chatbot, so the loop exists — the question is who owns it. Safety-critical steps (authorization, evidence construction, verification) must be explicit application code I can defend line by line. LangGraph is deferred, not rejected: the orchestrator seam is clean if weeks 2–3 need durable workflows.

**Why Claude and not the cheaper-sounding alternative?** It *is* the cheaper alternative at equivalent capability — $3/$15 vs $5/$30 — and 90%-off prompt caching matches my repeated-patient-prefix traffic exactly. Provider seam keeps the exit real.

**Why not direct database access?** It bypasses OpenEMR's application-layer authorization and audit, requires broad credentials, and couples me to internal schema. APIs preserve the security boundary that my whole trust story rests on.

**Why read-only?** The user need is chart review. Write capabilities demand clinical, legal, and compliance controls that don't fit MVP scope — and read-only structurally caps the blast radius of both bugs and prompt injection.

**Why Railway with its outage record?** Deliberate week-one tradeoff, documented with fallbacks: hours went to the agent instead of TLS. A hospital deployment gets the multi-region/self-hosted answer — already in my scaling and cost analysis.

**What failure mode worries me most?** Silent retrieval failure becoming a confident clinical claim — allergy lookup fails and the system says "no allergies." That is why empty, missing, and failed are three separate states with three different mandatory phrasings, why the allergy tool failure warning is verifier-enforced, and why adversarial evals attack exactly this seam.

**What did you cut, and why?** Voice I/O — researched it, found browser speech APIs ship audio to vendor clouds, which broke the trust story; cut it. Capabilities without a load-bearing use case count against this brief. The discipline is the point.

**What makes this defensible?** Every decision traces to the user's 90-second workflow or to a researched constraint — and the decision log (D1–D13) records the alternatives, the tradeoffs, and what would invalidate each choice.

---

# 11. Closing Position

This architecture does not try to make the model a doctor. It makes the model a constrained drafting layer inside a verified, auditable, authorization-inheriting workflow.

The physician gets faster access to patient context. OpenEMR remains the source of truth. The agent remains read-only. Every claim is verified field-by-field against the record it cites, the model's own words never reach the screen unverified, and the system refuses honestly when it cannot verify — because in a clinical setting, the confidently wrong answer is the one that does the damage.

---

## Appendix: 5-Minute Script (stripped, specific — speak this)

**[0:00–0:25] User & problem**
"My user is a primary care physician, 20 patients a day, 90 seconds between rooms. Four questions: who is this patient, why are they here, what changed since last visit, what do I review before walking in. Not a medical chatbot — a pre-visit co-pilot. The user choice is verified: OpenEMR is an outpatient system, and its sanctioned demo-data tool is Synthea, which is modeled on primary-care encounters. It's the only user I can serve with real data."

**[0:25–1:15] Integration**
"The agent is a separate FastAPI service that connects via SMART-on-FHIR EHR launch. Physician clicks a button in the chart; OAuth2 authorization-code flow with PKCE hands my service a read-only, provider-attributed token plus the open patient's context. Every read goes through OpenEMR's FHIR R4 API with that token — so OpenEMR's own ACL authorizes every single call. No database credentials. No system token — client-credentials would collapse every read to a system user and destroy provider attribution. This launch pattern is what ONC certification g-10 mandates, so the agent ports to any certified EHR. Cost I accepted: OAuth plumbing and a network hop — mitigated by token caching and firing all six baseline FHIR reads concurrently, so the brief costs the slowest call, not the sum."

**[1:15–2:45] Verification — the core**
"Ungrounded medical LLMs hallucinate at over sixty percent, so the model is never the authority. Pipeline: tool results normalize into an evidence packet — records with stable IDs; that packet is the only thing the model sees. The model must answer in typed claims — a medication claim is literally fields: name, dose, status — each carrying evidence IDs. The verifier compares fields against the cited record: reject on contradiction, not absence — model says 10 milligrams, record says 5, the claim dies. Then the display text is re-rendered from the verified fields by a deterministic templater — the model's own prose is discarded. It cannot phrase its way past verification. On top: constraint rules — allergy-versus-prescription contradiction, dosage bounds — and mandatory phrasings: an empty allergy result renders as 'no allergy records returned, confirm with patient' — never 'no known allergies.' Empty, missing, and failed are three separate states with three separate phrasings, because collapsing them is how a failed allergy lookup becomes 'no allergies.' Treatment verbs — start, stop, increase, prescribe — trigger refusal. And the LLM never computes over clinical data: 'what changed since last visit' is calculated by a deterministic tool; the model only narrates."

**[2:45–3:30] Failure & safety**
"Tool failure: bounded retries, then a partial answer that names what's missing — never silent. LLM down: the templater renders the evidence packet directly — degraded but grounded, with an explicit 'no LLM synthesis' banner. A deceased indicator is a hard-stop before any summarization. Ambiguous data — can't reliably resolve the prior visit — the agent refuses and says check the chart. Refusal is a feature: the confidently wrong answer is what kills clinical trust. And the non-goals are explicit: no diagnosis, no prescribing, no orders, no chart writes — read-only by construction, so worst-case prompt injection produces wrong words, never wrong writes."

**[3:30–4:15] Observability & evals**
"Every request mints a correlation ID that rides every log line, tool call, and LLM span — and goes to OpenEMR as an X-Copilot-Request-Id header, so traces join across both systems' logs. Traces land in Langfuse Cloud under an assumed BAA — the same posture as the LLM provider — minimized to hashes, never raw identifiers. Dashboard: requests, error rate, p50/p95, tool calls, verification pass/fail, cost per request; alerts on p95 latency, error rate, tool-failure rate, and fallback rate — each with a runbook. Evals are pytest: every case tagged boundary, invariant, or regression — empty records, cross-patient leakage, injection attempts — plus adversarial cases hand-built to beat my own verifier. Evals gate every deploy."

**[4:15–4:45] Stack, one line each**
"Claude Sonnet 4.6: three dollars in, fifteen out, versus five and thirty for GPT-5.5 — same capability class, and its ninety-percent prompt-cache discount matches my exact traffic: the same patient prefix every turn. Haiku for cheap utility calls, thin provider seam for the exit. Pydantic contracts on every tool boundary. No agent framework — one agent, six read-only tools, a loop I defend line by line. Railway: one project, all services; deploy-on-green, one-click rollback; its outage record and cost curve are documented risks, and the exit-at-scale is in my cost analysis. Load-tested with k6 at ten and fifty concurrent users for baselines."

**[4:45–5:00] Close**
"One honest number: a completed prior build of this case study measured about twenty-eight seconds end-to-end, mostly LLM time. My answer is streamed perceived latency — first tokens in two to three seconds — per-stage budgets, and re-baselining from real traces. Next gate: the audit, before any AI code ships."
