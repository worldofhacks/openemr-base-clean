# W2_ARCHITECTURE_DRAFT.md — Multimodal Evidence Agent (defense draft)

> Status: DRAFT for the Architecture Defense (2026-07-13). Finalizes to repo-root
> W2_ARCHITECTURE.md via arch-finalize after the defense, before MVP code.
> Decisions: W2_DECISIONS.md (W2-D1..D7). Builds on the frozen W1 system
> (docs/week1/ARCHITECTURE.md): SMART sidecar, EvidencePacket, verify-then-flush,
> Langfuse accountability record, eval-gated deploy.

## One-paragraph summary (the 60-second version)

A physician uploads a scanned lab PDF or intake form. The ingestion tool stores the
source in OpenEMR, content-hashes it, and OCRs it locally. A LangGraph supervisor routes
work to two workers: the intake-extractor (VLM reads the page, output is schema-bound,
and every extracted field must ground in the OCR text layer to earn a citation and a
bounding box — ungrounded fields render as unsupported) and the evidence-retriever
(hybrid BM25 + dense retrieval over a public-domain guideline corpus, reranked, snippets
only). Answers compose verified patient facts and cited guideline evidence as separate
classes; every claim carries the prescribed citation shape; document claims render a
bbox overlay. Derived facts write back to OpenEMR append-only with lineage. Every hop
logs under the W1 correlation ID, and a 50-case boolean-rubric CI gate blocks any PR
that regresses. The W1 thesis is unchanged: the model drafts, deterministic checks
decide — Week 2 extends it to pixels.

## §1 System overview

```
 Physician (OpenEMR chart, SMART launch — unchanged W1 auth)
    │ upload lab_pdf / intake_form            │ questions (chat, W1 UC flow)
    ▼                                          ▼
 attach_and_extract(patient_id, file, doc_type)          ┌──────────────────────┐
    │ 1. store source → OpenEMR DocumentReference        │  LangGraph SUPERVISOR │
    │ 2. content-hash (idempotency, W2-D1)               │  routes, logs handoffs│
    │ 3. local OCR (Tesseract: words + boxes, W2-D3)     └──────┬───────┬───────┘
    ▼                                                            ▼       ▼
 ┌────────────────────────┐                        ┌─────────────────────────────┐
 │ INTAKE-EXTRACTOR worker │                        │ EVIDENCE-RETRIEVER worker   │
 │ VLM (Claude) → strict   │                        │ hybrid BM25+dense over      │
 │ schema → OCR grounding  │                        │ guideline corpus → Cohere   │
 │ per field → bbox or     │                        │ rerank → top snippets with  │
 │ UNSUPPORTED (W2-D3)     │                        │ {source_id,section,chunk,   │
 └───────────┬────────────┘                        │  quote} (W2-D4)             │
             ▼                                      └──────────────┬──────────────┘
 verified fields → append-only FHIR writes                         ▼
 (DocumentReference + Observations, lineage,          answer composer: patient facts
  idempotent — W2-D1)                                 vs guideline evidence, separate
             │                                        classes, citation contract v2
             ▼                                        (W2-D6) + PDF bbox overlay
 OpenEMR = system of record ◄──────────────────────── physician-visible answer
```

Everything runs inside the existing W1 deployment (Railway: OpenEMR + MySQL + agent);
the vector index is in-process (small corpus, rebuildable from repo, W2-O1). No new PHI
processors: Claude is both LLM and VLM; OCR is local; reranker sees PHI-free queries
only (W2-D4/D7).

## §2 Components (delta over W1)

- **attach_and_extract tool** — file in, DocumentReference stored, OCR layer built,
  extraction routed; idempotent via content hash.
- **LangGraph graph** — supervisor + intake-extractor + evidence-retriever; handoff
  records {correlation_id, turn, supervisor_decision, reason_code, worker, input_ref,
  output_ref, handoff_ts}; supervisor span parents worker spans (W2-D2).
- **Extraction schemas (canonical contracts)** — lab_pdf: test name, value, unit,
  reference range, collection date, abnormal flag, source citation. intake_form:
  demographics, chief concern, current medications, allergies, family history, source
  citation. Raw VLM output never bypasses schema (PRD).
- **Grounding verifier** — field value must locate in the OCR layer; binary grounding =
  extraction confidence; disagreement → unsupported render (W2-D3).
- **Guideline corpus + hybrid retriever + reranker** — public-domain, provenance
  documented, repo-rebuildable (W2-D4).
- **Citation contract v2** — prescribed shape, source_type separation, bbox overlay
  (W2-D6).
- **Eval gate v2** — 50 boolean cases, 5 categories, PR-blocking hook + CI, PHI check
  (W2-D5).
- **Unchanged from W1:** SMART auth, session pin, EvidencePacket for chart reads,
  verify-then-flush, deterministic templater, refusal discipline, D13 degradation,
  Langfuse posture (D16 content switch default OFF).

