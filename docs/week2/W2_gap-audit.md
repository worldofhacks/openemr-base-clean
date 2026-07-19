# AgentForge Week 2 final-submission readiness audit

**Authoritative specification:** `docs/week2/Week_2_AgentForge.pdf`, visually checked pages 1–7

**Repository SHA:** `658307936f0396d292c94fff3f9ef8089f1697e7`

**Audit date:** 2026-07-18 (America/New_York); findings independently validated and evidence extended 2026-07-19 (cache-busted deployment re-probes, drill-branch cross-check, isolated re-execution of the gate arithmetic)

**Verdict:** **Not Ready**

> **Known gaps at submission (dated banner — D01-lite, 2026-07-19).** This submission is
> late (owner-accepted; see the deadline model in `W2_IMPLEMENTATION_PLAN.md`) and is
> published with its gaps stated honestly rather than hidden. As of this banner's date the
> open items are: AF-P0-01 (no enforced merge gate on either host), AF-P0-02 (golden gate
> does not traverse production retrieval), AF-P0-03 (final responses flatten claim→citation
> association), and AF-P1-01…11 (authenticated production journey, routing/trace nesting,
> data authority, operational observability, CI image/type gates, committed eval evidence,
> demo video, performance/cost report, backup/restore drill, Cohere retry, GitLab
> enforcement). Remediation is in flight on the tracks defined in
> `docs/week2/W2_IMPLEMENTATION_PLAN.md` (Track A salvage + Track B Ready; closure
> checklist §8 with per-task evidence in `docs/week2/evidence/W2_EVIDENCE_INDEX.md`).
> The verdict flips to Ready only through V01 — an independent verification pass on the
> release SHA — never by an implementing agent. Until then it stays **Not Ready**.

This is a current-state implementation and deployment audit, not a plan-coverage review. The
earlier 99-row planning matrix is retained by ID, re-evaluated against code/tests/runtime, and
extended with four suggested-schedule rows so every normative or schedule statement in the PDF
is represented. Status counts across 103 rows are:

| Status | Count |
|---|---:|
| Met | 46 |
| Partially Met | 33 |
| Not Met | 12 |
| Unable to Verify | 6 |
| Conflicting Requirement | 6 |

## Evidence actually inspected or executed

Repository claims were not accepted as proof. The evidence keys below are used in the matrix.

### Repository evidence keys

| Key | Evidence |
|---|---|
| R1 | Strict extraction/citation schemas: `agent/app/schemas/extraction.py:45-315`, `agent/app/schemas/citations.py:26-97`. |
| R2 | Upload/worker/extraction/write path: `agent/app/ingestion/pipeline.py:63-374`, `agent/app/ingestion/service.py:194-299`, `agent/app/ingestion/processor.py:28-242`, `agent/app/writeback/intents.py:219-325`. |
| R3 | Production hybrid retrieval: `agent/corpus/retrieval.py:240-258,323-539,590-800`; top-five context: `agent/app/orchestrator/composer.py:263-311`. |
| R4 | LangGraph routing/tracing: `agent/app/orchestrator/graph.py:91-99,208-229,359-458,591-613,714-770`; typed handoffs: `agent/app/schemas/handoff.py:29-97`, `agent/app/schemas/workers.py:25-53`. |
| R5 | Grounding/critic/final boundary: `agent/app/orchestrator/composer.py:115-195,386-478`, `agent/app/orchestrator/critic.py:64-160`, `agent/app/routes/chat.py:124-139,218-251,342-371`. |
| R6 | Golden gate: `agent/evals/golden/cases.json`, `agent/evals/harness.py:26-100`, `agent/evals/w2_runner.py:48-54,630-685`; pseudo retrieval: `agent/evals/execution.py:843-907`. |
| R7 | Gate workflows: `.github/workflows/agent-eval-gate.yml:3-187`, `.gitlab-ci.yml:13-46`, `githooks/pre-push`, `docs/week2/evidence/W2_CI_EVIDENCE.md:39-104`. |
| R8 | PR quality workflow: `.github/workflows/agent-quality.yml:28-153` (Ruff, targeted mypy, pytest/coverage, audit, Bandit/Semgrep, contracts/scans). |
| R9 | Events/health/reliability: `agent/app/observability/events.py:193-345`, `agent/app/service.py:202-205`, `agent/app/health.py:43-296`, `agent/ops/w2_dashboard.json`, `agent/ops/w2_alerts.json`. |
| R10 | API contracts and grader collection: `agent/ops/openapi.yaml:1-399`, `agent/tests/test_w2_openapi_and_bruno.py:121-449`, `agent/bruno/`. |
| R11 | Data integrity: `agent/migrations/003_document_jobs.sql:5-71`, `agent/migrations/004_extraction_refs.sql:4-15`, `agent/migrations/005_job_credentials.sql:5-28`. |
| R12 | Corpus provenance: `agent/corpus/manifest.json:1-45,262-265`, `agent/corpus/build.py:95-341`. |
| R13 | Submission evidence: `docs/week2/evidence/W2_DEMO_SCRIPT.md:1-20`, `docs/week2/evidence/W2_COST_LATENCY.md:17-40`, `docs/week2/evidence/W2_BASELINES.md:31-44`, `docs/week2/evidence/W2_BACKUP_RESTORE.md:1-25`. |
| R14 | Setup/deploy/config: `README.md:19-87`, `agent/.env.example:4-59`, `agent/railway.json:1-12`, `agent/railway.worker.json:1-12`, `DEPLOYMENT.md`. |
| R15 | Dual artifact authority: `agent/app/ingestion/artifacts.py:105-219`, `agent/app/ingestion/pipeline.py:219-253`, `agent/app/service.py:561-567`, `agent/app/ingestion/runtime.py:611-659`. |

### Test and deployment evidence keys

