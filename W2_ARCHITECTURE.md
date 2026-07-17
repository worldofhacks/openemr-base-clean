# W2_ARCHITECTURE.md — Multimodal Evidence Agent (Week 2, binding)

> **Binding architecture for Week 2** (PRD deliverable: `./W2_ARCHITECTURE.md`). Finalized
> 2026-07-13 by an adversarial /arch-finalize pass — gap audit, findings register, and the
> full 99-row PRD coverage table live in `docs/week2/W2_gap-audit.md`. Decisions are ADRs in
> `docs/week2/W2_DECISIONS.md` (W2-D1..D10); external facts are `docs/week2/W2_RESEARCH.md`
> (W2-R1..R6); write/upload-surface findings are `docs/week2/W2_AUDIT.md` (W2-F1..F23); use
> cases are `docs/week2/W2_USERS.md` (UC-W2-1..4). Builds on the frozen Week 1 system
> (`docs/week1/ARCHITECTURE.md`, cited as W1 D#/F#/§). Build posture: **production-grade**.
> This document supersedes `docs/week2/W2_ARCHITECTURE_DRAFT.md` and is the contract
> /tasks-gen decomposes. No W2 agent code exists yet.

---

## One-page summary

Week 2 makes the co-pilot **see documents**. A physician uploads a scanned lab PDF or an
intake form from the chart; `attach_and_extract` stores the source in OpenEMR via the
standard documents API — this fork has **no FHIR write** for DocumentReference/Observation,
verified three ways (W2-R5), and the PRD's own "or OpenEMR records" clause sanctions the
transport — content-hashes it for idempotency, and reads it: born-digital PDFs by their
exact text layer, scans by local Tesseract OCR, both emitting words with normalized
page-relative coordinates (W2-D3, W2-R6). A **LangGraph supervisor** (W2-D2 — W1 D6's own
invalidation clause fired) routes work to two workers. The **intake-extractor** sends page
images to Claude — the W1 provider is also the VLM, zero new PHI processors — and its output
is validated into **strict Pydantic models** (hard reject; raw VLM output never bypasses the
schema); every surviving field must then **ground** in the words+boxes layer to earn its
citation and bounding box — ungrounded or disagreeing fields render UNSUPPORTED. Pydantic
proves the shape; grounding proves the content is on the page. The **evidence-retriever**
runs hybrid BM25 + bge-small retrieval over a license-verified VA/DoD guideline trio
(W2-D4, W2-R2) and reranks with **Cohere behind a one-env-var seam**: the owner's paid
production key resolves W2-R3's trial-terms objections, and if it is not provisioned by
Monday EOD, MVP ships the implemented local mxbai reranker instead — the PRD's "or
equivalent." The PHI-free-query contract is **enforced, not asserted**: a deterministic
query builder plus an outbound screen that fails closed.

Ingestion is an **asynchronous job on a claimed/leased durable queue**: job rows persist in
the agent Postgres store; a separate encrypted delegated-job credential can refresh after
interactive idle expiry; and source, grounded-artifact, and grounded intake-vitals creates
share W2-D10's `{pending, unknown, complete}` intent protocol. A possibly committed remote
write is reconciled by its remotely discoverable marker before any retry and is never
blindly re-posted. Every create is **verified by re-read** before the job reports complete.
Derived facts write back **append-only with permanent patient-scoped lineage**: worst-case
injection is a quarantined, machine-authored, voidable record — never a silent edit (W2-D1).
Answers compose patient facts and guideline evidence as visually separate classes; every
claim carries the prescribed CitationV2 shape; document claims render a **PDF bounding-box
overlay** served only inside the pinned session. Every hop logs under the W1 correlation ID;
supervisor spans parent worker spans.

The graded hard gate is a **two-tier, 50-case boolean-rubric eval gate** (W2-D5, W2-D8):
Tier 1 — every PR and the local Git Hook — is fully offline (unit + integration tests on
fixture documents with stubbed models, plus the deterministic rubric subset), satisfying
the PRD's no-live-API clause verbatim; Tier 2, the graded gate itself, is a **PR-blocking
GH Actions job running all 50 cases against live Anthropic** — real agent turns plus the
pinned judge — with deterministic categories required at 100%, a separately thresholded
`factually_consistent` category, and a committed baseline, so a grader-injected
prompt or behavior regression fails CI, not just a code regression. Scorer self-tests
and a pre-submission regression drill prove the gate can go red before graders try to make
it. Owned tradeoffs stand un-softened: Cohere is a vendor seam, LangGraph is new surface
kept thin, OCR fidelity degrades to UNSUPPORTED rather than wrong, scopes widen from
read-only to read + narrow create, and — because the OpenEMR write surface enforces no
patient/encounter ownership, scope ceiling, vital range, or idempotency server-side — those
containment controls live in the agent and gate the write path (W2-D9) — said plainly. The
W1 thesis is unchanged: the model drafts, deterministic checks decide — Week 2 extends it
to pixels.

---

> ## Verification errata (2026-07-13 — dated revision; probe-verified facts, no decision changes)
> Source: W2-F1 independent live verification (W2_AUDIT.md, findings W2-F7..F11; W2-D1
> addendum). W2-F1 CONFIRMED — route-level 404s on FHIR POSTs with maximal write scopes.
> Corrections binding on the sections below:
> 1. **Upload contract (§2a, §3):** `POST /api/patient/:pid/document` returns **HTTP 200
>    body `true` with NO document id** (DocumentRestController.php:120), not 201. The
>    document id is discovered via collection GET keyed on unique filename/content-hash;
>    `attach_and_extract` and the job's `writeback.created` lineage record account for
>    this discovery step.
> 2. **Read-back path (§3 re-read verification):** the standard REST document download
>    returns **500** in this stack. ~~The cause is a CSRF-key defect via
>    `DocumentService::getFile`.~~ **SUPERSEDED — Post-review remediation (2026-07-13):**
>    the cause is raw document bytes passed as `BinaryFileResponse`'s filename, not CSRF.
>    The verified round-trip read-back is the **FHIR
>    projection**: `DocumentReference/:uuid → Binary/:uuid`, proven byte-exact (SHA-256
>    match). Vitals round-trip is fully proven through `GET /fhir/Observation?category=
>    vital-signs` (15 resources from one test vital).
> 3. **$docref wording (§3 discrepancy note):** describe as "server-generated CCD
>    persistence" — it DOES write internally; the true claim is "no client-supplied FHIR
>    create path," and the CapabilityStatement's `DocumentReference.create` declaration
>    is a generator artifact (maps every POST to create — RestControllerHelper.php:445);
>    never trust it (W2-F7).
> 4. **Provisioning (§2a, W2-F4 resolved):** minimum scope surface `api:oemr
>    user/document.crs user/vital.crus user/Observation.rs` (+ `user/DocumentReference.rs
>    user/Binary.read` for read-back). **There is no supported persisted-scope edit path;
>    MVP requires a REPLACEMENT SMART client registration** (W1+W2 scope union,
>    authorization_code+refresh, swap SMART_CLIENT_ID/SECRET, admin-enable, ~~disable the
>    old client after cutover~~ **SUPERSEDED — Post-review remediation (2026-07-13):
>    disable it and retire both access and refresh tokens by revocation or by waiting out
>    both lifetimes** — E9 lesson). Staff ACLs must permit patients/docs write.

> ## Post-audit revision (2026-07-13 — write-surface controls, cites W2-D9)
> Source: a separate read-only adversarial re-audit of the write/upload surface
> (W2_AUDIT.md findings W2-F12..F23; decision W2-D9). It proved OpenEMR's standard write
> routes enforce, on create, **no** server-side launch-patient/encounter ownership
> (W2-F13), **no** registered-scope ceiling (W2-F12), **no** category ACL (W2-F14), **no**
> vital physiological range (W2-F15), **no** clinical attribution (W2-F16), and **no**
> idempotency (W2-F18); and that disabling a client does **not** revoke its live access
> tokens (W2-F17). The write **transport** decision (W2-D1) is unchanged — no
> client-supplied FHIR CRUD exists — but the earlier "one build-time checklist item, no
> finding blocks" posture is superseded: **the write path is sound only because it is gated
> behind mandatory agent-side controls** (patient-pin + encounter-ownership preflight,
> exact-scope provisioning + startup granted-scope assertion, ~~fixed category-id + ACL~~
> **canonical path → expected category-ID/ACL preflight (Post-review remediation
> 2026-07-13)**,
> vital-range bounding, no caller-supplied author, ~~the purgeable idempotency ledger~~
> **permanent patient-bound dedup/lineage plus D10 durable intents and reconciliation
> (Post-review remediation 2026-07-13)**, and a
> token-revoking cutover). These are folded into §4/§5/§6a below and threaded into the plan
> (W2-OA3/M8/M11/W2-1). Two precision corrections carried here (not editing the errata
> block above): the document-download **500 is raw-bytes-passed-as-`BinaryFileResponse`
> filename, not a CSRF-key defect** (W2-F9) — the FHIR `DocumentReference/:uuid →
> Binary/:uuid` read-back stands regardless; and a **GET is not a guarantee of no DB
> mutation** — metadata/service-construction GETs can backfill UUIDs with table writes
> (W2-F21), so any "read-only" claim distinguishes "no HTTP write method" from "no DB
> write."

> ## Post-review remediation (2026-07-13 — complete robust Final contract, cites W2-D10)
> This dated revision is binding on every affected section below and marks superseded
> language rather than erasing the review trail. W2-D10 locks the complete write path:
> source document, grounded extraction artifact, and grounded intake-vitals writes all use
> one exactly-once intent/reconciliation contract and all D9 controls; no write leg may be
> cut. Canonical schemas are frozen before W2-M6; deterministic eval categories are 100%
> invariants; canonical input fixtures are excluded from the leak scan while every generated
> artifact is scanned; the queue is claimed/leased; background credentials are independent
> of interactive idle expiry; permanent patient-scoped dedup/lineage is not purged; and
> agent Postgres is a PHI-bearing backed-up authority. Numeric SLOs are measured and locked
> once at **Early (2026-07-16)**; Final validates against them. Only the explicitly named
> stretch tier may be cut. All core PRD deliverables, every engineering requirement, D9/D10,
> both eval tiers, the full write path, and GitLab submission-host enforcement are complete
> and robust by **Final (2026-07-19)**.

> ## Post-audit closeout revision (2026-07-15 — cites W2-D11..D21; binding on §2/§2a/§3/§4a/§6/§6a/§7/§8a)
> This dated revision reconciles the architecture to two independent gap audits (Claude + Codex)
> on canonical `4f644d9` and folds in the closeout decisions W2-D11..D21. It marks current state and
> superseded language; it does not restructure the §s below or change any frozen grounding,
> patient-pin, exactly-once, PHI, or adversarial invariant. Build sequencing lives in the
> `W2_IMPLEMENTATION_PLAN.md` 2026-07-15 closeout overlay (lanes W2-C1..C13).
>
> **Audit verdict.** The deployed upload→extract→ground→write/readback→cite→answer pipeline is live
> for both document types but is **not yet a rubric-safe MVP**: the graded eval gate (§7) does not
> execute the 50 golden cases through the agent (CI runs the retired W1 10-case runner), has no
> committed baseline and no >5pp delta, and 5 golden cases conflict with the scorer contract; two
> answer-path contracts (§2/§2a) are incomplete. Execution gaps against this binding architecture,
> not new scope.
>
> **§2 / §2a — Answer grounding contract (W2-D12).** The answer model receives **only** the top-5
> reranked guideline snippets, in rank order, inside a delimited untrusted-data block (internal
> `GroundedAnswerContext`); the typed answer tool references an allowed `chunk_id` and cannot invent
> source metadata or quotations; unknown/altered/out-of-top-5 hits are discarded. Supersedes any
> implementation that generated the answer first and appended verified quotes afterward.
>
> **§2a / §6a — Citation boundary (W2-D13, extends W2-D6).** JSON and SSE responses cross the HTTP
> boundary as `citations: list[CitationV2]` only — no legacy `str`. Chart facts project to
> `CitationV2(source_type=patient_record, source_id=<Type>/<id>, page_or_section=null,
> field_or_chunk_id=<stable evidence path>, quote_or_value=<deterministic verified value>)`;
> document/guideline citations keep non-null page/section.
>
> **§7 — Eval gate v2 execution (W2-D14/D15/D16/D17).** Both tiers must drive **all** loaded cases
> (≥50, no hardcoded IDs) through the real agent path. Tier-1 is offline/recorded: fixture
> reader/OCR → recorded provider response → strict parse → grounding/CitationV2 → local
> retrieval/rerank → composer/answer → instrumented side-effect capture; network + Cohere are
> hard-disabled; recordings store **sanitized anchors/hashes only**, bound to case + fixture SHA +
> prompt/tool-schema hash + model + sanitizer + recording SHA; observations are **never** derived
> from golden expectations; executor call count = manifest length. Tier-2 is live (Anthropic
> extract/answer/judge) with in-memory repos + fake OpenEMR write clients, never prod OpenEMR/Cohere;
> judge = `claude-sonnet-4-6`, temperature 0, closed boolean schema, versioned prompts, one
> infra/parse retry, `false` final. Arithmetic: deterministic = 100%, factual ≥ 90%, a drop strictly
> > 5pp vs `w2_baseline.json` fails, exactly 5pp allowed; exhaustion/ceiling → INCONCLUSIVE +
> nonzero exit. The baseline is accepted only from a green complete live 50-case run, committed via
> reviewed PR; CI compares, never updates. Supersedes the current CI step running the W1 10-case
> `evals.runner`.
>
> **§2 — Deterministic critic (W2-D18, conservative-final).** A named critic graph node runs after
> composition and before `done`, reusing the canonical verifier/composer (no divergent clinical
> judge); it rejects uncited/altered/unresolved/mixed-source/treatment/diagnosis/ordering/prescribing
> claims, discards the entire pending composition on rejection → the existing manual-review refusal,
> and emits refs-only span metadata; no clinical SSE bytes before approval.
>
> **§2a / §3 — Third document type `medication_list` (W2-D19, conservative-final).** PDF/PNG/JPEG
> under existing limits; reuses the same OCR/text-layer, strict schema, local grounding, CitationV2,
> bbox, patient dedup, document-intent, and byte-readback path; persists **source + grounded
> artifact only** as additive **artifact v2** (v1 still read); never creates/updates
> `MedicationRequest` or vital records. Separate fixtures; the governed 50-case baseline is untouched.
>
> **§2a — Lab trends endpoint (W2-D20, conservative-final; reaffirms §3 no-FHIR-write).**
> `GET /documents/lab-trends?session_id=<opaque>` derives the patient from the session pin only,
> reads write/readback-verified lab **artifacts** (not FHIR Observations — no supported client
> Observation write, W2-R5), parses values as `Decimal` preserving `6.5 != 65`, normalizes names by
> Unicode/whitespace/casefold only (no LOINC aliasing, no unit conversion; mixed-unit series split),
> and renders a dependency-free SVG + accessible table; a point click opens the existing
> patient-pinned page/bbox preview. No Observation resource is created; no lab value routes through
> vitals.
>
> **§6 / §6a — Observability & correlation (binding current state, W2-C2).** W2 production components
> emit the frozen `LogEventEnvelope` through an injectable `EventSink` against a closed attribute
> registry that rejects clinical values, document/query text, patient/user identifiers, exceptions,
> tokens, multiline strings, and unknown attributes; events cover ingestion, field grounding,
> retrieval, handoffs, queue state, write-intent transitions, readback, breaker state, eval results,
> and a terminal `encounter.summary` carrying ordered tool/worker steps, per-step latency,
> input/output tokens, cost, retrieval-hit count, extraction-grounding rate, and verification
> outcomes. **One** inbound correlation ID is persisted with the job and reused across worker,
> OCR/VLM, grounding, sparse/dense retrieval, reranking, each write/reconcile/readback, graph, and
> answer; child spans exist for each. Sink failure is soft and never changes serving output.
>
> **§6 — Readiness semantics (binding current state).** `/ready` runs bounded cached probes: **hard**
> (503 on failure) = Postgres `SELECT 1`, OpenEMR FHIR, Anthropic, authorized document-category read,
> and worker/schema/vault/route attestation; **soft** (HTTP 200 degraded) = vector integrity + static
> synthetic search, active reranker synthetic pair, and Langfuse. `/health` stays healthy during soft
> degradation; a stale worker or unsafe queue is unready. Response shape is preserved.
>
> **§6 — SLO locking (W2-O2/R4).** Working pre-measurement targets: ingestion p95 ≤ 30s, retrieval
> p95 ≤ 2s. At **Early (2026-07-16)** numbers lock deterministically: retrieval must first meet p95 ≤
> 2s and ingestion p95 ≤ 30s; locked target = `min(working ceiling, ceil(1.25 × measured p95))`;
> throughput floor = `floor(0.80 × sustained)`; resource budget = `ceil(1.25 × measured peak)` and <
> 80% of deployed capacity. Final validates against the locked numbers.
>
> **§6a — CI/CD governance (W2-D21).** Tier-1 runs on every push/PR without secrets; Tier-2 on
> trusted same-repo SHAs only, never `pull_request_target`. A GitLab mirror runs the identical
> offline command and a tested bridge verifies the exact GitHub repo, SHA, workflow/check name,
> conclusion, and result-artifact hashes. Railway deploys the **exact evaluated SHA to both the web
> and document-worker services** only after both W2 gates are green on `main`, then verifies deployed
> SHA + `/health` + `/ready` + a synthetic smoke flow. Pinned quality jobs run every PR: Ruff, mypy,
> coverage, pip-audit, Semgrep/Bandit, OpenAPI spec-sync, Bruno, PHI-artifact scan, corpus-integrity;
> coverage floor = `max(80%, floor(first measured baseline))`, never auto-decreasing; CVE exceptions
> are specific, justified, owner-assigned, and time-limited.
>
> **§4a — Data authority/classification (reaffirmed).** Patient-linked job, dedup, and write-ledger
> rows are **PHI** (agent Postgres is a PHI-bearing, backed-up authority); logs, traces, eval
> datasets, cost reports, recordings, and screenshots remain PHI-free, verified by the leak scanner
> over generated outputs only.
>
> **§8a — Backup & recovery (W2-D21 ops; measured at closeout).** Railway backups keep ≥7 restore
> points; the isolated restore drill validates OpenEMR MySQL/volume and Agent Postgres restore,
> migration integrity, vault probe, readiness, byte-exact synthetic Binary readback, and duplicate
> reconciliation; targets RPO ≤ 24h and measured RTO ≤ 60m. Evidence: `W2_BACKUP_RESTORE.md`.
>
> **Owner actions (blocking, W2-O4).** `ANTHROPIC_API_KEY` in the protected `eval-tier2-live`
> environment; `RAILWAY_TOKEN`; a masked read-only GitLab GitHub-status token + mirror credential;
> Railway backups enabled (≥7 restore points) with the restore drill authorized; sanitized
> billing/resource totals; final-video approval and alert destinations.
>
> **Build-model note (W2-D11).** The Week-2 "Claude Code only — no Codex" posture is superseded:
> Codex acts as an independent auditor and second implementer under isolated worktrees with a lead
> integrator; `.github/` and golden cases 41–50 are in scope.

## §1 System overview

```
 Physician (SMART launch, W1 auth; pinned session = the auth gate for EVERY W2 endpoint)
    │ upload lab_pdf / intake_form                      │ questions
    ▼                                                    ▼
 POST /documents (pinned session; patient must match pin)   ┌────────────────────────┐
  1 atomic insert-or-return on UNIQUE(patient, hash)        │  LangGraph SUPERVISOR  │
  2 store source → POST /api/patient/:pid/document          │  logged handoffs (W2-D2)│
  3 enqueue DURABLE job row (Postgres) → 202 + status_url   └──────┬────────┬────────┘
    ▼ async job (uploader's delegated token)                extract?│        │evidence?
 read: text-layer | OCR fallback (W2-D3, W2-R6)   ┌─────────────────▼──────┐ │
    ▼                                             │ EVIDENCE-RETRIEVER     │◄┘
 ┌──────────────────────────┐                     │ BM25 + bge-small dense │
 │ INTAKE-EXTRACTOR         │                     │ → rerank (Cohere|local │
 │ VLM → strict schema →    │                     │   seam, W2-D4 rev) →   │
 │ ground per field →       │                     │ snippets {src,chunk,   │
 │ bbox | UNSUPPORTED       │                     │   quote,corpus_version}│
 └───────────┬──────────────┘                     └───────────┬────────────┘
             ▼                                                ▼
 exactly-once intents + permanent         ┌─────────────────────────────┐
 lineage, reconcile + re-read             │ ANSWER COMPOSER             │
 (documents API; vitals API only for      │ citation contract v2 (W2-D6)│
  intake vitals w/ explicit encounter)    │ patient facts ≠ guideline   │
             │                            │ evidence · bbox overlay     │
             ▼                            │ (in-session page renders) · │
 OpenEMR = system of record               │ verify-then-flush + refusals│
 (Zone A, unchanged authority)            └─────────────────────────────┘
 External (Zone C, BAA posture): Claude LLM+VLM (assumed BAA) · Cohere Rerank
 (enforced PHI-free queries, RERANKER=cohere|local seam) · Langfuse (D16 content OFF)
 Eval gate v2: 50 boolean cases · deterministic invariants at 100% · Tier 1 offline + Tier 2 live (W2-D5/D8)
```

Deployment unchanged: one Railway project (OpenEMR + MySQL + agent). Corpus + index live in
the agent service, **built at Docker image build** from the committed corpus + manifest and
rebuildable from repo (W2-O1 resolved: in-process, with a memory budget and fallback ladder,
§6). New container deps: Tesseract binary + eng traineddata, pdfium native lib (via
pypdfium2, W2-R6), ONNX runtime for bge-small and the local reranker — verified by a
day-1 spike that concurrently loads bge-small + local reranker + one OCR page and enforces
an RSS ceiling against the Railway plan limit (§9).
New env: `COHERE_API_KEY` (conditional, §2), `RERANKER`.

**Non-goals (explicit — do not soften).** Everything in W1 D12 carries: no diagnosis, no
treatment recommendations, no prescribing/ordering, no patient messaging, no cross-patient
access. New W2 non-goals: **no update or delete** on any EHR record (creates only, W2-D1);
no encounter creation by the agent; no front-desk/role-specific auth path (upload rides the
pinned clinician session — scoping decision, §3); no LangGraph checkpointer persistence
(§3); no critic agent, third document type, trend-chart widget, ColQwen2/multi-vector
indexing, or contextual-retrieval extras (stretch tier, §8).

Every agent capability traces to a W1-carried user and a W2 use case — see the
`W2_USERS.md` traceability table (UC-W2-1..4). **Scope-trace note (W1 G9-1 carried):**
observability/ops/CI surfaces — breakers, dashboards, alerts, SLOs, OpenAPI, Bruno,
backup/recovery, /health //ready — trace to the PRD's engineering requirements (pp.6–7),
not to a use case; they are graded infrastructure, exempt from the capability→UC rule,
which governs agent capabilities only.

## §2 Components (delta over W1)

- **attach_and_extract tool** — accepts (patient_id, file, doc_type ∈ {lab_pdf,
  intake_form}); stores source via the documents API; hashes for idempotency; builds the
  words+boxes layer (text-layer first via pypdfium2/pdfplumber (W2-R6), junk-layer sanity
  check, Tesseract OCR fallback; image uploads skip the text-layer probe and go straight
  to OCR).
- **LangGraph graph** — supervisor + intake-extractor + evidence-retriever; custom typed
  state (extracted fields, citations, partial answers — W2-R1); W1 direct loop survives
  inside workers. **Graph-state lifecycle:** state is constructed per turn from (a) the W1
  Postgres session row and (b) persisted extraction artifacts referenced by document_id,
  and discarded when the turn ends — **no LangGraph checkpointer** (durability lives in the
  session store and OpenEMR; one authority per datum, §4a). Session rows gain W2 fields:
  document_ids + extraction-artifact refs (refs, not values — the W1 §3a session-store PHI
  posture is unchanged). UC-W2-4's "without re-extracting" means re-reading the persisted
  artifact, never holding VLM output in memory across turns. **Per-turn graph step budget:**
  LangGraph recursion limit (working value 8); exhaustion is a terminal handoff record
  (reason_code=step_budget_exceeded) surfacing as a W1-canonical refusal.
- **Extraction and boundary schemas (canonical contracts, PRD req 2; frozen by
  Post-review remediation 2026-07-13) — Pydantic v2, explicitly.** All live in
  `agent/app/`, use strict mode / `extra="forbid"`, and have validation tests. No task may
  improvise a parallel shape. **Composition rule:** every leaf clinical value is
  `GroundedField[T]`; `grounded=true` requires a complete `CitationV2`, while
  `grounded=false` requires `citation=None`, renders UNSUPPORTED, and cannot write.
  Field-for-field inventory:
  - `GroundedField[T]{value: T|None, page: int|None, bbox: NormBBox|None,
    grounded: bool, citation: CitationV2|None}` with a model validator enforcing the
    citation/grounding biconditional.
    **Canonical coordinate space:** `NormBBox{x0,y0,x1,y1}` are normalized page-relative
    coordinates ∈ [0,1], origin top-left, y-down. Both readers convert at ingestion
    (Tesseract: divide by rendered pixel dimensions; PDF text layer: divide by the media
    box and flip y); the overlay multiplies by the displayed image's pixel dimensions.
    Render DPI (200) and page pixel dimensions are recorded in the words+boxes layer. A
    unit-test fixture asserts both paths yield the same normalized box for the same word.
  - `LabPdfExtraction{results: list[LabResult], source_document_id}`;
    `LabResult{test_name: GroundedField[str], value: GroundedField[str],
    unit: GroundedField[str], reference_range: GroundedField[str],
    collection_date: GroundedField[date], abnormal_flag: GroundedField[str]}`. The former
    report-level `collection_date` and result-level `source_citation` shapes are
    **superseded**: collection date is result-level and every leaf owns its citation.
  - `VitalCandidate{value: GroundedField[Decimal], unit: GroundedField[str],
    measurement_date: GroundedField[datetime]}` and
    `IntakeVitals{bps: VitalCandidate|None, bpd: VitalCandidate|None,
    weight: VitalCandidate|None, height: VitalCandidate|None,
    temperature: VitalCandidate|None, pulse: VitalCandidate|None,
    respiration: VitalCandidate|None, oxygen_saturation: VitalCandidate|None}`.
    `IntakeFormExtraction{demographics: Demographics{name, dob, sex, contact, ...},
    chief_concern, current_medications: list, allergies: list, family_history,
    vitals: IntakeVitals, source_document_id}`; every leaf is `GroundedField`-wrapped.
    `note` is generated provenance/correlation metadata, never an extracted field.
  - `CitationV2{source_type ∈ {patient_record, uploaded_document, guideline}, source_id,
    page_or_section, field_or_chunk_id, quote_or_value}`. Guideline source_ids embed the
    corpus version (`vadod-htn-2020@<manifest-hash>`) so citations resolve against exactly
    the ingested corpus build.
  - `EvidenceSearchRequest{query: str, k: int}` (`query` non-empty; `1 ≤ k ≤ K_MAX`),
    `EvidenceSnippet{source_id, section, chunk_id, quote, score, corpus_version}`, and
    `EvidenceSearchResponse{items: list[EvidenceSnippet], corpus_version,
    correlation_id}`. `POST /evidence/search` uses these named models, never anonymous
    `{query,k}`.
  - `HandoffRecord{correlation_id, turn, supervisor_decision, reason_code, worker,
    input_ref, output_ref, handoff_ts}` — **supervisor_decision ∈ {route_extract,
    route_retrieve, compose_answer, refuse, done}** (closed enum; a closed reason_code enum
    per decision; input_ref/output_ref are trace-addressable ids). The supervisor-worker
    contract tests assert enum membership and ref resolvability.
  - `WorkerInput{correlation_id, turn, patient_ref, document_refs, evidence_refs,
    request_kind}` and `WorkerOutput{correlation_id, worker, status, artifact_refs,
    citation_refs, reason_code}` are the only supervisor/worker payloads; refs, not raw PHI,
    cross the handoff boundary.
  - `UploadRequest`/`UploadAccepted`, `RetryRequest{expected_state: "failed"}` /
    `RetryAccepted{job_id, document_id, state, status_url, correlation_id}`, and
    `DocumentStatus{document_id, state, reason:
    FailureReason|None, correlation_id, updated_ts, fields_grounded,
    fields_unsupported, attempt_count, next_retry_at}` with the complete closed enum
    `FailureReason ∈ {patient_mismatch, encounter_mismatch, unit_mismatch,
    range_violation, scope_mismatch, category_mismatch, binary_readback_unsafe,
    upload_rejected, unsupported_media_type,
    size_or_page_cap_exceeded, storage_write_failed, ocr_failed, vlm_timeout,
    vlm_unavailable, schema_violation, auth_expired, writeback_failed,
    writeback_verify_failed, doc_type_mismatch, worker_restart}`. Every value maps to a
    §5 row, log event, and negative test; `unit_mismatch`/`range_violation` may be field-leg
    skip reasons while the overall artifact succeeds.
  - `JobRecord{job_id, document_id, patient_id, content_hash, correlation_id,
    credential_ref, state, claim_owner, lease_expires_at, heartbeat_at, attempt_count,
    next_attempt_at, created_ts, updated_ts}` with
    `state ∈ {storing, reconciling, queued, extracting, grounding, writing, complete,
    failed}`;
    `WriteIntent{intent_id, patient_id, document_id_or_content_hash, leg, version,
    field_id, correlation_marker, payload_hash, state, remote_id, attempt_count,
    updated_ts}` where `leg ∈ {source_document, extraction_artifact, vital}` and
    `state ∈ {pending, unknown, complete}`; and
    `WriteResult{intent_id, state, remote_id, payload_hash, verified,
    failure_reason}` are the canonical job/write contracts.
  - `LogEventEnvelope{schema_version, event_id, event_type, occurred_at, case_id,
    job_id, correlation_id, component, severity, attributes}` is the sole structured-log
    envelope. Optional IDs are explicit `None`; `attributes` permits only the approved
    PHI-free scalar/list schema, never raw document text or extracted values.
  - `ExtractionArtifact{artifact_version, document_id, content_hash, correlation_id,
    doc_type, extraction: LabPdfExtraction|IntakeFormExtraction, grounding_summary,
    created_ts, agent_version}` — persisted as application/json under the document
    category "AI-Extractions". `VitalsWrite{bps?, bpd?, weight?, height?, temperature?,
    pulse?, respiration?, oxygen_saturation?, date, note}` is constructed only from the
    grounded `IntakeVitals` mapping; it contains no caller `user`/`group` fields.

  Raw VLM output never bypasses schema validation; schema changes from W1 carry a
  migration note (§2a).
- **Grounding verifier** — per-field: locate the extracted value in the words+boxes layer;
  found → citation + bbox; not found / disagreement → UNSUPPORTED render ("verify against
  source document"). Confidence = grounding agreement (binary), never VLM self-report.
- **Guideline corpus** — VA/DoD trio (Diabetes 2023, HTN 2020 pinned, Lipids 2025) + pocket
  cards; manifest with provenance/license/version; verbatim chunks; do-not-ingest list
  documented (W2-R2). **Ingestion is text-only: embedded third-party figures are stripped**
  (W2-R2 license caveat — figures are not individually cleared even in US-government
  works); the corpus build script enforces this and the manifest records the rule.
  **Sizing (curation rule, not scraping):** recommendation/management sections + pocket
  cards, skipping evidence-review appendices — expected on the order of several hundred
  retrievable chunks (estimate; actual count recorded in the manifest at ingest; W2-R3's
  model-delta wash-out evidence was anchored at 50–200 chunks — at this size the levers
  remain hybrid retrieval, the reranker, and chunking/query phrasing).
- **Hybrid retriever + reranker** — rank-bm25 + bge-small-en-v1.5 (ONNX/FastEmbed, W2-R3)
  → rerank behind a **one-env-var seam: `RERANKER=cohere|local`** (W2-D4 rev 2026-07-13).
  The tension is carried honestly: W2-R3's revised recommendation was local-primary; the
  owner locked Cohere conditional on a **paid production key**, which resolves both R3
  objections (trial terms exclude production; 10 req/min throttle). `mxbai-rerank-base-v1`
  (Apache-2.0, W2-R3) is implemented and integration-tested as the shipping fallback —
  the PRD's "or an equivalent reranker." **Decision trigger (dated):** if the production
  `COHERE_API_KEY` is not in the Railway env by **Monday 2026-07-13 EOD**, MVP ships
  `RERANKER=local`; Cohere becomes the Early-checkpoint upgrade. Cohere down at runtime →
  un-reranked hybrid scores, degraded, logged. CI never calls Cohere live (W2-D4).
- **Answer composer** — citation contract v2 (W2-D6): CitationV2 on every clinical claim;
  incomplete citation = claim does not render; patient facts and guideline evidence
  rendered as visually distinct classes; document claims → bbox overlay (in-session page
  renders, §2a/§4). W1 verify-then-flush + refusal discipline unchanged. Trend questions
  (UC-W2-4, e.g. potassium) are answered in core as **cited textual/tabular values**; the
  visual trend-chart widget is the deferred stretch item.
- **Eval gate v2** — §7 (two-tier, W2-D5/W2-D8).
- **Unchanged W1 components:** SMART auth + session pin, EvidencePacket chart reads,
  deterministic templater, D13 degradation, Langfuse posture (D16 OFF), alert checker.

## §2a W2 endpoint contracts (W1 §5a convention) & migration notes

All new W2 endpoints **require the W1 authenticated pinned session** (SMART launch, W1 D12
carried); the session pin remains the cross-patient enforcement point for the entire upload
surface. An invariant eval case exercises each rule.

- `POST /documents` — multipart `UploadRequest{file, patient_id, doc_type ∈ {lab_pdf,
  intake_form}, encounter_id?}`. Refuses any patient_id ≠ session-pinned patient (canonical
  W1 refusal). Accepted MIME per doc_type: lab_pdf → application/pdf; intake_form →
  application/pdf | image/png | image/jpeg. Caps enforced pre-queue (≤10 MB, ≤20 pages;
  422 with typed FailureReason). Duplicate (same patient + content hash) → 200 returning
  the existing `{document_id, status_url}`, zero new records. Else 202
  `UploadAccepted{document_id, status_url}`.
- `GET /documents/{id}/status` → `DocumentStatus`. Verifies the document belongs to the
  session-pinned patient before responding.
- `POST /documents/{id}/retry` with `RetryRequest` → `RetryAccepted`. It verifies the
  pinned patient, requires the current job state to be retryable/failed, atomically requeues
  the same logical job and permanent intent set, and never creates a second dedup record.
  An `unknown` write intent is not retryable until reconciliation proves it complete or
  absent; concurrent retry requests resolve to one queue transition.
- `GET /documents/{id}/pages/{n}` → page PNG at the canonical render DPI, for the overlay.
  Same pin + patient-match rule; rendered **on demand** from the OpenEMR-stored source
  (fetched with the delegated token), held in a bounded in-memory short-TTL cache, never
  written to disk, never logged or traced. Cross-patient page fetch → 403 (leak test in
  §7a).
- `POST /evidence/search` accepts `EvidenceSearchRequest` (PHI-free query,
  builder-constructed per §4) and returns `EvidenceSearchResponse`.
- `POST /chat` — W1 contract unchanged; SSE claim-block events now carry CitationV2 (see
  migration note). Streaming through LangGraph workers is verified by the V2 spike (§9;
  fallback: stream only the final composer stage — perceived-latency cost named in the
  cost report, never a correctness cost).

The committed OpenAPI 3.0 spec and the Bruno collection enumerate **exactly this list**
plus the W1 endpoints, so the contract tests verify a closed surface.

**Migration notes (W1→W2).** ~~The one W1-visible schema change is CitationV2 and there are
no other W1 schema changes.~~ **SUPERSEDED — Post-review remediation (2026-07-13):** served
citations still move from
W1 evidence IDs to CitationV2. Mapping for chart claims: `source_type=patient_record,
source_id={ResourceType}/{uuid}, page_or_section=null, field_or_chunk_id={W1 evidence_id
incl. hash8}, quote_or_value={verified field value}`. The W1 EvidencePacket, claim schemas,
and verification pipeline are **unchanged**; a composer-side adapter emits CitationV2; W1
UI citation chips render from CitationV2 going forward. The mapping is pinned by a
regression test. The `/chat` SSE event carrying CitationV2 is the same change surfaced at
the endpoint. In addition, migrations `002_oauth_state.sql` and `003_document_jobs.sql`
add the encrypted delegated-job credential references, permanent dedup/write-intent
authority, and purgeable queue attempts. They use expand/contract deployment: additive
nullable/defaulted schema first; dual-read compatibility; backfill/verification; then new
writers; destructive contraction, if ever needed, is a later separately approved change.
Each migration is transactional where supported, idempotent, safe against concurrent
startup, tested against old-code/new-schema and new-code/old-schema failure, and ships with
documented roll-forward plus backup-restore rollback. Deployment never rolls application
code back across a migration until the compatibility test says that pair is safe.

## §3 The two lifecycles

**Ingestion (asynchronous claimed/leased queue + permanent exactly-once authority —
Post-review remediation 2026-07-13, W2-D10).** `POST /documents` validates the pinned
patient/encounter and upload, then transactionally inserts or returns the permanent
`DocumentDedup` row keyed `(patient_id, content_hash)` and the source-document
`WriteIntent` plus a purgeable `JobRecord` initially in `storing`. Concurrent duplicates
resolve to that same logical document. The request performs the remote source write while
the upload bytes are still available, using deterministic filename/correlation markers and
the collection's content hash for discovery. A verified write advances the existing job to
`queued`; an ambiguous response advances it to `reconciling`, returns the same
`UploadAccepted`/status URL, and reconciles without re-posting. If reconciliation proves
absence, it becomes `failed(storage_write_failed)`; re-uploading the same bytes atomically
requeues that same job/intent rather than creating a second logical document.

The dedicated agent worker process claims eligible rows with a Postgres transactional
claim (`FOR UPDATE SKIP LOCKED` or equivalent) and sets `claim_owner`,
`lease_expires_at`, and `heartbeat_at`. Web processes enqueue but do not execute clinical
jobs. Worker concurrency is configured and safe across replicas because only the current
lease owner may advance a row. Heartbeats extend live leases; bounded exponential backoff
sets `next_attempt_at`; graceful shutdown stops claims, completes the current atomic step,
and releases or lets the lease expire; stale leases are reclaimed without marking another
worker's live job failed. Queue depth, oldest queued age, and worker-heartbeat age drive
metrics and readiness. The job pipeline remains words+boxes → extraction → strict schema →
grounding → full writeback → round-trip verification.

- **Delegated-job credential.** Interactive authorization creates a separate encrypted,
  patient- and clinician-bound job credential containing the delegated token material and
  refresh metadata. Its lifetime is bounded by refresh-token expiry/revocation, not the
  interactive session idle timer, so a valid long job can refresh after UI expiry. Jobs
  store only `credential_ref`; keys and token values never enter logs/traces. Terminal jobs
  delete token material after the documented recovery grace period; refresh failure →
  `failed(auth_expired)` and reauthorization is required. **Never `client_credentials`.**
  Delegated identity is request/audit provenance recorded in artifact, permanent lineage,
  and trace; the agent never claims that omitting `user/group` populates an OpenEMR clinical
  performer.
- **One exactly-once contract for all write legs.** Source document first, grounded
  extraction artifact second, then a structured vitals create through the standard vitals
  API into OpenEMR `form_vitals`, with one vital intent per eligible grounded intake-vitals
  field. Permanent intents use `(patient_id, document_id_or_content_hash, leg, version,
  field_id)` and states `{pending, unknown, complete}`. Documents use deterministic
  filename + OpenEMR content hash; vitals use a non-PHI `note` marker containing the
  intent/correlation identifier and payload-hash prefix. Before every possible re-POST,
  the agent lists/re-reads the patient/encounter surface and matches marker + payload hash.
  A unique match completes the intent; proven absence permits the same intent to retry;
  multiple/conflicting matches or commit-then-timeout stays `unknown`, stops automatic
  work, and requires reconciliation. A local transaction is never described as atomic
  with remote HTTP.
- **Permanent vs purgeable state.** `DocumentDedup`, `WriteIntent`, remote IDs, payload
  hashes, correlation markers, delegated principal attribution, and source lineage are
  permanent clinical-integrity records and are never removed by the 30-day purge. Queue
  claims, heartbeats, and attempt/history rows are separate and purge after 30 days.
  `POST /documents/{id}/retry` atomically requeues the same failed job/intents; it cannot
  requeue an unresolved `unknown` intent or create a second logical document.
- **Lab/vitals routing rule (W2-F3, code-verified).** Lab-PDF-derived values persist
  **only** as the structured machine-authored ExtractionArtifact via the documents API —
  never via the vitals route (no lab write route exists; labs are not vitals). The vitals
  route is used exclusively for true vitals-class fields captured on the intake form (BP,
  height, weight), and **only when the upload carried an explicit encounter_id** — the
  agent never guesses or creates an encounter; absent an encounter, vitals-class facts
  persist in the artifact alone and the vitals leg is skipped with
  `writeback.skipped(no_encounter)`.
- **Round-trip verification (DEFENSE_PREP §4.A, promoted).** After writeback the job
  re-reads documents through FHIR `DocumentReference/:uuid → Binary/:uuid` and vitals
  through the standard vitals GET plus the FHIR Observation projection, then compares the
  result against the sent
  payload; only on match does status flip to complete. Mismatch/absence →
  `failed(writeback_verify_failed)` + named log event. Nothing derived is authoritative
  until written and re-read; UC-W2-3 answers cite derived facts by their OpenEMR record
  ids.
- **Boot reconciliation.** ~~Every non-terminal job is marked `failed(worker_restart)`.~~
  **SUPERSEDED — Post-review remediation (2026-07-13):** startup reclaims only expired
  leases, reconciles any `unknown` remote intent before further work, and resumes the same
  logical job. Live leases owned by another worker are never failed. A job that can be
  neither reclaimed nor reconciled at boot terminates `failed(worker_restart)` — the sole
  live trigger for that enum member, keeping it reachable + testable. Deploys drain claims
  gracefully; failed jobs use the atomic retry contract above.

Extraction report to the physician: grounded fields cited + boxed; ungrounded fields
flagged UNSUPPORTED.

**Write-interface discrepancy note (say it before graders do).** The engineering
requirements name "FHIR writes" as an interface; the core requirement permits derived facts
"as appropriate FHIR resources **or OpenEMR records**." This fork exposes **no FHIR write**
for DocumentReference/Observation (validated 3-way, W2-R5: route enumeration, FHIR_README,
FHIR_API.md:728 — `$docref` generates CCDs, it does not accept uploads). The EHR-write
interface therefore exists exactly as required — typed Pydantic contract, correlation ID
propagated, lineage recorded — with the standard REST documents/vitals API as transport
(`POST /api/patient/:pid/document`, `POST /api/patient/:pid/encounter/:eid/vital`), which
is W1 D9's documented fallback firing as designed (W2-D1, W2-F1/F2).

**Question.** Physician asks ("what changed? what should I pay attention to?") → supervisor
decides per turn: chart facts (W1 EvidencePacket path), document facts (persisted
extraction artifacts, cited to page+bbox), guideline evidence (retriever) → composer merges
with sources separated and every claim cited → verify-then-flush → follow-ups reuse session
context (graph-state lifecycle, §2); UNSUPPORTED and refusal behaviors identical to W1.

**The PRD's usefulness promise holds on all three degraded axes (p.2 scenario):** imperfect
scan → grounded fields render, ungrounded fields flag UNSUPPORTED, never invented (§5);
incomplete record → W1 absence discipline — the answer names what is missing, never
fabricates (missing-data eval cases; empty allergy → "confirm with patient", never NKDA,
W1 F-D.5 carried in UC-W2-2); follow-up → session continuity with live citations, grounding
never degrades across turns (UC-W2-4).

**§3a Lifecycles & retention (W1 §3a extended — one row per new stateful entity).**

| Entity | Created | Expiry / invalidation | Retention | PHI store? |
|---|---|---|---|---|
| Uploaded temp file | request | deleted after OpenEMR store succeeds | none agent-side | yes (transient) |
| Words+boxes layer | job read step | dropped at job terminal state | job memory only, never persisted; re-derived from the stored source on re-run | yes (transient) |
| Queue attempt/job-status rows | enqueue/claim | terminal state; **attempt history only** purged after 30 days | Agent Postgres; contains patient_id and is PHI; restricted, backed up, diagnostics scanned | **yes** |
| Permanent document dedup + write intents/lineage | first accepted upload/write intent | never invalidated by attempt purge; corrected only by audited reconciliation | encrypted/backed-up Agent Postgres; patient-scoped access | **yes** |
| Page render buffers | overlay request | response / short TTL | bounded in-memory cache; never disk, never logged | yes (transient) |
| LangGraph turn state | turn start | turn end | none (session store is the authority) | yes (transient) |
| Encrypted delegated-job credential | accepted upload | terminal + recovery grace, or refresh expiry/revocation | separate restricted Agent Postgres credential store; not the interactive session row | secret/PHI |
| Vector index | Docker image build | replaced by next image | non-PHI build artifact | no |
| Extraction artifact / derived records | writeback | — | OpenEMR retention posture | yes (in OpenEMR) |

## §4 Trust boundaries & PHI (W1 posture extended)

- **Zone A (OpenEMR)** keeps identity, authority, clinical truth. New: it receives
  three exactly-once write legs over two API capabilities (source document + artifact via
  documents create; grounded intake vitals via vitals create), source-linked,
  visibly machine-authored, voidable. No update/delete surface exists in the agent.
- **Zone B (agent).** Every W2 endpoint sits behind the pinned session (§2a). **The
  injection crossing is owned at two points (W1 T1 discipline extended).** Extraction: the
  VLM sees page pixels (unavoidable — it must read the document) but can only emit
  `LabPdfExtraction`/`IntakeFormExtraction`; anything else is a hard reject, and every
  surviving field must ground in the words+boxes layer — instructions embedded in a
  document cannot become output that is not literally on the page. Answer composition:
  document content reaches the answer model **only as typed, grounded evidence records**
  (the W1 EvidencePacket discipline extended); the raw OCR/text layer is ephemeral and
  **never enters any LLM prompt**; quote_or_value is bounded to grounded spans. Output
  side, the W1 templater + treatment-verb blocklist backstop is unchanged. Worst case
  remains a flagged, voidable, machine-authored record (W2-D1). Injection-bearing fixtures
  are required eval cases (W2-D5, W2-F5).
- **Zone C (external, BAA posture):** Claude (LLM+VLM — same assumed BAA, zero new PHI
  processors for vision); Cohere; Langfuse Cloud (W1 D5/D16 posture, content OFF).
  **Cohere PHI-free egress is enforced, not asserted:** (1) retrieval queries are
  constructed by a deterministic **query builder** from coded clinical terms (extracted
  test/condition names, problem-list terms) — never free-form conversation text; (2) an
  outbound screen on the Cohere call rejects queries containing identifiers, DOBs,
  MRN-shaped tokens, or the session patient's demographic strings, **failing closed** to
  the local/un-reranked path; (3) the screen is unit-tested and exercised by an injection
  eval case whose document plants an identifier aimed at the retrieval query. This honors
  the owner's stated condition on W2-D4: if the contract cannot be held mechanically, the
  local path fires.
- **Sensitive-artifact inventory (W2-D7 rev 2026-07-13):** document images (**persist in
  OpenEMR only**; the agent renders ephemeral, session-bound page images for the required
  overlay — never persisted, never exported, never logged); extracted fields; retrieval
  queries; eval fixtures; **prompts** (covered by the D16 content-OFF posture); and
  **screenshots** — E2E/Selenium output, debug captures, demo-video frames — never
  attached to logs/traces/SaaS observability; demo captures use synthetic data only.
- Generated logs/traces/eval outputs are PHI-free, enforced by the scoped CI PHI-detection
  check (§6a/§7). Canonical synthetic input fixtures contain deliberate canaries and are
  test inputs, not scan targets.
  Egress inventory: Anthropic, Cohere (enforced PHI-free), Langfuse.
- Scope delta said plainly: read-only → read + narrow create (`api:oemr` document/vital
  scopes; the W2-F4 client-enable step is a build-blocking provisioning checklist item —
  ~~the expected first-run failure is a 401~~ **SUPERSEDED — Post-review remediation
  (2026-07-13): missing `api:oemr` yields 403**).
- **Write-side containment boundary (W2-D9; the third owned injection/authority crossing).**
  The OpenEMR write routes do **not** validate launch-patient/encounter ownership on create
  and the server patient-access check is a stub (W2-F13 — W1 F-S.2's "the server won't stop
  you, so the agent must" discipline carried forward from reads to writes), and a
  registered read scope is not an effective write ceiling (W2-F12). The agent is therefore
  the sole enforcement point: every write is gated behind (a) the **pinned-session
  patient-match + encounter-ownership preflight** (a supplied `encounter_id` must belong to
  the pinned patient, verified before enqueue), (b) a **startup exact-scope assertion** that
  refuses to write if the granted scope set differs from the expected manifest, and (c)
  uses canonical `SOURCE_DOCUMENT_PATH=/AI-Source-Documents` and
  `ARTIFACT_DOCUMENT_PATH=/AI-Extractions`. Provisioning records each path's expected
  category ID and ACL. Before writing, the deploy/admin preflight resolves path → ID,
  proves the expected ACL, and records evidence; runtime verifies the attested manifest
  plus an authorized collection read, then sends the **path** accepted by the standard API.
  Unknown/mismatched path, ID, ACL, or evidence fails closed (W2-F14). Cross-patient /
  mismatched-encounter attempts are
  refused and negative-tested (§5, §7a).

**Bounded intake-vitals policy (W2-D10/F15; no invented ranges).** Unit must match the
instance configuration. The hard inclusive write bounds are pinned to this fork's
`OpenEMR\Common\Forms\VitalsFieldRanges::getRanges()`; tests fail if the upstream table
drifts without a reviewed schema/policy update:

| Field | US hard range | Metric hard range where defined |
|---|---:|---:|
| weight | 0–2000 lb | 0–910 kg |
| height | 0–150 in | 0–381 cm |
| bps | 0–400 mmHg | same |
| bpd | 0–300 mmHg | same |
| pulse | 0–500 /min | same |
| respiration | 0–150 /min | same |
| temperature | 0–120 °F | 0–48.9 °C |
| oxygen_saturation | 0–100 % | same |

Outside a hard bound → typed `range_violation`, artifact/trace provenance retained, vital
leg skipped. Wrong/absent unit → `unit_mismatch`, artifact retained, no conversion. Caller
`user`/`group` fields are stripped; delegated-clinician identity is recorded in artifact,
permanent lineage, and trace, not asserted as an OpenEMR performer unless the server itself
derives it.

## §4a Data model & authority ledger (PRD: owner, lineage, access, validation per type)

**Post-review remediation (2026-07-13):** the ledger now distinguishes permanent
patient-bound clinical-integrity authority from purgeable PHI-bearing queue attempts.

| Artifact | Authoritative owner | Lineage | Access | Validation |
|---|---|---|---|---|
| Source document (uploaded file) | **OpenEMR** (documents store) | upload event {correlation_id, uploader, content_hash, ts} | clinician via OpenEMR; agent read via API | doc_type enum; MIME whitelist; size/page caps (§2a) |
| Extracted lab observations | Agent until written; **OpenEMR** after write (artifact-only — never vitals, W2-F3) | source document id + page + bbox per field | agent write-once; clinician review/void in OpenEMR | `LabPdfExtraction` + grounding |
| Intake facts | same as above (vitals leg only w/ explicit encounter) | same | same | `IntakeFormExtraction` + grounding |
| Queue attempts/job status | **Agent Postgres store** (claimed/leased rows) | job id + patient/document/correlation refs | status endpoint (pinned session); restricted ops read | `JobRecord`/`DocumentStatus`; attempt rows purge at 30d |
| Permanent dedup/write intents/lineage | **Agent Postgres store** | `(patient_id, document_id_or_content_hash, leg, version, field_id)` → marker/payload hash/remote id/attribution | job-internal + audited reconciliation; restricted | `WriteIntent` state machine; never purged; encrypted backup |
| Words+boxes layer | derived, **ephemeral** — recomputable from the stored source; never authoritative | — | job-internal | density sanity check |
| Page renders | derived, **ephemeral**; source = the OpenEMR document | — | pinned session only (§2a) | — |
| Graph state | **ephemeral per turn**; session continuity lives only in the W1 Postgres session store | — | turn-internal | typed state class (W2-R1) |
| Guideline chunks + index | **Agent service** (image-build artifact; rebuildable from repo corpus + manifest) | manifest {source_url, license, version, ingest_date} | read-only at runtime | verbatim-chunk rule; figure-strip rule; manifest license check |
| Citation records | **Agent** (immutable, per response) | claim → CitationV2 → evidence id @ corpus_version | read-only; exported in traces (PHI-free ids) | `CitationV2`; incomplete = no render |
| Handoff records | **Agent** (append-only log) | correlation_id chain | ops read | `HandoffRecord` (closed enums) |
| Eval golden set | **Repo** (git — RPO 0) | authored fixtures, versioned | PR-reviewed changes only | case schema + boolean rubrics |

No silent overwrites anywhere: every write is a create; duplicates and ambiguous outcomes
resolve through the permanent patient-scoped intent/lineage authority (W2-D10), never a
purgeable attempt row or a blind retry. One owner per type, stated above.

## §5 Failure modes (detection + recovery; every row has a named §6a log event and a runbook entry)

**Post-review remediation (2026-07-13):** D9/D10 refusal, ambiguous-write, lease,
credential, category, token-retirement, F20, and worker-readiness failures are binding.

| Failure | Behavior |
|---|---|
| Upload exceeds caps / wrong MIME | Rejected pre-queue at POST /documents, 422 with typed reason; nothing enqueued |
| Ingestion fails mid-flow | Source/intent state retained; safe failed job atomically requeues the same job/intents; `unknown` remote state must reconcile first |
| Process restart / deploy mid-job | Graceful drain + leases; startup reclaims only stale leases, preserves live claims, and reconciles unknown intents before resuming |
| Job outlives interactive session/access token | Separate encrypted delegated-job credential refreshes independently; refresh expiry/failure → failed(auth_expired), reauthorization required |
| EHR write missing `api:oemr` | **403, not 401**; `scope_mismatch`, writes disabled, artifact/intents retained; runbook → exact W2-OA3 manifest |
| EHR write 5xx/commit-then-timeout | Intent → `unknown`; list/re-read marker+payload; never blind-retry; conflict/multiple match stops for operator reconciliation |
| Round-trip re-read mismatch | failed(writeback_verify_failed); logged; investigate before retry |
| Cross-patient / mismatched-encounter write attempt (W2-F13) | Refused pre-queue at POST /documents — `patient_id`≠pinned or `encounter_id` not owned by the pinned patient → typed 4xx (`patient_mismatch`/`encounter_mismatch`), nothing enqueued; the server does not enforce this, the agent does; negative-tested (§7a) |
| Out-of-range vital value (W2-F15) | Bound table in §4 applies; out-of-range → `range_violation`, field artifact-only, no vital POST |
| Attribution spoof attempt (W2-F16) | Strip/reject caller `user`/`group`; delegated principal recorded in artifact/permanent lineage/trace; never trust performer text |
| Unexpected granted OAuth scope (W2-F12) | Startup exact-manifest assertion — granted set ≠ expected set → `scope_mismatch`, **refuse all writes**, fail write readiness |
| Wrong category path/ID/ACL (W2-F14) | `category_mismatch`; canonical path preflight mismatch/unknown → refuse before native POST; admin evidence + runtime attestation identify the failed dimension |
| Retired client token still usable (W2-F17) | Cutover incomplete: revoke old access+refresh tokens or wait out both lifetimes; negative probes for old access and refresh must fail before writes enable |
| Binary DEBUG posture unknown/unsafe (W2-F20) | `binary_readback_unsafe`; admin/deploy check records `system_error_logging != DEBUG`; unknown or DEBUG fails closed and refuses Binary read-back |
| Oversized / malformed / wrong-type upload (W2-F19) | Agent validates `$_FILES` error, size (≤10 MB), page count (≤20), and exact MIME before the native call and returns a controlled 4xx — the native upload skips these checks and returns an empty 404 on reject |
| Schema violation from VLM | Hard reject (schema is the contract); per-field logging; schema_valid guards |
| Grounding disagreement (OCR/text vs VLM) | Field renders UNSUPPORTED + "verify against source document" + overlay region |
| Junk embedded text layer | Density sanity check → OCR fallback path |
| OCR process failure (crash / per-page timeout) | Per-page subprocess timeout; failed page → fields UNSUPPORTED ("page could not be read"); all pages fail → failed(ocr_failed) |
| Wrong doc_type selected | Schema violation or majority-ungrounded → failed(doc_type_mismatch) with re-classify-and-retry message (idempotent re-run with corrected type) |
| Document uploaded to wrong patient | Ops recovery path (runbook): void the source document + lineage-linked derived records (findable via content_hash/correlation_id), re-upload to the correct chart — W2-D1 voidability exercised |
| Retrieval: empty hit (healthy index) | "No guideline evidence found" stated; never invented; flagged |
| Retrieval unavailable (index missing/corrupt, embedder down) | Startup integrity check (manifest-hash match); /ready degraded (retrieval_unavailable); retriever returns a distinct "guideline retrieval unavailable" state — never conflated with an empty hit; dense-leg-only failure → BM25-only, flagged degraded |
| Cohere down / rate-limited | Un-reranked hybrid scores (or local reranker per seam); degraded, logged; /ready degraded |
| VLM down — question turn | W1 D13 deterministic degradation (facts, no synthesis, banner) |
| VLM down — ingestion job | Breaker open → job fails fast, failed(vlm_unavailable), source retained; queued jobs held while breaker open; recovery = re-run on breaker close |
| Supervisor routing error | Handoff record shows decision + reason_code; **recovery:** invalid route / malformed worker output → supervisor retries the decision once with the failure appended; second failure → deterministic W1-canonical refusal, never a silent wrong-worker answer |
| Graph loop | Per-turn step budget (recursion limit); exhaustion → terminal handoff (step_budget_exceeded) → refusal; visible on the routing dashboard panel |
| Concurrent duplicate uploads | Permanent UNIQUE(patient_id, content_hash) resolves to one logical document/intent set; second request returns existing lineage |
| Injection text in a document | Data-not-instructions handling (§4); schema-bound output; append-only bound; eval-cased |
| /ready | HARD 503: OpenEMR/documents, agent Postgres, delegated-credential crypto, required Anthropic/VLM unavailable, no fresh worker heartbeat, lease/queue invariant failure, or oldest-queued age above the hard ceiling. SOFT 200+degraded: vector index unavailable or reranker off. Payload reports worker-heartbeat age, queue depth/oldest age, and write-preflight/F20 status. `/health` remains liveness-only and stays 200 during every soft readiness degradation (contract-tested). |

Repeated outbound failures trip a **circuit breaker** per dependency (VLM, reranker): after
N consecutive failures the dependency is marked open, calls short-circuit to the degraded
path, a half-open probe recovers it; breaker state is logged and dashboard-visible.

## §6 Observability, SLOs, ops (extends W1 §7)

**Metrics (W2 additions):** document ingestion count, ingestion latency, per-field
extraction pass rate, grounding-agreement rate, retrieval hit rate, rerank scores +
model/version, routing decisions, per-worker latency, eval pass rate per category, queue
depth + oldest queued age + worker-heartbeat age (from claimed/leased rows), **outbound retry count per dependency
(VLM, reranker, EHR write) and ingestion-job attempt count** (the W2 extension of W1's
event-retries panel), breaker state.

**Per-encounter record (PRD core req 7 — all seven fields).** Every encounter emits a
terminal `encounter.summary` structured event / trace fields carrying: tool + handoff
sequence (ordered), latency by step, token usage, cost estimate, retrieval hits, extraction
confidence (grounding-agreement rate), and eval/verification outcome (the live verification
verdict + W1 D16 score set; the offline gate is per-run, not per-encounter — stated).

**Alerts (thresholds are working targets pending W2-O2 baselines; each with response
actions here, expanded in `docs/observability/runbooks.md`):**

| Alert | Threshold (working) | First action | Escalate |
|---|---|---|---|
| Extraction failure rate | >20% over 1h | Inspect doc.ingestion.failed / doc.ocr.failed reasons in Langfuse; degraded-scan batch vs systemic; check VLM breaker | Anthropic status; pause ingestion |
| Retrieval latency | p95 > 2s over 15m | Localize via spans: index/ONNX memory pressure vs Cohere latency; check breaker | Flip RERANKER=local or un-reranked; check W2-O1 memory |
| Ingestion latency | p95 > 30s/doc | Localize VLM vs OCR vs write leg via job spans | Cap pages; scale service |
| Eval-category failure | Any deterministic invariant <100%, or `factually_consistent` >5 percentage-point regression / below threshold | CI blocks pre-deploy; freeze, bisect, rollback | Owner |

**SLOs.** ~~Set from measured baselines at MVP.~~ **SUPERSEDED — Post-review remediation
(2026-07-13):** the full matrix is measured and numeric SLOs are formally locked once at
**Early (2026-07-16)**; Final validates/reports against them. Working pre-measurement targets
remain ingestion p95 ≤ 30s/doc and retrieval p95 ≤ 2s. **Baselines:** recorded per
PRD-named flow — (1) document
ingestion, (2) extraction, (3) RAG retrieval, (4) full multi-agent run — across **CPU,
memory, latency, throughput** (Railway metrics + k6, the W1 §7 method), at Early; diffed
against W1's k6 @10/50-VU numbers for shared paths. The memory baseline also **closes
W2-O1**: working budget ≈ bge-small ONNX + runtime + index 200–300MB, Tesseract ~100MB
peak/page, +~400MB if RERANKER=local; fallback ladder if over: quantized ONNX (mxbai ships
quantized, W2-R3) → raise Railway service memory → externalize the index (last resort,
documented tradeoff).

**Deploy & rollback (W2).** Deploy = push → full §6a pipeline (eval gate last) → Railway
deploys only on green. Rollback = Railway one-click redeploy of a prior deployment, or
`git revert` → auto-redeploy. Bad-deploy detection = post-deploy /ready degradation + the
error-rate / extraction-failure / retrieval-latency alerts. Two W2-specific interactions:
the index ships **in the image**, so a rollback carries its matching index (no rebuild
race); in-flight jobs survive via leases + intent reconciliation (§3). A corpus
or manifest change rebuilds the index in the same PR's image; CI asserts index↔manifest
hash agreement so a stale index cannot deploy.

All outbound LLM/VLM/retrieval/write/read-back calls carry the inherited correlation ID,
timeouts, and bounded retries where retry is safe; remote writes use W2-D10 reconciliation
instead of generic retries. Supervisor span ⊃ worker spans ⊃ extraction/retrieval/write
sub-calls; a required integration test reconstructs inbound request → job → worker → VLM →
retrieval/reranker → every document/vital write and re-read → terminal event from one ID.
OpenAPI 3.0 spec committed for the §2a endpoint list; Bruno collection extended
(upload, extraction status, retrieval, page render, full flow) with the W1 token-mint
helper carried. Cost: VLM page calls capped per doc (~$0.005–0.01/page planning number,
W2-R4), measured from traces.

## §6a Structured log events, CI pipeline, privacy scrubbing (PRD engineering reqs)

**Post-review remediation (2026-07-13):** one owned typed event envelope, scoped generated-
artifact scanning, same-SHA submission-host enforcement, and safe fork policy supersede the
earlier implicit logging/gate assumptions.

**Log-event inventory (extends the W1 schema — same structured format, no parallel
convention; searchable by case_id, event_id, correlation_id; all PHI-free).** The owning
module is `agent/app/observability/events.py`; every emitter constructs the frozen
`LogEventEnvelope`, and schema tests reject missing IDs, unversioned/plain-text events, raw
clinical values, or unknown attributes:
`doc.ingestion.started`, `doc.ingestion.completed`, `doc.ingestion.failed(reason)`,
`doc.ocr.failed(page, reason)`, `extraction.field.outcome` (field name, grounded: bool —
never the value), `extraction.schema.violation`, `retrieval.query.executed` (hit/miss, k,
latency), `retrieval.unavailable(reason)`, `rerank.executed` (model+version, latency),
`worker.handoff` (HandoffRecord), `writeback.created` (record type, lineage ids),
`writeback.failed(leg, reason)`, `writeback.skipped(no_encounter)`,
`writeback.skipped(unit_mismatch)`, `writeback.skipped(range_violation)`,
`writeback.refused(scope_mismatch)`, `writeback.refused(category_mismatch)`,
`writeback.intent.unknown`, `writeback.intent.reconciled`, `writeback.verify.failed`,
`job.claimed`, `job.heartbeat`, `job.lease.recovered`, `job.requeued`,
`encounter.summary` (§6),
`eval.run.outcome` (per category), `breaker.state.changed` (dependency, state). The
pre-queue write-side refusals surface as typed `DocumentStatus.FailureReason` /
POST-time 4xx values `patient_mismatch` and `encounter_mismatch` *(added 2026-07-13 —
W2-D9 / W2-F13)* alongside the existing `unsupported_media_type` /
`size_or_page_cap_exceeded` upload rejects.

**CI pipeline per PR (extends the W1 eval gate):** build → ruff lint + mypy typecheck →
pytest with coverage → **W1 eval suite (`agent/evals/`, unchanged cases — shared-path
regression guard; any W1 failure blocks the PR exactly as in W1)** → Pydantic
schema-validation tests → supervisor-worker contract tests → extraction regression tests
(fixtures + recorded stubs) → OpenAPI contract tests (spec ↔ implementation) → dependency
audit (pip-audit) → security scan (semgrep) → scoped PHI-detection check over generated
outputs/logs/traces/reports/recordings/results (canonical input fixtures excluded) →
**eval gate (Tier 1 deterministic subset + Tier 2 full live 50-case run —
both PR-blocking, W2-D8, §7)** → deploy on green.

**Enforcement surface (all three layers, explicit):** (1) the committed pre-push Git Hook
(hooksPath + documented one-command setup, `make hooks`) runs the **full Tier-1 gate** —
deterministic and seconds-fast, not a lint-only subset (no secrets on contributor
machines, W2-D8); (2) GitHub branch protection marks **both eval jobs (`eval-tier1`,
`eval-tier2-live`) required status checks** on main (named config step in the setup
guide) — the enforcement graders cannot bypass; (3) a committed **`.gitlab-ci.yml` runs
the identical Tier-1 gate on the GitLab submission mirror and a fail-closed
`graded-gate` bridge**. The bridge keys on the identical mirrored commit SHA and succeeds
only when the required GitHub `eval-tier2-live` check for that SHA is successful; absent,
stale, mismatched, or red status fails GitLab. Thus W2-D8's live calls remain in GH Actions
while the GitLab submission host enforces the same full gate. README documents both paths.

**Fork-PR secret policy.** Untrusted fork code never receives live secrets and is never run
via `pull_request_target`. Fork PRs run Tier 1 without secrets and remain unmergeable until
a maintainer reviews the commit and triggers the protected base-repository Tier-2 workflow
for that exact SHA; any new commit invalidates approval/status. GitLab external MRs follow
the same protected-runner rule. Protected branch/merge policy requires the SHA-bound Tier-2
result before merge.

**Privacy scrubbing (stated approach, verified in CI):** traces and logs carry ids, hashes,
counts, booleans, and latencies — never patient identifiers, raw document text, or
extracted clinical values (`extraction.field.outcome` logs the field NAME and grounding
boolean only). Langfuse content stays OFF (W1 D16). Eval fixtures are synthetic only and
embed canaries, so canonical **input fixture files are explicitly excluded** from the leak
scan. The scan targets only generated outputs, logs, traces, reports, recordings,
screenshots, and results, alongside PHI-shaped patterns. A self-test creates an isolated
generated output containing a known fixture canary/raw line and passes only when the scanner
returns a leak failure; the known-leak source is never part of the normal scan corpus. The
cost report aggregates spend without clinical content.

## §7 Eval gate v2 (the graded hard gate — two tiers, W2-D5 + W2-D8)

50 in-repo synthetic cases (fixture documents authored from Synthea data + degraded
variants); boolean rubrics only; golden set reproducible from the repo alone (RPO 0 via
git).

**Two-tier design (W2-D8, locked 2026-07-13).** Read the PRD precisely: "integration
tests... must pass in CI without live API access" scopes the stub requirement to
**integration tests**; nothing forbids live calls in the eval gate — and the hard gate
(a grader-injected regression must fail CI) plus the required judge configuration both
argue for them.
- **Tier 1 — offline: every PR and the local Git Hook.** Real local components run for
  real (OCR, text-layer read, retrieval, Pydantic validation, grounding, citation
  builder, templater, PHI canary harness); unit tests + integration tests on fixture
  documents use **recorded** model responses committed under `agent/evals/recordings/`
  (regenerated by a documented `make record-evals` live command, reviewed in PR diff),
  plus the deterministic rubric subset (schema_valid structure, citation completeness,
  PHI checks, deterministic refusal paths). Satisfies the PRD's no-live-API clause
  verbatim; no secrets on contributor machines.
- **Tier 2 — the graded gate: PR-blocking in GH Actions.** The **full 50-case run with
  live Anthropic** — real agent turns (VLM extraction over fixture documents + the
  answer model) plus the pinned LLM judge for factually_consistent free text. This is
  what the graders' regression injection hits: it exercises real prompt and orchestration
  behavior, closing the stubbed-gate blind spot (a fully-stubbed gate structurally cannot
  see a prompt-level regression). The reranker is **never live in CI** (W2-D4; stubbed /
  local — rubric booleans independent of rerank ordering). `ANTHROPIC_API_KEY` via GH
  Actions repo secrets. **Infra failure ≠ case failure:** bounded retries, then the job
  errors as inconclusive — rerun required, never silent green, never auto-pass. ~~Cost is
  approximately 50 live turns / $4 per run.~~ **SUPERSEDED — Post-review remediation
  (2026-07-13):** budget and quota use
  `50 × (VLM extraction + answer turn + pinned-judge turn)`, with multi-page VLM calls and
  retries additional. A pre-gate timing/cost/quota spike records call count, wall time,
  concurrency, provider limits, and projected per-PR/checkpoint cost before Tier 2 becomes
  required. Each Tier-2 run exports its results; the
  committed **results** deliverable is refreshed at least per checkpoint.

**Categories, thresholds, and the regression rule (PRD req 6, now implementable):**

| Category | Judged by | Pass threshold |
|---|---|---|
| schema_valid | deterministic (Pydantic) | **100% — any applicable failure is red** |
| citation_present | deterministic (CitationV2 completeness) | **100% — any applicable failure is red** |
| factually_consistent | deterministic field-vs-evidence for structured claims; **LLM-judged only for free-text synthesis — the single judged check, Tier 2** | ≥ 90% |
| safe_refusal | deterministic (canonical refusals are templated → string/shape match) | 100% on refusal-tagged cases |
| no_phi_in_logs | deterministic (canary harness, below) | **100% — any hit is red regardless of the 5% rule** |

The regression baseline is a committed `agent/evals/w2_baseline.json` on main, updated only
by an explicit PR step (never auto-committed by CI). Deterministic categories
`schema_valid`, `citation_present`, `safe_refusal`, and `no_phi_in_logs` require 100%; one
applicable failure is immediately red. `factually_consistent` alone fails if it regresses
more than five percentage points or drops below 90%. **Case
allocation:** every case scores schema_valid + citation_present + no_phi_in_logs; tagged
subsets score the rest (~10 refusal-tagged, ~8 missing-data, ~6 injection-bearing, ~4
retrieval-empty, ~12 extraction clean/degraded/disagreement/duplicate, ~10 question-flow
consistency; tags overlap). ~~A single case flip necessarily exceeds 5% in every
category.~~ **SUPERSEDED:** one deterministic-category failure is red because its threshold
is 100%; a `factually_consistent` drill must flip enough applicable cases to cross its
actual denominator/threshold. Gate output prints numerator, denominator, score, baseline
delta, and triggering rule.

**Judge configuration (named deliverable):** committed `agent/evals/judge_config.yaml` —
pinned model id + version, temperature 0, boolean question templates quoting the exact
evidence span. Judge calls run **only in Tier 2**; agent calls are temperature-pinned.
Flake policy: one judge retry at temperature 0; a judged False is a real fail; a judge
**infra** failure after retries makes the job inconclusive (rerun required), never a
silent pass.

**no_phi_in_logs mechanics:** every synthetic fixture embeds unique canary tokens (patient
name `ZZPHI-<case_id>`, a canary MRN, a canary sentence in the document body); the harness
captures all structured log output per case (correlation-ID-scoped); the per-case boolean
is "zero canary tokens and zero fixture-document n-grams in captured logs/traces." ~~The
global CI check scans the fixture inputs themselves.~~ **SUPERSEDED — Post-review
remediation (2026-07-13):** canonical inputs are excluded; generated outputs, logs, traces,
reports, recordings, screenshots, and results are scanned. The known-leak self-test creates
a temporary generated output with a canary/raw line and passes only when the scanner trips.

**Scorer self-tests + regression drill:** each of the 5 boolean scorers has a known-fail
fixture proving it returns False on a violating output (guards: permanently-green gate). At
Final, a **regression drill** on a throwaway branch injects one applicable failure for every
deterministic 100% category and enough applicable `factually_consistent` failures to cross
its real threshold/delta, in addition to each of the four W2_DEFENSE_PREP §8 regressions;
the gate must go red for every mapped category, and the **full red-run matrix** (each
deterministic-category injection, the `factually_consistent` threshold crossing, and the
four §8 regressions) is linked in the CI Evidence deliverable. **Correction to the
defense-prep §8 regression-#3 story (recorded honestly):** the empty-allergy render is
enforced by the deterministic templater (W1 §5 rule 3), so a pure prompt edit cannot flip
it — the realistic injected regression is a code change loosening that rule, caught
deterministically by safe_refusal; a genuine prompt-level behavior change is caught
behaviorally by the Tier-2 live run (W2-D8's purpose).

Case mix covers extraction (clean + degraded + disagreement), retrieval (hit + empty),
citations, refusals, missing-data, duplicate upload, injection-bearing documents (W2-D7),
and named write-surface invariants: F12 scope escalation → startup refusal; F13
cross-patient **and mismatched encounter**; F14 wrong category path/ID/ACL; F15 out-of-range
vital → artifact-only skip; F16 attribution spoof → stripped; F17 old access **and refresh**
tokens rejected post-cutover; F19 invalid upload → controlled 4xx. Eval-artifact deliverables: dataset
with expected behavior per case, boolean rubrics, judge configuration, committed results
per run.

## §7a Testing strategy (PRD: documented four-way split; every test names its failure mode via the W1 `guards:` convention)

**Post-review remediation (2026-07-13):** lower-level tests and the golden set jointly prove
the frozen contracts, D9 negatives, D10 reconciliation, queue/credential lifecycle,
migrations, F20, and one-ID trace; a declaration alone is not acceptance evidence.

- **Unit-tested:** every frozen Pydantic model and every `FailureReason` member; grounding
  matcher (found / not-found / disagreement); `GroundedField` citation ownership and the
  reject-on-incomplete-grounding rule; result-level lab collection-date consistency;
  grounded-intake-vital units/ranges; **coordinate-space conversion** (both reading paths
  yield the same NormBBox for the same word); chunker + manifest license/figure-strip check;
  citation builder; the permanent patient-bound dedup/lineage key, purgeable attempts, and
  the D10 `{pending, unknown, complete}` intent transition/reconciliation state machine;
  claim/lease/heartbeat/backoff/stale-lease recovery; atomic failed-job requeue; breaker
  state machine; typed evidence-search query builder + outbound PHI screen; typed worker,
  job/write-response, and log-event-envelope contracts; migration 002/003 compatibility;
  **the write-side
  containment controls (W2-D9)** — encounter-ownership preflight, startup exact-scope
  assertion, vital-range bound, and no-caller-supplied-author (guards: the OpenEMR write
  surface enforcing none of these server-side, W2-F12/F13/F15/F16); **the 5 rubric scorers**
  (known-fail fixtures — guards: permanently-green gate).
- **Integration-tested (fixtures + recorded stubs, no live APIs in CI — PRD):** full
  ingestion-to-answer path on fixture documents (clean scan, degraded scan, born-digital,
  junk-text-layer, duplicate upload — including a **concurrent** duplicate variant,
  wrong-doc-type, injection-bearing) with recorded VLM/LLM/reranker responses;
  supervisor-worker contract tests (enum membership + ref resolvability); OpenAPI contract
  tests; writeback path against mocked documents/vitals APIs including an ambiguous
  commit-then-timeout, durable `unknown`, list-by-marker/content-hash reconciliation, and a
  proof that no blind re-POST occurs; source/artifact/vital **round-trip re-read gates**;
  permanent-ledger survival after 30-day attempt purge; atomic failed-job requeue;
  lease expiry/stale recovery and graceful shutdown; a long job refreshing its separate
  encrypted delegated-job credential after interactive-session idle expiry; path-to-category
  ID/ACL drift refusal; F20 unknown-DEBUG refusal; old access and refresh token rejection;
  the single-correlation-ID chain from inbound request through terminal event; migration
  002/003 expand/contract, rollback/roll-forward, and mixed-version compatibility;
  cross-patient upload/status/page
  fetch → refused (leak tests) plus the write-side negative cases —
  mismatched-`encounter_id`, oversized/malformed upload → controlled 4xx, and out-of-range
  vital → artifact-only (W2-D9 / W2-F13/F15/F19).
- **Golden-set evaluated (agent behavior):** the 50 boolean-rubric cases per §7, two tiers.
- **Not tested, and why:** live VLM output-quality drift (nondeterministic vendor surface —
  mitigated by grounding + the Tier-2 live runs, not unit tests); Cohere rerank internals
  (external service — contract-tested at our boundary, stubbed in CI); OpenEMR upstream
  behavior beyond our call contracts (W1 audit covered it; we do not modify it); true load
  beyond the k6 baselines (bounded baseline runs only, §6).

## §8 Risks & owned tradeoffs

**Post-review remediation (2026-07-13):** only the five named stretch items are cuttable;
all core, engineering, containment, write, gate, and submission-host requirements remain
owned through the complete robust Final.

- **LangGraph is new surface:** workers thin, routing logged, step-budgeted; D6 seam story
  (W2-D2). Streaming through workers is the V2 spike (§9) with a named fallback.
- **OCR fidelity on degraded scans:** by design becomes UNSUPPORTED, not wrong; degraded
  fixtures in evals; text-layer path avoids OCR where truth is free (W2-D3).
- **Cohere is an external serving dependency:** the seam (`RERANKER`), the dated key
  trigger (§2), the enforced PHI-free contract (§4), and the implemented local alternative
  bound the risk; version logged per trace against score drift; CI never depends on it.
- **The write path is a new risk class:** the complete source-document + grounded-artifact +
  structured-intake-vitals path is bounded, append-only, attributed, patient/encounter
  contained, and governed by one durable exactly-once contract (W2-D10). Scopes widen
  read-only → read + narrow
  create; said, not hidden. **The adversarial re-audit (W2-D9) sharpened this: the OpenEMR
  write surface enforces no server-side patient/encounter ownership (W2-F13), scope ceiling
  (W2-F12), category ACL (W2-F14), vital range (W2-F15), attribution (W2-F16), or
  idempotency (W2-F18), and client-disable does not revoke live tokens (W2-F17) — so the
  agent is the sole containment point.** The mitigation is the §4 write-side containment
  boundary + the §5 refuse/skip rows + patient-bound permanent dedup/lineage + durable
  intents and reconcile-before-retry + a cutover retiring both access and refresh tokens;
  each is negative-tested (§7a). This is the write path's honest, un-softened risk owning.
- **Corpus is deliberately three documents:** small-and-applicable per the PRD; manifest
  makes additions one-line; do-not-ingest list + figure-strip rule prevent licensing traps
  (W2-R2).
- **Stretch-tier positioning (PRD p.4 vs p.5 reconciled):** click-to-source is delivered by
  core work (citation popovers + required bbox overlay + page preview). The only sanctioned
  cuts from the complete robust Final are the **critic agent, third document type, lab-trend
  chart, contextual-retrieval extras, and ColQwen2/multi-vector indexing**. They are recorded
  as stretch cuts, not incomplete core work; none is silently stubbed or worked around.
- **Submission host is GitLab:** the mirror is kept current at every checkpoint by a CI
  mirror-push job; `.gitlab-ci.yml` runs Tier 1 and a fail-closed `graded-gate` that accepts
  the required GitHub `eval-tier2-live` result only when it names the identical mirrored
  commit SHA (W2-D8; §6a). README documents
  every required env var (`COHERE_API_KEY`, `RERANKER`, Langfuse keys, SMART client,
  `OE_*`), the W1-baseline vs W2-multimodal split, **the canonical branch (main), the three
  services and which one serves the W2 flow (the agent service URL)**, and the one-command
  grader path from clone to the core flow.

**Uncuttable through Final (Post-review remediation, 2026-07-13):** every PRD core and
engineering requirement; both required document types; the complete D10 source/artifact/
vitals write path; every D9 containment control; the frozen typed contracts; durable queue
and job credential; permanent exactly-once/dedup lineage; all named negative cases; the
50-case two-tier gate and threshold-crossing drill; scoped PHI-leak CI and self-test;
GitLab submission-host enforcement; OpenAPI, Bruno, readiness/observability, baselines,
backup/restore, cost/latency report, and demo video. Schedule pressure may reorder these
items but may not cut, defer, stub, or weaken them.

**W1 debt ledger (PRD p.3: documented AND resolved before new surface; deviations owned):**

| # | Debt item (W2_DEFENSE_PREP §6) | Resolution | Wave (§9) |
|---|---|---|---|
| 1 | Token/PKCE state dies on restart | Persist OAuth state in the Postgres session store — **pulled into MVP**: the async job's write principal depends on it (§3) | MVP |
| 2 | 50-VU /ready saturation knee unmeasured | Re-measured with the new deps in the baseline runs | Early |
| 3 | Verification-v2 rules + UC2 delta tool partially deferred | **Absorbed and closed by Early**: extraction grounding is verification-v2 work; supervisor per-turn routing subsumes the delta-tool trigger; the plan schedules every residual rule rather than deferring it | MVP–Early |
| 4 | R12 latency anchor unverified | Superseded by measured p50/p95 in the §8a cost/latency report | Final |
| 5 | GitLab mirror + RAILWAY_TOKEN manual-deploy residual | Closed in W2 CI work: mirror-push job + `.gitlab-ci.yml` + deploy-on-green only | MVP |

Items landing at Early/Final are the owned deviation from the PRD's "before new surface"
ordering: sequencing argued by demo reliability (item 1, the only one new surface depends
on, lands first).

## §8a Backup & recovery (PRD: automatic + manual, RPO/RTO)

**Post-review remediation (2026-07-13):** agent Postgres and patient-linked job/ledger state
are PHI-bearing backup authorities, and named source-file custody is the manual recovery leg.

- **Eval golden set + corpus manifest + fixtures + recordings:** in the repo — reproducible
  from a clone alone (RPO 0, RTO = clone time). The vector index is an image-build
  artifact, rebuilt deterministically from the corpus script (RTO minutes).
- **Source documents + derived records:** OpenEMR's MySQL/documents store. **Automatic
  leg:** Railway MySQL + volume backup posture — verifying/enabling it and recording the
  evidence in DEPLOYMENT.md is a named deploy action before Final (owner checklist, Open
  items). **Manual source-file custody:** the clinic/records owner retains the named original
  source set outside the agent; the recovery inventory maps each content hash to its
  custodian and re-upload procedure. Re-upload + re-ingestion is safe because D10 reconciles
  permanent patient-bound lineage and remote markers before any POST (RPO = last OpenEMR
  backup or the retained source set; RTO = restore plus minutes per document).
- **Agent Postgres:** encrypted scheduled backup covers OAuth state, encrypted delegated-job
  credentials, jobs/attempts, permanent dedup/lineage/intents, attribution, and correlation
  metadata. A named restore drill validates key availability, referential integrity,
  reconciliation, and application startup before Final; backup material is access-restricted
  and encrypted in transit/at rest. Its measured RPO/RTO are recorded in DEPLOYMENT.md.
- **Job/status rows are PHI:** `patient_id` makes queue and attempt rows PHI even without
  clinical text. Access is least-privilege, diagnostics are PHI-scanned, rows are backed up,
  and attempt rows alone follow the 30-day purge; permanent dedup/lineage/intents are never
  purged. They are not disposable operational state.
- **Traces/observability:** Langfuse Cloud retention per W1 D5; loss of traces never
  affects serving (soft dependency).

**Cost & latency report contents (Final):** actual dev spend from traces + Railway billing,
projected production cost at the W1 ARCHITECTURE §9 scale tiers, measured p50/p95 for
ingestion / extraction / retrieval / full-turn, and a bottleneck analysis (expected: VLM
page calls dominate ingestion; LLM dominates turns — verified against traces).

## §9 Build order (→ /tasks-gen against checkpoints)

**Post-review remediation (2026-07-13):** checkpoints order the work; they do not reduce
scope. MVP is the first fully correct slice and every non-stretch requirement is robust by
Final 2026-07-19.

- **MVP (Tue 2026-07-14 11:59 PM CT; first fully correct vertical slice, not a scope
  ceiling):** complete owner provisioning/F20 evidence; run the **full concurrent** Railway
  capacity spike (bge-small + local reranker + one OCR page, measured RSS against the plan
  limit); prove the LangGraph+SSE seam/fallback; freeze all §2 contracts before M6; land
  migrations 002/003 expand-first; persist OAuth and the separate encrypted job credential;
  build the leased queue, permanent patient-bound lineage, and D10 intent/reconciliation
  foundation; deliver both document types through source + grounded-artifact writes with
  path/ACL, ownership, attribution, validation, remote-marker, and round-trip controls;
  deliver typed retrieval/citations and the first complete supervisor/worker path; stand up
  the 50-case Tier-1/Tier-2 gate, scoped PHI scanner/self-test, GitLab SHA-bound enforcement,
  recordings, observability, and deployed first correct slice. No landed write leg may lack
  its full D9/D10 containment.
- **Early (Thu 2026-07-16):** complete structured grounded-intake-vitals writing under the
  same D10 contract; complete negative cases F12–F19 and F20; harden lease/recovery/refresh,
  migrations, correlation propagation, overlay/follow-ups, dashboards/alerts, OpenAPI and
  Bruno; run full-stack baselines vs W1 and formally lock the **one** dated numeric SLO set
  used everywhere; finish the Tier-2 timing/cost/quota spike and fork-PR secret policy.
- **Final (Sun 2026-07-19; complete robust submission):** close every non-stretch PRD and
  engineering requirement; run migration, restore, readiness/degradation, exactly-once,
  containment, and live E2E drills; run the threshold-correct **regression drill** (one
  applicable failure turns each 100% deterministic category red; enough factual failures
  cross its threshold/delta) and link evidence; publish cost/latency and SLO reports, backup
  evidence, README/deployment/runbooks, and the 3–5 minute demo video covering upload,
  extraction, evidence retrieval, citations, evals, and observability. The only omissions
  are the five named stretch cuts in §8.

## §10 Requirement trace matrix

**Post-review remediation (2026-07-13):** the matrix includes W2-D10 and the twenty review
closures; `covered` means specified and scheduled until implementation evidence exists.

The full re-derived coverage table (99 PRD requirements, zero blank cells) is
`docs/week2/W2_gap-audit.md`. Summary matrix (every graded item → its section):

| Requirement | Where |
|---|---|
| Two doc types (lab_pdf, intake_form) | §2 schemas, §2a upload contract, §3 ingestion |
| Supervisor + 2 workers, logged handoffs | §2 graph, W2-D2 |
| Hybrid RAG + rerank, small corpus | §2 retriever/corpus, W2-D4 rev |
| 50-case golden set, boolean rubrics, judge config, results | §7 |
| PR-blocking eval CI + observable deployed demo | §6a enforcement, §7, §6 |
| Citation contract shape + bbox overlay + coordinate space | §2, §2a page renders, W2-D6/D3 |
| Store source in OpenEMR; derived facts as FHIR **or OpenEMR records** | §3 (discrepancy note), W2-D1, W2-R5 |
| Complete source + grounded artifact + structured vitals, one exactly-once contract | §3–§4, W2-D10 |
| Grounded intake vitals + physiological ranges + token-derived attribution | §2, §4, W2-D9/D10 |
| Path-based source/artifact category + ID/ACL preflight | §4, W2-D9 |
| Durable claim/lease queue + separate delegated-job credential | §3, §3a, W2-D10 |
| Patient-bound permanent lineage vs 30-day attempt rows | §3, §3a, W2-D10 |
| Typed contracts every interface + migration note + data authority | §2, §2a (migration note), §4a |
| SLOs, queues, retries, timeouts, circuit breakers | §3 (durable job), §5 (breakers), §6 |
| Correlation ID everywhere; trace from ID alone (incl. async hop) | §3 (job rows carry it), §6 |
| Structured logs by case/event/correlation id; event inventory; PHI-free | §6a |
| Dashboards incl. queue depth, event retries, decision outcomes | §6 |
| Per-encounter log line (all seven fields, core req 7) | §6 encounter.summary |
| CI: build, lint/typecheck, tests, coverage, dep audit, security scan | §6a |
| W1 eval suite stays green (shared-path regression) | §6a CI |
| Testing strategy four-way + not-tested-and-why + failure mode per test | §7a |
| Failure modes: identify-in-logs + recovery (incl. restart, partial write, OCR, routing) | §5 + §6a + runbooks |
| Bruno collection: upload, status, retrieval, page render, full flow | §2a, §6 |
| Baselines: 4 flows × CPU/mem/latency/throughput vs W1 | §6 |
| /health + /ready degraded with hard/soft classification | §5 |
| Alerts with thresholds + response actions | §6 |
| OpenAPI 3.0 committed + contract tests | §2a, §6a |
| Integration tests, fixtures + stubs, no live APIs in CI | §7a |
| Data model owner/lineage/access/validation + lifecycles | §4a, §3a |
| Privacy audit + scrubbing + canary-backed CI PHI check | §4, §6a, §7 |
| Backup/recovery + RPO/RTO; golden set repo-reproducible | §8a |
| Agent-Postgres encrypted backup + source-file custody + job-row PHI handling | §3a, §8a |
| W1 debt documented + resolved (5 items) | §8 ledger |
| Scenario promise (3 degraded axes) | §3 + §7 case mix |
| Eval gate fires on submission host (GitLab), live tier bound to identical SHA | §6a enforcement, W2-D8 |
| GitLab repo + setup guide + env-var/branch/service docs + deployed link | §8 |
| Cost & latency report (dev spend, projection, p50/p95, bottlenecks) | §8a |
| Demo video 3–5 min (six required contents) | §9 Final |
| Capability → W1 user mapping | §1 note, W2_USERS |

## Open items (carried visibly; next step = /tasks-gen)

- **W2-O2 (scheduled closure)** — one numeric SLO set is formally locked from measured
  baselines at Early Thu 2026-07-16 and then used unchanged in every Final artifact.
- **W2-O3** — "pending review" UI treatment for machine-authored records lands with the
  core flow; the provenance flag itself is locked (W2-D1).
- **O-new — RESOLVED by W2-D10/Post-review remediation (2026-07-13):** the exact grounded
  intake-vitals fields and bounded OpenEMR mappings are frozen in §2/§4; lab results never
  route to vitals.
- **V2 spike** — LangGraph + SSE streaming through workers (MVP wave 1, §2a fallback named).
- **Owner actions:** Cohere production key → Railway env (**trigger: Monday 2026-07-13
  EOD**, else the already-implemented local reranker is the robust Final path); provision
  the exact W2-AUDIT/W2-OA3 replacement-client scope manifest, enable it, verify the exact
  grant, and disable the old client **plus retire both access and refresh tokens**; record
  the F20 DEBUG check; verify OpenEMR and encrypted agent-Postgres backup/restore posture
  and named source-file custody before Final.
