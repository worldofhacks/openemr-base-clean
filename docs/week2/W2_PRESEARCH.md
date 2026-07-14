# W2_PRESEARCH.md — Pre-Search Checklist (Week 2: Multimodal Evidence Agent)

> Completed 2026-07-13, before any W2 code, per the Gauntlet Pre-Search Checklist
> (3 phases, 16 sections). New artifact per the week-scoped convention; Week 1
> PRESEARCH.md is frozen history. Open items are tagged OPEN and resolved in the
> conversation record at the bottom. Research follow-ups are tagged → W2-R#.
>
> **Point-in-time note (superseded where in conflict by the binding docs, 2026-07-13):**
> this presearch predates the write-path verification + remediation. Current state: the
> eval gate is four 100%-required deterministic categories + a factual threshold (not
> ">5% only"); there is no client-supplied FHIR DocumentReference/Observation write (writes
> go through the standard documents/vitals APIs under the W2-D10 exactly-once contract); the
> PHI-scan covers generated artifacts only. Binding: W2_ARCHITECTURE.md + W2_DECISIONS.md.

## Phase 1 — Define Your Constraints

### 1. Domain Selection
- Domain: healthcare (unchanged). Same user as W1: PCP with a 20-patient day (D1).
- W2 use cases: (UC-W2-1) ingest + extract a scanned lab PDF; (UC-W2-2) ingest +
  extract a patient intake form; (UC-W2-3) answer "what changed / what should I pay
  attention to / what evidence supports it" grounding patient facts + guideline
  evidence separately; (UC-W2-4) follow-ups in-session without losing grounding.
- Verification requirements: every clinical claim carries the prescribed citation shape
  {source_type, source_id, page_or_section, field_or_chunk_id, quote_or_value}; PDF
  claims additionally render a bounding-box overlay; extracted fields that cannot be
  grounded are rendered as unsupported, never silently kept; patient-record facts vs
  guideline evidence visually and structurally separated.
- Data sources: OpenEMR FHIR (W1 surface), uploaded documents (lab PDF, intake form),
  and a self-sourced clinical-guideline corpus (public-domain candidates: USPSTF, ADA
  Standards of Care, CDC schedules, JNC-8-class HTN guidance) → W2-R2 (corpus vetting).

### 2. Scale & Performance
- Query volume: demo scale (single physician, tens of requests/day); graded burst = the
  50-case eval run + live demo.
- Latency: W1 brief SLO thinking carries over (perceived latency via streaming). New
  SLOs required by the PRD: document ingestion p95 < X and evidence retrieval p95 < X,
  with X set from measured baselines, not invented → W2-R4 (baseline before promise).
  Working targets: ingestion+extraction p95 ≤ 30s per document; retrieval p95 ≤ 2s.
- Concurrency: 1-5 concurrent users; W1 50-VU /ready saturation is known debt — W2 adds
  dependencies to /ready (doc storage, vector index, reranker), so readiness must go
  degraded-not-binary.
- Cost: VLM page-image calls are the new cost driver; cap pages per document; measure
  from traces (W1 cost discipline). Reranker adds per-query cost if hosted.

### 3. Reliability Requirements
- Cost of wrong answer: clinical mis-orientation; worst case = invented lab value or
  fabricated guideline recommendation. Same trust thesis as W1: confident-wrong kills.
- Non-negotiable verification: schema validation on all extraction (raw VLM output never
  bypasses schema); OCR-grounding for extracted values (no bbox, no unsupported render);
  citation contract on every claim; refusals for what the data cannot support.
- Human-in-the-loop: physician remains the decision-maker; extracted facts write to
  OpenEMR as machine-authored, source-linked records a human can review/void; no
  autonomous clinical action. OPEN: do derived FHIR writes need an explicit
  "pending review" status flag in the UI story?
- Audit/compliance: W1 posture extends; new PHI surfaces (document images, extracted
  fields, retrieval queries) inventoried in W2-D7; no_phi_in_logs is now a GRADED eval
  category with a CI PHI-detection check.

### 4. Team & Skill Constraints
- Solo owner + two build agents (Codex, Claude Code) with tdd-swarm for
  verification-touching code (W1 discipline, carries over).
- Framework familiarity: W1 was deliberately framework-free (D6); LangGraph is new
  surface → W2-R1 (LangGraph supervisor pattern + Langfuse integration).
- Eval tooling: strong (W1 gate exists); the 50-case boolean-rubric format is new scale.

## Phase 2 — Architecture Discovery

### 5. Agent Framework Selection
- OPEN (lean LangGraph): PRD names LangGraph / OpenAI Agents SDK / "another inspectable
  orchestration framework." W1 D6's invalidation clause fired (multi-agent requirement).
  Recommendation: LangGraph for supervisor + 2 workers; W1 direct loop survives inside
  workers. Alternative: hand-rolled explicit state machine (spends defense capital
  proving inspectability). → W2-R1.