| Key | Evidence |
|---|---|
| T1 | `cd agent && .venv/bin/python -m pytest -q`: **936 passed, 5 skipped**, one warning, 9.46s. Skips include opt-in/live paths. |
| T2 | Recorded 50-case eval executed locally: PASS; schema 50/50, citation 50/50, factual 23/23, safety 10/10, no-PHI 50/50. Generated result changes were reverted. |
| T3 | Targeted threshold/known-fail scorer tests: 7 passed. Committed CI evidence records red schema, citation, safety, PHI, and factual (-8.695pp) drills. |
| T4 | GitHub exact-SHA runs inspected: quality/Tier 1/main/deploy green; protected-environment live Tier 2 green for 50 cases, p50 5.61s, p95 12.27s, about $3.07. |
| T5 | Contract/Bruno, graph, ingestion, retrieval, critic, privacy, readiness, and integration suites are included in T1. |
| D1 | Public `/health` 200 with exact SHA; `/ready` 200 `ready`, all eight reported checks green. |
| D2 | Public `/evidence/search` 200 with correlation ID and five version-pinned corpus hits. |
| D3 | Browser plus cookie-aware redirect probe reached OpenEMR Authorization sign-in from `/week2/launch`. Cookie-less curl's 500 is not treated as an outage. |
| D4 | Authenticated upload/extract/write/chat/citation/preview/follow-up flow **Unable to Verify**; no tester credentials were available. |
| D5 | Deployed `W2_GRAPH_ENABLED` value and graph trace **Unable to Verify**; documented default is off. |
| D6 | GitHub API: `main` branch protection returned 404 and repository rulesets returned `[]`; required checks are not enforced. |
| D7 | GitLab protected-branch/required-pipeline settings **Unable to Verify**; repository and pipeline file exist. |
| D8 | Operational W2 event sink, dashboard import, alert destinations, and one-ID deployed reconstruction **Unable to Verify**. |
| D9 | Final video, protected billing/resource evidence, automatic backups, and isolated restore evidence not present. |
| D10 | No SHA drift: checked-out SHA equals public `/health` and inspected current CI/deploy SHA. |
| D11 | 2026-07-19 cache-busted re-probes: `/health` fresh = exact HEAD SHA; `/ready` fresh = `ready`, all eight checks green (`document_runtime: ready`, `document_category_read: authorized_read_ok`). Plain-URL probes first returned a stale intermediary-cached pair (older SHA `24227a7…` + `degraded`/reranker-timeout) — the staleness class fixed by commit `8a21edc`; the cached snapshot is also live evidence that non-binary `degraded` readiness has actually served. Graders should cache-bust with a unique query string. |
| T6 | Isolated re-execution of the committed gate arithmetic (`evals/harness.aggregate_scores`, real code, isolated copy, 2026-07-19): green control passed; deliberate factual regression 21/23 vs baseline 1.0 failed with `failed >5 percentage-point baseline regression` at −8.695652pp (reproducing the W2_CI_EVIDENCE.md factual drill to six decimals); exact −5.00pp allowed; schema 49/50 failed `failed 100% invariant`; 20/23 with no baseline failed `failed >=90% threshold`. No repository files were touched. |
| T7 | Local drill branches `drill/w2-red-{schema,citation,factual,short-phi,unsafe-side-effect}` tip SHAs match all five documented red-drill SHAs in `docs/week2/evidence/W2_CI_EVIDENCE.md` exactly. GitHub run URLs themselves were not independently fetchable from this environment. |

## P0 — final-submission blockers

### AF-P0-01 — eval workflow is not an unbypassable PR/merge gate

- **Requirement / PDF:** eval-driven CI must block regressions (pp.2–5; REQ-01, 05, 31, 36, 41, 51).
- **Current status:** **Not Met**.
- **Evidence:** R7, T3, T4, T6, T7, D6, D7.
- **Gap:** scorer failures and deploy dependency work, but GitHub `main` has no protection or ruleset;
  Tier 2 is not an ordinary pull-request job; hooks are optional; GitLab enforcement is unverified.
  Additionally, the recorded Tier-1 PR gate never loads the baseline (`run_gate` passes
  `baseline=None` for `tier=="recorded"`, `agent/evals/w2_runner.py:341-347`), so the >5pp
  category-regression rule is enforced only in the live tier — on a pull request a factual
  regression is caught only by the absolute ≥0.90 floor (the four deterministic rubrics remain
  100% invariants at PR time).
- **Remediation:** protect GitHub and GitLab submission branches; require quality, Tier 1, and an
  exact-SHA live Tier 2 status (or a secure trusted-event bridge) before merge; restrict bypass;
  document fork-secret handling and required check names.
- **Acceptance criteria:** platform APIs show the rules; a disposable regression PR produces a
  required red status and cannot merge; the restored green SHA can merge; GitLab verifies the same SHA.
- **Dependencies:** repository administrators, protected environment/secrets, GitLab project access.
- **Estimated effort:** Medium.
- **Verification method:** read branch/ruleset APIs and execute one safe red/green PR drill.
- **Completion status:** **Open**.

### AF-P0-02 — required golden gate does not execute production hybrid retrieval/reranking

- **Requirement / PDF:** real evidence retrieval must be covered by the 50-case hard gate (pp.3–5;
  REQ-01, 05, 12, 36, 39, 40, 50).
- **Current status:** **Partially Met**.
- **Evidence:** R3, R6, T2, T4. `_local_retrieve` performs token overlap and uses reversed sparse
  IDs as its “dense” ranking; it never instantiates `HybridRetriever` or the configured reranker.
- **Gap:** production BM25+dense fusion, reranker ordering, miss, low-confidence, and breaker
  regressions can remain green. Golden expected citations are uploaded-document citations only.
- **Remediation:** make the golden executor call the production retrieval interface using the
  committed index and deterministic recorded embedding/rerank adapters; add explicit guideline
  citation, hit, miss, low-confidence, and reranker-order expectations; add a deliberate retriever
  and reranker regression drill.
- **Acceptance criteria:** all retrieval-applicable cases traverse the production
  `HybridRetriever` contract; at least one applicable case asserts each required retrieval
  behavior; each planted regression
  turns the required gate red.
- **Dependencies:** deterministic model artifacts/cache, golden-case review, CI resource budget.
- **Estimated effort:** Medium.
- **Verification method:** instrument constructor/search calls and run red/green Tier 1 plus live Tier 2.
- **Completion status:** **Open**.

### AF-P0-03 — final JSON/fallback responses lose claim-to-citation association

- **Requirement / PDF:** every final clinical claim must carry machine-readable source metadata
  (pp.2, 5; REQ-27, 28, 98).
- **Current status:** **Not Met**.
- **Evidence:** R5, T1, D4. Internal `VerifiedComposition` and Week 2 SSE claim events are per-claim;
  `ChatResponse` and the initial SSE/fallback block expose one multi-claim brief plus a flat list.
