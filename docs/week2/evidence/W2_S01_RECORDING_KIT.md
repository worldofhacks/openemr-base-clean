# W2 S01 Recording Kit — 3–5 minute demo (dry run 2026-07-19)

Companion to [W2_DEMO_SCRIPT.md](W2_DEMO_SCRIPT.md). Every step below marked **WORKS** was
executed against the deployed app on 2026-07-19 (dry-run screenshots in
[`s01/`](s01/)). Recording and publishing are **owner actions**.

## Dry-run state (re-verify on recording day)

- `GET /health?cb=<unique>` → `{"status":"alive","sha":"658307936f0396d292c94fff3f9ef8089f1697e7"}`
- `GET /ready?cb=<unique>` → all eight probes `ok:true`, including the `active_reranker`
  soft probe (no flap observed at probe time). Hard: `openemr_fhir`, `anthropic`,
  `session_store`, `document_runtime`, `document_category_read`. Soft: `langfuse`,
  `retrieval_index`, `active_reranker`.
- Recorded eval evidence for this exact SHA is committed:
  `eval-results/results-tier1-6583079.json` (recorded tier, 50 cases, PASS) and
  `eval-results/results-tier2-live-6583079.json` (live tier, 50 cases, PASS).
- Demo patient used in the dry run: **Daron260 Windler79** (Synthea synthetic,
  DOB 1964-05-12; pinned chart badge `a234b772…0155`). This patient already carries a
  verified `lab-clean-glucose` artifact (Glucose 92 mg/dL, 2026-06-01), so lab trends,
  citations, and the bbox preview resolve immediately. Reuse this patient.
