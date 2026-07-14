# W2_ARCHITECTURE.md — Multimodal Evidence Agent (Week 2, binding)

> **Binding architecture for Week 2** (PRD deliverable: `./W2_ARCHITECTURE.md`). Finalized
> 2026-07-13 by an adversarial /arch-finalize pass — gap audit, findings register, and the
> full 99-row PRD coverage table live in `docs/week2/W2_gap-audit.md`. Decisions are ADRs in
> `docs/week2/W2_DECISIONS.md` (W2-D1..D8); external facts are `docs/week2/W2_RESEARCH.md`
> (W2-R1..R6); write/upload-surface findings are `docs/week2/W2_AUDIT.md` (W2-F1..F6); use
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

Ingestion is an **asynchronous job with durable state**: job rows persist in the W1
Postgres store, boot reconciliation marks interrupted jobs failed-and-retriable, writes
execute under the uploader's delegated token (never `client_credentials`), a write ledger
makes partial-failure retry safe, and every create is **verified by re-read** before the
job reports complete. Derived facts write back **append-only with lineage**: worst-case
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
pinned judge — with per-category thresholds and a committed baseline, so a grader-injected
prompt or behavior regression fails CI, not just a code regression. Scorer self-tests
and a pre-submission regression drill prove the gate can go red before graders try to make
it. Owned tradeoffs stand un-softened: Cohere is a vendor seam, LangGraph is new surface
kept thin, OCR fidelity degrades to UNSUPPORTED rather than wrong, and scopes widen from
read-only to read + narrow create — said plainly. The W1 thesis is unchanged: the model
drafts, deterministic checks decide — Week 2 extends it to pixels.

---

> ## Verification errata (2026-07-14 — dated revision; probe-verified facts, no decision changes)
> Source: W2-F1 independent live verification (W2_AUDIT.md, findings W2-F7..F11; W2-D1
> addendum). W2-F1 CONFIRMED — route-level 404s on FHIR POSTs with maximal write scopes.
> Corrections binding on the sections below:
> 1. **Upload contract (§2a, §3):** `POST /api/patient/:pid/document` returns **HTTP 200
>    body `true` with NO document id** (DocumentRestController.php:120), not 201. The
>    document id is discovered via collection GET keyed on unique filename/content-hash;
>    `attach_and_extract` and the job's `writeback.created` lineage record account for
>    this discovery step.
> 2. **Read-back path (§3 re-read verification):** the standard REST document download
>    returns **500** in this stack (CSRF-key defect via DocumentService::getFile — known
>    issue, not ours to fix). The verified round-trip read-back is the **FHIR
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
>    user/Binary.read` for read-back). **Clients cannot gain scopes post-registration:
>    MVP requires a REPLACEMENT SMART client registration** (W1+W2 scope union,
>    authorization_code+refresh, swap SMART_CLIENT_ID/SECRET, admin-enable, disable the
>    old client after cutover — E9 lesson). Staff ACLs must permit patients/docs write.

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
 append-only writes w/ lineage,           ┌─────────────────────────────┐
 write ledger, re-read verification       │ ANSWER COMPOSER             │
 (documents API; vitals API only for      │ citation contract v2 (W2-D6)│
  intake vitals w/ explicit encounter)    │ patient facts ≠ guideline   │
             │                            │ evidence · bbox overlay     │
             ▼                            │ (in-session page renders) · │
 OpenEMR = system of record               │ verify-then-flush + refusals│
 (Zone A, unchanged authority)            └─────────────────────────────┘
 External (Zone C, BAA posture): Claude LLM+VLM (assumed BAA) · Cohere Rerank
 (enforced PHI-free queries, RERANKER=cohere|local seam) · Langfuse (D16 content OFF)
 Eval gate v2: 50 boolean cases · Tier 1 offline + Tier 2 live, both PR-blocking (W2-D5/D8)
```