- **Gap:** a machine or UI cannot reliably determine which citation supports which sentence.
- **Remediation:** add a closed `claims[]` response contract containing claim text, CitationV2,
  source class, verdict, and optional overlay; migrate JSON, initial SSE, fallback UI, OpenAPI,
  Bruno, and contract tests; preserve a display-only brief only as a derived field.
- **Acceptance criteria:** every rendered clinical claim has exactly its citation association;
  uncited or multiply ambiguous claims fail closed; JSON/SSE/UI contract tests and a deployed
  click-to-source smoke test pass.
- **Dependencies:** response-version decision and client migration.
- **Estimated effort:** Medium.
- **Verification method:** schema/contract tests, deliberately uncited claim test, authenticated UI smoke.
- **Completion status:** **Open**.

## P1 — required compliance gaps

| ID | Requirement / PDF | Status and evidence | Specific remediation | Acceptance criteria | Dependencies | Effort | Verification | Completion |
|---|---|---|---|---|---|---|---|---|
| AF-P1-01 | Public Week 2 core flow (pp.4–5) | **Unable to Verify**; D1–D5. Public services and sign-in work, but authenticated flow and graph flag were not available. | Confirm `W2_GRAPH_ENABLED=1`; run exact-SHA synthetic lab and intake uploads through association, extraction, OpenEMR readback, evidence, answer, citation click/preview, follow-up, and missing-data behavior; retain correlation-only evidence. | One sanitized evidence bundle proves every step and the same SHA; readiness fails if graph/document runtime is disabled. | Tester credentials, synthetic patient, deployment owner. | Medium | Browser + Bruno + one-ID trace. | Open |
| AF-P1-02 | Need-sensitive routing and nested sub-call traces (pp.4,7) | **Partially Met**; R4, T1, D5. Routing is missing-ref sequential and spans are flat worker children. | Route from explicit request/available-input predicates; add OCR/VLM/schema/write and BM25/dense/rerank child spans under their worker span. | Extraction/retrieval can each be skipped for a valid turn; one trace asserts complete parentage and reason codes. | Graph and tracer changes. | Medium | Unit route matrix + trace-tree integration + deployed trace. | Open |
| AF-P1-03 | Typed interfaces, migration accuracy, one authority per data type (p.6) | **Partially Met**; R1, R2, R11, R15, T1. Weak `object`/open-string facades and dual PostgreSQL/OpenEMR artifact copies remain. | Replace weak interfaces with closed DTOs/enums; select canonical derived-artifact authority and make the other a verified projection/cache; document conflict/reconciliation; correct migration notes to 003–005. | Static typing has no weak boundary in scope; read paths use the declared authority; divergence test fails closed; migration doc/tests match files. | Data migration/backward compatibility. | Large | Full mypy/static contract checks, clean upgrade, divergence tests. | Open |
| AF-P1-04 | Operational W2 logs, metrics, traces, dashboards, alerts (pp.5–7) | **Partially Met**; R9, T1, D8. Event schemas/configs exist; production composition defaults to `NullEventSink`. Verified root causes: `RETRIEVAL_COMPLETED`/`RetrievalAttributes` is registered but never emitted (`agent/app/observability/events.py:246-250`); the two encounter-summary emitters zero-fill each other's halves (`agent/app/ingestion/telemetry.py:207`; `agent/app/observability/langfuse.py:486-487`); and `agent/ops/alert_checker.py` evaluates a four-signal Week-1 set (`evaluate()`, `:481-515`), never reads `w2_alerts.json`, and is not scheduled by any workflow, cron, or Railway config. | Wire a PHI-safe production sink; emit all declared events (including `RETRIEVAL_COMPLETED`); fuse or join the encounter summary across emitters; point a scheduled `alert_checker` (or replacement) at `w2_alerts.json`; import dashboard; connect extraction, retrieval-latency, and eval-regression alerts to documented runbooks. | One correlation ID reconstructs all required hops; every required panel has data; three injected alert conditions notify and link to response actions. | Observability tenant and alert destinations. | Medium | Synthetic end-to-end trace plus alert drills. | Open |
| AF-P1-05 | Every PR builds, fully type-checks, tests, scans, and gates (p.6) | **Partially Met**; R8, T4. No deployable-image build on every PR; mypy covers a selected file list. | Add reproducible Docker build/smoke and expand mypy to the application or a tracked ratchet with no unowned exclusions. | A PR that breaks image construction or typing is a required red check; all agent code is covered or time-bounded in a ratchet. | CI minutes and typing cleanup. | Medium | Deliberate Docker/type failures. | Open |
| AF-P1-06 | Current committed eval configuration/results (p.5) | **Partially Met**; R6, T4. Tier 1 says `local-uncommitted`; committed Tier 2 is zero-call INCONCLUSIVE while current CI artifact is green. | Publish a sanitized exact-SHA result or a durable canonical artifact pointer/digest; make stale/mismatched committed evidence fail submission checks. | Repository evidence resolves to a green 50-case artifact whose SHA/digest matches the release and whose PHI scan passes. | Protected artifact publication policy. | Small | Fresh clone resolves and verifies artifact offline. | Open |
| AF-P1-07 | 3–5 minute demo video (p.5) | **Not Met**; R13, D9. A script exists, no recording/link. | Record the exact-SHA synthetic flow with upload, extraction, retrieval, citations, evals, and observability; scan and publish the link. | Accessible 3–5 minute video shows all six items, states the SHA, and passes sensitive-artifact scan. | Working P0/P1 flow and recording owner. | Small | Reviewer playback checklist. | Open |
| AF-P1-08 | Week 2 CPU/memory/latency/throughput, cost, and SLO evidence (pp.5–7) | **Not Met**; R13, T4, D9. Live eval aggregates exist, but the required four-flow profile, W1 comparison, spend/forecast, and resource data do not. | Run exact-SHA bounded profiles for ingestion, extraction, retrieval, graph; capture CPU/memory/throughput/p50/p95, W1 deltas, actual spend, forecast, and bottlenecks; lock SLOs. | Sanitized report contains every required metric, source/method/sample size, capacity headroom, and pass/fail against locked SLOs. | Railway/provider billing access and spend approval. | Medium | Re-run scripts and reconcile provider/Railway totals. | Open |
| AF-P1-09 | Automatic backup plus tested manual recovery and RPO/RTO (p.7) | **Not Met**; R13, D9. Plan/script exists; production backups and restore target unavailable. | Enable versioned automatic backups for OpenEMR documents/MySQL and Agent PostgreSQL; execute isolated restore including migrations, credentials, readback, intents, and repo golden set. | At least seven restore points; measured RPO ≤24h and RTO ≤60m; byte/readback/dedup checks pass; failure procedure is usable. | Railway backup controls, isolated target, keys. | Medium | Observe schedule and execute restore drill. | Open |
| AF-P1-10 | Timeouts and retry logic for all outbound LLM/retrieval calls (p.6) | **Partially Met**; R3, R9, T1. Anthropic has bounded retry; Cohere times out/breaks/falls back but does not retry. | Define bounded retry policy for retriable reranker failures, or document/obtain acceptance that immediate local fallback is the equivalent reliability policy; emit retry/fallback metrics. | Deterministic tests prove retry budget, no retry on permanent errors, fallback, breaker, and total latency bound. | Provider rate-limit semantics. | Small | Fault-injection tests and metrics. | Open |
| AF-P1-11 | GitLab submission-host enforcement (p.5) | **Unable to Verify**; R7, D7. Pipeline bridge exists; project protection/settings were not accessible. | Verify/protect the submission branch and require the Tier 1 plus exact-SHA live-result bridge; prohibit ordinary bypass. | GitLab API/screenshots show settings and a disposable red commit cannot merge. | GitLab maintainer access. | Small | GitLab API and red/green drill. | Open |

