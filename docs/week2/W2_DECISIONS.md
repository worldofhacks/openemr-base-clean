# W2_DECISIONS.md — Week 2 ADR log

> New file; W1 DECISIONS.md (D1–D16) is frozen at docs/week1/. Tags: **locked** /
> proposed / open. Owner decisions recorded 2026-07-13 (see W2_PRESEARCH.md).

## W2-D1. Writes: append-only creates with lineage — **locked** (mechanism validated 2026-07-13, W2-R5)
- W1 was read-only (D9/D12). W2 requires storing documents and derived facts.
- Mechanism (R5-validated: this fork has NO FHIR write for DocumentReference or
  Observation): source files + structured extraction artifacts via
  `POST /api/patient/:pid/document` (standard REST, confirmed real); vitals-class
  facts via `POST /api/patient/:pid/encounter/:eid/vital`. The PRD's "or OpenEMR
  records" clause sanctions this; W1 D9's standard-REST fallback clause fires as
  written. Verify at build: `api:oemr` scope set on the SMART client (D14-class
  enable step).
- The agent gets exactly two write capabilities: create documents, create
  vitals-class records. No update. No delete. Nothing clinician-authored is touchable.
- **Addendum (2026-07-13, W2-F1 live verification):** mechanism confirmed end-to-end
  with corrections. Upload returns **200 `true` with no id** (id via collection GET by
  content hash); the reliable read-back is the **FHIR projection**
  (DocumentReference/uuid → Binary/uuid, byte-exact) since the standard download 500s
  in this stack; vitals round-trip fully proven through to FHIR Observation reads.
  Minimum scope surface: `api:oemr user/document.crs user/vital.crus
  user/Observation.rs` (+ DocumentReference.rs/Binary.read for read-back). **Clients
  cannot gain scopes post-registration → MVP requires a REPLACEMENT SMART client
  registration** (W1+W2 scope union, auth-code+refresh, swap credentials, disable the
  old client after cutover — E9 lesson). Staff ACLs must permit patients/docs write.
- Idempotent: content-hash on files, deterministic IDs on facts. Re-upload creates
  nothing. That is the PRD round-trip requirement.
- Discrepancy note (owner-flagged 2026-07-13): the PRD's engineering section says
  "FHIR writes"; its core requirement says "FHIR resources **or OpenEMR records**."
  This fork has no FHIR write for our targets (W2-R5, 3-way validated), so the write
  interface meets every requirement placed on "FHIR writes" (typed contract,
  correlation ID, lineage) over the sanctioned standard-REST transport. Stated
  explicitly in the architecture (§3 note), not silently substituted.
- New safety claim: injection worst case is a quarantined, machine-authored,
  source-linked record a human can void. Never a silent edit. Say plainly: scopes
  widened from read-only to read + narrow create.
- Rejected: pretending it is still read-only (false); a store outside OpenEMR (splits
  data authority, fails round-trip).
- **Addendum (2026-07-13, /arch-finalize — owner-confirmed):** the write mechanism is
  fully designed, not just named. (a) Ingestion jobs are **durable Postgres rows** with
  boot reconciliation (non-terminal jobs at boot → failed(worker_restart), retriable
  idempotently) — never in-process-only state. (b) **Write principal:** jobs execute
  under the uploading clinician's delegated token via the persisted session store (the
  W1 token-persistence debt fix, pulled into MVP because this depends on it); refresh
  grant if a write outlives the access token; never client_credentials (W1 D9/F-S.5
  carried). (c) Idempotency is **enforced, not asserted**: atomic insert-or-return on
  UNIQUE(patient_id, content_hash) + a write ledger keyed (content_hash, field_id) so
  partial-failure retry re-executes only incomplete legs. (d) Every create is **verified
  by re-read** before the job reports complete. (e) The vitals leg fires only for
  intake-form vitals fields AND only with an explicit encounter_id — labs never route
  to vitals (W2-F3); the agent never creates encounters. Binding doc §3.

## W2-D2. Orchestration: LangGraph, supervisor + 2 workers — **locked**
- D6 banned frameworks and named its own invalidation: multi-agent requirements. That
  clause fired. The seam (tool registry + orchestrator interface) was built for this.
- LangGraph owns routing. The W1 direct loop lives inside workers.
- Every handoff is a record: {correlation_id, turn, supervisor_decision, reason_code,
  worker, input_ref, output_ref, handoff_ts}. Supervisor span parents worker spans.
  Full trace reconstructs from the correlation ID alone.
- Rejected: hand-rolled graph (allowed, but spends defense capital proving
  inspectability LangGraph gets for free); critic agent in core (PRD: extension).