Deployment unchanged: one Railway project (OpenEMR + MySQL + agent). Corpus + index live in
the agent service, **built at Docker image build** from the committed corpus + manifest and
rebuildable from repo (W2-O1 resolved: in-process, with a memory budget and fallback ladder,
§6). New container deps: Tesseract binary + eng traineddata, pdfium native lib (via
pypdfium2, W2-R6), ONNX runtime for bge-small — verified by a day-1 container spike (§9).
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
- **Extraction schemas (canonical contracts, PRD req 2) — Pydantic v2, explicitly.** All in
  `agent/app/`, same stack as W1 D3, each with validation tests (a named PRD deliverable).
  **Composition rule (one sentence, load-bearing):** every leaf clinical value is
  `GroundedField[T]` and earns its `CitationV2` only when `grounded=true`; ungrounded
  leaves render UNSUPPORTED and carry no citation. Inventory:
  - `GroundedField[T]{value: T, page: int|None, bbox: NormBBox|None, grounded: bool}`.
    **Canonical coordinate space:** `NormBBox{x0,y0,x1,y1}` are normalized page-relative
    coordinates ∈ [0,1], origin top-left, y-down. Both readers convert at ingestion
    (Tesseract: divide by rendered pixel dimensions; PDF text layer: divide by the media
    box and flip y); the overlay multiplies by the displayed image's pixel dimensions.
    Render DPI (200) and page pixel dimensions are recorded in the words+boxes layer. A
    unit-test fixture asserts both paths yield the same normalized box for the same word.
  - `LabPdfExtraction{results: list[LabResult], collection_date: GroundedField[date],
    source_document_id}`; `LabResult{test_name, value, unit, reference_range,
    abnormal_flag — each GroundedField-wrapped — source_citation: CitationV2}` (a lab
    report is N results, not one).
  - `IntakeFormExtraction{demographics: Demographics{name, dob, sex, contact, ...},
    chief_concern, current_medications: list, allergies: list, family_history — each leaf
    GroundedField-wrapped with per-field CitationV2}`.
  - `CitationV2{source_type ∈ {patient_record, uploaded_document, guideline}, source_id,
    page_or_section, field_or_chunk_id, quote_or_value}`. Guideline source_ids embed the
    corpus version (`vadod-htn-2020@<manifest-hash>`) so citations resolve against exactly
    the ingested corpus build.
  - `EvidenceSnippet{source_id, section, chunk_id, quote, score, corpus_version}`.
  - `HandoffRecord{correlation_id, turn, supervisor_decision, reason_code, worker,
    input_ref, output_ref, handoff_ts}` — **supervisor_decision ∈ {route_extract,
    route_retrieve, compose_answer, refuse, done}** (closed enum; a closed reason_code enum
    per decision; input_ref/output_ref are trace-addressable ids). The supervisor-worker
    contract tests assert enum membership and ref resolvability.
  - `UploadRequest`/`UploadAccepted`, `DocumentStatus{document_id, state, reason:
    FailureReason|None, correlation_id, updated_ts, fields_grounded, fields_unsupported}`
    with `FailureReason ∈ {unsupported_media_type, size_or_page_cap_exceeded,
    storage_write_failed, ocr_failed, vlm_timeout, vlm_unavailable, schema_violation,
    auth_expired, writeback_failed, writeback_verify_failed, doc_type_mismatch,
    worker_restart}` — each value cross-referenced to its §5 row and §6a event.
  - `ExtractionArtifact{artifact_version, document_id, content_hash, correlation_id,
    doc_type, extraction: LabPdfExtraction|IntakeFormExtraction, grounding_summary,
    created_ts, agent_version}` — persisted as application/json under the document
    category "AI-Extractions". `VitalsWrite` — typed mapping of intake-form vitals fields
    to the vitals API body (field mapping resolved before /tasks-gen; open item O-new).

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
  `COHERE_API_KEY` is not in the Railway env by **Monday 2026-07-14 EOD**, MVP ships
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
- `GET /documents/{id}/pages/{n}` → page PNG at the canonical render DPI, for the overlay.
  Same pin + patient-match rule; rendered **on demand** from the OpenEMR-stored source
  (fetched with the delegated token), held in a bounded in-memory short-TTL cache, never
  written to disk, never logged or traced. Cross-patient page fetch → 403 (leak test in
  §7a).
- `POST /evidence/search {query: str — PHI-free, builder-constructed (§4), k: int ≤ cap}`
  → `list[EvidenceSnippet]`.
- `POST /chat` — W1 contract unchanged; SSE claim-block events now carry CitationV2 (see
  migration note). Streaming through LangGraph workers is verified by the V2 spike (§9;
  fallback: stream only the final composer stage — perceived-latency cost named in the
  cost report, never a correctness cost).

The committed OpenAPI 3.0 spec and the Bruno collection enumerate **exactly this list**
plus the W1 endpoints, so the contract tests verify a closed surface.

