# W2_DEFENSE_PREP — Architecture Defense (Week 2: Multimodal Evidence Agent)

> Written 2026-07-13, ~2h before the defense. Presearch + decision slate + grill bank.
> Source: Week_2_AgentForge.pdf. No code exists yet; this is the defense package.

## 1. What Week 2 actually is (one paragraph)

Extend the Week 1 co-pilot so it can SEE: ingest a scanned lab PDF and a patient intake
form, extract strict-schema facts with citations (including a visual PDF bounding-box
overlay), retrieve guideline evidence via hybrid RAG + rerank, route work through one
supervisor + two workers with logged handoffs, and gate every change with a 50-case
boolean-rubric eval CI that graders will actively try to break. Narrower is stronger.

## 2. Hard gates and graded traps

- **CI gate is THE gate.** Graders inject a regression; if CI does not fail, Week 2 fails.
  Categories required: schema_valid, citation_present, factually_consistent, safe_refusal,
  no_phi_in_logs. Fail if any category regresses >5% or drops below threshold. PR-blocking
  Git Hook specifically (plus CI).
- **Bounding-box overlay is core, not stretch** (citation contract, req 5).
- **Citation shape is prescribed:** {source_type, source_id, page_or_section,
  field_or_chunk_id, quote_or_value}. Machine-readable on every clinical claim.
- **Round-trip integrity:** uploads + derived observations must not create duplicate or
  untraceable OpenEMR records (idempotency + lineage).
- **PHI discipline extends:** no raw PHI in logs/traces/eval sets/cost reports; a
  PHI-detection check must run in CI. Document images are sensitive artifacts.
- **W2_ARCHITECTURE.md** is a named deliverable (ingestion flow, worker graph, RAG design,
  eval gate, risks, tradeoffs, testing strategy, failure modes).
- Schedule: Defense now; MVP Tue 11:59 PM; Early Thu 11:59 PM; Final Sun noon.

## 3. Ambiguities to name out loud (presearch)

- The Core Deliverables list on p.5 mixes core and stretch. p.4 says critic agent is
  "extension work, not core," and the MVP table lists five items. Read: first five bullets
  are core; critic, click-to-source UI polish, third doc type, trend chart, contextual
  retrieval are the stretch tier. Say this interpretation in the defense.
- "Cohere Rerank or equivalent": equivalence is ours to defend (PHI posture matters).
- Guideline corpus is not provided; sourcing and licensing are ours to defend.
- SLO thresholds ("p95 < X") are ours to set from Week 1 baselines.

## 4. The two defense vulnerabilities (and the winning stories)

### A. "Last week you were read-only by construction. Now you write."
The Week 1 claim was load-bearing (D9/D12: worst-case injection is wrong words, never
wrong writes). Week 2 requires storing documents and persisting derived facts.
**Winning story: constrained, append-only writes.**
- New decision (W2-D1): the agent gains exactly two write capabilities: create
  DocumentReference (source file) and create Observation/derived records, both linked to
  the source document. Create-only: no update, no delete, no overwrite. Data authority
  stays OpenEMR; nothing derived is authoritative until written and re-read.
- Idempotency: content-hash the file + deterministic derived-fact IDs so re-upload cannot
  duplicate (answers round-trip integrity verbatim).
- The injection story survives restated: worst case is a QUARANTINED wrong record that is
  visibly machine-authored, source-linked, and reversible by a human; never a silent edit
  of clinician-authored data. Scopes widen from read-only to read + narrow create; say it
  plainly, do not pretend it is still read-only.

> **Live-evidence addendum (2026-07-13 — post-defense; W2-F1 independent verification,
> W2_AUDIT.md).** The write story above now has probe evidence, not just design: FHIR
> POSTs for our targets return route-level 404 even with maximal write scopes (W2-F1
> CONFIRMED — this fork literally cannot do the FHIR create this section's draft wording
> assumed; the shipping transport is the documents/vitals API per W2-R5/W2-D1, so read
> "create DocumentReference / create Observation" above as its era's shorthand for
> "create document / create vitals record"). Upload verified live: 200 `true`, id via
> collection GET by content hash (W2-F9); round-trip proven **byte-exact** via the FHIR
> DocumentReference→Binary projection, and vitals proven end-to-end through FHIR
> Observation reads (W2-F10) — the PRD's round-trip requirement is demonstrated, not
> asserted. Provisioning is a verified sequence with a hard constraint: clients cannot
> gain scopes post-registration → **replacement SMART client** at MVP (W2-F4 resolved).
> Defense line for grill Q1/Q9: "we probed it live — here is the 404, here is the
> SHA-256 match."

