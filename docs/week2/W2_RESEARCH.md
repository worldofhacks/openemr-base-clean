# W2_RESEARCH.md — Week 2 sourced research

> New file per week-scoped convention. Entries W2-R#. Facts verified 2026-07-13;
> code citations are file:line in this fork. Feeds W2_DECISIONS.md and the
> architecture draft.

## W2-R1. LangGraph supervisor pattern + Langfuse integration — verified

- The supervisor pattern is first-class: a central StateGraph node routes to worker
  nodes, collects output, decides next step or termination. Prebuilt
  `langgraph-supervisor` package exists with `create_handoff_tool`; handoffs are
  customizable (we will emit our own handoff record: {correlation_id, turn,
  supervisor_decision, reason_code, worker, input_ref, output_ref, handoff_ts}).
- Langfuse integrates via the LangChain callback handler: pass `langfuse_handler` on
  invocation and the SDK creates a nested trace per run — supervisor span parents
  worker spans, which satisfies the PRD's distributed-tracing requirement directly.
- State: default state is the message list; richer shared state (extracted facts,
  citations, partial answers) uses a custom MessagesState subclass with structured
  worker updates. That is where the EvidencePacket-style typed state lives.
- 2026 guidance: supervisor topologies are debuggable with tracing; peer/swarm
  topologies without tracing are explicitly warned against. Supports W2-D2.
- **Impact:** W2-D2 confirmed feasible as designed; W1 direct loop embeds inside
  worker nodes; one custom state class carries typed facts between workers.
- Sources: [Langfuse LangGraph cookbook](https://langfuse.com/guides/cookbook/integration_langgraph),
  [langgraph-supervisor reference](https://reference.langchain.com/python/langgraph-supervisor),
  [langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py),
  [Langfuse LangChain callbacks](https://langfuse.com/integrations/frameworks/langchain)

## W2-R2. Guideline corpus sourcing — recommendation, license-gated

- Cleanest sources are US-government works (public domain): **USPSTF recommendation
  statements**, **CDC clinical guidance** (immunization schedules, chronic-disease
  pages), and **VA/DoD Clinical Practice Guidelines** (diabetes, hypertension,
  lipids — strong match to the Synthea panel and the W1 PCP user).
- **Flag:** ADA Standards of Care and JAMA-published guideline reports are
  copyrighted — exclude, or link-only without ingesting text. Each ingested document
  gets a provenance + license line in the corpus manifest; license check is part of
  corpus build, not an afterthought.
- **Impact:** W2-D4 corpus = gov-source set; manifest with provenance committed to
  repo; corpus rebuildable from repo (backup requirement).

## W2-R3. Reranker + embeddings — verified

- Cohere Rerank: current per-search pricing ~$0.001–0.0025 depending on version;
  **Trial keys are free (~1,000 calls/month) but explicitly not for production use**;
  production keys are pay-as-you-go. Rate limits on trial tiers are real but fine for
  demo scale. For a graded one-week demo, a trial key is defensible for development
  with a documented production-key path; usage is tiny either way.
- PHI posture holds: queries are condition/test terms only (W2-D4 contract), corpus
  is public guidance — Cohere never receives PHI.
- Embeddings: local sentence-transformers is the recommended default for the dense
  leg — small corpus, zero vendor, zero egress question, no rate limits; hosted
  embeddings add a vendor for no capability we need at this scale. (Owner decision
  in the arch-draft interview.)
- Sources: [Cohere pricing](https://cohere.com/pricing),
  [Cohere pricing docs](https://docs.cohere.com/docs/how-does-cohere-pricing-work)

## W2-R4. Extraction cost, OCR reality, SLO baselining — planning numbers

- VLM page cost: Claude images tokenize by area (roughly (w×h)/750 tokens); a
  typical scanned page runs on the order of 1.5–2K input tokens ≈ **$0.005–0.01/page
  on Sonnet input pricing** before prompt/output. Cap pages per document; measure
  from traces (the W1 cost discipline; numbers re-baselined from real traces before
  the cost report).
- OCR: Tesseract is strong on clean 300-DPI scans, degrades on noise/handwriting —
  which is the point of the grounding design (degraded fields become UNSUPPORTED,
  not wrong). Note: born-digital PDFs have an extractable text layer with exact
  coordinates (no OCR needed); scanned PDFs need OCR. A text-layer-first,
  OCR-fallback ingestion keeps accuracy highest per document type.
- SLOs: set from first measured baselines (W2-O2); working targets ingestion p95
  ≤ 30s/doc, retrieval p95 ≤ 2s, revised at MVP with real numbers.

## W2-R5. OpenEMR write surface — VERIFIED from this fork's code (decisive)

- **FHIR write for our targets does not exist.**
  `POST /fhir/DocumentReference/$docref`
  (`apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:259`) is the US Core
  generate-document operation — its handler calls `getAll(...)`; it is not an upload.
  No `POST /fhir/Observation` route exists. FHIR POST exists only for
  Organization (:546), Patient (:560), Practitioner (:677).
- **The standard REST API has the write paths:**
  `POST /api/patient/:pid/document`
  (`apis/routes/_rest_routes_standard.inc.php:496` → `DocumentRestController->
  postWithPath`, multipart `$_FILES`, category path + optional encounter id) — the
  source-document store. `POST /api/patient/:pid/encounter/:eid/vital` (:140) covers
  vitals-class derived facts. No lab-result write route exists.
- **PRD alignment:** Core requirement 1 says persist derived facts "as appropriate
  FHIR resources **or OpenEMR records**" — the standard-API path is sanctioned.
- **W1 continuity:** D9 documented standard REST as the fallback "only if FHIR lacks
  a workflow field." FHIR lacks the write entirely; the fallback clause fires as
  designed.
- **Impact:** W2-D1 mechanism revision — source documents via the documents API;
  derived facts as a structured machine-authored extraction artifact linked to the
  source (plus vitals API where applicable). To verify at build: the standard API
  (`api:oemr`) scope set on the SMART client registration (D14-class enable step).

## Spawned verification items (build-phase, not blocking)

- V1: standard-API scopes + client enablement for `user/document.write`-class access.
- V2: LangGraph + SSE streaming through worker nodes (spike in MVP wave 1).
- V3: Cohere trial-key rate limits under the 50-case eval run (may need caching or
  a local fallback for CI — CI must not depend on live APIs anyway per the PRD).