## §3 The two lifecycles

**Ingestion:** upload → hash (duplicate? return existing lineage, create nothing) →
store source → OCR → supervisor routes to extractor → schema-validated extraction →
per-field grounding → verified fields written append-only with lineage → extraction
report to physician (grounded fields cited + boxed; ungrounded fields flagged).

**Question:** physician asks ("what changed, what should I pay attention to?") →
supervisor decides: chart facts (W1 EvidencePacket path), document facts (extracted,
cited to page+bbox), guideline evidence (retriever) → composed answer, facts vs
evidence separated, every claim cited → verify-then-flush → follow-ups reuse session
context without losing grounding.

## §4 Trust & PHI deltas (W1 posture extended)

- Writes exist now: exactly two creates, append-only, idempotent, source-linked,
  machine-authored provenance (W2-D1). Injection worst case = quarantined wrong record,
  reviewable, voidable — never silent edits of clinician data.
- Uploaded documents are the new injection surface: content is data, never
  instructions; schema-bound output + append-only writes bound blast radius; injection
  cases in the eval set (W2-D7).
- PHI egress inventory: Anthropic (LLM+VLM, assumed BAA), Langfuse Cloud (W1 D5/D16
  posture), Cohere (PHI-free queries only), OpenEMR (system of record). Document images
  never leave OpenEMR; logs/traces/eval artifacts PHI-free, enforced by a CI check.

## §5 Failure modes (each: detection + recovery)

| Failure | Behavior |
|---|---|
| Ingestion fails mid-flow | Source doc already stored; extraction marked failed; physician told explicitly; retry re-runs idempotently |
| Extraction schema violation | Hard reject (schema is the contract); logged per field; case category schema_valid |
| OCR/VLM disagreement | Field renders UNSUPPORTED + "verify against source document" + overlay region (W2-D3) |
| Retrieval returns nothing | Answer states "no guideline evidence found"; never invented evidence; flagged |
| Reranker down | Fall back to un-reranked hybrid scores; degraded, logged |
| VLM/LLM down | W1 D13 deterministic degradation: facts without synthesis, banner |
| Supervisor routing error | Handoff record shows the decision + reason_code; trace reconstructable from correlation ID; routing eval cases |
| Duplicate upload | Content hash → return existing lineage; zero new records (W2-D1) |
| /ready dependencies | Extended to doc storage, vector index, reranker; degraded status, not binary (PRD) |

## §6 Observability & SLOs (extends W1 dashboard)

New metrics: ingestion latency, per-field extraction pass rate, grounding-agreement
rate, retrieval hit rate, rerank scores, routing decisions, per-worker latency, eval
pass rate per category. New alerts: extraction failure rate, retrieval latency, eval
regression >5% in any category. SLOs (working targets pending measured baselines,
W2-O2): ingestion p95 ≤ 30s/doc, retrieval p95 ≤ 2s. All outbound LLM/retrieval calls
carry timeouts + retries. Correlation ID reconstructs the full multi-agent trace alone.

## §7 Eval gate (the graded hard gate)

50 in-repo synthetic cases; boolean rubrics; categories schema_valid, citation_present,
factually_consistent, safe_refusal, no_phi_in_logs; PR-blocking Git Hook + GH Actions;
fail on >5% category regression or threshold drop. Every category maps to a named
realistic one-line regression it catches (W2_DEFENSE_PREP §8 Q8) — designed for the
graders' injection test, not around it.

## §8 Risks & owned tradeoffs

- **LangGraph is new surface** (W1 was framework-free): contained by keeping workers
  thin (W1 loop inside), routing logged, D6's documented seam cited (W2-D2).
- **Two new quality dependencies** (OCR fidelity on degraded scans, rerank quality):
  both measured, not assumed (→ W2-R4); degraded-scan case in the eval set.
- **Cohere = one new vendor**: bounded by the PHI-free-query contract; local
  cross-encoder fallback named (W2-D4).
- **Write path is new risk class**: bounded append-only + idempotent + lineage (W2-D1);
  said plainly, not hidden.
- **Scope discipline**: core only until green (owner-locked); stretch (critic, 3rd doc
  type, trend chart, ColQwen2, contextual retrieval) explicitly deferred with dated
  cuts.

## §9 Build order (defense → MVP Tue 11:59PM CT)

1. Finalize W2_ARCHITECTURE.md (arch-finalize) + W2_RESEARCH.md (W2-R1..R5).
2. W2_IMPLEMENTATION_PLAN.md via tasks-gen.
3. MVP: schemas + attach_and_extract (fixtures first) → OCR grounding → LangGraph
   supervisor + workers → hybrid RAG + rerank → citation contract v2 + overlay →
   50-case gate + Git Hook → deploy + README W1/W2 separation.
```