## W2-D3. Vision: VLM proposes, local OCR grounds — **locked**
- Claude is the VLM. Same provider, same assumed BAA. Zero new PHI processors.
- Tesseract OCR runs locally per page: text + word coordinates. $0, no egress.
- VLM output validates into strict **Pydantic v2** models (`LabPdfExtraction`,
  `IntakeFormExtraction` — PRD req 2; same stack as W1 D3); malformed = hard reject.
- Every validated field must locate its value in the OCR/text layer. Found: citation +
  bbox. Not found or disagreement: rendered UNSUPPORTED, "verify against source
  document." Pydantic proves shape; grounding proves the content is on the page.
- Confidence = grounding agreement, binary per field. VLM self-report is never trusted.
- This is W1 verify-then-flush extended to pixels. One mechanism answers invention,
  confidence inflation, and the required overlay.
- Reading path (owner-confirmed 2026-07-13): **text-layer first, OCR fallback** —
  born-digital PDFs use their embedded text + exact coordinates; true scans go
  through Tesseract; a junk-text-layer sanity check routes to OCR. Both emit the
  same words+boxes shape; downstream grounding is one code path.
- Rejected: trusting VLM output (the PRD's named pitfall); hosted OCR (new egress for
  nothing); OCR-always (degrades perfect digital text to guesses); ColQwen2 (stretch).

## W2-D4. RAG: VA/DoD corpus trio + hybrid retrieval + Cohere rerank — **locked** (revised 2026-07-13 after deep research + owner decisions)
- Corpus: **VA/DoD CPG trio** — Diabetes (2023), Hypertension (2020, version pinned),
  Lipids (2025) — plus their pocket-card summaries. US-government works
  (license-verified, W2-R2); exactly the PCP panel's chronic conditions; literally
  "agreed clinical practices" per the PRD. Manifest-driven (per-doc provenance,
  license, version) so CDC/USPSTF are one-line additions if a demo case needs them.
  Do-not-ingest list documented (ADA bans ML use; AHA/ACC, JAMA, GINA all-rights-
  reserved; never JAMA-branded PDFs).
- Retrieve: BM25 (`rank-bm25`) + dense (`bge-small-en-v1.5`, MIT, ONNX/FastEmbed —
  clinical-retrieval evidence in W2-R3). Rerank: **Cohere Rerank** (owner decision;
  production key recommended ~$2/1k searches — trial terms exclude production use,
  W2-R3). Reranker down → serve un-reranked hybrid scores, degraded, logged.
- Hard contract: queries are condition/test terms only, never identifiers. Corpus has
  no PHI. Cohere never sees PHI.
- CI never calls Cohere live (PRD: tests pass without live APIs); reranker stubbed in
  fixtures; rubric booleans must not depend on exact rerank ordering.
- Snippets carry {source_id, section, chunk_id, quote} and are the only guideline
  content the model sees. Verbatim chunks only (license + citation contract aligned).
- Rejected: licensed content (indefensible); raw patient text as queries; local
  cross-encoder as primary (owner chose Cohere; mxbai-rerank-base-v1 documented as
  the vendor-independence alternative, W2-R3).
- **Revision (2026-07-13, /arch-finalize — owner-confirmed):** the W2-R3-vs-D4 tension
  is carried honestly and bounded by design. (a) Reranker sits behind a one-env-var
  seam `RERANKER=cohere|local`; `mxbai-rerank-base-v1` (Apache-2.0) is **implemented
  and integration-tested** as the shipping fallback — the PRD's "or an equivalent
  reranker." (b) **Dated trigger:** if the paid production `COHERE_API_KEY` is not in
  the Railway env by **Monday 2026-07-13 EOD**, MVP ships `RERANKER=local`; Cohere
  becomes the Early-checkpoint upgrade. A paid production key resolves both R3
  objections (trial terms exclude production; 10 req/min throttle). (c) The PHI-free
  contract is **enforced, not asserted**: a deterministic query builder over coded
  clinical terms + an outbound screen that fails closed to the local/un-reranked path
  (unit-tested; injection eval case). (d) Figure-strip license rule from W2-R2 binds
  the corpus build (text-only ingestion). Binding doc §2/§4.

## W2-D5. Eval gate: 50 boolean cases, PR-blocking — **locked**
- 50 synthetic cases in-repo (reproducible from repo alone). Boolean rubrics only.
- Categories: schema_valid, citation_present, factually_consistent, safe_refusal,
  no_phi_in_logs. Any category >5% regression or below threshold fails the build.
- Delivery: PR-blocking Git Hook plus the existing GH Actions gate. CI PHI-detection
  check enforces no_phi_in_logs.
- Judges: deterministic first; unavoidable LLM judgments pinned to boolean questions
  quoting the evidence span.
- Designed for the graded regression injection: every category maps to a named
  one-line break it catches (W2_DEFENSE_PREP §8).