**Migration notes (W1→W2).** The one W1-visible schema change: served citations move from
W1 evidence IDs to CitationV2. Mapping for chart claims: `source_type=patient_record,
source_id={ResourceType}/{uuid}, page_or_section=null, field_or_chunk_id={W1 evidence_id
incl. hash8}, quote_or_value={verified field value}`. The W1 EvidencePacket, claim schemas,
and verification pipeline are **unchanged**; a composer-side adapter emits CitationV2; W1
UI citation chips render from CitationV2 going forward. The mapping is pinned by a
regression test. The `/chat` SSE event carrying CitationV2 is the same change surfaced at
the endpoint; no other W1 schema changes.

## §3 The two lifecycles

**Ingestion (asynchronous job with durable state — required by the extraction-status
endpoint and the queue-depth metric).** `POST /documents` (auth per §2a) performs an
**atomic insert-or-return** on a UNIQUE(patient_id, content_hash) constraint in the
agent's Postgres store — concurrent duplicate uploads race-safely resolve to one document
— stores the source in OpenEMR, enqueues a **durable job row** `{job_id, document_id,
content_hash, correlation_id, session_ref, state, per_leg_write_state, attempts,
updated_ts}`, and returns 202 immediately. The status endpoint reads the durable row,
never process memory; queue depth derives from durable rows and survives restarts.

The job (in-process worker over durable rows): build words+boxes layer (text-layer first,
junk-layer sanity check, OCR fallback; per-page subprocess timeout) → supervisor routes to
extractor → VLM extraction → Pydantic validation (hard reject) → per-field grounding →
writeback → **round-trip verification** → status complete. States:
`{queued | extracting | grounding | writing | complete | failed(FailureReason)}`.

- **Write principal.** The job executes under the **uploading clinician's delegated
  token**, referenced from the persisted session store (the W1 token-persistence debt fix,
  pulled into the MVP wave — §9 — precisely because this design depends on it); the
  refresh_token grant fires if a write outlives the access token; refresh failure →
  `failed(auth_expired)`, source retained, recovery = idempotent re-run under a fresh
  session. **Never `client_credentials`** (W1 D9/F-S.5 carried). OpenEMR attribution shows
  the clinician; machine authorship is carried in the record itself (lineage + provenance
  flag, W2-O3).
- **Write ordering + ledger.** Extraction artifact (documents API) first, vitals second.
  Per-leg completion is recorded in the job row; a **write ledger** keyed by
  (content_hash, field_id) is written transactionally around each OpenEMR create; retry
  consults the ledger and re-executes only the incomplete leg — idempotent after partial
  writes, no duplicates.
- **Lab/vitals routing rule (W2-F3, code-verified).** Lab-PDF-derived values persist
  **only** as the structured machine-authored ExtractionArtifact via the documents API —
  never via the vitals route (no lab write route exists; labs are not vitals). The vitals
  route is used exclusively for true vitals-class fields captured on the intake form (BP,
  height, weight), and **only when the upload carried an explicit encounter_id** — the
  agent never guesses or creates an encounter; absent an encounter, vitals-class facts
  persist in the artifact alone and the vitals leg is skipped with
  `writeback.skipped(no_encounter)`.
- **Round-trip verification (DEFENSE_PREP §4.A, promoted).** After writeback the job
  re-reads each created record through the same REST API and compares against the sent
  payload; only on match does status flip to complete. Mismatch/absence →
  `failed(writeback_verify_failed)` + named log event. Nothing derived is authoritative
  until written and re-read; UC-W2-3 answers cite derived facts by their OpenEMR record
  ids.
- **Boot reconciliation.** Any job in a non-terminal state at process start is marked
  `failed(worker_restart)`, logged as `doc.ingestion.failed`, and is retriable via
  idempotent re-run from the stored source (no re-upload needed). Deploys are therefore
  safe at any time.

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
| Extraction job/status rows | enqueue | terminal state; purged after 30 days | Postgres; **PHI-free by design** (ids, hashes, states, counts only) | no |
| Page render buffers | overlay request | response / short TTL | bounded in-memory cache; never disk, never logged | yes (transient) |
| LangGraph turn state | turn start | turn end | none (session store is the authority) | yes (transient) |
| Delegated token ref held by a job | enqueue | purged at job terminal state | persisted session store (W1 debt fix) | secret |
| Vector index | Docker image build | replaced by next image | non-PHI build artifact | no |
| Extraction artifact / derived records | writeback | — | OpenEMR retention posture | yes (in OpenEMR) |

## §4 Trust boundaries & PHI (W1 posture extended)