## P2 — improvements, ambiguous requirements, and extension work

| ID | PDF pages | Finding | Readiness with disputed item excluded | Readiness with disputed item required | Action / acceptance |
|---|---:|---|---|---|---|
| AF-P2-01 | 3–5 | Critic is “extension work” on p.4 but a Core Deliverable on p.5. | Deterministic claim rejection is already required and implemented. | A graph critic node also exists; deployed activation is UTV. | Obtain grader interpretation; demonstrate critic decision in the authenticated trace. |
| AF-P2-02 | 3–5 | MVP says two document types, while p.5 requires a third. | Two required types are implemented/tested. | `medication_list` is implemented as source/artifact-only. | Clarify grading scope; retain non-write safety tests for third type. |
| AF-P2-03 | 3–5 | Preview/UI and contextual retrieval appear as additional core bullets despite narrower MVP wording. | Core ingestion/RAG still exists. | Both are implemented locally; deployed UI is UTV. | Demonstrate both in the exact-SHA flow. |
| AF-P2-04 | 3–5 | p.5 requests a trend chart from extracted Observation data, but MVP does not require lab Observation writes. | Artifact-backed trends are an optional safe visualization. | Current widget is not Observation-backed. | Clarify whether an artifact-backed trend qualifies; do not add clinical writes without approved data modeling. |
| AF-P2-05 | 3–7 | p.7 forbids extracted values/raw docs in eval datasets while pp.4–5 require stored fixture documents and expected extraction behavior. | Synthetic fixtures present no confirmed real-PHI leak. | Literal “no extracted values” conflicts with evaluable expected fields. | Obtain written synthetic-fixture exception; keep inputs synthetic and scan all generated outputs. |
| AF-P2-06 | 3 | The PDF's four checkpoints (Architecture Defense “4 hours”; MVP Tue 11:59PM; Early Thu 11:59PM; Final Sun noon, all Central) carry weekday deadlines with no calendar dates or acceptance artifacts. | Readiness is judged by outputs, not unverifiable scheduling. | Schedule conformance cannot be established objectively from the repository. | If graded, record dated milestone evidence and rubric interpretation. |

## Unable-to-verify items

- Authenticated lab/intake upload, patient association, extraction, OpenEMR readback, cited answer,
  preview/overlay, follow-up, and missing-data behavior (D4).
- Deployed graph feature flag and nested graph trace (D5).
- GitLab protected/required pipeline settings (D7).
- Operational W2 dashboards, event export, alert destinations, and correlation-only reconstruction (D8).
- Production backup schedule/restore and protected billing/resource totals (D9).
- Current exact-SHA exactly-once OpenEMR/FHIR round trip (REQ-96).

## Resolved or verified requirements

The following high-risk areas have concrete implementation and test evidence: strict Pydantic
lab/intake schemas; rejection of raw VLM output; local bbox grounding; patient/encounter pinning;
durable leased jobs; permanent dedup/write intents; encrypted delegated credentials; curated
versioned corpus; BM25+dense production retrieval and reranking; closed CitationV2; composer/critic
safety; OpenAPI/Bruno contract tests; synthetic-only PHI scans; separate health/readiness; all five
rubric scorers and threshold arithmetic; reproducible 50-case fixtures; and exact-SHA deployment.

## Historical findings disposition

This preserves the earlier `/arch-finalize` findings while replacing planning-coverage claims
with present evidence.

| Prior IDs | Current disposition |
|---|---|
| C1, C3, I20, I26, I27 | **Resolved:** thresholds, boolean judge configuration, two-tier posture, output scanning, known-fail/scorer drills are implemented (R6–R7, T2–T4). |
| C2, I25 | **Open → AF-P0-02:** live Tier 2 exists, but the required golden executor still substitutes pseudo retrieval. |
| I1–I4, I8, I11–I14, I18, I21–I24, I30 | **Resolved in code/tests; deployment UTV where applicable:** durable jobs/credentials/intents, OCR limits, bbox/preview/contracts, reader stack, auth/query screens, and sensitive artifacts (R1–R2, R9–R11, T1). |
| I5, I6, I10 | **Resolved/accepted design:** per-turn graph has no checkpointer, upload uses the pinned clinician, and the graph has an eight-hop refusal bound (R4). |
| I7 | **Open → AF-P1-09:** state classification is documented, but automatic backup/restore evidence is absent. |
| I9, I19 | **Partially resolved → AF-P1-01/10:** index/readiness and local fallback exist; production reranker behavior remains only partly verified. |
| I15–I17 | **Open → AF-P1-03:** implementation advanced, but migration wording, weak contracts, and dual artifact authority remain. |
| I28 | **Open → AF-P0-01/AF-P1-11:** workflows exist; unbypassable GitHub/GitLab enforcement is absent/UTV. |
| I29 | **Partially resolved:** W1/W2 split and debt are documented; remaining authority/observability evidence is tracked in AF-P1-03/04. |
| I31 | **Open → AF-P1-04:** terminal event schema exists, but production W2 event export defaults to a null sink. |

## Complete requirements traceability matrix

Statuses use only the five values required by the audit request. “Close AF-…” refers to the
fully specified remediation and acceptance criteria above.

