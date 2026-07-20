# S01 recording script v2 — click-by-click, say-this-exactly edition

Target: under 5:00. Covers all six required PDF elements (upload, extraction, evidence
retrieval, citations, eval results, observability), both feedback-named items (bbox
click-to-source, green eval-gate run), all 3 doc types, both formats (PDF and PNG),
and hybrid RAG with reranking is called out by name in Beat 6. Read the SAY lines out
loud as written. Do exactly what each DO line says.

## PRE-FLIGHT (do all 5 before recording, no red allowed)

1. Health check three times: load `https://agent-production-9f62.up.railway.app/ready?cb=1`
   then `?cb=2` then `?cb=3`. All three must say ready and green. If any say degraded,
   STOP, tell the session, do not record.

2. Pre-upload one file: go to `https://agent-production-9f62.up.railway.app/week2/launch`,
   sign in `admin` plus OE password, pick patient **Daron260 Windler79**, click
   **Authorize**, doc type **Lab PDF**, upload
   `agent/evals/fixtures/golden/lab-clean-hba1c-high.pdf`, wait for **complete**.
   This makes two trend charts exist later.

3. Four tabs open, in this order: (1) the workbench from step 2, (2) Langfuse signed in
   with your project open, (3) GitHub, repo, **Actions**, the GREEN `agent-eval-gate` run
   on the accepted SHA, (4) Actions, the RED drill run (the session names it).

4. Files on Desktop: `lab-clean-glucose.pdf`, `intake-full-valid.png` (the PNG, not the
   PDF), the R09 medication-list fixture PDF, one messy handwritten-style image.

5. Screen hygiene: only these tabs, only that patient, no password visible on camera.

## THE RECORDING (11 beats)

**BEAT 1 (0:00–0:20) — opening.**

DO: Tab 1 visible. In a second tab load `/health?cb=9` so the SHA shows. Point your
cursor at the SHA string.

SAY: "This is Clinical Co-Pilot Week 2, a multimodal evidence agent on OpenEMR. It is a
FastAPI service deployed on Railway, signed in through SMART-on-FHIR, with PostgreSQL as
the durable artifact store. This is the deployed release SHA, and everything you will
see uses synthetic data only."

**BEAT 2 (0:20–1:00) — lab PDF upload and extraction.**

DO: Tab 1, doc type **Lab PDF**, click **Browse**, pick `lab-clean-glucose.pdf`, click
**Upload and extract**. Point at the status pill while it changes to **complete**. Point
at the document id and the `w2.` correlation id under it. Point at the two
"Verified byte-for-byte" sha256 lines.

SAY: "Document upload with strict-schema extraction. Under the hood, pdfplumber reads
the text layer, pypdfium2 renders pages, Tesseract handles OCR on scans, and Claude
vision proposes the extraction, which is validated against frozen Pydantic schemas and
then grounded by our own deterministic verifier. The source is stored in OpenEMR and
read back. These two digests are re-read from OpenEMR byte for byte, not echoed. Every
job prints a correlation ID. Remember this one, I will use it again at the end."

**BEAT 3 (1:00–1:15) — the bounding box. Do not rush this.**

DO: Click any grounded field's citation, then click **Open page 1**. A dialog opens
showing the PDF page. Point your cursor at the box drawn around the value.

SAY: "Click-to-source. Every grounded value opens its exact page with a bounding box
around the value, resolved by our own verifier, never trusted from the model."

**BEAT 4 (1:15–1:40) — intake as PNG, uploaded twice.**

DO: Doc type **Intake form**. Point at the vitals checkbox text. Click **Browse**, pick
`intake-full-valid.png`, upload. While it processes, say the first SAY sentence. When
complete, note the document id. Upload the SAME PNG again. Point: same document id,
digests re-verified.

SAY: "An intake form as an image. This is the OCR path, no text layer. Now the same
bytes again: one permanent identity, no duplicate records, and vitals are written
exactly once. That is the FHIR-integrity requirement on the multimodal path."

**BEAT 5 (1:40–2:00) — medication list, the third type.**

DO: Doc type **Medication list**. Point at the badge "source + grounded artifact only".
Upload the medication fixture, wait for complete, point at the digests.

SAY: "Third document type. Stored and grounded, and deliberately never written as
medication orders. No MedicationRequest path exists. That is a recorded safety decision."

