# W2_ARCHITECTURE_DRAFT.md — Multimodal Evidence Agent (v2, post-research)

> Status: DRAFT v2 (2026-07-13) — regenerated after W2_RESEARCH.md (R1–R5, deep-research
> pass) and owner decisions (W2_DECISIONS.md W2-D1..D7, all locked). Supersedes the
> defense draft v1. Next step: cold-eyes /arch-finalize (fresh session) → binding
> repo-root W2_ARCHITECTURE.md → /tasks-gen → W2_IMPLEMENTATION_PLAN.md.
> Builds on the frozen W1 system (docs/week1/ARCHITECTURE.md); W1 refs D#/F#/§.

## One-page summary

Week 2 makes the co-pilot **see documents**. A physician uploads a scanned lab PDF or an
intake form from José Oquendo's chart. `attach_and_extract` stores the source file in
OpenEMR via the standard documents API (the fork has **no FHIR write** for
DocumentReference/Observation — verified three ways, W2-R5), content-hashes it for
idempotency, and reads it: born-digital PDFs by their exact text layer, true scans by
local Tesseract OCR — both yielding words + coordinates (W2-D3). A **LangGraph
supervisor** (W2-D2 — W1 D6's own invalidation clause fired) routes work to two workers.
The **intake-extractor** sends page images to Claude (the W1 provider is also the VLM —
zero new PHI processors) and its JSON output is validated into **strict Pydantic models**
(`LabPdfExtraction`, `IntakeFormExtraction` — PRD req 2; malformed output is a hard
reject, raw VLM output never bypasses the schema); every validated field must then
**ground** in the OCR/text layer to earn its citation and bounding box — ungrounded or
disagreeing fields render UNSUPPORTED with a "verify against source document" flag.
Pydantic proves the shape; grounding proves the content is on the page. The
**evidence-retriever** runs hybrid BM25 + bge-small dense retrieval over a three-document
guideline corpus — the VA/DoD Diabetes/Hypertension/Lipids CPGs, license-verified
US-government works that literally are "agreed clinical practices" (W2-D4, W2-R2) — and
reranks with **Cohere** under a PHI-free-query contract. Answers compose patient facts
and guideline evidence as visually separate classes; every claim carries the prescribed
citation shape; document claims render a **PDF bounding-box overlay**. Derived facts
write back **append-only** with lineage: worst-case injection is a quarantined,
machine-authored, voidable record — never a silent edit (W2-D1). Every hop logs under
the W1 correlation ID; supervisor spans parent worker spans in Langfuse. A **50-case
boolean-rubric eval gate** (schema_valid, citation_present, factually_consistent,
safe_refusal, no_phi_in_logs; >5% category regression fails; PR-blocking hook + CI)
stands where the graders will strike. The W1 thesis is unchanged: the model drafts,
deterministic checks decide — Week 2 extends it to pixels.

## §1 System overview

```
 Physician (SMART launch, W1 auth unchanged)
    │ upload lab_pdf / intake_form             │ questions
    ▼                                           ▼
 attach_and_extract(patient_id, file, doc_type)      ┌────────────────────────┐
  1 store source → POST /api/patient/:pid/document   │  LangGraph SUPERVISOR  │
  2 content-hash (idempotent, W2-D1)                 │  logged handoffs (W2-D2)│
  3 read: text-layer | OCR fallback (W2-D3)          └──────┬────────┬────────┘
    ▼                                                 extract?│        │evidence?
 ┌──────────────────────────┐              ┌──────────────────▼──────┐ │
 │ INTAKE-EXTRACTOR         │              │ EVIDENCE-RETRIEVER      │◄┘
 │ VLM → strict schema →    │              │ BM25 + bge-small dense  │
 │ ground per field →       │              │ → Cohere rerank →       │
 │ bbox | UNSUPPORTED       │              │ snippets {src,chunk,quote}│
 └───────────┬──────────────┘              └───────────┬─────────────┘
             ▼                                          ▼
 append-only writes w/ lineage            ┌─────────────────────────────┐
 (documents API + vitals API)             │ ANSWER COMPOSER             │
             │                            │ citation contract v2 (W2-D6)│
             ▼                            │ patient facts ≠ guideline   │
 OpenEMR = system of record               │ evidence · bbox overlay ·   │
 (Zone A, unchanged authority)            │ verify-then-flush + refusals│
                                          └─────────────────────────────┘
 External (Zone C, BAA posture): Claude LLM+VLM (assumed BAA) · Cohere Rerank
 (PHI-free queries only, production key) · Langfuse Cloud (D16 content OFF)
 Eval gate v2 across everything: 50 boolean cases · 5 categories · hook + CI (W2-D5)
```

Deployment unchanged: one Railway project (OpenEMR + MySQL + agent). Corpus + index live
in the agent service, rebuildable from repo (W2-O1: in-process index). New container
deps: Tesseract binary, ONNX runtime for bge-small. New env: `COHERE_API_KEY`.

## §2 Components (delta over W1)

- **attach_and_extract tool** — accepts (patient_id, file, doc_type ∈ {lab_pdf,
  intake_form}); stores source via documents API; hashes for idempotency; builds the
  words+boxes layer (text-layer first, junk-layer sanity check, OCR fallback).
- **LangGraph graph** — supervisor + intake-extractor + evidence-retriever; custom
  typed state (extracted fields, citations, partial answers — W2-R1); handoff record
  {correlation_id, turn, supervisor_decision, reason_code, worker, input_ref,
  output_ref, handoff_ts}; W1 direct loop survives inside workers.
- **Extraction schemas (canonical contracts, PRD req 2) — Pydantic v2, explicitly.**
  All Week 2 typed contracts are Pydantic models in `agent/app/` (same stack as W1 D3),
  each with validation tests (a named PRD deliverable):
  `LabPdfExtraction{test_name, value, unit, reference_range, collection_date,
  abnormal_flag, source_citation}`; `IntakeFormExtraction{demographics, chief_concern,
  current_medications, allergies, family_history, source_citation}`;
  `CitationV2{source_type, source_id, page_or_section, field_or_chunk_id,
  quote_or_value}`; `EvidenceSnippet{source_id, section, chunk_id, quote, score}`;
  `HandoffRecord{correlation_id, turn, supervisor_decision, reason_code, worker,
  input_ref, output_ref, handoff_ts}`; `GroundedField{value, page, bbox, grounded:
  bool}`. Raw VLM output never bypasses schema validation; schema changes from W1
  carry a migration note (PRD engineering req).
- **Grounding verifier** — per-field: locate the extracted value in the words+boxes
  layer; found → citation + bbox; not found / disagreement → UNSUPPORTED render.
  Confidence = grounding agreement (binary), never VLM self-report.
- **Guideline corpus** — VA/DoD trio (Diabetes 2023, HTN 2020 pinned, Lipids 2025) +
  pocket cards; manifest with provenance/license/version; verbatim chunks;
  do-not-ingest list documented (W2-R2). **Sizing (curation rule, not scraping):**
  ingest the recommendation/management sections + pocket cards, skip evidence-review
  and methodology appendices — the trio yields several hundred retrievable chunks
  (the CPGs are 100+ page documents), which is small-and-applicable per the PRD
  without being thin. Corpus size and chunk count are recorded in the manifest.
- **Hybrid retriever + reranker** — rank-bm25 + bge-small-en-v1.5 (ONNX/FastEmbed) →
  Cohere Rerank (production key; PHI-free queries; down → un-reranked hybrid scores,
  degraded).
- **Answer composer** — citation contract v2: {source_type, source_id, page_or_section,
  field_or_chunk_id, quote_or_value} on every clinical claim; source_type ∈
  {patient_record, uploaded_document, guideline}, rendered as distinct classes;
  document claims → bbox overlay (server-rendered page PNG + scaled coordinate divs);
  incomplete citation = claim does not render. W1 verify-then-flush + refusal
  discipline unchanged.
- **Eval gate v2** — 50 in-repo boolean cases; 5 categories; PR-blocking Git Hook
  (committed hooksPath + documented setup) AND GH Actions; CI PHI-detection check;
  reranker/VLM stubbed in CI (PRD: no live APIs in tests).
- **Unchanged W1 components:** SMART auth + session pin, EvidencePacket chart reads,
  deterministic templater, D13 degradation, Langfuse posture (D16 OFF), alert checker.

## §3 The two lifecycles

**Ingestion.** upload → hash (duplicate → return existing lineage, create nothing) →
store source in OpenEMR → build words+boxes layer → supervisor routes to extractor →
VLM extraction → schema validation (hard reject on violation) → per-field grounding →
verified fields persist append-only (documents API artifact + vitals API where
applicable) with source lineage → extraction report to physician: grounded fields cited
+ boxed, ungrounded fields flagged UNSUPPORTED.

**Question.** physician asks ("what changed? what should I pay attention to?") →
supervisor decides per turn: chart facts (W1 EvidencePacket path), document facts
(extracted, cited to page+bbox), guideline evidence (retriever) → composer merges with
sources separated and every claim cited → verify-then-flush → follow-ups reuse session
context; UNSUPPORTED and refusal behaviors identical to W1's discipline.

## §4 Trust boundaries & PHI (W1 posture extended)

- **Zone A (OpenEMR)** keeps identity, authority, clinical truth. New: it now receives
  agent-authored creates — bounded to two create operations, idempotent, source-linked,
  visibly machine-authored, voidable. No update/delete surface exists in the agent.
- **Zone B (agent)**: uploaded documents are the NEW injection surface — document
  content is data, never instructions; schema-bound extraction output + append-only
  writes bound the blast radius; injection cases required in the 50 (W2-D7).
- **Zone C (external, BAA posture)**: Claude (LLM+VLM — same assumed BAA, zero new PHI
  processors for vision); Cohere (PHI-free queries by contract — condition/test terms
  only, corpus is public guidance); Langfuse Cloud (W1 D5/D16 posture, content OFF).
- Document images live in OpenEMR only. Logs/traces/eval artifacts PHI-free, enforced
  by a CI PHI-detection check. Egress inventory: Anthropic, Cohere (PHI-free), Langfuse.
- Scope delta said plainly: read-only → read + narrow create (`api:oemr` document/vital
  scopes; verify the D14-class client-enable step at build).

## §5 Failure modes (detection + recovery, PRD engineering reqs)

| Failure | Behavior |
|---|---|
| Ingestion fails mid-flow | Source already stored; extraction marked failed; explicit message; idempotent retry |
| Schema violation from VLM | Hard reject (schema is the contract); per-field logging; schema_valid guards |
| Grounding disagreement (OCR/text vs VLM) | Field renders UNSUPPORTED + "verify against source document" + overlay region |
| Junk embedded text layer | Density sanity check → OCR fallback path |
| Retrieval returns nothing | "No guideline evidence found" stated; never invented evidence; flagged |
| Cohere down / rate-limited | Un-reranked hybrid scores; degraded, logged; /ready reports degraded |
| VLM/LLM down | W1 D13 deterministic degradation (facts, no synthesis, banner) |
| Supervisor routing error | Handoff record shows decision + reason_code; trace reconstructable from correlation ID |
| Duplicate upload | Content hash → existing lineage returned; zero new records |
| Injection text in a document | Data-not-instructions handling; schema-bound output; append-only bound; eval-cased |
| /ready | Extended deps (doc storage, index, reranker) with degraded-not-binary status |

## §6 Observability, SLOs, ops (extends W1 §7)

New metrics: ingestion latency, per-field extraction pass rate, grounding-agreement
rate, retrieval hit rate, rerank scores + model/version, routing decisions, per-worker
latency, eval pass rate per category. New alerts: extraction failure rate, retrieval
latency, eval-regression >5% in any category. SLOs set from measured baselines at MVP
(W2-O2; working targets: ingestion p95 ≤ 30s/doc, retrieval p95 ≤ 2s); W2 baselines
recorded and compared against W1's (shared-path regression check, PRD). All outbound
LLM/VLM/retrieval calls carry timeouts + retries. Supervisor span ⊃ worker spans ⊃
extraction/retrieval sub-calls; full trace reconstructable from the correlation ID
alone. OpenAPI 3.0 spec published for new endpoints; Bruno collection extended
(upload, extraction status, retrieval, full flow). Cost: VLM page calls capped per doc
(~$0.005–0.01/page planning number, W2-R4), measured from traces.

## §7 Eval gate v2 (the graded hard gate)

50 in-repo synthetic cases (fixture documents authored from Synthea data + degraded
variants); boolean rubrics only; categories schema_valid, citation_present,
factually_consistent, safe_refusal, no_phi_in_logs; any category >5% regression or
below threshold fails. Delivery: committed Git Hook (hooksPath + setup doc) + GH
Actions; PHI-detection check in CI; VLM/reranker stubbed (no live APIs). Case mix
covers extraction (clean + degraded + disagreement), retrieval (hit + empty), citations,
refusals, missing-data, duplicate upload, injection-bearing documents. Every category
maps to a named realistic one-line regression it catches (defense prep §8). Golden set
reproducible from repo alone (backup req; RPO 0 via git).

## §8 Risks & owned tradeoffs

- LangGraph is new surface: workers thin, routing logged, D6 seam story (W2-D2).
- OCR fidelity on degraded scans: by design becomes UNSUPPORTED, not wrong; degraded
  fixture in evals; text-layer path avoids OCR where truth is free (W2-D3).
- Cohere is an external serving dependency: degraded fallback defined; production key
  (owner action) resolves terms/limits; version logged per trace against score drift;
  CI never depends on it. Local mxbai documented as vendor-independence alternative.
- Write path is a new risk class: bounded append-only + idempotent + lineage; stated
  plainly (W2-D1).
- Corpus deliberately three documents: small-and-applicable per PRD; manifest makes
  additions one-line; do-not-ingest list prevents licensing traps (W2-R2).
- W1 debt carried intentionally: token persistence across restarts lands early in W2
  build (demo reliability); /ready knee re-measured with new deps.

## §9 Build order (→ /tasks-gen against checkpoints)

- **MVP (Tue 11:59 PM):** schemas + fixtures → attach_and_extract (store, hash, read
  layer) → grounding verifier → LangGraph skeleton (supervisor + workers, handoff
  records) → corpus build + hybrid retrieval + Cohere → citation contract v2 + minimal
  overlay → 50-case gate + hook + CI PHI check → deploy + README W1/W2 split.
- **Early (Thu):** overlay polish, follow-up question flows, W2 dashboard panels +
  alerts, baselines vs W1, token persistence debt, Bruno + OpenAPI.
- **Final (Sun noon):** hardening, cost/latency report from traces, demo video, cuts
  documented, final live E2E.

## Open items (→ /arch-finalize)

- W2-O1 index in-process (default) — confirm memory fit alongside ONNX models on
  Railway.
- W2-O2 SLO numbers from first measured baselines.
- W2-O3 "pending review" UI treatment for machine-authored records.
- O-new: exact vitals-API field mapping for lab-adjacent values (build-time detail).
- Owner actions: Cohere production key → Railway env; confirm SMART client gets
  `api:oemr` scopes.