- Single vs multi: prescribed — one supervisor, intake-extractor, evidence-retriever.
  Critic agent is stretch, not core.
- State: session state stays in the W1 Postgres store; graph state must be
  correlation-ID-threaded; every handoff logged {correlation_id, turn,
  supervisor_decision, reason_code, worker, input_ref, output_ref, handoff_ts}.
- Tool integration: W1 FHIR tools unchanged; new tools: attach_and_extract(patient_id,
  file_path, doc_type), retrieve_evidence(query, k).

### 6. LLM Selection
- Claude Sonnet stays primary; it is also the VLM (page images + extraction prompt) —
  zero new PHI processors for extraction (assumed BAA unchanged, D4/W2-D3).
- Haiku for cheap utility (unchanged). Function calling: unchanged requirement.
- Context: page images + extraction schema fit comfortably; cap pages/doc.
- OPEN (cost check): VLM cost per scanned page vs budget → measure in W2-R4.

### 7. Tool Design
- attach_and_extract: stores source in OpenEMR (DocumentReference), OCRs locally,
  VLM-extracts to strict schema, grounds fields to OCR coordinates, persists derived
  facts append-only with lineage (W2-D1). Idempotent via content hash.
- retrieve_evidence: hybrid BM25 + dense over the guideline corpus, rerank, return
  snippets with {source_id, section, chunk_id, quote}.
- External APIs: Anthropic (existing), reranker (OPEN: Cohere hosted vs local
  cross-encoder — PHI posture decides), embeddings (OPEN: hosted vs local
  sentence-transformers; queries are PHI-free by contract either way) → W2-R3.
- Mock vs real: fixture documents (synthetic scanned PDFs + form images) committed to
  repo; integration tests run on stubs, no live APIs in CI (PRD requirement).
- Error handling per tool: ingestion failure, schema violation, empty retrieval, and
  routing error each get a named failure mode + recovery action in W2_ARCHITECTURE.md.

### 8. Observability Strategy
- Langfuse Cloud stays (W1 D5/D16 posture; content switch default OFF). Supervisor span
  parents worker spans; extraction and retrieval sub-calls are children within workers.
- New metrics: ingestion latency, extraction confidence (= grounding agreement, not VLM
  self-report), field-level extraction pass rate, retrieval hit rate, rerank scores,
  routing decisions, per-worker latency, eval pass rate per category.
- New alerts: extraction failure rate, retrieval latency, eval regression >5% in any
  category.
- Cost tracking: per-encounter cost including VLM page calls + reranker.

