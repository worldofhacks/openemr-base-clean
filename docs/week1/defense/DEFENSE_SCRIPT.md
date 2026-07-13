# Defense Script — Decision Walkthrough

Every section: what I chose → what else I considered → why mine is the right choice.

---

## 1 · The User

**Chose:** primary care physician · 20-patient day · 90 seconds between rooms.

**Considered:**

- ED resident on overnight intake
- Hospitalist rounding on 12 admissions

**Why mine wins:**

- OpenEMR is an outpatient system at its core — scheduling, office encounters, billing. A hospitalist persona fights the platform.
- The repo's own sample-data tool imports **Synthea** — synthetic records explicitly modeled on primary-care encounters. Labs, meds, allergies, visits: all present for this persona.
- ED and inpatient personas would force me to fabricate data — which is exactly what this project punishes.
- The PRD itself lists this persona as its first example of a well-chosen narrow user.

> The only user I can serve end-to-end with real data instead of invented data.

---

## 2 · Integration Pattern

**Chose:** SMART-on-FHIR sidecar — a separate agent service, launched from inside the patient chart.

```
patient chart → [Co-Pilot button] → SMART EHR launch
             → OAuth2 code + PKCE
             → agent receives:  who is asking  +  which patient is open
             → read-only, provider-attributed token
```

**Considered:**

- Embedded PHP module with internal data access
- Standalone external app

**Why mine wins:**

- **Authorization is inherited, not rebuilt.** Every read is authorized by OpenEMR's own certified ACL, under the clinician's own token. I didn't build a parallel permission system to get wrong.
- **Blast radius.** The agent crashes → the EHR doesn't. A co-pilot, not a transplant.
- **Regulation mandates this pattern.** ONC §170.315(g)(10) requires certified EHRs to support FHIR R4 + SMART launch → the agent ports to any certified EHR in the country.
- The embedded module's only advantage — raw data-path speed — is the one thing I can mitigate (caching, parallel calls). Its drawbacks — authz burden, blast radius, legacy PHP lock-in — I can't afford.
- A standalone app fails the brief: physicians shouldn't leave the EHR for pre-visit context.

**Tradeoff I accepted:** OAuth plumbing up front, one extra network hop. Mitigated below.

---

## 3 · Data Access

**Chose:** FHIR R4 API only at runtime, called with the clinician's delegated token. All independent reads fan out in parallel.

```
brief request → 6 independent FHIR reads, CONCURRENT
                demographics · conditions · meds · labs · encounters · allergies
              → wall-clock ≈ slowest call, not the sum
              → every call carries X-Copilot-Request-Id (cross-system trace join)
```

**Considered:**

- Direct database access (fast, tempting)
- `client_credentials` system token
- Standard REST as primary

**Why mine wins:**

- Direct SQL bypasses OpenEMR's ACL and audit logging — it forfeits the entire trust-boundary story in section 2. Allowed only as audit-time research, never the runtime path.
- A system token collapses every read to one system user — destroying provider attribution. In a clinical audit, *who asked* matters as much as *what was read*.
- FHIR maps to real clinical resources (Patient, Condition, MedicationRequest, AllergyIntolerance, Observation…) and is the portable, standards story. Standard REST remains a documented fallback if FHIR lacks a workflow field.

---

## 4 · Agent Design

**Chose:** direct Anthropic SDK tool-use loop. No agent framework. Pydantic contracts on every tool input and output.

**Considered:**

- LangGraph
- LangChain agent loop
- CrewAI / multi-agent
- PydanticAI-style typed wrapper

**Why mine wins:**

- The brief *requires* a multi-turn agentic chatbot that invokes tools — the loop must exist. The real question is **who owns the loop**.
- The safety-critical steps — authorization, evidence construction, verification — must be explicit application code I can defend line by line, not framework internals.
- One agent, one provider, ~6 read-only tools, linear retrieve → draft → verify. That is the documented sweet spot for a hand-rolled loop; frameworks earn their complexity on multi-stage, multi-provider stateful graphs, which this is not.
- CrewAI: agent teams add hidden reasoning paths with zero user value here. LangGraph: deferred, not rejected — the orchestrator seam stays clean for weeks 2–3.
- Two determinism rules on top:
  - The LLM **never computes** over clinical data — "what changed since last visit" is calculated by a deterministic tool; the model only narrates.
  - The prompt is not the safety boundary. The verifier is.