| ID | Priority | Requirement | PDF Page | Repository Evidence | Test Evidence | Deployment Evidence | Status | Gap | Remediation | Acceptance Criteria |
|----|----------|-------------|----------|---------------------|---------------|---------------------|--------|-----|-------------|---------------------|
| W2-REQ-01 | P0 | Eval-driven CI is non-negotiable and must block regressions. | 2 | R6–R7 | T2–T4 | D6–D7 | Not Met | Gate is bypassable and retrieval-blind. | Close AF-P0-01/02. | Both red drills are required and block merge. |
| W2-REQ-02 | P1 | Ingest `lab_pdf` and `intake_form` with strict schemas. | 3 | R1–R2 | T1 | D4 | Met | — | — | Keep both schema/flow suites green. |
| W2-REQ-03 | P1 | Small corpus with keyword+dense retrieval and rerank. | 3 | R3, R12 | T1 | D1–D2 | Met | — | — | Public result remains version-pinned and reranked. |
| W2-REQ-04 | P1 | Supervisor routes to two named workers with logged handoffs. | 3 | R4 | T1 | D5 | Partially Met | Deployment and need-sensitive routing unproven. | Close AF-P1-01/02. | Deployed trace shows explainable conditional hops. |
| W2-REQ-05 | P0 | 50-case boolean PR-blocking eval hook/equivalent. | 3 | R6–R7 | T2–T4 | D6–D7 | Not Met | Not an enforced required check; pseudo retrieval. | Close AF-P0-01/02. | Red PR cannot merge. |
| W2-REQ-06 | P1 | Deployed grounded UI, cost/latency report, and video. | 3 | R5, R13–R14 | T1, T4 | D1–D4, D9 | Partially Met | Auth flow UTV; report/video incomplete. | Close AF-P1-01/07/08. | Exact-SHA demo, report, video all accepted. |
| W2-REQ-07 | P1 | Patient-associated file, OpenEMR source, strict JSON, fact lineage. | 3 | R1–R2, R11 | T1 | D4 | Partially Met | Live source/write round trip UTV. | Close AF-P1-01. | Authenticated one-ID round trip passes. |
| W2-REQ-08 | P1 | Self-source relevant clinical-guideline corpus. | 4 | R12 | T1 | D2 | Met | — | — | Manifest provenance/hash/license checks stay green. |
| W2-REQ-09 | P1 | Keyword+vector retrieval, rerank, evidence metadata. | 4 | R3, R12 | T1 | D2 | Met | — | — | Production search returns ranked metadata. |
| W2-REQ-10 | P2 | ColQwen2/multi-vector indexing is stretch. | 4 | R3, R12 | T1 | D2 | Met | Optional stretch intentionally not selected. | — | Reliable core hybrid retriever remains green. |
| W2-REQ-11 | P1 | Supervisor decides extraction, retrieval, final readiness. | 4 | R4 | T1 | D5 | Partially Met | Decisions are missing-ref sequential. | Close AF-P1-02. | Valid turns independently skip each worker. |
| W2-REQ-12 | P0 | 50 cases cover extraction, retrieval, citations, refusals, missing data. | 4 | R6 | T2–T4 | D2, D10 | Partially Met | Retrieval cases do not exercise production retriever. | Close AF-P0-02. | Golden cases assert real hit/miss/rerank behavior. |
| W2-REQ-13 | P1 | Deploy flow, traces, demo, W1-user mapping. | 4 | R9, R13–R14 | T1 | D4, D8–D9 | Partially Met | Trace/demo deployment evidence incomplete. | Close AF-P1-01/04/07. | Exact-SHA trace and video show mapping. |
| W2-REQ-14 | P1 | Typed `attach_and_extract` or equivalent tool. | 4 | R2 | T1 | D4 | Partially Met | Equivalent exists but weak facade types remain. | Close AF-P1-03. | Closed typed facade and flow test pass. |
| W2-REQ-15 | P1 | Tool supports required two document types. | 4 | R1–R2 | T1 | D4 | Met | — | — | Both flow suites remain green. |
| W2-REQ-16 | P1 | Store source document in OpenEMR. | 4 | R2, R11 | T1 | D4 | Partially Met | Current live write/readback UTV. | Close AF-P1-01. | Byte/digest readback at release SHA. |
| W2-REQ-17 | P1 | Return strict-schema JSON. | 4 | R1–R2 | T1 | D4 | Met | — | — | Wrong/incomplete provider output stays rejected. |
| W2-REQ-18 | P1 | Persist facts as appropriate FHIR/OpenEMR records. | 4 | R2, R11, R15 | T1 | D4 | Partially Met | Live write UTV; artifact authority duplicated. | Close AF-P1-01/03. | Declared authority and live readback agree. |
| W2-REQ-19 | P1 | Use Pydantic/Zod/equivalent strict schemas. | 4–5 | R1 | T1 | D4 | Met | — | — | Strict validation tests stay green. |
| W2-REQ-20 | P1 | Lab schema has seven required fields/citation. | 4–5 | R1 | T1 | D4 | Met | — | — | Schema snapshot/validation stays green. |
| W2-REQ-21 | P1 | Intake schema has demographics, concern, meds, allergies, family history, citation. | 4–5 | R1 | T1 | D4 | Met | — | — | Schema snapshot/validation stays green. |
| W2-REQ-22 | P1 | Index corpus and retrieve sparse+dense. | 4 | R3, R12 | T1 | D2 | Met | — | — | Public and integration results prove both legs. |
| W2-REQ-23 | P1 | Rerank with Cohere or equivalent. | 4 | R3 | T1 | D1–D2 | Met | — | — | Active reranker readiness/search stays green. |
| W2-REQ-24 | P1 | Feed only top grounded evidence to answer model. | 4 | R3, R5 | T1 | D4 | Met | — | — | Context-cap/grounding tests remain green. |
| W2-REQ-25 | P1 | Use inspectable orchestration framework. | 4 | R4 | T1 | D5 | Partially Met | LangGraph exists; deployed activation UTV. | Close AF-P1-01. | Deployed graph trace proves use. |
| W2-REQ-26 | P1 | Workers are intake-extractor and evidence-retriever. | 4 | R4 | T1 | D5 | Met | — | — | Named-node contract remains green. |
| W2-REQ-27 | P0 | Every final clinical claim has machine-readable citation metadata. | 5 | R5 | T1 | D4 | Not Met | JSON/fallback flatten associations. | Close AF-P0-03. | Every claim serializes its own CitationV2. |
| W2-REQ-28 | P1 | Citation has five prescribed fields. | 5 | R1, R5 | T1 | D2 | Met | — | — | Closed CitationV2 tests remain green. |
| W2-REQ-29 | P1 | PDF citation visual bounding-box overlay. | 5 | R1, R5 | T1 | D4 | Partially Met | Implemented/tested; deployment UTV. | Close AF-P1-01. | Authenticated click opens correct page/box. |
| W2-REQ-30 | P1 | Build a 50-case golden set. | 5 | R6 | T2, T4 | D10 | Met | — | — | Exactly 50 valid cases remain reproducible. |
| W2-REQ-31 | P0 | PR-blocking Git Hook/equivalent. | 5 | R7 | T3–T4 | D6–D7 | Not Met | Hook optional; branch unprotected. | Close AF-P0-01. | Required red status blocks merge. |
| W2-REQ-32 | P1 | Five named boolean rubric categories. | 5 | R6 | T2–T4 | D10 | Met | — | — | Results retain all five denominators. |
| W2-REQ-33 | P1 | Fail below threshold or >5pp regression. | 5 | R6 | T3–T4, T6 | D10 | Met | Delta rule is live-tier only (see AF-P0-01 gap note); PR tier enforces the ≥0.90 floor and 100% invariants. | — | Boundary and red tests remain green/red as designed. |
| W2-REQ-34 | P1 | Encounter records sequence, latency, tokens, cost, hits, confidence, eval. | 5 | R9 | T1 | D8 | Partially Met | Schema exists; production sink/reconstruction unproven. | Close AF-P1-04. | One deployed correlation query returns all fields. |
| W2-REQ-35 | P0 | Logs contain no raw PHI. | 5 | R6, R9 | T2–T3 | D1 | Met | No real PHI found. | — | Known-leak stays red; outputs stay clean. |
| W2-REQ-36 | P0 | Small regression must be blocked. | 5 | R7 | T3, T6–T7 | D6–D7 | Not Met | Jobs turn red (proven by five committed drills, harness self-tests, and independent re-execution) but merge need not block; retrieval-stack regressions escape the golden gate. | Close AF-P0-01/02. | Both regression classes block merge. |
| W2-REQ-37 | P1 | Core has lab PDF and intake form. | 5 | R1–R2 | T1 | D4 | Met | — | — | Required flows remain green. |
| W2-REQ-38 | P1 | One supervisor and two named workers. | 5 | R4 | T1 | D5 | Partially Met | Deployed graph UTV. | Close AF-P1-01. | Deployed trace shows all three. |
| W2-REQ-39 | P1 | Basic hybrid RAG plus rerank. | 5 | R3 | T1 | D2 | Met | — | — | Public/integration retrieval stays green. |
| W2-REQ-40 | P1 | 50-case dataset with boolean rubrics. | 5 | R6 | T2–T4 | D10 | Partially Met | Dataset's retrieval expectations are not production-equivalent. | Close AF-P0-02. | Real retrieval behaviors are asserted. |
| W2-REQ-41 | P0 | PR-blocking eval CI and observable deployed demo. | 5 | R7, R9 | T3–T4 | D4, D6, D8 | Not Met | Neither merge block nor full deployed observability proven. | Close AF-P0-01, AF-P1-01/04. | Required checks plus observable exact-SHA demo. |
| W2-REQ-42 | P2 | Critic agent rejects uncited/unsafe action claims. | 4–5 | R4–R5 | T1 | D5 | Conflicting Requirement | p.4 extension vs p.5 core. | Clarify AF-P2-01; demo existing critic. | Grader accepts scope and deployed behavior. |
| W2-REQ-43 | P2 | Click-to-source UI and preview. | 3–5 | R5 | T1 | D4 | Conflicting Requirement | MVP/additional-core classification conflicts. | Clarify AF-P2-03; run UI smoke. | Scope decision plus deployed click/preview pass. |
| W2-REQ-44 | P2 | Third document type. | 3–5 | R1–R2 | T1 | D4 | Conflicting Requirement | Two-type MVP vs p.5 third type. | Clarify AF-P2-02; retain medication safety. | Written scope; selected flow passes. |
| W2-REQ-45 | P2 | Lab trend chart using extracted Observation data. | 3–5 | R5, R15 | T1 | D4 | Conflicting Requirement | Additional-core conflict; implementation is artifact-backed. | Clarify AF-P2-04. | Grader accepts model or approved Observation design passes. |
| W2-REQ-46 | P2 | Contextual retrieval improvement. | 3–5 | R3, R12 | T1 | D2 | Conflicting Requirement | Additional-core vs narrower MVP. | Clarify AF-P2-03. | Scope decision; improved retrieval remains green. |
| W2-REQ-47 | P1 | GitLab fork, setup, deployed link, environment docs. | 5 | R7, R14 | T4 | D7, D10 | Partially Met | Artifacts exist; GitLab enforcement/settings UTV. | Close AF-P1-11. | Fresh clone setup and protected pipeline verified. |
| W2-REQ-48 | P1 | Root Week 2 architecture document. | 5 | `W2_ARCHITECTURE.md` | T1 | D10 | Met | — | — | Current-state labels/evidence stay accurate. |
| W2-REQ-49 | P1 | Two schemas with citations and validation tests. | 5 | R1 | T1 | D4 | Met | — | — | Schema tests stay green. |
| W2-REQ-50 | P0 | 50 cases, expected behavior, rubrics, judge config, results. | 5 | R6 | T2–T4 | D10 | Partially Met | Current committed result stale; real retrieval absent. | Close AF-P0-02/AF-P1-06. | Release-SHA result and production-equivalent cases. |
| W2-REQ-51 | P0 | CI evidence blocks regressions. | 5 | R7 | T3–T4 | D6–D7 | Not Met | Red evidence exists, enforced merge block does not. | Close AF-P0-01. | Red drill cannot merge. |
| W2-REQ-52 | P1 | 3–5 minute six-element demo video. | 5 | R13 | — | D9 | Not Met | Script only. | Close AF-P1-07. | Accessible scanned video meets shot list/time. |
| W2-REQ-53 | P1 | Actual dev spend, forecast, p50/p95, bottlenecks. | 5 | R13 | T4 | D9 | Not Met | Eval aggregate is not complete report. | Close AF-P1-08. | Final report contains every required field. |
| W2-REQ-54 | P0 | Public deployed app with working core flow. | 5 | R14 | D1–D3 | D4–D5, D11 | Unable to Verify | Public shell, exact-SHA health/readiness, live OpenAPI, and attested document runtime verified; authenticated core flow not exercised. | Close AF-P1-01. | Exact-SHA authenticated acceptance flow passes. |
| W2-REQ-55 | P1 | Typed contracts at every W2 interface. | 6 | R1, R4, R11 | T1 | D4 | Partially Met | `object` and open-string facades remain. | Close AF-P1-03. | Closed types/static checks cover all boundaries. |
| W2-REQ-56 | P1 | Week 1 schema changes have migration notes. | 6 | R11 | T1 | D10 | Partially Met | Old note named nonexistent migration 002. | Close AF-P1-03. | Notes and clean-upgrade tests match 003–005. |
| W2-REQ-57 | P1 | One source of truth; no silent overwrite. | 6 | R11, R15 | T1 | D4 | Partially Met | Derived artifact has dual durable authority. | Close AF-P1-03. | Declared authority and divergence test pass. |
| W2-REQ-58 | P1 | W2 ingestion/retrieval/routing/worker observability. | 6 | R9 | T1 | D8 | Partially Met | Schemas/logs exist; sink/panels unproven. | Close AF-P1-04. | Deployed panels and one-ID trace show all measures. |
| W2-REQ-59 | P1 | Ingestion and retrieval SLOs. | 6 | R13 | T1 | D9 | Partially Met | Working ceilings, no qualifying baseline/lock. | Close AF-P1-08. | Exact-SHA profiles lock and meet SLOs. |
| W2-REQ-60 | P1 | Timeouts, retries, queues, breakers. | 6 | R2–R3, R9 | T1 | D1 | Partially Met | Cohere has fallback/breaker but no retry loop. | Close AF-P1-10. | Fault matrix proves bounded policy for every call. |
| W2-REQ-61 | P1 | Canonical schemas; raw VLM never bypasses. | 6 | R1–R2 | T1 | D4 | Met | — | — | Bypass tests remain red/green as intended. |
| W2-REQ-62 | P1 | Correlation ID reconstructs ingestion/handoffs/writes. | 6 | R2, R4, R9 | T1 | D8 | Partially Met | Production sink and full reconstruction unproven. | Close AF-P1-04. | One deployed ID resolves every hop/write. |
| W2-REQ-63 | P1 | Searchable PHI-free W2 structured events. | 6 | R9 | T1–T3 | D8 | Partially Met | Some events lack emit sites; sink defaults null. | Close AF-P1-04. | Required events searchable by all three IDs. |
| W2-REQ-64 | P1 | Required W1/W2 dashboards. | 6 | R9 | T1 | D8 | Partially Met | JSON config only; operational import/data UTV. | Close AF-P1-04. | Every named panel has exact-SHA data. |
| W2-REQ-65 | P1 | Every PR build/lint/type/test/coverage/audit/security. | 6 | R8 | T4 | D6 | Partially Met | No image build; mypy selected surface; merge unprotected. | Close AF-P1-05 and AF-P0-01. | Required complete quality suite blocks merge. |
| W2-REQ-66 | P1 | Eval gate includes schema, handoff, extraction regressions. | 6 | R6–R8 | T1–T3 | D10 | Met | — | — | These suites remain in required Tier 1. |
| W2-REQ-67 | P1 | Architecture documents unit/integration/golden/not-tested. | 6 | `W2_ARCHITECTURE.md:section 9` | T1–T4 | D4–D9 | Met | — | — | Document stays synchronized with evidence. |
| W2-REQ-68 | P1 | Test categories document guarded failure modes. | 6 | `W2_ARCHITECTURE.md:section 9` | T1–T3 | D4 | Met | — | — | Failure mapping remains current. |
| W2-REQ-69 | P1 | Runbooks cover four named W2 failures. | 6 | R9, `W2_RUNBOOKS.md` | T1 | D8 | Met | — | — | Each injected failure maps to action. |
| W2-REQ-70 | P1 | Runnable collection covers upload/status/retrieval/full flow. | 6 | R10 | T1 | D4 | Met | — | — | Bruno contract tests and authenticated run pass. |
| W2-REQ-71 | P1 | Four-flow CPU/memory/latency/throughput and W1 comparison. | 6 | R13 | — | D9 | Not Met | Required measurements absent. | Close AF-P1-08. | Complete reproducible baseline report accepted. |
| W2-REQ-72 | P1 | W2 uses one W1-compatible structured-log format. | 6 | R9 | T1 | D8 | Met | — | — | No application plain-text event path appears. |
| W2-REQ-73 | P1 | Correlation ID across workers, VLM, retrieval, EHR. | 6 | R2, R4, R9 | T1 | D8 | Partially Met | Isolated propagation tested; deployed reconstruction UTV. | Close AF-P1-04. | One deployed ID reconstructs all boundaries. |
| W2-REQ-74 | P1 | Worker spans nested under supervisor; sub-calls nested within workers. | 7 | R4 | T1 | D5 | Not Met | Only flat worker-hop children; no nested sub-calls. | Close AF-P1-02. | Trace tree asserts full nesting. |
| W2-REQ-75 | P1 | Separate health/ready; storage/index/reranker/degraded checks. | 7 | R9 | T1 | D1, D11 | Met | — (a served `degraded` readiness snapshot was observed live, evidencing non-binary status in production). | — | Live hard/soft probes remain accurate. |
| W2-REQ-76 | P1 | Three alerts with response actions. | 7 | R9, `W2_RUNBOOKS.md` | T1 | D8 | Partially Met | Definitions/runbooks exist; active wiring UTV. | Close AF-P1-04. | Three alert drills notify and link actions. |
| W2-REQ-77 | P1 | Committed synchronized OpenAPI 3.0 with contract tests. | 7 | R10 | T1 | public `/openapi.json` | Met | — | — | Spec/mounted/Bruno sync remains green. |
| W2-REQ-78 | P1 | Full fixture/stub integration without live APIs. | 7 | R1–R6, R10 | T1–T2 | D10 | Met | — | — | Network-disabled integration/eval remains green. |
| W2-REQ-79 | P1 | Owner/lineage/access/validation for labs, intake, chunks, citations. | 7 | R11–R12, R15; architecture §3 | T1 | D4 | Partially Met | Artifact source-of-truth remains dual. | Close AF-P1-03. | Authority ledger matches all read/write paths. |
| W2-REQ-80 | P2 | Analytics/eval artifacts contain no IDs, raw docs/text/images, extracted values. | 7 | R6, R9 | T2–T3 | D8 | Conflicting Requirement | Expected extraction fixtures require synthetic docs/values. | Clarify AF-P2-05. | Written exception plus clean output scans. |
| W2-REQ-81 | P1 | Automatic/manual backup, recovery, RPO/RTO. | 7 | R13 | — | D9 | Not Met | Plan only; no backup/restore evidence. | Close AF-P1-09. | Automatic points and measured drill meet targets. |
| W2-REQ-82 | P1 | Golden set reproducible from repository. | 7 | R6 | T2 | D10 | Met | — | — | Fresh clone runs recorded gate. |
| W2-REQ-83 | P2 | Do not attempt five doc types before two work. | 7 | R1–R2 | T1 | D4 | Met | Three types, with required two strongly tested. | — | Required two remain release-gated. |
| W2-REQ-84 | P0 | Never use VLM answer without schema/source metadata. | 7 | R1–R2 | T1 | D4 | Met | — | — | Provider bypass remains impossible. |
| W2-REQ-85 | P1 | Supervisor is inspectable; handoffs explainable. | 7 | R4 | T1 | D5, D8 | Partially Met | Records exist; need-sensitive decisions and deployment UTV. | Close AF-P1-01/02. | Deployed trace explains each conditional hop. |
| W2-REQ-86 | P1 | LLM judge has clear boolean rubric. | 7 | R6 | T2–T4 | D10 | Met | — | — | Pinned config and actionable results remain. |
| W2-REQ-87 | P0 | Never send raw text, IDs, screenshots to SaaS observability. | 7 | R6, R9 | T1–T3 | D1, D8 | Met | No confirmed leakage. | — | Known-leak test and tenant review stay clean. |
| W2-REQ-88 | P1 | Build on Week 1 auth/tools/verification/observability/evals. | 3 | R4–R9, R14 | T1–T4 | D1–D3 | Met | — | — | W1 regression suites remain green. |
| W2-REQ-89 | P1 | Document and resolve Week 1 debt first. | 3 | R11, R14–R15 | T1 | D8–D9 | Partially Met | Authority/observability/ops debt remains. | Close AF-P1-03/04/09. | Debt ledger has evidence-backed closure. |
| W2-REQ-90 | P1 | README separates W1/W2 and removes grader guesswork. | 3 | R14 | T1 | D1–D3 | Met | — | — | Fresh grader follows documented entry points. |
| W2-REQ-91 | P1 | Useful/safe on imperfect scans. | 2 | R1–R2, R5 | T1–T2 | D4 | Met | — | — | Degraded scan cases remain safe. |
| W2-REQ-92 | P1 | Useful/safe on incomplete records. | 2 | R5–R6 | T1–T2 | D4 | Met | — | — | Missing-data cases remain explicit. |
| W2-REQ-93 | P1 | Useful/safe follow-up behavior. | 2 | R4–R6 | T1–T2 | D4 | Met | — | — | Multi-turn grounding tests remain green. |
| W2-REQ-94 | P0 | Use only demo/synthetic data. | 2–3 | R6, R14 | T2–T3 | D1–D4 | Met | No real PHI found. | — | Dataset/prod policy remains synthetic-only. |
| W2-REQ-95 | P0 | Treat prompts/fields/images/traces/screenshots as sensitive. | 2–3 | R6, R9 | T1–T3 | D8 | Met | — | — | Sensitive-artifact inventory/scans stay complete. |
| W2-REQ-96 | P0 | OpenEMR round trip has no duplicate/untraceable records. | 3 | R2, R11 | T1 | D4 | Unable to Verify | Exactly-once design/tests exist; current live proof absent. | Close AF-P1-01. | Exact-SHA timeout/reconcile/readback drill passes live. |
| W2-REQ-97 | P0 | Unsupported extracted facts are visible, never invented. | 2 | R1–R2, R5 | T1–T2 | D4 | Met | — | — | Ungrounded/adversarial cases remain refused. |
| W2-REQ-98 | P0 | Separate chart/guideline evidence; medication/lab claims cite sources. | 2 | R1, R5 | T1 | D4 | Partially Met | Internal separation works; final association is flat. | Close AF-P0-03. | Every final claim owns its source link. |
| W2-REQ-99 | P1 | Small multi-agent graph does not lose grounding. | 2 | R4–R5 | T1 | D5 | Partially Met | Graph deployment UTV; final JSON association incomplete. | Close AF-P0-03/AF-P1-01. | Deployed graph preserves per-claim citations. |
| W2-REQ-100 | P2 | Architecture Defense checkpoint (“4 hours”). | 3 | `docs/week2/W2_DEFENSE_PREP.md`, `docs/week2/W2_ARCHITECTURE_DRAFT.md`; no acceptance record | — | — | Unable to Verify | PDF gives a duration, not a calendar date; no graded-acceptance artifact in repo. | Close AF-P2-06 if graded. | Dated defense/acceptance evidence recorded. |
| W2-REQ-101 | P2 | MVP checkpoint — Tuesday @ 11:59PM Central. | 3 | Commit history exists but has no authoritative mapping to the checkpoint calendar | — | — | Unable to Verify | Weekday deadline has no calendar date or acceptance record in the repository. | Close AF-P2-06 if graded. | Dated milestone evidence accepted. |
| W2-REQ-102 | P2 | Early Submission checkpoint — Thursday @ 11:59PM Central. | 3 | Commit history exists but has no authoritative mapping to the checkpoint calendar | — | — | Unable to Verify | Same schedule ambiguity. | Close AF-P2-06 if graded. | Dated milestone evidence accepted. |
| W2-REQ-103 | P2 | Final checkpoint — Sunday @ Noon Central. | 3 | Commit history exists but has no authoritative mapping to the checkpoint calendar | — | — | Unable to Verify | Same schedule ambiguity; open P0/P1 rows above are the substantive blockers for this checkpoint. | Close AF-P2-06 if graded. | Dated milestone evidence accepted. |

## Final readiness decision

The repository demonstrates substantial, tested implementation and an exact-SHA public service,
but final submission is blocked by three hard requirements: enforceable merge gating,
production-equivalent retrieval evaluation, and per-claim final citation traceability. After
those are fixed, the authenticated deployed flow, operational observability, video,
performance/cost evidence, and backup/restore drill remain required before this can be marked
Ready.
