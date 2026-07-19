# S01 recording script v2 — every rubric display item, 3:00–5:00

Companion to `W2_S01_RECORDING_KIT.md` (setup, credentials, dry-run evidence keys) and
`W2_RUBRIC_WALKTHROUGH.md` (row numbers cited per beat). Record AFTER the merge train +
REL1 so the per-claim chips (#26), critic marker, R08 robustness, and R09 medication
fixture are live. PDF requirement (p.5): 3–5 minutes showing document upload, extraction,
evidence retrieval, citations, eval results, observability — all six are primary beats.

## Pre-flight (do before recording)

1. `/ready?cb=<unique>` all-green ×3 (R07 merged + deployed). Never record against degraded.
2. Pre-upload `golden/lab-clean-hba1c-high.pdf` so Lab trends shows TWO charts (HbA1c % +
   Glucose mg/dL) at beat 5.
3. Tabs open: workbench (`…/week2/launch`), Langfuse project filtered view, CI page with the
   green recorded-gate run AND the red drill run, `W2_RUBRIC_WALKTHROUGH.md`.
4. Fixtures staged: `golden/lab-clean-glucose.pdf` (PDF), `golden/intake-full-valid.png`
   (PNG — rendered at 200 DPI from the golden intake PDF, markers preserved; commit it),
   R09 medication-list golden fixture (PDF), one degraded/handwritten-style image (R08
   beat). Format matrix on camera: lab = PDF (PDF-only by design), intake = PNG ×2,
   medication list = PDF, degraded robustness = second PNG — all three doc types AND both
   input formats appear.
5. Synthetic patient **Daron260 Windler79** only. Frame/transcript PHI scan before publish.

## Script (timestamps target 4:50)

| Time | On screen | Say (roughly) | Rubric rows |
|---|---|---|---|
| 0:00–0:20 | Deployed app; `/health` cache-busted showing the release SHA | "Clinical Co-Pilot Week 2 — multimodal evidence agent on OpenEMR. This is the deployed release SHA; everything you'll see is synthetic data." | 16 |
| 0:20–1:00 | **Upload lab PDF** `lab-clean-glucose.pdf` → status pill → complete → document id + `w2.<hex>` correlation id → OpenEMR readback: two "Verified byte-for-byte" sha256 digests | "Upload, strict-schema extraction, and the source document stored in OpenEMR — the digests are re-read from OpenEMR, not echoed. Every job prints a correlation id; hold that thought." | 1 |
| 1:00–1:15 | Click a grounded field's citation → **Open page 1** → page renders with the value visibly boxed | "The rubric's click-to-source: every grounded value cites its exact page and bounding box — resolved by our own verifier, never trusted from the model." | 4 |
| 1:15–1:40 | **Intake form as PNG** `intake-full-valid.png`; point at the vitals checkbox copy; upload the SAME file again → same document id, digests re-verify | "Intake form as an image — the OCR path, not a text layer. Second upload of the same bytes: one permanent identity, no duplicates, vitals written exactly once — the FHIR-integrity requirement, live, on the multimodal path." | 2 |
| 1:40–2:00 | **Medication list** (badge "source + grounded artifact only") → upload R09 fixture → complete → digests | "Third document type. Stored and grounded — and deliberately NEVER written as medication orders; no MedicationRequest path exists. That's a safety decision, recorded and tested." | 3 |
| 2:00–2:35 | **Cited answer**: ask `type 2 diabetes; glucose` → per-claim citations across three source classes (chart / uploaded document / guideline); click an uploaded-doc chip → bbox preview again | "Evidence retrieval and the citation contract: hybrid keyword+dense retrieval with reranking over our guideline corpus, and every clinical claim carries machine-readable citations. A deterministic critic approved this composition before a single byte flushed — uncited claims don't ship." | 5, 6, 7 |
| 2:35–3:00 | **Lab trends**: HbA1c (%) beside Glucose (mg/dL); click a point → its verified page/bbox | "Trend chart from extracted observation data — exact units, so 6.5 percent can never be conflated with 65 milligrams. Points are backed by write/readback-verified artifacts; this fork exposes no supported Observation write, and we chose verified records over minting unverifiable ones." | 9 |
| 3:00–3:30 | CI tab: **green recorded 50-case gate run** (five boolean categories visible) → flip to the **red drill run** blocked from merging | "The eval gate: fifty golden cases, boolean rubrics — schema validity, citation presence, factual consistency, safe refusal, no PHI in logs. And here's the hard-gate proof: an introduced regression turns CI red and cannot merge." | 10, 11 |
| 3:30–4:00 | Langfuse filtered by the beat-1 correlation id: queue → OCR/VLM → grounding → retrieval → writes/readback → critic → summary; point at latency/token/cost fields | "Observability: one correlation id reconstructs the whole asynchronous path, with per-step latency, token usage, and cost. No raw PHI anywhere in these traces." | 8, 12 |
| 4:00–4:30 | Upload the degraded/handwritten-style image → extraction completes; UNSUPPORTED fields visibly redacted/unverified; then ask a question beyond the evidence → refusal | "Messy inputs are the point: a handwritten form our OCR can't read still extracts — unsupported values stay visible and unverified, never invented. And past the evidence, the agent refuses instead of guessing." | 13, 14, 15 |
| 4:30–4:50 | `W2_RUBRIC_WALKTHROUGH.md` on screen; scroll once | "Every rubric row — including the ones from early feedback — is mapped here to a live proof and durable evidence, with our open findings stated, not hidden. Release SHA <sha>. Thanks." | all |

## Coverage check before publishing

Six PDF elements: upload ✔ (beats 2–5) · extraction ✔ · evidence retrieval ✔ (beat 6) ·
citations ✔ (beats 3, 6) · eval results ✔ (beat 8) · observability ✔ (beat 9).
Feedback-named: bbox click-to-source ✔ (beats 3, 6) · green eval-gate run ✔ (beat 8).
Length 3:00–5:00 ✔ · synthetic-only ✔ · SHA stated twice ✔ · PHI scan before publish ✔.

*If running long, cut beat 7 to 15 s (skip the point-click) and trim beat 9's trace walk —
never cut beats 3, 8, or the degraded-upload half of beat 10.*
