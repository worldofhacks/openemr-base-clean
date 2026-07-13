# W2_DECISIONS.md — Week 2 ADR log

> New file per the week-scoped convention; W1 DECISIONS.md (D1–D16) is frozen at
> docs/week1/. Cross-references to W1 use D#. Tags: **locked** / proposed / open.
> Owner decisions recorded 2026-07-13 pre-defense (see W2_PRESEARCH.md conversation).

## W2-D1. Write path: append-only creates with lineage — **locked**
**Context:** W1 was read-only by construction (D9/D12); the W2 PRD requires storing
uploaded documents in OpenEMR and persisting derived facts as FHIR resources.
**Decision:** the agent gains exactly two write capabilities: create DocumentReference
(source file) and create derived Observation/records. Create-only: no update, no delete,
no overwrite of anything clinician-authored. Every derived record links to its source
document (lineage). Writes are idempotent: content-hash on the file, deterministic IDs
on derived facts, so re-upload creates nothing (PRD round-trip integrity).
**Safety story (replaces the W1 pillar honestly):** scopes widen from read-only to
read + narrow create. Worst case under prompt injection is a quarantined, visibly
machine-authored, source-linked record a human can review and void — never a silent
edit of clinician data. Data authority stays OpenEMR; nothing derived is authoritative
until written and re-read.
**Rejected:** pretending the agent is still read-only (false); a parallel store outside
OpenEMR (violates PRD round-trip requirement; splits data authority).

## W2-D2. Orchestration: LangGraph supervisor + 2 workers — **locked**
**Context:** W1 D6 chose a direct SDK loop, no framework, and documented its own
invalidation: "wk2–3 multi-agent requirements." The W2 PRD requires supervisor +
intake-extractor + evidence-retriever on an inspectable orchestration framework and
names LangGraph.
**Decision:** LangGraph owns routing. The W1 direct loop survives inside workers. Every
handoff is a record: {correlation_id, turn, supervisor_decision, reason_code, worker,
input_ref, output_ref, handoff_ts}. Supervisor span parents worker spans in Langfuse;
a full multi-agent trace reconstructs from the correlation ID alone (PRD).
**Why not hand-rolled:** permitted ("another inspectable framework") but spends defense
capital proving inspectability the PRD grants LangGraph for free. D6's seam (tool
registry + orchestrator interface) was built for exactly this migration.
**Rejected:** CrewAI/autonomous-crew styles (opaque routing); critic agent in core
(PRD: extension, not core).

## W2-D3. Vision extraction: VLM proposes, OCR grounds, disagreement renders unsupported — **locked**
**Context:** PRD hard problems: "vision extraction without invention" + required PDF
bounding-box overlay. The VLM (Claude, same provider as D4 — zero new PHI processors)
returns no coordinates and can hallucinate fields or overstate confidence.
**Decision:** local Tesseract OCR runs per page (word-level boxes, $0, no PHI egress).
The VLM extracts strict-schema fields; the verifier grounds every field value in the
OCR text layer. Grounded → field earns citation + OCR coordinates as its bbox.
Not grounded (or OCR/VLM disagree) → rendered unsupported, "verify against source
document," overlay points at the region. Extraction confidence = grounding agreement
(binary per field), never VLM self-report.
**This is W1 verify-then-flush extended to pixels** — one mechanism answers invention,
confidence inflation, and the overlay requirement.
**Rejected:** trusting VLM output (PRD pitfall verbatim); hosted OCR (new PHI egress
for no capability we need); ColQwen2/multi-vector (PRD: stretch).

## W2-D4. Hybrid RAG: public-domain corpus, BM25 + dense + Cohere rerank under a PHI-free-query contract — **locked**
**Decision:** corpus = public-domain US clinical guidance matched to the demo panel
(USPSTF recommendations, ADA Standards of Care, CDC schedules, JNC-8-class HTN
guidance); provenance documented per document; corpus + index rebuildable from repo.
Retrieval = BM25 + dense embeddings; rerank = Cohere (PRD-named) under a hard contract:
queries are built from condition/test terms only, never identifiers — the corpus holds
no PHI, so the reranker never sees PHI. If the contract cannot hold in practice, fall
back to a local cross-encoder ("or equivalent").
**Evidence snippets carry {source_id, section, chunk_id, quote}** and are the ONLY
guideline content the answer model sees.
**Rejected:** scraping licensed content (licensing indefensible); embedding raw patient
text as queries (breaks the PHI contract).

## W2-D5. Eval gate v2: 50-case golden set, boolean rubrics, PR-blocking hook + CI — **locked**
**Decision:** 50 synthetic/demo cases in-repo (reproducible from repo alone — the PRD
backup requirement), covering extraction, retrieval, citations, refusals, missing-data.
Boolean rubrics only; minimum categories per PRD: schema_valid, citation_present,
factually_consistent, safe_refusal, no_phi_in_logs. Gate fails if any category regresses
>5% or drops below threshold. Delivered as a PR-blocking Git Hook AND the existing GH
Actions gate. Judges: deterministic checks first; where an LLM judge is unavoidable,
pinned boolean questions quoting the exact evidence span. A CI PHI-detection check
enforces no_phi_in_logs.
**Design target:** the graded regression injection (hard gate) — every category maps to
a named, realistic one-line break it catches (see W2_DEFENSE_PREP §8 Q8).

## W2-D6. Citation contract v2: prescribed shape + source-type separation — **locked**
**Decision:** every clinical claim carries {source_type, source_id, page_or_section,
field_or_chunk_id, quote_or_value}. source_type ∈ {patient_record, uploaded_document,
guideline}; the UI renders patient facts and guideline evidence as visually distinct
classes (PRD: answers must separate them). Document-sourced claims render the bbox
overlay (W2-D3). Extends W1 evidence IDs; W1's citation discipline is unchanged.

## W2-D7. PHI surfaces v2 — **locked**
**Decision:** new sensitive artifacts (document images, extracted fields, retrieval
queries, eval fixtures) inventoried. Images live in OpenEMR only; traces stay
PHI-minimized (W1 D16 content switch remains default OFF); retrieval queries PHI-free by
construction (W2-D4); CI PHI-detection check over logs + eval artifacts. Post-W2 egress
inventory: Anthropic (LLM+VLM, assumed BAA), Langfuse Cloud (D5 posture), Cohere
(PHI-free queries only), OpenEMR (system of record). Uploaded documents are the NEW
prompt-injection surface: document content is data, never instructions; schema-bound
output + append-only writes bound the blast radius; injection cases required in the 50.

## Open
- W2-O1. Vector index location: in-process (small corpus) vs external store — default
  in-process, revisit only if corpus growth demands (→ W2-R3).
- W2-O2. SLO numeric thresholds (ingestion p95, retrieval p95): set from first measured
  baselines, not invented (→ W2-R4). Working targets: 30s ingestion, 2s retrieval.
- W2-O3. "Pending review" UI affordance for machine-authored records: provenance flag is
  locked (W2-D1); the UI treatment lands with the core flow.
