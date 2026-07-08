# RESEARCH.md — verified external facts (2026-07-06)

> Playbook Research phase output. Each finding: fact → source → architecture impact.

## R1. LLM pricing (July 2026)

| Model | Input /M | Output /M | Notes |
|---|---|---|---|
| Claude Sonnet 4.6 | $3.00 | $15.00 | Prompt caching: 90% off cache hits; batch 50% off |
| Claude Haiku 4.5 | $1.00 | $5.00 | Cheap enough for verification/secondary calls |
| Claude Opus 4.8 | $5.00 | $25.00 | Overkill for per-query use |
| GPT-5.5 (standard) | $5.00 | $30.00 | Cached input $0.50/M; batch/flex $2.50/$15 |

Sources: [Claude Platform pricing docs](https://platform.claude.com/docs/en/about-claude/pricing), [Anthropic API pricing guide](https://www.finout.io/blog/anthropic-api-pricing), [OpenAI API pricing](https://developers.openai.com/api/docs/pricing), [GPT-5.5 pricing breakdown](https://apidog.com/blog/gpt-5-5-pricing/)

**Architecture impact:** Sonnet 4.6 is ~40% cheaper than GPT-5.5 on input, 50% on output. The pre-visit-brief workload re-sends the same patient context repeatedly within a session → **prompt caching (90% off cached input) dominates the cost model**. Two-model split (Sonnet for reasoning, Haiku for cheap secondary calls) anchors the 100/1K/10K/100K cost analysis.

## R2. Observability platforms

| | Langfuse | LangSmith | Braintrust |
|---|---|---|---|
| License | OSS (MIT), free self-host | Proprietary SaaS | Proprietary SaaS |
| Pricing | $0 self-host; cloud from $29/mo | $39/seat + $0.50/1k traces (~$2,514/mo @ 1M traces) | Free tier 1GB; Pro $249/mo |
| PHI residency | **Stays within our deployment** | Third-party | Third-party |
| Strengths | Traces, dashboards, cost tracking, eval datasets, correlation IDs | LangChain-native insights | Best-in-class eval UX |
| Self-host burden | Postgres+ClickHouse+Redis+S3 via docker compose | n/a | n/a |

Sources: [Latitude comparison 2026](https://latitude.so/blog/best-llm-observability-tools-agents-latitude-vs-langfuse-langsmith), [AppScale definitive comparison](https://appscale.blog/en/blog/langfuse-vs-langsmith-vs-braintrust-vs-helicone-2026), [Braintrust on Langfuse alternatives](https://www.braintrust.dev/articles/langfuse-alternatives-2026), [Morph on LangSmith at scale](https://www.morphllm.com/comparisons/langsmith-alternatives)

**Architecture impact:** Traces contain PHI (patient names, meds, labs flow through prompts). Self-hosted Langfuse keeps PHI inside our trust boundary → strongest HIPAA/BAA story, $0, and satisfies the dashboard requirement natively. SaaS options would require trace scrubbing or another assumed BAA.

## R3. OpenEMR integration surface (from codebase, /tmp/oemr @ Gauntlet-HQ fork)

- FHIR R4 API, US Core 8.0 compliant, SMART on FHIR v2.2.0 **certified**, incl. **EHR launch flow** (`API_README.md`, `FHIR_README.md`, `Documentation/api/`)
- OAuth2/OIDC server built in (`/oauth2/default/registration`), granular SMART scopes (`patient/Patient.rs`, `.cruds` syntax), token introspection
- Standard REST API controllers for Appointment, Encounter, Condition, Drug, AllergyIntolerance, Immunization, etc. (`src/RestControllers/`)
- ACL layer (`src/Common/Acl/AclMain.php` etc.) enforced behind the APIs
- Dev stack: `docker/development-easy/docker-compose.yml` (MariaDB 11.8 + openemr/openemr:flex), app :8300/:9300, phpMyAdmin :8310

**Architecture impact:** The SMART-on-FHIR EHR-launch pattern is not just viable — it's the certified, documented path. Agent = OAuth2 client; OpenEMR remains the authorization authority.

## R5. SMART on FHIR is federally mandated, not just "a standard" (backs D2)

ONC Health IT Certification § 170.315(g)(10) "Standardized API for patient and population services" **requires** certified EHRs to expose FHIR R4 + US Core via standardized API with SMART app launch capabilities (incl. HTI-1 Final Rule updates); it replaced the older (g)(8) criterion on Jan 1, 2023. USCDI data classes must be retrievable this way.
Sources: [ONC API Resource Guide, (g)(10) criterion](https://onc-healthit.github.io/api-resource-guide/g10-criterion/), [HealthIT.gov standardized API test method](https://www.healthit.gov/test-method/standardized-api-patient-and-population-services), [Inferno (g)(10) test kit](https://fhir.healthit.gov/test-kits/onc-certification-g10/)
**Impact:** D2's integration pattern is the one US regulation obligates certified EHRs to support. Defense line: *"Our agent integrates the way federal certification says clinical apps must — portable beyond OpenEMR to any certified EHR."*

## R6. Agent framework landscape 2026 (backs D6)

Consensus framing: the decision is *who owns the loop* — vendor-managed runtime, self-hosted graph engine (LangGraph 1.0: graph state, retries, HITL, time-travel debug), or a plain loop you wrote. Direct Anthropic SDK/Claude-native loops are the recognized choice for **single-agent, Claude-only** systems needing streaming, caching, tool use with nothing hidden; LangGraph earns its complexity for multi-stage, multi-provider, stateful branching workflows.
Sources: [Developers Digest — who should run your agent loop](https://www.developersdigest.tech/blog/managed-agents-vs-langgraph-vs-diy-2026), [Anthropic tool use vs LangGraph](https://agentsindex.ai/compare/anthropic-tool-use-vs-langgraph-platform), [8-SDK comparison](https://www.morphllm.com/ai-agent-framework), [framework showdown](https://qubittool.com/blog/ai-agent-framework-comparison-2026)
**Impact:** Ours is exactly the direct-loop case: one agent, one provider, ~6 read-only tools, linear retrieve→reason→verify. Every abstraction between user and model is one we must defend; a hand-rolled loop keeps the trace story fully explainable. Migration seam documented if wk2–3 goes multi-agent.

## R7. Clinical LLM grounding/verification (backs D7)

2026 findings: medical LLM hallucination rates exceed 60% *without grounding*; 45%+ of AI-generated references in tested studies were fabricated; contextual grounding reduces hallucinations 30–50%; emerging best practice = **span/claim-level verification** — match each generated claim against retrieved evidence, flag unsupported ones — plus structured citations in the output schema and runtime registry checks (resolve IDs against a known store).
Sources: [Presenc medical hallucination rates 2026](https://presenc.ai/research/medical-ai-hallucination-rates-2026), [SQ Magazine hallucination statistics](https://sqmagazine.co.uk/llm-hallucination-statistics/), [FutureAGI architectural deep dive](https://futureagi.com/blog/llm-hallucination-deep-dive-2026/), [arXiv: multi-agent citation hallucination detection](https://arxiv.org/pdf/2605.08583)
**Impact:** D7 (claim-level citation tags resolved against the session's actual tool-call record; structured output schema; deterministic checks) matches the documented state of the art — we can cite literature, not vibes, when defending the verification layer.

## R8. Web Speech API reality check (corrects & backs D11)

Support: Chrome 25+, Edge 87+, Safari 14.1+ (webkit-prefixed); Firefox behind a flag (never shipped to users). **Critical finding: `SpeechRecognition` is not on-device by default** — Chrome routes audio to Google's speech service, Edge to Azure Cognitive Services, Safari prompts before sending audio to Apple's recognition service. `speechSynthesis` (TTS) primarily uses local/OS voices.
Sources: [caniuse — Speech Recognition](https://caniuse.com/speech-recognition), [MDN SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition), [TestMu browser support detail](https://www.testmuai.com/learning-hub/speech-recognition-api-browser-support/), [AddPipe deep dive](https://blog.addpipe.com/a-deep-dive-into-the-web-speech-api/)
**Impact:** falsified the original "audio never leaves the device" premise for STT — this finding drove the decision to **cut voice from wk1 scope entirely** (D11): doing it defensibly requires self-hosted Whisper + safety UX that the core gates can't spare this week.

## R9. FastAPI/Pydantic production standing (backs D3)

FastAPI is the de facto standard for high-performance Python APIs in 2025–26 (confirmed production use at Microsoft, Uber, Netflix, OpenAI; common in healthcare/fintech); ASGI-native async (Starlette) → non-blocking concurrent I/O (the D10 fan-out mechanism); Pydantic v2 validation runs on a Rust core (~17× v1) and is the PRD's own named example for schema contracts.
Sources: [FastAPI docs](https://fastapi.tiangolo.com/), [FastAPI in 2026 architecture](https://kawaldeepsingh.medium.com/fastapi-in-2026-the-architecture-behind-3-000-requests-per-second-automatic-api-documentation-43f2cf573f57), [2026 relevance review](https://medium.com/@pravinkunnure9/is-fastapi-still-relevant-in-2026-46fb6da63c26)

## R10. Demo data & platform shape (backs D1 — target user)

**Codebase fact:** this repo's sanctioned sample-data path is Synthea — `CONTRIBUTING.md` documents a built-in devtool ("Create and add random patient data. This will use synthea to create random patients that are then imported into OpenEMR").
**Synthea's design (sourced):** it explicitly models "the 10 most frequent reasons for **primary care** encounters and the 10 chronic conditions with the highest morbidity in the US" — birth-to-death longitudinal records with conditions, allergies, medications, vaccinations, observations/vitals, labs, procedures, care plans; native FHIR R4 export (which is exactly what our D9 data path consumes). ED encounters exist only as scattered longitudinal events (no triage/acuity/real-time intake context); inpatient census/rounding structure effectively absent.
**Platform fact (codebase):** OpenEMR's core surface is ambulatory practice management — appointment calendar, office encounters, insurance/billing controllers (`src/RestControllers/`: Appointment, Encounter, Insurance, Employer…). No inpatient ADT/census/bed workflow at its core.
Sources: [Synthea (JAMIA paper)](https://academic.oup.com/jamia/article/25/3/230/4098271), [Synthea project](https://synthetichealth.github.io/synthea/), [Synthea feature list](https://github.com/proxsys/synthea-1), repo `CONTRIBUTING.md`.
**Impact:** PCP is the only persona whose core workflow (scheduled outpatient visits + longitudinal chart review) is fully expressible in both the platform's data model AND the sanctioned demo data. ED/hospitalist personas would require context (acuity, census) neither models — forcing fabricated data, which violates the PRD's grounding principle from day one. Residual check: lab *trend* depth varies by Synthea module — verify richness during Stage 1 (D1 invalidation clause stands).

## R11. Railway platform facts (backs D8 revision)

Docker image + Dockerfile deploys; volumes persist across deploys (resizable on paid plans); managed MySQL/Postgres/Redis with backups; multi-service projects; deploy-on-push with GitHub integration; usage-based pricing (small projects typically $5–20/mo; Hobby plan + compute). Compose apps are migrated service-by-service (guide exists; Railway does not execute compose files directly). Risk note: multiple major outages Nov 2025–May 2026 (incl. one 8-hour event). No OpenEMR-on-Railway prior art found in search.
Sources: [Railway docs — Docker Compose migration guide](https://docs.railway.com/guides/docker-compose), [Railway volumes](https://docs.railway.com/volumes/reference), [Railway pricing](https://railway.com/pricing), [Railway vs Render vs Fly 2026](https://techsy.io/en/blog/railway-vs-render-vs-fly-io)
**Impact:** platform mechanics support the whole stack; the two open risks (OpenEMR pathfinding, ClickHouse cost) carry explicit fallbacks in D8.

## R4. Open-source/self-hosted LLM (rejected for wk1, revisited at scale)

Self-hosting an open-weight model would keep PHI fully in-boundary, but: our managed-platform deployment has no GPU tier at demo scale, clinical-grade tool-use reliability of small open models is unproven for this timeline, and the PRD explicitly permits assuming a BAA with LLM providers. Revisit at 100K-user scale in the cost analysis (dedicated GPU inference becomes cost-competitive and removes per-token exposure).

## R12. End-to-end latency anchor — UNVERIFIED planning assumption, not a sourced fact (added 2026-07-07, gap-audit G7-1)

The "p50 ≈ 28s end-to-end, LLM ≈ 85% of wall-clock" figure used in D10 (latency calibration), ARCHITECTURE_DRAFT §7 (the 15s p95 alert threshold), and §9 (cost model) is an **anecdotal prior-art observation** from a completed prior implementation of this same case study — it is **not** independently sourced and **not** measured against our system (no agent exists yet to measure). The audit measured only OpenEMR's own FHIR floor (≈0.39s live per read, F-P.5), not an agent end-to-end p50.
**Status:** treat as a *directional planning assumption* to be **replaced by real measured Langfuse data at Early submission** (the re-baseline is already the primary story). Do not present 28s as a defended number; the defensible claim is the *shape* (LLM dominates wall-clock, so perceived-latency via streaming + prompt caching is the lever), which the audit's own FHIR-floor measurement supports (FHIR block <1s parallel is small next to any multi-second LLM turn). **Invalidation/replacement:** the first week of Langfuse traces supersedes this entry.