---

## 5 · LLM Choice

**Chose:** Claude Sonnet 4.6 primary · Haiku 4.5 for cheap utility calls · thin provider seam.

**Considered:**

- GPT-5.5
- Self-hosted open-weight models

**Why mine wins:**

- Price at equivalent capability class: **$3 / $15** per million tokens vs **$5 / $30** — roughly 40–50% less.
- **Prompt caching (90% off cached input) matches my exact traffic shape:** the same patient-context prefix re-sent on every turn of a session. The cache discount isn't a bonus — it's the cost model.
- Open-weight self-hosting: no GPU in this deployment, unproven clinical tool-use reliability inside one week. It returns at the 100K-user tier of my cost analysis, where dedicated inference flips the economics.
- The provider seam (`llm.complete()`) keeps the exit real — swap is a config change, and verification is provider-independent by design.

---

## 6 · Verification (the core of the system)

**Chose:** deterministic evidence-packet pipeline. The model drafts; the verifier decides.

```
tool results → EvidencePacket        normalized records, stable IDs (Type:id:hash)
                                     the ONLY thing the model ever sees
            → typed claims           MedicationClaim{name, dose, status} + evidence_ids
            → field-level verify     claim fields vs cited record fields
                                     REJECT ON CONTRADICTION, NOT ABSENCE
                                     (model: 10mg · record: 5mg → claim dies)
            → templater              display text RE-RENDERED from verified fields
                                     the model's own prose is discarded
            → constraint rules       allergy-vs-prescription · dosage bounds · stale labs
            → phrasing rules         empty allergy result ≠ "no known allergies"
                                     treatment verbs (start/stop/prescribe) → refuse
            → verdict                pass | flag | block | refuse(kind)
```

**Considered:**

- Freeform prose with citation tags (my own v1)
- LLM-as-judge in the serving path

**Why mine wins:**

- Ungrounded medical LLMs hallucinate at 60%+ — this layer is the product, not a feature.
- Prose-with-citations was my first design. It's weaker: citations attach incorrectly, subclaims can't be individually killed, and text matching is fuzzier than field matching. I upgraded after studying a completed prior implementation of this case study — concepts adopted, nothing copied.
- The templater is the anti-hallucination mechanism that matters: **the model's words never reach the screen.** It cannot phrase its way past verification.
- An LLM judge is a second probabilistic system that can agree with fluent hallucinations. Judges live in the eval suite only.
- Stated limitation, before anyone asks: field-match proves provenance and consistency, **not** perfect synthesis. That residual gap is covered by golden-answer evals — and it's why the physician sees citations, not just answers.

---

## 7 · Failure Design

**Chose:** three separate data states, refusal as a feature, and a grounded fallback. Designed, not discovered.

```
tool fails      → bounded retries → partial answer that NAMES what's missing
LLM down        → templater renders the EvidencePacket directly
                  grounded, degraded, explicit "no LLM synthesis" banner
deceased flag   → hard-stop before any summarization
ambiguous data  → refuse: "review the chart manually"
                  never silently answer a different question
```

**The distinction that must never collapse:**

- **empty** — tool succeeded, zero records
- **missing** — expected data unavailable
- **failed** — could not check

Three states, three mandatory phrasings. Collapsing them is how a failed allergy lookup becomes "no allergies" — the single failure mode most likely to hurt a patient and kill clinician trust.

**Why mine wins:** the alternative — generic error handling and best-effort answers — is precisely the "confidently wrong" behavior the PRD's why-this-matters paragraph warns about. In this domain, an honest refusal outperforms a plausible guess every time.

---

## 8 · Observability

**Chose:** correlation IDs end-to-end + Langfuse Cloud under an assumed BAA.

```
request → correlation ID minted
        → every log line · every tool call · every LLM span
        → X-Copilot-Request-Id header on every FHIR call  → joins OpenEMR's own logs
        → Langfuse trace: steps, order, per-step latency, tokens, cost, verdicts
```