- Final-take upload fixtures (committed, synthetic only — the set used in the
  published video, superseding earlier takes via PR #43):
  `docs/week2/evidence/s01/demo-uploads/{daron-lab-a1c-glucose.pdf,
  daron-lab-lipid.pdf, daron-intake-rich.png, daron-medication-list.pdf,
  daron-medication-list.png}`.

## Prerequisites (before pressing record)

1. **Recorder + mic.** Screen recorder at 1080p+ with system-audio/mic; do a 10-second
   sound check. Target ≤ 5:00 total (hard requirement 3–5 min).
2. **Credentials.** OpenEMR admin username `admin`; password is the `OE_PASS` value in the
   local, gitignored `tmp/railway-secrets.env`. Never show that file, the password, or a
   password manager on screen. The login form masks the field.
3. **Token hygiene.** After authorize, the workbench URL carries `?sid=<session token>`.
   Either hide the browser URL bar (full-screen / app-mode window) for workbench segments
   or blur it in post; the demo script requires tokens out of the recording.
4. **Browser prep.** Fresh profile or cleared autofill (the dry-run profile auto-filled
   the login form), bookmarks bar hidden, no unrelated tabs.
5. **Upload assets.** Use only synthetic fixtures from the repo:
   - Rich beats: `agent/evals/fixtures/golden/lab-clean-glucose.pdf`,
     `intake-full-valid.pdf`, `lab-clean-hba1c-high.pdf` (for the 6.5-vs-65 trend beat).
   - The geometry fixtures in `agent/evals/fixtures/documents/` (`clean.pdf`,
     `degraded.pdf`, `junk_layer.pdf`) also flow end-to-end (dry-run verified) but ground
     0 fields — fine for the honesty/idempotency beats, wrong for the extraction beat.
6. **Terminal prep** (eval beat): a terminal sized for on-camera text with the repo at
   the demo SHA, ready to run `cd agent && python -m evals.w2_runner run --tier recorded`
   or to `cat` the two committed results files.
7. **Publishing decision is an owner action** (plan §4): approve the link's
   audience/access before it goes anywhere.

## Beat-by-beat (timestamps from W2_DEMO_SCRIPT.md)

| Time | Beat | Exact steps (verified 2026-07-19) | Say on camera | Status today |
|---:|---|---|---|---|
| 0:00–0:25 | Health + readiness | Open `https://agent-production-9f62.up.railway.app/health?cb=<unique>` then `/ready?cb=<unique>` in two tabs. | Read the SHA aloud (`6583079…`) — "one exact deployed SHA for web and worker". Name hard probes (openemr_fhir, anthropic, session_store, document_runtime, document_category_read) vs soft (langfuse, retrieval_index, active_reranker). | **WORKS** (`s01-beat0`) |
| 0:25–1:05 | Lab PDF upload → status → extraction → bbox click | `.../week2/launch` → OpenEMR sign-in (`admin` + OE_PASS) → select **Daron260 Windler79** → **Authorize** → workbench. Doc type **Lab PDF** → Browse → `golden/lab-clean-glucose.pdf` → **Upload and extract**. Status pill polls to `complete`; document id + `w2.<hex>` correlation id render under it; OpenEMR readback shows two `Verified byte-for-byte` sha256 digests. In **Lab trends** (or a grounded field's citation) click **Open page 1** → dialog "Uploaded document · page 1 · verified grounded field" with the value visibly boxed. | "Bounded status polling, then a byte-attested source and grounded artifact — digests re-read from OpenEMR, not echoed. Clicking the citation opens the exact page with the bounding box on the value." | **WORKS** (`s01-beat2`, `s01-beat3`; dry run verified upload path with `documents/clean.pdf` and bbox click against the existing lab-clean-glucose artifact; a golden lab re-upload dedupes to the same document id, which is fine on camera) |
| 1:05–1:40 | Intake double-upload idempotency | Doc type **Intake form** (note the "Write grounded intake vitals … agent never creates one" checkbox) → upload `golden/intake-full-valid.pdf` → complete → note document id. Upload the **same file again**: the second `POST /documents` returns immediately (dry run: HTTP 200 vs first-run 202), the **same document id** persists, and readback digests re-verify fresh. | "Same bytes → one permanent document identity, no duplicate writes; vitals are exactly-once; the digests you see were re-read after the second attempt." | **WORKS** (`s01-beat4`; dry-run used `documents/degraded.pdf`: id `4f8da660…` unchanged across both uploads, 6 UNSUPPORTED fields stayed redacted, allergy-honesty note "never treated as NKDA" rendered). Eligible-vitals write itself not exercised in dry run (geometry fixture has no vitals) — use `intake-full-valid.pdf` on camera |
| 1:40–2:10 | Medication list = source + artifact only | Doc type **Medication list** (badge "source + grounded artifact only") → upload a synthetic medication-list PDF → complete → readback digests verify; no MedicationRequest/vital write exists (UI copy states it; optionally show the OpenEMR chart unchanged). | "Third document type: stored and grounded, never written as medication orders — no MedicationRequest path exists." | **PARTIAL** (`s01-beat5`; mechanics verified with `documents/junk_layer.pdf` — complete, digests verified, 1 UNSUPPORTED redacted). No medication-list golden fixture exists yet — **R09** adds the golden cases + gate beat |
| 2:10–2:40 | Cited question | In **Cited answer** enter condition/test terms, e.g. `type 2 diabetes; glucose` → **Ask**. Answer renders: patient-record verified summary with citation chips, **uploaded document** chips (`results.0.value: 92` · page 1), **guideline** chips (VA/DoD). Click an uploaded-document chip → same bbox page preview. | "Three CitationV2 source classes — chart, uploaded document, guideline. A deterministic critic approved the composition before any bytes flushed" (critic runs server-side; no visible badge yet). | **WORKS** (`s01-beat6`, `s01-beat7`). Per-claim inline citations + any visible critic marker land with **R01** — today chips are grouped per source block |
| 2:40–3:05 | Lab trends 6.5 ≠ 65 | **Lab trends** panel: exact-unit charts ("exact unit · mg/dL"), click a point → page/bbox preview. For the 6.5-vs-65 contrast, upload `golden/lab-clean-hba1c-high.pdf` first so an HbA1c chart (unit %) sits beside Glucose (mg/dL). | "Exact units, no interpretation — 6.5 % can never be conflated with 65 mg/dL; every point clicks through to its verified page." | **PARTIAL** (mechanism + click-through verified with the single Glucose point; the HbA1c artifact must be uploaded at record time) |
| 3:05–3:35 | Eval aggregates | Terminal: `cat docs/week2/evidence/eval-results/results-tier1-6583079.json` (or run `python -m evals.w2_runner run --tier recorded`, ~11 s) and the tier-2 file. Show `source_sha` = the on-camera SHA, `status: PASS`, category arithmetic (schema_valid 50/50, citation_present 50/50, factually_consistent 23/23 vs 0.9 threshold, safe_refusal 10/10), tier-2 `cost_usd: 3.07` under the `10.0` ceiling, `retries: 0`. | "Real 50-case Tier-1 recorded gate plus the approved live Tier-2 aggregate — same SHA as the health probe, category thresholds, cost and retries explicit." | **WORKS** (committed evidence matches deployed SHA exactly) |
| 3:35–4:05 | Red gate | Open the committed drill links in `W2_CI_EVIDENCE.md`: a schema-mutation / incomplete-citation drill run (red `eval-tier1` job) then the post-drill green control on the canonical SHA. | "A deliberately mutated output goes red in the governed gate; removing the mutation restores green on the exact SHA." | **WORKS** via recorded CI links (live local mutation drill optional) |
| 4:05–4:35 | Correlation-ID walk | The workbench prints `document <uuid> · correlation w2.<hex>` for every job (dry run: `w2.41dc89c5…`, `w2.6cc19148…`, `w2.0cf8f555…`). Filter the Langfuse project by that correlation id and walk queue → OCR/VLM → grounding → retrieval → writes/readback → critic → terminal summary. | "One correlation id reconstructs the whole asynchronous path." | **PARTIAL** — id is visible in the UI (works); the trace walk needs owner Langfuse dashboard access (soft probe green; not verifiable in this dry run). **O01** packages the standalone trace evidence |
| 4:35–5:00 | Dashboard/alerts → healthy close | Week-2 dashboard/alerts view, then finish on `/ready?cb=<unique>` all-green. | "Scheduled `w2_alerts` evaluation and the dashboard, ending healthy on the same SHA." | Dashboard **BLOCKED by R05** (not deployed). Fallback that works today: close on `/ready` all-green + Langfuse overview |

## Re-record triggers

Track A records against today's SHA; re-record the affected beat (or the whole video if
the SHA changes) when:

- **R01** merges — per-claim citation contract changes the cited-answer beat (chips become
  claim-level; critic approval may become visible).
- **R09** merges — adds the medication-list golden-gate beat (≥2 golden cases green).
- **R05** merges — adds the real dashboard/alerts closing beat.
- **REL1** deploys — the on-camera SHA changes; the 0:00 beat (and the eval `source_sha`
  match) must be recaptured against the new SHA.

## Pre-publish checklist (owner)

1. **PHI/token scan:** step through every frame (and the transcript/captions if any) —
   synthetic names only (Synthea `Name###` pattern), no real-looking data, no
   `tmp/railway-secrets.env`, no password field contents, no `sid=`/bearer tokens, no
   provider prompts/responses, no raw document dumps beyond the synthetic fixtures
   (which carry the visible "SYNTHETIC TEST RECORD — NOT FOR CLINICAL USE" banner and
   canary sentence).
2. **Length check:** 3:00–5:00; all six PDF elements present (upload, extraction,
   evidence retrieval, citations with click-to-source bbox, eval results, observability).
3. **Access decision (owner):** choose the audience for the link; verify it opens from a
   logged-out/incognito context reviewers can use.
4. **Stable link:** add the link to `README.md` (Week-2/grader section) and to
   [`W2_EVIDENCE_INDEX.md`](W2_EVIDENCE_INDEX.md); D01 keeps both in sync at release.

## Dry-run defect notes (do not fix on camera)

- One retrieved guideline snippet rendered garbled reversed text
  ("…yparehtocamrahp-noN yparehT noitirtuN lacideM…") — a PDF-extraction artifact in the
  guideline corpus. Cosmetic; prefer question terms whose snippets render cleanly, or
  keep that chip off-screen.
- Intake (OCR path) processing ran ~60–90 s in the dry run; either upload early and
  narrate over the poll, or trim the wait in the edit.