### B. "You refused frameworks last week (D6). The assignment names LangGraph."
D6 documented its own invalidation: "wk2-3 multi-agent requirements (migration seam: tool
registry + orchestrator interface stay framework-shaped)." That clause fired.
**Winning story: the seam worked as designed.**
- New decision (W2-D2): adopt LangGraph for the supervisor + 2 workers (inspectable, named by the PRD,
  Langfuse-integrated). The Week 1 direct loop becomes the inside of workers where it
  still fits; the graph owns routing.
- Alternative defense (hold the direct loop, build an explicit hand-rolled state machine)
  is permitted by "another inspectable orchestration framework" but spends defense capital
  proving inspectability the PRD grants LangGraph for free. Recommend: LangGraph.
- Either way: routing decisions are records, not vibes. Schema:
  {correlation_id, turn, supervisor_decision, reason_code, worker, input_ref, output_ref,
  handoff_ts} logged per hop; supervisor span parents worker spans.

## 5. The rest of the decision slate (draft entries, defend today, finalize after)

> Convention: Week 2 artifacts are all NEW files with their own numbering (W2-D#, W2-R#,
> W2-F#). Week 1 documents are frozen history and are never edited. Cross-reference Week 1
> decisions as "builds on D9" etc. File set to generate after the defense:
> W2_PRESEARCH.md, W2_RESEARCH.md, W2_DECISIONS.md, W2_ARCHITECTURE_DRAFT.md →
> repo-root W2_ARCHITECTURE.md (named deliverable), W2_IMPLEMENTATION_PLAN.md.

- **W2-D3 Vision extraction with grounding, not trust.** VLM = Claude (same provider as D4:
  no NEW PHI processor for extraction; the assumed BAA already covers it). But the VLM's
  output is never authoritative: a local OCR pass (Tesseract: word-level boxes, $0, no
  egress) provides the text layer + coordinates; every extracted field must locate its
  value in the OCR layer to earn a bbox + citation. Field found = cited with bbox. Field
  not found = rendered as unsupported, never silently kept. This is Week 1
  verify-then-flush extended to pixels, and it answers "vision extraction without
  invention" and the bbox requirement with one mechanism.
- **W2-D4 Hybrid RAG.** Corpus: public-domain US clinical guidance matching the demo panel
  conditions (USPSTF recommendations, ADA Standards of Care summaries, CDC schedules,
  JNC-8 class hypertension guidance). Clean licensing, defensible relevance to the Week 1
  PCP user. Retrieval: BM25 + dense embeddings, rerank top-k. Reranker: Cohere Rerank IF
  queries are PHI-free by contract (queries are built from condition/test names only,
  never identifiers); otherwise local cross-encoder as the "equivalent." Evidence snippets
  carry {source_id, section, chunk_id, quote}.
- **W2-D5 Eval gate v2.** 50 cases, boolean rubrics only, in-repo golden set (reproducible
  from repo alone: backup requirement). Categories per PRD + per-category thresholds and
  the >5% regression rule. Git pre-push hook AND the existing GH Actions gate. Judge:
  deterministic checks wherever possible; where an LLM judge is unavoidable
  (factually_consistent on free text), pin it to boolean questions with quoted evidence.
- **W2-D6 Citation contract v2.** Extend Week 1 evidence IDs to the prescribed shape;
  source_type distinguishes patient_record | uploaded_document | guideline. The UI renders
  patient-fact vs guideline-evidence as visually distinct classes (the PRD demands the
  separation in answers).
- **W2-D7 PHI surfaces v2.** New sensitive artifacts: document images, extracted fields,
  retrieval queries, eval fixtures. Rules: images stored in OpenEMR only; traces carry
  hashes/IDs (D16 content switch stays OFF by default); retrieval queries PHI-free by
  construction; CI adds a PHI-detection check over logs/fixtures. Egress inventory after
  W2: Anthropic (LLM+VLM, BAA), Langfuse (BAA posture per D5), reranker (only if PHI-free
  queries hold), OpenEMR (system of record).
- **Data authority table (engineering req):** source documents: OpenEMR. Extracted
  observations: OpenEMR after write, agent before. Guideline chunks + index: the agent
  service (rebuildable from corpus in repo). Citation records: agent, immutable, linked to
  correlation_id. One owner per type, no silent overwrites.

## 6. Week 1 debt to name (assignment: document + resolve before new surface)

1. In-process token/PKCE state dies on restart (re-launch required). Resolve: persist
   OAuth state alongside the Postgres session store early in W2.
2. 50-VU /ready saturation; fan-out cap conservative, knee unmeasured. Resolve or re-cap.
3. Verification v2 rules partially deferred; UC2 delta tool deferred. State which W2
   stages absorb them (extraction verification IS v2 work).
4. R12 latency anchor: replace with measured trace data in the W2 cost/latency report.
5. GitLab mirror + RAILWAY_TOKEN manual-deploy residual: close in W2 CI work.

## 7. Cuts to declare up front (narrower is stronger)

Core only for MVP: two doc types, two workers, one gate. Explicitly deferred: critic
agent, third document type, ColQwen2/multi-vector, trend chart, contextual-retrieval
extras, click-to-source polish beyond the required overlay. Each gets a dated Cut entry.

## 8. Likely grill questions (with the strong-answer core)

1. You were read-only. Now you write. Rebuild the safety story.
   → W2-D1: append-only creates, idempotent, source-linked, quarantined-not-silent; scopes
   widen narrowly and I say so.
2. Your D6 banned frameworks. Flip-flop?
   → D6 carried its own invalidation clause for exactly this; the seam was built for it.
   LangGraph adopted for routing only; workers stay thin.
3. Where do bounding boxes come from? Your VLM does not emit coordinates.
   → Local OCR provides words+boxes; extracted values must ground in that layer to earn a
   box; ungrounded = flagged unsupported. Never draw a box the OCR cannot justify.
4. OCR reads 5.0, the paper says 5.9 potassium. What renders?
   → The value renders with its citation + overlay; confidence comes from grounding
   agreement, not VLM self-report. VLM/OCR disagreement = unsupported-field path +
   "verify against source document" flag. The eval set includes a degraded-scan case.
5. What is in the corpus and why is sending queries to a reranker safe?
   → Public-domain guidance matched to the panel; queries are PHI-free by contract
   (condition/test terms only); if that contract cannot hold, local reranker.
6. Show me a supervisor routing decision.
   → The handoff record schema (§4B); every hop logged under the correlation ID; graph
   trace reconstructable from the ID alone.
7. How is factually_consistent boolean without judge vibes?
   → Deterministic field-vs-evidence comparison where structured; where text, pinned
   boolean questions quoting the exact evidence span. No 1-10 anywhere.
8. Name three regressions and the category that catches each.
   → Drop citation metadata field → citation_present. Loosen schema (unit optional) →
   schema_valid. Prompt change makes empty-allergy answer "NKDA" → safe_refusal.
   (Have a fourth: log a raw document line → no_phi_in_logs.)
9. Same PDF uploaded twice?
   → Content hash dedupes at ingestion; DocumentReference create is idempotent; derived
   facts carry deterministic IDs; second upload returns the existing lineage, creates
   nothing.
10. What Week 1 debt dies first and why?
    → Token persistence (it blocks reliable W2 demos through deploys), then /ready knee
    re-measure since W2 adds dependencies (vector index, reranker) to readiness.

## 9. The one-slide architecture (say-able in 60 seconds)

Physician uploads lab PDF / intake form → ingestion tool stores the source in OpenEMR,
hashes it, OCRs it locally → supervisor (LangGraph) routes: intake-extractor (VLM reads,
schema validates, every field grounds to OCR text+bbox or is flagged) and
evidence-retriever (hybrid BM25+dense over a public-domain guideline corpus, reranked) →
answers compose ONLY verified patient facts + cited guideline snippets, each claim carrying
the prescribed citation shape, PDF claims rendering a bounding-box overlay → derived facts
write back to OpenEMR append-only with lineage → every hop logged under the Week 1
correlation ID → a 50-case boolean eval gate blocks any PR that regresses. Week 1's thesis
unchanged: the model drafts, deterministic checks decide.
