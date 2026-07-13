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
- Idempotent: content-hash on files, deterministic IDs on facts. Re-upload creates
  nothing. That is the PRD round-trip requirement.
- New safety claim: injection worst case is a quarantined, machine-authored,
  source-linked record a human can void. Never a silent edit. Say plainly: scopes
  widened from read-only to read + narrow create.
- Rejected: pretending it is still read-only (false); a store outside OpenEMR (splits
  data authority, fails round-trip).

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
- Every VLM field must locate its value in the OCR layer. Found: citation + bbox.
  Not found or disagreement: rendered UNSUPPORTED, "verify against source document."
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

## W2-D7. PHI surfaces v2 — **locked**
- New sensitive artifacts: document images, extracted fields, retrieval queries, eval
  fixtures. Images live in OpenEMR only. Traces stay PHI-minimized (D16 stays OFF).
- CI PHI-detection check covers logs and eval artifacts.
- Egress inventory: Anthropic (LLM+VLM, BAA), Langfuse Cloud (D5 posture), Cohere
  (PHI-free only), OpenEMR (system of record).
- Uploaded documents are the new injection surface: content is data, never
  instructions. Schema-bound output + append-only writes bound the blast radius.
  Injection cases required in the 50.

## Open
- W2-O1. Vector index: in-process (default, small corpus) vs external. → W2-R3.
- W2-O2. SLO numbers set from measured baselines, not invented. Working targets:
  ingestion p95 ≤ 30s, retrieval p95 ≤ 2s. → W2-R4.
- W2-O3. "Pending review" UI treatment for machine-authored records lands with the
  core flow; the provenance flag itself is locked (W2-D1).