### 9. Eval Approach
- 50-case golden set, synthetic/demo only, committed to repo (reproducible from repo
  alone — the PRD's backup requirement).
- Boolean rubrics only: schema_valid, citation_present, factually_consistent,
  safe_refusal, no_phi_in_logs (minimum categories, per PRD).
- Ground truth: fixture documents with known expected extractions; deterministic checks
  first; where an LLM judge is unavoidable, pinned boolean questions quoting evidence.
- CI: PR-blocking Git Hook + existing GH Actions gate; fail on any category regressing
  >5% or dropping below threshold. Graders WILL inject a regression (hard gate).

### 10. Verification Design
- Claims needing verification: extracted lab fields (name, value, unit, range, date,
  abnormal flag), intake fields (demographics, chief concern, meds, allergies, family
  history), guideline attributions (quote must exist in the cited chunk).
- Fact-checking sources: the OCR text layer (for document claims), the EvidencePacket
  (for chart claims), the indexed chunk text (for guideline claims).
- Confidence: grounding agreement (VLM value located in OCR layer) — binary per field,
  not a self-reported score. Disagreement → unsupported-field path.
- Escalation: unsupported field renders flagged; unverifiable claim blocked; empty
  retrieval → answer marked "no guideline evidence found" (never invented evidence).

## Phase 3 — Post-Stack Refinement

### 11. Failure Mode Analysis
- Tool failures: ingestion fails → document stored, extraction marked failed, physician
  told explicitly; retrieval empty → grounded answer without guideline section, flagged;
  VLM down → W1 D13-style deterministic degradation (no synthesis, facts only);
  reranker down → fall back to un-reranked hybrid scores (degraded, logged).
- Ambiguous queries: W1 refusal discipline carries over verbatim.
- Rate limits: timeouts + retries on all outbound LLM/retrieval calls (PRD requirement);
  bounded retries within the turn budget.
- Degradation: /ready reports degraded (not down) when vector index or reranker is
  unavailable; serving continues where possible.

### 12. Security Considerations
- Prompt injection: uploaded documents are the NEW injection surface (a scanned form can
  contain adversarial text). Same containment as W1: document content is data, never
  instructions; extraction output is schema-bound; append-only writes bound the blast
  radius; injection eval cases required in the 50.
- Data leakage: document images stored only in OpenEMR; traces PHI-minimized; retrieval
  queries PHI-free by construction; CI PHI-detection check over logs + eval artifacts.
- Keys: Railway env vars, gitignored .env, never in prompts/logs (unchanged, absolute).
- Audit: every write carries correlation_id + source lineage; supervisor decisions logged.

### 13. Testing Strategy
- Unit: schema validators, grounding matcher, chunker, citation builder, idempotency
  hasher.
- Integration: full ingestion→answer path on fixture docs with stubbed LLM/VLM (runs in
  CI without live APIs, per PRD).
- Adversarial: injection-bearing documents, degraded scans, wrong-doc-type uploads,
  duplicate uploads.
- Regression: the 50-case golden set + W1 suite stays green (shared-path protection).
- Every test names the failure mode it guards (W1 convention, PRD requirement).

### 14. Open Source Planning
- Same fork, same license posture as W1 (upstream OpenEMR licensing untouched).
- README must separate W1 baseline behavior from W2 multimodal behavior (PRD).
- Corpus: public-domain sources only, provenance documented per document → W2-R2.

### 15. Deployment & Operations
- Railway project unchanged (OpenEMR + MySQL + agent); vector index lives in the agent
  service (small corpus; rebuildable from repo) unless research says otherwise → W2-R3.
- CI/CD: existing eval-gate deploy flow + new Git Hook; dependency audit + security scan
  per PR (PRD).
- Rollback: unchanged (Railway one-click + git revert).
- Backup/recovery: golden set + corpus reproducible from repo (RPO 0 via git); extracted
  documents + derived records live in OpenEMR's DB (its backup posture documented);
  manual recovery = re-run attach_and_extract on stored source docs (idempotent).

### 16. Iteration Planning
- Eval-driven: the 50-case gate is the improvement loop; failures become new cases.
- Feedback: owner-as-physician demo runs; grader feedback at checkpoints.
- Prioritization: MVP table order (ingest → RAG → graph → gate → demo); stretch only
  after core is green. Cuts get dated entries (W1 discipline).
- Maintenance horizon: one week; debt documented in W2 docs, not hidden.

## OPEN items going into the presearch conversation (2026-07-13)

1. Orchestration: adopt LangGraph (recommended) or hand-rolled explicit graph?
2. Reranker: Cohere hosted (PHI-free queries by contract) or local cross-encoder?
3. OCR/bbox layer: local Tesseract (recommended: $0, no egress) or a hosted OCR API?
4. Build posture: production-grade, default mode, same as W1?
5. Scope: strictly core-first (2 doc types, 2 workers, 1 gate) with stretch only after
   Final-core is green?
6. Derived writes: do machine-authored records need a visible "pending review" flag in
   the demo UI story?

## Conversation record (owner decisions, 2026-07-13, pre-defense)

Conducted as the checklist's "save your AI conversation as a reference" step; full
context in the Cowork session of 2026-07-13.

1. **Orchestration → LangGraph** (confirmed by Alex). Supervisor + 2 workers on
   LangGraph; W1 direct loop survives inside workers; D6's invalidation clause cited as
   the reason this is consistency, not flip-flop. Feeds W2-D2.
2. **Reranker → Cohere hosted** (confirmed by Alex), conditional on the PHI-free-query
   contract holding (condition/test terms only; corpus contains no PHI). If the contract
   cannot hold in practice, fall back to a local cross-encoder. Feeds W2-D4.
3. **OCR/bbox → local Tesseract** (default accepted): $0, no PHI egress, word-level
   coordinates for the required overlay. Feeds W2-D3.
4. **Build posture → production-grade, default mode** (confirmed by Alex): test-first,
   tdd-swarm on verification-touching code, dated cuts, honesty over overclaiming.
5. **Scope → core only until green** (confirmed by Alex): 2 doc types, 2 workers, 1
   gate, deployed; stretch items only after Final-core is green.
6. **Derived writes → visible machine-authored provenance** (default accepted): records
   created by the agent are source-linked and presented as machine-extracted pending
   clinician review; no silent merging into clinician-authored data. Feeds W2-D1.

## Research follow-ups spawned (→ W2_RESEARCH.md)

- W2-R1: LangGraph supervisor pattern, handoff logging, Langfuse span integration.
- W2-R2: guideline corpus sourcing + licensing vetting (USPSTF/ADA/CDC/JNC-8 class).
- W2-R3: hybrid retrieval + reranker options and their PHI/egress posture; embedding
  model choice; index storage.
- W2-R4: VLM extraction cost/latency per scanned page; OCR engine accuracy on degraded
  scans; SLO baselining method.
- W2-R5: FHIR DocumentReference/Observation write pattern in OpenEMR (round-trip
  integrity, idempotency, lineage fields).
