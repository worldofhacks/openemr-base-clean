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

## W2-R2. Guideline corpus sourcing — DEEP-RESEARCHED 2026-07-13 (5-agent pass, live-fetched policies)

**Ingestable (with conditions), matched to the panel:**
- **VA/DoD Clinical Practice Guidelines** (healthquality.va.gov): US-government works,
  VA copyright policy states government-produced materials "are not copyright
  protected." Full free PDFs verified for **Diabetes (2023), Hypertension (2020,
  update in progress — pin the ingested version), Lipids (2025)**, plus provider
  summaries and pocket cards (good chunking units). Best PRD fit: these literally are
  "agreed clinical practices" an office adopts. Caveat: embedded third-party figures
  not individually cleared — strip figures, ingest text.
- **CDC clinical content** (adult immunization schedule, chronic-disease guidance):
  public domain by default with named conditions — attribute ("Source: CDC"),
  non-endorsement disclaimer, no substantive alteration, note free availability, no
  logos, exclude non-PHIL images. Covers immunizations (USPSTF formally refers
  immunizations to CDC/ACIP).
- **USPSTF recommendation statements**: NOT clean public domain — AHRQ's copyright
  notice *permits* public reproduction/redistribution "without any changes,"
  noncommercial, with citation of the USPSTF page. Verbatim chunking is compliant
  (and matches our quote-based citation contract, W2-D6). Use the site HTML full
  text; **never the JAMA-branded PDFs** (AMA reserves all rights "including text and
  data mining, AI training"). Topics verified: prediabetes/T2D screening (2021),
  hypertension screening (2021), statin use (2022).
- Optional: MedlinePlus **health topic summaries only** (PD subset; never A.D.A.M.
  encyclopedia or ASHP monographs — NLM explicitly bans ingesting those into health
  IT systems).

**DO-NOT-INGEST list (all verified all-rights-reserved):** ADA Standards of Care
(explicitly bans "text or data mining, machine learning" without permission); AHA/ACC
guidelines (per-item permission fees, $100–$550); JNC 8 (AMA/JAMA copyright); GINA;
KDIGO (CC BY-NC-ND but conflicting terms + embedded AMA figures). Default posture:
no explicit license = do not ingest.

**Impact on W2-D4:** corpus = VA/DoD CPG trio + CDC immunization/chronic guidance +
USPSTF statements, manifest with per-document provenance/license/version committed to
repo; repo README carries the CDC/AHRQ attribution + non-endorsement disclaimers;
verbatim chunks only (also what the citation contract wants).
Key sources: [AHRQ/USPSTF copyright notice](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/copyright-notice),
[CDC agency materials policy](https://www.cdc.gov/other/agencymaterials.html),
[VA copyright policy](https://department.va.gov/copyright-policy/),
[VA/DoD CPG index](https://www.healthquality.va.gov/),
[ADA license page](https://diabetesjournals.org/journals/pages/license),
[JAMA copyright](https://jamanetwork.com/pages/copyright),
[MedlinePlus reuse](https://medlineplus.gov/about/using/usingcontent/)

## W2-R3. Retrieval stack (embeddings + reranker) — DEEP-RESEARCHED 2026-07-13

**Embeddings — recommendation: `BAAI/bge-small-en-v1.5` (MIT, 33M params, ~130MB),
run via FastEmbed/ONNX (no torch) + `rank-bm25` for the sparse leg.**
- Evidence: the BGE family significantly outperformed medical-specific models AND
  higher-MTEB-ranked 7-8B models on clinical retrieval (Myers et al., JAMIA 2025,
  3,488 configurations, p<0.05) — leaderboard rank does not transfer to clinical
  text; BGE does.
- At 50–200 clean guideline chunks, model deltas wash out; the levers that matter are
  hybrid BM25+dense, the reranker, and chunking/query phrasing (the clinical study
  saw ~9-point swings from query phrasing alone).
- ONNX-without-torch keeps Railway RSS low (the torch runtime, not the weights,
  dominates memory).
- Rejected: jina-v3/v4 (CC-BY-NC), embeddinggemma (Gemma ToU, not MIT/Apache),
  bge-m3 (2.2GB, overkill), hosted APIs (a vendor + egress question for no quality
  gain at this scale — OpenAI 3-small ≈ bge-base tier).

**Reranker — REVISED recommendation: local-primary.**
- Cohere trial terms verified verbatim: free, **1,000 calls/month, Rerank 10 req/min**,
  and "not permitted to be used for production or commercial purposes"
  (cohere.com/pricing). A graded PoC is defensibly evaluation use, BUT our deployed
  public app + repeated CI eval runs sit uncomfortably close to "persistently
  hosted," and 10 req/min could throttle a 50-case eval pass.
- Local pick: **`mixedbread-ai/mxbai-rerank-base-v1`** (Apache-2.0, 184M, DeBERTa —
  standard architecture, no trust_remote_code, ships quantized ONNX; beats
  bge-reranker-base on BEIR evals). CPU latency for ~30 candidates: sub-second class
  for small/base cross-encoders.
- Posture: local reranker in the deployed path = the PRD's "or an equivalent
  reranker," zero vendor, zero rate-limit risk during the graded CI regression test,
  zero egress questions. Cohere documented as the managed-production upgrade path.
- PHI note (either path): queries are condition/test terms + corpus is public
  guidance; no PHI leaves regardless.
Key sources: [Cohere rate limits](https://docs.cohere.com/docs/rate-limits),
[Cohere pricing FAQ](https://cohere.com/pricing),
[bge-small-en-v1.5 card](https://huggingface.co/BAAI/bge-small-en-v1.5),
[JAMIA clinical retrieval study](https://arxiv.org/html/2409.15163v1),
[mxbai-rerank-base-v1](https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v1)

## W2-R4. Extraction cost, OCR reality, SLO baselining — planning numbers

> **Post-remediation note (2026-07-13):** SLO closure is locked to **Early (Thu
> 2026-07-16)**, not "revised at MVP" (single dated answer, W2_DECISIONS/gap-audit).
> Corpus scope for the build is the **VA/DoD CPG trio only**; CDC/USPSTF remain
> license-vetted manifest-ADD options (not claimed as ingested) unless a demo case needs
> them. The research below is the point-in-time source material.

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

## W2-R5. OpenEMR write surface — VALIDATED 3-way (owner challenged; re-verified 2026-07-13)

Validation sources, all in-fork: (1) exhaustive write-route enumeration — POST/PUT exist
only for Patient, Organization, Practitioner (+ `$docref` op, + bulk-status DELETE);
(2) FHIR_README.md documents `$docref` as "Generate Clinical Summary (CCD)";
(3) Documentation/api/FHIR_API.md:728 — "The `$docref` operation creates clinical summary
documents (C-CDA)" — and the route handler itself calls `getAll()`. No FHIR upload path
for DocumentReference, no FHIR Observation write, in this fork.

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
- **Verified live 2026-07-13** — independent probe run against the local live stack
  (production read-only): verdict **CONFIRMED and strengthened** (route-level 404s on
  FHIR POSTs even with maximal write scopes). See W2_AUDIT.md "W2-F1 independent
  verification" for the corrections that bind the build: upload returns 200 `true`
  with no id (W2-F9), FHIR DocumentReference→Binary is the byte-exact read-back (the
  standard download 500s — known issue), vitals proven end-to-end incl. FHIR
  Observation reads (W2-F10), replacement-client provisioning constraint (W2-F4
  resolved), CapabilityStatement untrustworthy (W2-F7), scope/discovery drift (W2-F11).

## W2-R6. PDF text-layer + page-rendering library — ADDED 2026-07-13 (/arch-finalize; license-vetted, verify at build)

The draft leaned on an unnamed PDF library in three load-bearing places (words+boxes
text-layer read, page rasterization for OCR/VLM input, page-PNG rendering for the
required bbox overlay) — and the most common Python choice, **PyMuPDF, is AGPL-3.0**,
a real licensing trap in this GPL-3 fork with a deployed network service.

- **Working default: `pypdfium2`** (PDFium binding; permissive Apache-2.0/BSD-3-class
  licensing) — one library for (a) text extraction with character/word boxes, (b) page
  rasterization for Tesseract input and VLM page images, (c) page-PNG rendering for the
  overlay.
- **Alternative for the words+boxes leg: `pdfplumber`** (MIT, on pdfminer.six) —
  mature word-level `extract_words()` bboxes; no rendering, so it pairs with pypdfium2
  if its word segmentation proves better on the fixture set.
- **Rejected:** PyMuPDF (AGPL-3.0); pdf2image (adds a poppler system dependency for
  rendering only).
- Both candidates emit into the canonical NormBBox space (binding §2). Final selection
  is a day-1 build spike item alongside Tesseract packaging (binding §9); licenses
  re-verified from the shipped package metadata at that point.

## Spawned verification items (build-phase, not blocking) — statuses 2026-07-13

- V1: standard-API scopes + client enablement for `user/document.write`-class access.
  **Carried** as an owner action / build-blocking checklist item (W2-F4; binding §4).
- V2: LangGraph + SSE streaming through worker nodes (spike in MVP wave 1).
  **Carried visibly** in the binding doc (§2a/§9) with a named fallback: stream only
  the final composer stage; perceived-latency cost, never a correctness cost.
- V3: Cohere trial-key rate limits under the 50-case eval run. **Mooted 2026-07-13**
  by the W2-D4 revision: CI never calls Cohere live, and the deployed path requires
  the paid production key by Monday EOD or ships `RERANKER=local`.
