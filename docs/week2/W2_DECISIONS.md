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
- **Note (2026-07-13, pre-authorized via /tasks-gen Needs-architecture item):** metric
  value on a US-units vitals instance → skip the vitals leg with reason `unit_mismatch`
  (`writeback.skipped(unit_mismatch)`, added to the binding doc §6a event inventory),
  never convert — a converted number is a derived value not on the page (grounding rule).
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
- **Addendum (2026-07-13, /arch-finalize — owner-confirmed; boot-reconciliation clause
  superseded by the Post-review remediation, see ARCHITECTURE §3):** the write mechanism
  is fully designed, not just named. (a) Ingestion jobs are **durable Postgres rows** with
  lease-based boot reconciliation — startup reclaims only expired leases and reconciles any
  `unknown` remote intent, resuming the same logical job; a job that can be neither
  reclaimed nor reconciled terminates `failed(worker_restart)` (the one live trigger for
  that reason). Never in-process-only state. (b) **Write principal:** jobs execute
  under the uploading clinician's delegated token via the persisted session store (the
  W1 token-persistence debt fix, pulled into MVP because this depends on it); refresh
  grant if a write outlives the access token; never client_credentials (W1 D9/F-S.5
  carried). (c) Idempotency is **enforced, not asserted**: atomic insert-or-return on
  UNIQUE(patient_id, content_hash) + a write ledger keyed (content_hash, field_id) so
  partial-failure retry re-executes only incomplete legs. (d) Every create is **verified
  by re-read** before the job reports complete. (e) The vitals leg fires only for
  intake-form vitals fields AND only with an explicit encounter_id — labs never route
  to vitals (W2-F3); the agent never creates encounters. Binding doc §3.
- **Post-review remediation (2026-07-13)** (supersedes the named D1 implementation
  details without reopening the standard-REST transport):
  - There is no supported persisted-scope edit path, so the installed client's registered
    manifest cannot be extended through supported administration and the replacement client
    remains required. The precision correction is different: registered scope is not an
    effective same-resource authorization ceiling because of W2-F12. W2-OA3 and W2_AUDIT.md
    own the exact manifest and registration payload. The former disable-only cutover is
    **superseded**:
    disable the old client **and retire its access and refresh tokens** (revoke them,
    or wait out both token classes with recorded evidence) before enabling writes.
  - The earlier persisted-interactive-session credential lifecycle is **superseded**
    for background work. A separately encrypted delegated-job credential, bound to
    the uploading clinician and patient, owns refresh and can outlive interactive idle
    expiry; interactive authorization still gates job creation.
  - The earlier `content_hash`/deterministic-ID assertion, ledger key
    `(content_hash, field_id)`, and restart-to-failed description are **superseded by
    W2-D10's exactly-once contract**, permanent patient-scoped dedup/lineage ledger,
    and claimed/leased durable queue. A local transaction alone is never described as
    atomic with a remote OpenEMR create.
  - The former CSRF explanation for the standard document-download 500 is
    **superseded**: the controller passes raw document bytes as a filename. FHIR
    DocumentReference→Binary remains the verified read-back.

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
  no_phi_in_logs. ~~Any category uses only the generic >5% regression/below-threshold
  rule.~~ **Superseded by the post-review arithmetic below.**
- Delivery: PR-blocking Git Hook plus the existing GH Actions gate. CI PHI-detection
  check enforces no_phi_in_logs.
- Judges: deterministic first; unavoidable LLM judgments pinned to boolean questions
  quoting the evidence span.
- Designed for the graded regression injection: every category maps to a named
  one-line break it catches (W2_DEFENSE_PREP §8).
- **Post-review remediation (2026-07-13):** `schema_valid`, `citation_present`,
  `safe_refusal`, and `no_phi_in_logs` are deterministic invariants with a **100%**
  required score; one applicable-case failure makes the gate red immediately.
  `factually_consistent` retains its approved **≥90%** threshold and >5
  percentage-point regression rule. Its injected-regression drill must flip enough
  applicable cases to cross that threshold; it may not claim that one failed factual
  case necessarily fails the gate. Threshold and denominator arithmetic are emitted
  with every result.

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
- ~~CI PHI-detection check covers logs and eval artifacts (canary-token mechanics,
  binding §7).~~ **Post-review remediation (2026-07-13):** canonical
  input fixtures contain deliberate synthetic canaries and are excluded from the
  leak scan. The scan covers only generated outputs, logs, traces, reports,
  recordings, screenshots, and eval results. A generated known-leak self-test must
  trip the same scanner, proving the exclusion has not made the check vacuous.
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
- ~~Cost owned: ~50 live turns/run, W1-measured ~$0.08/request upper bound ≈ $4/run
  before prompt-cache savings.~~ **Post-review remediation (2026-07-13):** the
  budget and quota model is
  `50 × (VLM extraction + answer turn + pinned-judge turn)`, including multi-page VLM
  work and retries. A timing/cost/quota spike measures the real bound before the live
  gate is enabled; traces report the measured cost rather than the retired $4 claim.