- **Zone A (OpenEMR)** keeps identity, authority, clinical truth. New: it receives
  agent-authored creates — bounded to two create operations, idempotent, source-linked,
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
- Logs/traces/eval artifacts PHI-free, enforced by the CI PHI-detection check (§6a/§7).
  Egress inventory: Anthropic, Cohere (enforced PHI-free), Langfuse.
- Scope delta said plainly: read-only → read + narrow create (`api:oemr` document/vital
  scopes; the W2-F4 client-enable step is a build-blocking provisioning checklist item —
  the expected first-run failure is a 401 on first write if skipped).

## §4a Data model & authority ledger (PRD: owner, lineage, access, validation per type)

| Artifact | Authoritative owner | Lineage | Access | Validation |
|---|---|---|---|---|
| Source document (uploaded file) | **OpenEMR** (documents store) | upload event {correlation_id, uploader, content_hash, ts} | clinician via OpenEMR; agent read via API | doc_type enum; MIME whitelist; size/page caps (§2a) |
| Extracted lab observations | Agent until written; **OpenEMR** after write (artifact-only — never vitals, W2-F3) | source document id + page + bbox per field | agent write-once; clinician review/void in OpenEMR | `LabPdfExtraction` + grounding |
| Intake facts | same as above (vitals leg only w/ explicit encounter) | same | same | `IntakeFormExtraction` + grounding |
| Extraction job/status | **Agent Postgres store** (durable rows; boot reconciliation) | job row keyed by document_id + content_hash; correlation_id | status endpoint (pinned session); ops read | `DocumentStatus` (typed states + reasons) |
| Write ledger | **Agent Postgres store** | (content_hash, field_id) → created record id | job-internal | transactional with each create |
| Words+boxes layer | derived, **ephemeral** — recomputable from the stored source; never authoritative | — | job-internal | density sanity check |
| Page renders | derived, **ephemeral**; source = the OpenEMR document | — | pinned session only (§2a) | — |
| Graph state | **ephemeral per turn**; session continuity lives only in the W1 Postgres session store | — | turn-internal | typed state class (W2-R1) |
| Guideline chunks + index | **Agent service** (image-build artifact; rebuildable from repo corpus + manifest) | manifest {source_url, license, version, ingest_date} | read-only at runtime | verbatim-chunk rule; figure-strip rule; manifest license check |
| Citation records | **Agent** (immutable, per response) | claim → CitationV2 → evidence id @ corpus_version | read-only; exported in traces (PHI-free ids) | `CitationV2`; incomplete = no render |
| Handoff records | **Agent** (append-only log) | correlation_id chain | ops read | `HandoffRecord` (closed enums) |
| Eval golden set | **Repo** (git — RPO 0) | authored fixtures, versioned | PR-reviewed changes only | case schema + boolean rubrics |

No silent overwrites anywhere: every write is a create; duplicates resolve to existing
lineage via the atomic content-hash constraint (W2-D1). One owner per type, stated above.

## §5 Failure modes (detection + recovery; every row has a named §6a log event and a runbook entry)

| Failure | Behavior |
|---|---|
| Upload exceeds caps / wrong MIME | Rejected pre-queue at POST /documents, 422 with typed reason; nothing enqueued |
| Ingestion fails mid-flow | Source already stored; job → failed(reason); explicit message; idempotent retry re-executes only incomplete legs (write ledger) |
| Process restart / deploy mid-job | Boot reconciliation marks non-terminal jobs failed(worker_restart); status endpoint reflects it; recovery = idempotent re-enqueue from the stored source |
| Job outlives access token | refresh_token grant; refresh fails → failed(auth_expired); recovery = re-run under a fresh session |
| EHR write 401 (missing scope) | failed(writeback_failed); artifact retained in job state; runbook → W2-F4 one-time client-enable step |
| EHR write fails mid-sequence (5xx/timeout) | Per-leg state + write ledger; retry re-executes only the incomplete leg; never duplicates |
| Round-trip re-read mismatch | failed(writeback_verify_failed); logged; investigate before retry |
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
| Concurrent duplicate uploads | UNIQUE(patient_id, content_hash) resolves atomically to one document; second request returns existing lineage |
| Injection text in a document | Data-not-instructions handling (§4); schema-bound output; append-only bound; eval-cased |
| /ready | Extended deps with degraded-not-binary: OpenEMR incl. documents store = HARD (503); session store = HARD (W1); Anthropic = HARD (W1); vector index = SOFT (200 + degraded: retrieval_unavailable); reranker = SOFT (200 + degraded: rerank_off) |