**Considered:**

- Self-hosted Langfuse (my own v1 of this decision)
- LangSmith
- Braintrust

**Why mine wins:**

- Traces contain PHI. Langfuse Cloud offers a **BAA** — a dedicated HIPAA data region — so the observability vendor sits in **the same assumed-BAA posture as the LLM provider**, traces are minimized to hashes, and the MIT self-host migration path stays as my exit. That's a compliance argument, not a tooling preference — and it's why self-hosting (my v1) no longer earns its four-service ops cost.
- LangSmith's per-trace pricing scales badly (~$2.5K/month at 1M traces) and is LangChain-shaped. Braintrust is eval-first, closed SaaS at $249/mo, no equivalent BAA story at that tier.
- Dashboard covers the required minimum and more: requests, error rate, p50/p95, tool calls, retries, verification pass/fail, cost per request. Four alerts with runbooks: p95 latency, error rate, tool-failure rate, LLM-fallback rate.

---

## 9 · Evaluation

**Chose:** pytest, three eval classes, every case tagged, evals gate every deploy.

- **boundary** — empty record, malformed query, huge chart
- **invariant** — every claim cites · no cross-patient leakage · allergy contradiction always flagged
- **regression** — pinned outputs for canonical queries
- plus an **adversarial set built to beat my own verifier** — contradicting claim text, valid-ID-wrong-category, treatment advice hidden in "consider reviewing whether to increase," wrong-patient evidence, prompt injection in notes

**Considered:** happy-path suites, SaaS eval platforms.

**Why mine wins:**

- The brief explicitly fails happy-path-only suites — every case documents the failure mode it guards.
- A safety boundary you haven't tried to break is one you don't trust.
- Deterministic and local: evals inspect evidence packets and verdicts directly, and run as the CI gate — deploys only happen on green.

---

## 10 · Deployment

**Chose:** Railway — one project: OpenEMR (image + volume) · managed MySQL · agent service. (Langfuse is cloud-hosted — nothing observability-shaped to operate.)

```
git push → GitHub Actions: tests + evals → green → Railway deploys
        → /ready healthcheck (real checks: FHIR metadata · LLM · Langfuse · sessions)
        → rollback = one-click previous deployment
local dev = docker compose (parity) · k6 load tests @ 10 / 50 users → baselines
```

**Considered:**

- VPS + Docker Compose (my own v1)
- AWS ECS / Kubernetes

**Why mine wins:**

- In a one-week solo sprint the scarcest resource is **engineering hours**. Railway zeroes out TLS, domains, deploy pipeline, DB provisioning, per-service metrics — and SMART/OAuth requires HTTPS everywhere, which comes free.
- Risks owned out loud, each with a mitigation: no OpenEMR-on-Railway prior art (timeboxed pathfinding; local compose keeps the demo alive) · a real 2025–26 outage record (one-click previous-deployment rollback; production answer: exit managed PaaS at the ~10K-user tier — it's in my cost analysis) · usage-cost variance (monitored from day one).
- ECS/K8s are production-plausible and deadline-implausible. The risk this week is safe brownfield AI integration, not container orchestration.

---

## 11 · What I Cut

**Voice I/O (STT/TTS).** Researched it: browser speech APIs ship audio to Google/Azure/Apple clouds — a new PHI trust zone that broke my own compliance story. Doing it right means self-hosted Whisper plus safety UX the core gates can't spare this week. Cut, documented, revisitable.

> Capabilities without a load-bearing use case count against this brief. The discipline is the point.

---

## Closing Position

- OpenEMR stays the source of truth and the authorization authority.
- The agent is read-only by construction — worst-case prompt injection produces wrong words, never wrong writes.
- Every claim is verified field-by-field against the record it cites; the model's own words never reach the screen unverified.
- The system refuses honestly when it cannot verify — because in a clinical setting, the confidently wrong answer is the one that does the damage.

One honest number to close: a completed prior build of this case study measured ~28 seconds end-to-end, mostly LLM time. My latency story is streamed perceived latency — first tokens in 2–3 seconds — per-stage budgets, and re-baselining from real traces once deployed.

Next gate: the audit, before any AI code ships.
