# W2_DECISIONS.md — Week 2 ADR log

> New file; W1 DECISIONS.md (D1–D16) is frozen at docs/week1/. Tags: **locked** /
> proposed / open. Owner decisions recorded 2026-07-13 (see W2_PRESEARCH.md).

## W2-D1. Writes: append-only creates with lineage — **locked**
- W1 was read-only (D9/D12). W2 requires storing documents and derived facts.
- The agent gets exactly two write capabilities: create DocumentReference, create
  derived Observations. No update. No delete. Nothing clinician-authored is touchable.
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
- Rejected: trusting VLM output (the PRD's named pitfall); hosted OCR (new egress for
  nothing); ColQwen2 (stretch).

## W2-D4. RAG: hybrid + Cohere rerank, PHI-free by contract — **locked**
- Corpus: public-domain US guidance matched to the panel (USPSTF, ADA, CDC, JNC-8
  class). Provenance documented. Rebuildable from repo.
- Retrieve BM25 + dense. Rerank with Cohere (PRD-named).
- Hard contract: queries are condition/test terms only, never identifiers. Corpus has
  no PHI. Cohere never sees PHI. If the contract breaks, local cross-encoder fallback.
- Snippets carry {source_id, section, chunk_id, quote} and are the only guideline
  content the model sees.
- Rejected: licensed content (indefensible); raw patient text as queries.

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