Repeated outbound failures trip a **circuit breaker** per dependency (VLM, reranker): after
N consecutive failures the dependency is marked open, calls short-circuit to the degraded
path, a half-open probe recovers it; breaker state is logged and dashboard-visible.

## §6 Observability, SLOs, ops (extends W1 §7)

**Metrics (W2 additions):** document ingestion count, ingestion latency, per-field
extraction pass rate, grounding-agreement rate, retrieval hit rate, rerank scores +
model/version, routing decisions, per-worker latency, eval pass rate per category, queue
depth (from durable job rows — survives restarts), **outbound retry count per dependency
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
| Eval-category regression | >5% any category | CI blocks pre-deploy by design; if seen on a live run: freeze deploys, bisect, Railway one-click rollback | Owner |

**SLOs** set from measured baselines at MVP (W2-O2; working targets: ingestion p95 ≤
30s/doc, retrieval p95 ≤ 2s). **Baselines:** recorded per PRD-named flow — (1) document
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
race); in-flight jobs survive via the durable job table + idempotent re-run (§3). A corpus
or manifest change rebuilds the index in the same PR's image; CI asserts index↔manifest
hash agreement so a stale index cannot deploy.

All outbound LLM/VLM/retrieval calls carry timeouts + retries. Supervisor span ⊃ worker
spans ⊃ extraction/retrieval sub-calls; full trace reconstructable from the correlation ID
alone. OpenAPI 3.0 spec committed for the §2a endpoint list; Bruno collection extended
(upload, extraction status, retrieval, page render, full flow) with the W1 token-mint
helper carried. Cost: VLM page calls capped per doc (~$0.005–0.01/page planning number,
W2-R4), measured from traces.

## §6a Structured log events, CI pipeline, privacy scrubbing (PRD engineering reqs)

**Log-event inventory (extends the W1 schema — same structured format, no parallel
convention; searchable by case_id, event_id, correlation_id; all PHI-free):**
`doc.ingestion.started`, `doc.ingestion.completed`, `doc.ingestion.failed(reason)`,
`doc.ocr.failed(page, reason)`, `extraction.field.outcome` (field name, grounded: bool —
never the value), `extraction.schema.violation`, `retrieval.query.executed` (hit/miss, k,
latency), `retrieval.unavailable(reason)`, `rerank.executed` (model+version, latency),
`worker.handoff` (HandoffRecord), `writeback.created` (record type, lineage ids),
`writeback.failed(leg, reason)`, `writeback.skipped(no_encounter)`,
`writeback.verify.failed`, `job.reconciled(worker_restart)`, `encounter.summary` (§6),
`eval.run.outcome` (per category), `breaker.state.changed` (dependency, state).

**CI pipeline per PR (extends the W1 eval gate):** build → ruff lint + mypy typecheck →
pytest with coverage → **W1 eval suite (`agent/evals/`, unchanged cases — shared-path
regression guard; any W1 failure blocks the PR exactly as in W1)** → Pydantic
schema-validation tests → supervisor-worker contract tests → extraction regression tests
(fixtures + recorded stubs) → OpenAPI contract tests (spec ↔ implementation) → dependency
audit (pip-audit) → security scan (semgrep) → PHI-detection check over logs/fixtures/eval
artifacts → **eval gate (Tier 1 deterministic subset + Tier 2 full live 50-case run —
both PR-blocking, W2-D8, §7)** → deploy on green.

**Enforcement surface (all three layers, explicit):** (1) the committed pre-push Git Hook
(hooksPath + documented one-command setup, `make hooks`) runs the **full Tier-1 gate** —
deterministic and seconds-fast, not a lint-only subset (no secrets on contributor
machines, W2-D8); (2) GitHub branch protection marks **both eval jobs (`eval-tier1`,
`eval-tier2-live`) required status checks** on main (named config step in the setup
guide) — the enforcement graders cannot bypass; (3) a committed **`.gitlab-ci.yml` runs
the identical Tier-1 gate on the GitLab submission mirror**; the README states GitHub is
the canonical CI remote where the full graded Tier-2 gate runs, and documents the grader
path: clone → `make hooks` → commit the regression → watch it block locally, or PR
against GitHub for the full live gate.