**BEAT 6 (2:00–2:35) — cited answer: hybrid RAG, reranking, citations, critic.**

DO: In **Cited answer** type `type 2 diabetes; glucose` and click **Ask**. When it
renders, point at the three chip groups one by one: patient-record chips, uploaded
document chips, guideline chips (VA/DoD). Click one uploaded-document chip, the bbox
page opens again, close it.

SAY: "This is the hybrid RAG pipeline. Retrieval runs BM25 keyword search and dense
vector search with bge-small embeddings on ONNX together over our clinical guideline
corpus. Cohere Rerank then orders the candidates, with a local ONNX cross-encoder as
the fallback behind a circuit breaker and bounded retries, so only the top grounded
evidence reaches the model. Every clinical claim carries machine-readable citations
across three source classes: chart, uploaded document, and guideline. A deterministic
critic approved this composition before a single byte flushed. Uncited claims do not
ship."

**BEAT 7 (2:35–3:00) — lab trends.**

DO: Open **Lab trends**. Point at the HbA1c chart's unit label (%), then the Glucose
chart's (mg/dL). Click one data point, its page and bbox preview opens.

SAY: "Trend charts from extracted observation data with exact units, so 6.5 percent can
never be confused with 65 milligrams. Every point is backed by a write-and-readback
verified artifact. This fork exposes no supported Observation write, so we chose
verified records over minting unverifiable ones."

**BEAT 8 (3:00–3:30) — green gate, then red drill. Do not rush.**

DO: Switch to Tab 3, the green run. Point at the green check, scroll to the category
summary. Switch to Tab 4, the red drill. Point at the red X and the blocked merge.

SAY: "The eval gate: fifty golden cases with boolean rubrics covering schema validity,
citation presence, factual consistency, safe refusal, and no PHI in logs, green on this
exact SHA. And the hard-gate proof: an introduced regression turns CI red and cannot
merge."

**BEAT 9 (3:30–4:10) — observability and latency.**

DO: Switch to Tab 2, Langfuse. Paste the correlation id from Beat 2 into the filter.
Click the trace. Move the cursor slowly down the spans: queue, OCR/VLM, grounding,
retrieval, writes, critic. Point at `latency_ms` in the trace metadata (the full turn
duration). Then open the Langfuse Home dashboard: point at the Trace latency
percentiles row for `previsit-brief` (real p50/p95), the per-tool span latencies, the
Model latencies chart, and the token + cost totals. Do NOT scroll to the graph-turn
percentile row (known export-timestamp artifact; true duration lives in metadata).
Last, show `docs/week2/evidence/W2_COST_LATENCY.md` for a few seconds: point at the
p50/p95-per-flow table and the bottleneck line.

SAY: "Orchestration is a LangGraph supervisor routing to two workers, and this is
Langfuse showing it: one correlation ID reconstructs the entire asynchronous path.
Inputs and outputs are never logged, references only, because traces must stay
PHI-free. Latency is measured at every layer: per-tool spans, per-turn duration, and
the committed cost and latency report with p50 and p95 for each flow plus a bottleneck
analysis. The measured bottleneck is the local reranker's ONNX session cost, documented
openly, not hidden."

**BEAT 10 (4:10–4:35) — messy input and refusal.**

DO: Tab 1, intake form, upload the handwritten-style image. When complete, point at the
UNSUPPORTED redacted fields. Then in Cited answer ask something with no evidence, for
example `colonoscopy results`, and point at the refusal.

SAY: "Messy inputs are the whole point. A handwritten form our OCR cannot read still
extracts. Unsupported values stay visible and unverified, never invented. Past the
evidence, the agent refuses instead of guessing."

**BEAT 11 (4:35–4:55) — close.**

DO: Open `docs/week2/evidence/W2_RUBRIC_WALKTHROUGH.md` on GitHub. Scroll it once,
slowly.

SAY: "Every rubric row, including the ones from early feedback, maps here to a live
proof and durable evidence, with open findings stated, not hidden. Release SHA <read
it>. Thanks."

## AFTER RECORDING

Watch it once. All 11 beats present, under 5:00, no real names or passwords visible.
Publish per the kit, PHI-scan the frames and transcript, put the link where D01 expects
it. Running long: shorten Beats 7 and 9. Never cut Beats 3, 8, or the handwritten
upload in Beat 10.