- **Post-review submission-host enforcement (2026-07-13; implementation of D8, not a new
  decision):** GitLab runs the identical Tier-1 gate and a fail-closed `graded-gate` that
  accepts the protected GitHub `eval-tier2-live` result only for the identical mirrored
  commit SHA. Absent, stale, mismatched, or red status fails GitLab. Fork code receives no
  secrets and is never executed through `pull_request_target`; a maintainer-authorized
  protected run is bound to the reviewed SHA.
- Resolves: the judge contradiction (a real judge needs a real call) and the
  stubbed-gate blind spot (prompt/behavior regressions now catchable). The two-tier
  design stands either way; this decision fixes WHERE live calls are allowed.

## W2-D9. Adversarial audit-review response: transport stands, agent-side write controls made mandatory — **locked** (2026-07-13)
- Trigger: a separate read-only adversarial re-audit of the whole write/upload surface
  (`W2_AUDIT_REVIEW_RAW.md`; distilled into W2_AUDIT.md as findings W2-F12..F23 + imprecise
  corrections to W2-F1/F4/F9/F11). 7 of W2-F1..F11 CONFIRMED, 4 imprecise, 12 new findings,
  0 false-positives. Owner made two load-bearing calls on the review:
- **Call 1 — the W2-D1 transport survives (unchanged).** The review's bottom line confirms
  no client-supplied FHIR create/update/upload exists for DocumentReference, Observation,
  or Binary; the standard documents (`POST /api/patient/:pid/document`) and vitals
  (`POST /api/patient/:pid/encounter/:eid/vital`) APIs remain the sanctioned append-only
  transport. The transport decision is **not reopened**. (`$docref`/`$export`/UUID-backfill
  GETs can mutate DB state — W2-F8/F21 — but none is a target-resource client CRUD.)
- **Call 2 — the "no finding blocks the architecture" gate verdict is retired.** The review
  proves the OpenEMR write surface does **not** enforce, on create: launch-patient /
  encounter ownership (W2-F13), a registered-scope ceiling (W2-F12), category ACLs (W2-F14),
  vital physiological ranges (W2-F15), clinical attribution (W2-F16), or idempotency
  (W2-F18); and client-disable does not revoke live tokens (W2-F17). These are now
  **MANDATORY agent-side controls that must land before writes are enabled** — promoted
  from defense-in-depth to blocking. Threaded into the plan at W2-OA3 (exact-scope
  provisioning + granted-scope assertion + token-revoking cutover + canonical category-path
  preflight),
  W2-M8 (patient-pin + encounter-ownership preflight, upload validation, idempotency
  ledger), and W2-M11 (bounded vital ranges, no caller-supplied author, ledger,
  Binary-readback DEBUG-logging check). Transport remains sound; the write path is simply
  gated behind these controls.
- Precision corrections adopted (no decision change): missing `api:oemr` → **403 not 401**
  (W2-F4); the document-download **500 is not a CSRF defect** but raw-bytes-as-filename —
  FHIR DocumentReference→Binary stays the read-back (W2-F9); scope discovery advertises
  legacy `.read` **plus** v2 `.rs` and the validator ignores constraints (W2-F11/F12);
  `api_log` copies the JSON **response** into both request/response columns, so inbound PDF/
  vital bodies are NOT logged (W2-F20 — the earlier leak hypothesis is FALSE, but FHIR JSON
  readback still hits W1 F-S.4).
- Non-D1 note carried for hygiene: **W2-F23** (soap_note PUT IDOR) — the agent touches no
  soap_note route; recorded so no build agent adds one.
- Rejected: treating the new HIGH findings as defense-in-depth (the surface enforces none
  of these server-side — the agent is the only enforcement point); reopening the transport
  (the findings are about missing *server-side* controls, not a wrong transport).

- **Post-review remediation (2026-07-13):**
  - "Fixed category" means **canonical path control**, not direct category-ID input:
    source and artifact paths are separately fixed; before any write the agent resolves
    each path and verifies the expected category ID and ACL, then sends the path accepted
    by the standard API. Unknown, mismatched, or unauthorized resolution fails closed.
  - Missing `api:oemr` is **403**, not 401. The old client's access **and refresh**
    tokens must be retired as described in the D1 remediation; disable-only is not a
    completed cutover.
  - The raw-bytes-as-filename download cause is the sole recorded explanation for the
    standard download 500; the retired CSRF explanation must not appear as current fact.