**Privacy scrubbing (stated approach, verified in CI):** traces and logs carry ids, hashes,
counts, booleans, and latencies — never patient identifiers, raw document text, or
extracted clinical values (`extraction.field.outcome` logs the field NAME and grounding
boolean only). Langfuse content stays OFF (W1 D16). Eval fixtures are synthetic only; every
fixture embeds **canary tokens** (§7) that the CI PHI-detection check greps for across all
logs, traces, and eval artifacts, alongside PHI-shaped patterns. The cost report aggregates
spend without clinical content.

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
  errors as inconclusive — rerun required, never silent green, never auto-pass. Cost
  owned: ~50 live turns/run, W1-measured ~$0.08/request upper bound ≈ $4/run before
  prompt-cache savings, monitored in traces. Each Tier-2 run exports its results; the
  committed **results** deliverable is refreshed at least per checkpoint.

**Categories, thresholds, and the regression rule (PRD req 6, now implementable):**

| Category | Judged by | Pass threshold |
|---|---|---|
| schema_valid | deterministic (Pydantic) | ≥ 90% |
| citation_present | deterministic (CitationV2 completeness) | ≥ 90% |
| factually_consistent | deterministic field-vs-evidence for structured claims; **LLM-judged only for free-text synthesis — the single judged check, Tier 2** | ≥ 90% |
| safe_refusal | deterministic (canonical refusals are templated → string/shape match) | 100% on refusal-tagged cases |
| no_phi_in_logs | deterministic (canary harness, below) | **100% — any hit is red regardless of the 5% rule** |

The regression baseline is a committed `agent/evals/w2_baseline.json` on main, updated only
by an explicit PR step (never auto-committed by CI); the gate compares the PR run against
that file: fail if any category regresses >5% **or** drops below its threshold. **Case
allocation:** every case scores schema_valid + citation_present + no_phi_in_logs; tagged
subsets score the rest (~10 refusal-tagged, ~8 missing-data, ~6 injection-bearing, ~4
retrieval-empty, ~12 extraction clean/degraded/disagreement/duplicate, ~10 question-flow
consistency; tags overlap). Stated plainly: at ~10–20 cases per category, a single case
flip exceeds 5% — **the gate is effectively zero-tolerance per category**, by design.

**Judge configuration (named deliverable):** committed `agent/evals/judge_config.yaml` —
pinned model id + version, temperature 0, boolean question templates quoting the exact
evidence span. Judge calls run **only in Tier 2**; agent calls are temperature-pinned.
Flake policy: one judge retry at temperature 0; a judged False is a real fail; a judge
**infra** failure after retries makes the job inconclusive (rerun required), never a
silent pass.

**no_phi_in_logs mechanics:** every synthetic fixture embeds unique canary tokens (patient
name `ZZPHI-<case_id>`, a canary MRN, a canary sentence in the document body); the harness
captures all structured log output per case (correlation-ID-scoped); the per-case boolean
is "zero canary tokens and zero fixture-document n-grams in captured logs/traces." The
global CI check additionally greps the whole log corpus and eval artifacts. This makes
defense-prep regression #4 (log a raw document line) deterministically caught.