## W2-D6. Citations: prescribed shape, sources separated — **locked**
- Every clinical claim carries {source_type, source_id, page_or_section,
  field_or_chunk_id, quote_or_value}. Incomplete citation = claim does not render.
- source_type ∈ {patient_record, uploaded_document, guideline}; the UI keeps patient
  facts and guideline evidence visually distinct (PRD requirement).
- Document claims render the bounding-box overlay (W2-D3).

## W2-D7. PHI surfaces v2 — **locked** (revised 2026-07-13, /arch-finalize)
- New sensitive artifacts: document images, extracted fields, retrieval queries, eval
  fixtures, **prompts** (covered by the D16 content-OFF posture), and **screenshots**
  (E2E/Selenium output, debug captures, demo-video frames — never attached to
  logs/traces/SaaS observability; demo captures synthetic-only). Traces stay
  PHI-minimized (D16 stays OFF).
- **Revision (2026-07-13):** document images **persist in OpenEMR only**; the required
  bbox overlay (PRD req 5) means the agent renders **ephemeral, session-bound page
  images** on demand — bounded in-memory TTL cache, never persisted to disk, never
  logged or traced, served only after a session-pin + patient-match check (binding
  §2a/§4). The original "images live in OpenEMR only" wording and the overlay
  requirement stop contradicting each other; no new egress.
- CI PHI-detection check covers logs and eval artifacts (canary-token mechanics,
  binding §7).
- Egress inventory: Anthropic (LLM+VLM, BAA), Langfuse Cloud (D5 posture), Cohere
  (PHI-free, mechanically enforced — W2-D4 rev), OpenEMR (system of record).
- Uploaded documents are the new injection surface: content is data, never
  instructions. Schema-bound output + append-only writes bound the blast radius;
  the raw OCR/text layer never enters any LLM prompt (binding §4). Injection cases
  required in the 50.

## W2-D8. CI tiers: live Anthropic in the graded eval gate; stubs everywhere else — **locked** (2026-07-13)
- Reading the PRD precisely: "integration tests... must pass in CI without live API
  access" scopes the stub requirement to INTEGRATION TESTS. Nothing forbids live
  calls in the eval gate — and the hard gate (grader-injected regression must fail
  CI) plus the required judge configuration both argue for them.
- **Tier 1 — offline, every PR, and the local Git Hook:** unit tests, integration
  tests on fixture documents with stubbed LLM/VLM/reranker, deterministic eval
  subset (schema_valid structure, citation completeness, PHI checks, deterministic
  refusal paths). Satisfies the PRD's no-live-API clause verbatim. No secrets on
  contributor machines.
- **Tier 2 — the graded gate, PR-blocking in GH Actions:** full 50-case run with
  LIVE Anthropic (real agent turns + the pinned LLM judge for factually_consistent).
  This is what the graders' regression injection hits; it exercises real prompt and
  orchestration behavior, closing the stubbed-gate blind spot.
- Guardrails: judge = pinned model + version, temperature 0, boolean questions
  quoting evidence spans; agent calls temperature-pinned; infra failure ≠ case
  failure (bounded retries, then the job errors as inconclusive — reruns required,
  never silent green, never auto-pass); Cohere NEVER live in CI (rate limits —
  stubbed; rubric booleans independent of rerank ordering); ANTHROPIC_API_KEY via
  GH Actions repo secrets (sanctioned store, same class as RAILWAY_TOKEN).
- Cost owned: ~50 live turns/run, W1-measured ~$0.08/request upper bound ≈ $4/run
  before prompt-cache savings; acceptable for a graded gate, monitored in traces.
- Resolves: the judge contradiction (a real judge needs a real call) and the
  stubbed-gate blind spot (prompt/behavior regressions now catchable). The two-tier
  design stands either way; this decision fixes WHERE live calls are allowed.

## Open
- W2-O1. ~~Vector index: in-process vs external~~ **Resolved 2026-07-13
  (/arch-finalize):** in-process, built at Docker image build from the committed
  corpus + manifest (rollback carries its matching index). Carried with a working
  memory budget (bge-small ONNX + runtime + index ≈ 200–300MB; +~400MB if
  RERANKER=local; Tesseract ~100MB peak/page), a measurement point (agent-service RSS
  in the MVP baseline run vs the Railway plan limit), and a fallback ladder (quantized
  ONNX → raise service memory → externalize the index, last resort). Binding §6.
- W2-O2. SLO numbers set from measured baselines, not invented. Working targets:
  ingestion p95 ≤ 30s, retrieval p95 ≤ 2s. Closes at MVP baselines. → W2-R4.
- W2-O3. "Pending review" UI treatment for machine-authored records lands with the
  core flow; the provenance flag itself is locked (W2-D1).
- O-new (renamed at finalize): exact vitals-API field mapping for **intake-form vitals
  fields** (never lab values, W2-F3) — resolve during /tasks-gen, before the
  writeback task.