## W2-D10. Full contained write path on one exactly-once contract — **locked** — **Post-review remediation (2026-07-13)**
- The complete Final submission writes all three required legs: **source document**, a
  source-linked **grounded extraction artifact**, and, for grounded intake-vitals
  fields with an explicit owned encounter, structured vitals to `form_vitals`. Labs
  never route to vitals. Build order may land source/artifact before vitals, but no
  write leg is cut, stubbed, or declared optional; all three are complete with D9
  containment by Final on 2026-07-19.
- `IntakeFormExtraction` owns typed grounded-vitals fields for `bps`, `bpd`, `weight`,
  `height`, `temperature`, `pulse`, `respiration`, and `oxygen_saturation`, plus a
  grounded `measurement_date`. Every candidate vital is a `GroundedField` with its
  on-page value, unit where applicable, citation, and bbox. `note` is generated
  provenance metadata, never an extracted field.
  Missing or unsupported grounding never writes. A deterministic, unit-specific
  physiological-range table enforces W2-F15; `unit_mismatch` and `range_violation`
  are typed skip reasons and the source artifact records the skip.
- W2-F16 is mandatory: caller-supplied `user`/`group` attribution is stripped. The
  delegated-clinician token is the only attribution principal; that principal and the
  source-field lineage are recorded in the extraction artifact, permanent write
  lineage, and trace. The agent never claims a clinician performer from request-body
  fields.
- Every source, artifact, and vital create uses the same durable intent protocol with
  states `{pending, unknown, complete}`. A remotely discoverable correlation marker
  and content/payload fingerprint support reconciliation. Before any re-POST the agent
  lists/re-reads the remote surface by patient and content/payload hash. A timeout after
  a possible commit moves the intent to `unknown`; it stops for reconciliation and is
  **never blind-retried**. Only a proven-absent remote object may be posted again.
- Permanent dedup/lineage records are keyed by
  `(patient_id, document_id_or_content_hash, leg, version, field_id)` and are never
  purged. Purgeable attempt rows are separate and retain for 30 days. Atomic
  failed-job requeue changes queue state without creating a second logical job or
  bypassing the permanent ledger.
- Durable jobs are a typed claimed/leased queue, not merely rows: transactional claim,
  lease owner/expiry, heartbeat, bounded exponential retry backoff, stale-lease
  recovery, explicit worker topology, and graceful shutdown are part of the contract.
  The separate encrypted delegated-job credential from D1 is patient/principal-bound
  and refreshes independently of interactive idle expiry.
- D9 remains the enablement gate for every leg: exact granted scopes, pinned-patient and
  encounter-ownership checks, canonical path/ID/ACL preflight, upload validation,
  bounded vitals, trusted attribution, DEBUG-safe Binary read-back, verified re-read,
  and post-cutover token retirement. If any control is unavailable or indeterminate,
  that write refuses safely rather than weakening the contract.

## Open
- W2-O1. ~~Vector index: in-process vs external~~ **Resolved 2026-07-13
  (/arch-finalize):** in-process, built at Docker image build from the committed
  corpus + manifest (rollback carries its matching index). Carried with a working
  memory budget (bge-small ONNX + runtime + index ≈ 200–300MB; +~400MB if
  RERANKER=local; Tesseract ~100MB peak/page), a measurement point (**superseded:**
  MVP-only baseline; **post-review answer:** Wave-0 concurrently loads bge-small, the
  local reranker, and one OCR page and enforces an RSS ceiling against the Railway
  plan limit), and a fallback ladder (quantized
  ONNX → raise service memory → externalize the index, last resort). Binding §6.
- W2-O2. ~~SLO numbers set from measured MVP baselines; closes at MVP.~~ **Resolved —
  Post-review remediation (2026-07-13):** measure the full baseline matrix and
  formally lock numeric SLOs at the **Early checkpoint, Thursday 2026-07-16**. Final
  validates and reports against those already-locked SLOs; it is not a second closure
  point. Working pre-measurement targets remain ingestion p95 ≤ 30s and retrieval
  p95 ≤ 2s. → W2-R4.
- W2-O3. "Pending review" UI treatment for machine-authored records lands with the
  core flow; the provenance flag itself is locked (W2-D1).
- O-new. ~~Exact vitals-API field mapping for intake-form vitals fields — resolve
  during /tasks-gen.~~ **Resolved 2026-07-13 by W2-D10 and the frozen canonical
  schema:** only grounded intake-vitals fields map to the typed `form_vitals` write;
  labs never map to vitals (W2-F3).