**Scorer self-tests + regression drill:** each of the 5 boolean scorers has a known-fail
fixture proving it returns False on a violating output (guards: permanently-green gate). At
Final, a **regression drill** on a throwaway branch injects each of the four
W2_DEFENSE_PREP §8 regressions and confirms the gate goes red for the mapped category; the
four red CI runs are linked in the CI Evidence deliverable. **Correction to the
defense-prep §8 regression-#3 story (recorded honestly):** the empty-allergy render is
enforced by the deterministic templater (W1 §5 rule 3), so a pure prompt edit cannot flip
it — the realistic injected regression is a code change loosening that rule, caught
deterministically by safe_refusal; a genuine prompt-level behavior change is caught
behaviorally by the Tier-2 live run (W2-D8's purpose).

Case mix covers extraction (clean + degraded + disagreement), retrieval (hit + empty),
citations, refusals, missing-data, duplicate upload, injection-bearing documents (W2-D7),
and cross-patient access attempts (§2a invariants). Eval-artifact deliverables: dataset
with expected behavior per case, boolean rubrics, judge configuration, committed results
per run.

## §7a Testing strategy (PRD: documented four-way split; every test names its failure mode via the W1 `guards:` convention)

- **Unit-tested:** Pydantic schema validators (each model); grounding matcher (found /
  not-found / disagreement); **coordinate-space conversion** (both reading paths yield the
  same NormBBox for the same word); chunker + manifest license/figure-strip check; citation
  builder (incomplete-citation rejection); content-hash idempotency + write ledger; breaker
  state machine; **retrieval query builder + outbound PHI screen**; **the 5 rubric scorers**
  (known-fail fixtures — guards: permanently-green gate).
- **Integration-tested (fixtures + recorded stubs, no live APIs in CI — PRD):** full
  ingestion-to-answer path on fixture documents (clean scan, degraded scan, born-digital,
  junk-text-layer, duplicate upload — including a **concurrent** duplicate variant,
  wrong-doc-type, injection-bearing) with recorded VLM/LLM/reranker responses;
  supervisor-worker contract tests (enum membership + ref resolvability); OpenAPI contract
  tests; writeback path against a mocked documents/vitals API including partial-write
  retry (ledger) and the **round-trip re-read gate**; cross-patient upload/status/page
  fetch → refused (leak tests).
- **Golden-set evaluated (agent behavior):** the 50 boolean-rubric cases per §7, two tiers.
- **Not tested, and why:** live VLM output-quality drift (nondeterministic vendor surface —
  mitigated by grounding + the Tier-2 live runs, not unit tests); Cohere rerank internals
  (external service — contract-tested at our boundary, stubbed in CI); OpenEMR upstream
  behavior beyond our call contracts (W1 audit covered it; we do not modify it); true load
  beyond the k6 baselines (bounded baseline runs only, §6).

## §8 Risks & owned tradeoffs

- **LangGraph is new surface:** workers thin, routing logged, step-budgeted; D6 seam story
  (W2-D2). Streaming through workers is the V2 spike (§9) with a named fallback.
- **OCR fidelity on degraded scans:** by design becomes UNSUPPORTED, not wrong; degraded
  fixtures in evals; text-layer path avoids OCR where truth is free (W2-D3).
- **Cohere is an external serving dependency:** the seam (`RERANKER`), the dated key
  trigger (§2), the enforced PHI-free contract (§4), and the implemented local alternative
  bound the risk; version logged per trace against score drift; CI never depends on it.
- **The write path is a new risk class:** bounded append-only + idempotent + lineage +
  re-read verification; stated plainly (W2-D1). Scopes widened read-only → read + narrow
  create; said, not hidden.
- **Corpus is deliberately three documents:** small-and-applicable per the PRD; manifest
  makes additions one-line; do-not-ingest list + figure-strip rule prevent licensing traps
  (W2-R2).
- **Stretch-tier positioning (PRD p.4 vs p.5 reconciled):** core = the first five
  deliverables. Click-to-source is substantially delivered by core work (W1 citation
  popovers + the required bbox overlay + page preview); critic agent, third doc type,
  trend-chart widget, contextual retrieval, and **ColQwen2/multi-vector indexing** (stretch
  by the PRD's own Stage-2 language) are deferred with dated cut entries unless Final-core
  is green early.
- **Submission host is GitLab:** the mirror is kept current at every checkpoint by a CI
  mirror-push job; `.gitlab-ci.yml` runs the Tier-1 gate there (§6a). README documents
  every required env var (`COHERE_API_KEY`, `RERANKER`, Langfuse keys, SMART client,
  `OE_*`), the W1-baseline vs W2-multimodal split, **the canonical branch (main), the three
  services and which one serves the W2 flow (the agent service URL)**, and the one-command
  grader path from clone to the core flow.

**W1 debt ledger (PRD p.3: documented AND resolved before new surface; deviations owned):**

| # | Debt item (W2_DEFENSE_PREP §6) | Resolution | Wave (§9) |
|---|---|---|---|
| 1 | Token/PKCE state dies on restart | Persist OAuth state in the Postgres session store — **pulled into MVP**: the async job's write principal depends on it (§3) | MVP |
| 2 | 50-VU /ready saturation knee unmeasured | Re-measured with the new deps in the baseline runs | Early |
| 3 | Verification-v2 rules + UC2 delta tool partially deferred | **Absorbed**: extraction grounding IS verification-v2 work; supervisor per-turn routing subsumes the delta-tool trigger; any residual rule gets a dated deferral entry | MVP–Early |
| 4 | R12 latency anchor unverified | Superseded by measured p50/p95 in the §8a cost/latency report | Final |
| 5 | GitLab mirror + RAILWAY_TOKEN manual-deploy residual | Closed in W2 CI work: mirror-push job + `.gitlab-ci.yml` + deploy-on-green only | MVP |

Items landing at Early/Final are the owned deviation from the PRD's "before new surface"
ordering: sequencing argued by demo reliability (item 1, the only one new surface depends
on, lands first).

## §8a Backup & recovery (PRD: automatic + manual, RPO/RTO)

- **Eval golden set + corpus manifest + fixtures + recordings:** in the repo — reproducible
  from a clone alone (RPO 0, RTO = clone time). The vector index is an image-build
  artifact, rebuilt deterministically from the corpus script (RTO minutes).
- **Source documents + derived records:** OpenEMR's MySQL/documents store. **Automatic
  leg:** Railway MySQL + volume backup posture — verifying/enabling it and recording the
  evidence in DEPLOYMENT.md is a named deploy action before Final (owner checklist, Open
  items). **Manual recovery** if automated backup fails: re-upload source files and re-run
  ingestion — idempotent by content hash + write ledger, so recovery cannot duplicate
  (RPO = last backup or last upload set; RTO = re-ingestion time, minutes per doc).
- **Job/status rows:** operational state, not clinical truth — lost rows are rebuilt by
  re-running ingestion; boot reconciliation handles interrupted jobs (§3).
- **Traces/observability:** Langfuse Cloud retention per W1 D5; loss of traces never
  affects serving (soft dependency).

**Cost & latency report contents (Final):** actual dev spend from traces + Railway billing,
projected production cost at the W1 ARCHITECTURE §9 scale tiers, measured p50/p95 for
ingestion / extraction / retrieval / full-turn, and a bottleneck analysis (expected: VLM
page calls dominate ingestion; LLM dominates turns — verified against traces).

## §9 Build order (→ /tasks-gen against checkpoints)

- **MVP (Tue 11:59 PM):** day-1 container spike (Dockerfile + tesseract + traineddata +
  pdfium; verify Railway build; record image-size delta + cold start in W2-R4) → token
  persistence (W1 debt #1 — prerequisite for the job write principal) → schemas + fixtures
  → attach_and_extract (store, hash, durable job rows, read layer) → grounding verifier →
  V2 spike: LangGraph + SSE streaming through workers → LangGraph skeleton (supervisor +
  workers, handoff records, step budget) → corpus build + hybrid retrieval + reranker seam
  (**Cohere key trigger: Monday EOD**) → citation contract v2 + minimal overlay → 50-case
  gate (Tier 1 offline + Tier 2 live PR-blocking, W2-D8) + recordings + hook + branch
  protection + `.gitlab-ci.yml` + CI PHI check → deploy + README W1/W2 split.
- **Early (Thu):** overlay polish, follow-up flows, W2 dashboard panels + alerts, baselines
  vs W1 (closes W2-O1 memory + W2-O2 SLOs + debt #2), Bruno + OpenAPI.
- **Final (Sun noon):** hardening, **regression drill** (four injected regressions → four
  red runs linked in CI Evidence), cost/latency report from traces, Railway backup
  verification (§8a), demo video — 3–5 min covering the six required contents: document
  upload, extraction, evidence retrieval, citations, eval results, observability — cuts
  documented, final live E2E.

## §10 Requirement trace matrix

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
| W1 debt documented + resolved (5 items) | §8 ledger |
| Scenario promise (3 degraded axes) | §3 + §7 case mix |
| Eval gate fires on the submission host (GitLab) | §6a enforcement |
| GitLab repo + setup guide + env-var/branch/service docs + deployed link | §8 |
| Cost & latency report (dev spend, projection, p50/p95, bottlenecks) | §8a |
| Demo video 3–5 min (six required contents) | §9 Final |
| Capability → W1 user mapping | §1 note, W2_USERS |

## Open items (carried visibly; next step = /tasks-gen)

- **W2-O2** — SLO numbers set from first measured baselines at MVP (working targets in §6).
- **W2-O3** — "pending review" UI treatment for machine-authored records lands with the
  core flow; the provenance flag itself is locked (W2-D1).
- **O-new (renamed)** — exact vitals-API field mapping for **intake-form vitals fields**
  (never lab values, W2-F3); resolve during /tasks-gen, before the writeback task.
- **V2 spike** — LangGraph + SSE streaming through workers (MVP wave 1, §2a fallback named).
- **Owner actions:** Cohere production key → Railway env (**trigger: Monday 2026-07-14
  EOD**, else `RERANKER=local` ships); `api:oemr` document/vital scopes + client re-enable
  (W2-F4, build-blocking checklist); verify Railway backup posture + record in
  DEPLOYMENT.md (before Final).
