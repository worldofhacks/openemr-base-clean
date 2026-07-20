# W2 O01 — Production Journey Evidence Bundle

- **Task:** O01 (AF-P1-01) — prove the production journey on the release SHA.
- **Deployed base URL:** `https://agent-production-9f62.up.railway.app`
- **Release SHA (expected):** `293f18bb9a8203af58c0159f02e218e74ee1edd1`
- **Run started (UTC):** 2026-07-19T23:57:50Z
- **Patient (synthetic only):** Daron260 Windler79 (Synthea, DOB 1964-05-12)
- **Fixtures:** `agent/evals/fixtures/` only (synthetic; carry the "SYNTHETIC TEST RECORD" banner)
- **Written incrementally** — each step appends its section immediately after execution.

## Step 0 — Health + readiness probes (pre-browser)

Cache-buster `cb=1784505470-o01`, probed 2026-07-19T23:57:50Z UTC.

`GET /health?cb=1784505470-o01`:

```json
{"status":"alive","sha":"293f18bb9a8203af58c0159f02e218e74ee1edd1"}
```

**SHA match: YES** — deployed SHA equals the release SHA exactly.

`GET /ready?cb=1784505470-o01` — status `ready`, **9/9 checks ok** (all-green, including the new `graph_state` probe):

```json
{"status":"ready","checks":[
 {"name":"openemr_fhir","kind":"hard","ok":true,"detail":"HTTP 200"},
 {"name":"anthropic","kind":"hard","ok":true,"detail":"HTTP 200"},
 {"name":"session_store","kind":"hard","ok":true,"detail":"ok"},
 {"name":"langfuse","kind":"soft","ok":true,"detail":"HTTP 200"},
 {"name":"retrieval_index","kind":"soft","ok":true,"detail":"ok"},
 {"name":"active_reranker","kind":"soft","ok":true,"detail":"ok"},
 {"name":"document_runtime","kind":"hard","ok":true,"detail":"ready"},
 {"name":"document_category_read","kind":"hard","ok":true,"detail":"authorized_read_ok"},
 {"name":"graph_state","kind":"soft","ok":true,"detail":"graph_enabled"}
]}
```

**Verdict: WORKS.**

> **Note on prior partial attempt:** an earlier run the same evening (23:33–23:54 UTC, same
> deployment) crashed before writing this bundle but left screenshots `o01-01-*` … `o01-09-*`
> and `o01-chat-claims-structure.sanitized.json` in this directory. They are retained as
> supplementary artifacts; the authoritative journey below was re-executed fresh, with files
> named `o01-step*`.

## Step 1 — SMART launch → sign-in → authorize → workbench

- `GET /week2/launch` redirected to the OpenEMR authorization server
  (`openemr-production-cc95.up.railway.app/oauth2/default/provider/login`).
- Signed in as `admin`; the password was verified against `OE_PASS` by SHA-256 digest
  comparison only (digest `29e0067f…851e7` matched on both sides; the secret itself was never
  echoed to any log or file). **Sign-in succeeded on attempt 1 of the allowed 2.**
- SMART patient-select listed synthetic patients; selected **Daron260 Windler79**
  (1964-05-12, Male) — the only patient touched in this run.
- Authorization completed and the browser landed on the workbench
  (`/week2?sid=<redacted session token>`), title "Week 2 Document Write · Clinical Co-Pilot".
- Workbench header confirms: **Pinned OpenEMR chart `a234b772…0155`** (matches the recording
  kit's pinned badge for Daron260 Windler79), "source + grounded artifact only",
  "machine-authored · pending clinical review".
- Operational note: the local Playwright browser crashed twice mid-flow (tooling instability,
  not the app); on relaunch the OAuth flow completed cleanly. One extra sign-in was required
  after a browser restart (fresh OAuth launch, not a failed attempt).

Screenshot: `o01-step1-workbench.jpeg`

**Verdict: WORKS.**

## Step 2 — Lab PDF: upload → status → extraction → readback → citation → bbox preview

Fixture: `agent/evals/fixtures/golden/lab-clean-hba1c-high.pdf` (Doc type **Lab PDF**).

- **Status pill** polled to **complete**; summary "**6 grounded / 0 Unsupported**".
- **Document id:** `49bf83f3-794a-4af3-ba96-938a2f5fb6b6`
- **Correlation id:** `w2.23278219a6f02daf83305170e92ccf85`
- **OpenEMR readback** (fresh Binary digest attestation, re-read from OpenEMR — not echoed):
  - Source document — **Verified byte-for-byte** — `sha256 · c58142bd7530…`
  - Grounded artifact — **Verified byte-for-byte** — `sha256 · 103823e31309…`
- **Extraction report** grounded fields (each with an "uploaded document · page 1" citation chip):
  - result 0 · test name = **Hemoglobin A1c**
  - result 0 · value = **8.1**
  - result 0 · unit = **%**
  - result 0 · reference range = **4.0-5.6**
  - result 0 · abnormal flag = **H**
  - result 0 · collection date = **2026-06-02**
- **Lab trends** now shows the HbA1c chart (**exact unit · %**, 8.1 % @ 2026-06-02) sitting
  beside Glucose (**exact unit · mg/dL**, 92 mg/dL @ 2026-06-01) and lipid panel entries —
  the 6.5-vs-65 unit-safety beat is satisfied (% never conflated with mg/dL).
- **Citation click:** clicked the "uploaded document · page 1" chip on result 0 · value (8.1).
  A **"Source page"** dialog opened: header "**Uploaded document · page 1 · verified grounded
  field**" with the source-page image rendered and the value region boxed.

Digest pair recorded: source `c58142bd7530…` / artifact `103823e31309…`.

Screenshots: `o01-step2a-lab-extract-readback.jpeg`, `o01-step2b-citation-bbox.jpeg`

**Verdict: WORKS.**

## Step 3 — Intake PNG double-upload (idempotency / REQ-96 positive)

Fixture: `agent/evals/fixtures/golden/intake-full-valid.png` (Doc type **Intake form**,
"Write grounded intake vitals" checkbox was **checked** by default).

- **Upload 1:** `POST /documents` → **HTTP 200**, document `c592d7b6-8801-46ad-8202-d0201c632691`,
  correlation `w2.e46ddda6e8359ff5def824ed49efd180`.
- **Upload 2 (same bytes):** `POST /documents` → **HTTP 200**, **same document id**
  `c592d7b6-8801-46ad-8202-d0201c632691`, **same correlation id**. No second document identity
  was created.
- Network confirmation (browser devtools): two distinct `POST /documents` calls (requests #10
  and #12) both returned **200** and both resolved to the identical document id.

**Idempotency verdict: WORKS at the document-identity / upload layer** — same bytes yield one
permanent document id across both attempts (and, since this fixture was also uploaded in the
prior run, across sessions), with no duplicate write.

**PRODUCTION DEFECT (reported, not fixed):** the intake **image (OCR/VLM) path fails** —
status pill = **failed**, banner "**Processing stopped: vlm_unavailable**", 0 grounded /
0 unsupported, a **Retry failed job** button is offered. Because extraction never completed,
no fresh grounded-artifact digest was produced for the intake document (the readback panel
still shows the step-2 lab digests `c58142bd…` / `103823e3…`, which are stale w.r.t. this
document — a UI carryover). Consequently the "fresh digests re-verify on second upload" half
of the beat could **not** be demonstrated for intake. The `/ready` probe reports `anthropic`
hard-OK and `active_reranker` soft-OK, but there is **no dedicated VLM readiness probe**, so
this failure is invisible to readiness. This is the same `vlm_unavailable` condition the prior
run captured (`o01-04-intake-vlm-unavailable.jpeg`) — reproducible.

Note: no induced write-timeouts were run against production OpenEMR (per plan). Crash-safe
exactly-once behavior is covered by the intent-ledger test suite (see REQ-96 note at end).

Screenshot: `o01-step3-intake-idempotency-vlm-fail.jpeg`

**Verdict: PARTIAL — upload idempotency proven (200/200, same id); intake extraction failed
(`vlm_unavailable`), so grounded-vitals write + fresh-digest re-verify not demonstrable.**

## Step 4 — Medication list (third type, source + grounded artifact only)

Fixture: `agent/evals/fixtures/golden/med-list-clean-grounded.pdf` (present in repo).
Doc type **Medication list**; UI copy: "Medication list: source + grounded artifact only.
No MedicationRequest or vital record is created."

- **Status:** complete; **7 grounded / 0 Unsupported**.
- **Document id:** `ec713543-1194-4fa2-9c7e-1da64d7bdb53`
- **Correlation id:** `w2.710d40a49af595a8d8687a3b9fcfc193`
- **OpenEMR readback:** Source document **Verified byte-for-byte** `sha256 · 253d60acc695…`;
  Grounded artifact **Verified byte-for-byte** `sha256 · ade03b609205…`.
- **Extraction report** (each field carries an "uploaded document · page 1" citation):
  medication name **Synthetic Multivitamin**, strength **500 mg**, dose **1 tablet**,
  route **oral**, frequency **once daily**, status **active** (+ 7th grounded field).
- **No MedicationRequest / vital write** — artifact-only path, as the UI states. This closes
  the recording-kit `s01-beat5` PARTIAL (which had no medication golden fixture); the golden
  case now exists and flows end-to-end.

Screenshot: `o01-step4-medlist-artifact-only.jpeg`

**Verdict: WORKS.**

## Step 5 — Cited question "type 2 diabetes; glucose" (R01 claims[] lane / AF-P0-03 deployed half)

Question submitted in **Cited answer** against synthetic patient Daron260 Windler79. The live
UI answer rendered a verified summary with citation chips grouped into **three CitationV2
source classes**: **patient record**, **uploaded document**, and **VA/DoD guideline**.

**Network / JSON body evidence (captured via browser fetch of `POST /chat`, same endpoint the
UI uses; sanitized structure saved to `o01-step5-chat-claims-structure.sanitized.json`):**

- Response is `application/json`. On a **successfully composed** answer (`source:"llm"`), the
  top-level keys are `brief, source, degraded, verdicts, citations, **claims**, patient,
  correlation_id`.
- **`claims[]` IS PRESENT** (R01 per-claim lane is LIVE in production). Example answer:
  **22 claims**, each claim shaped `{ text, source_class, verdict, citations[], overlay }`.
  - **Every claim carried ≥1 per-claim CitationV2** (per-claim citation counts were `[1]×22`).
  - CitationV2 shape: `source_type, source_id, page_or_section, field_or_chunk_id,
    quote_or_value`.
  - Each claim also carries a per-claim **`overlay`** with `page` + normalized `bbox`
    `{x0,y0,x1,y1}` — the per-claim click-to-source geometry.
  - claim0 example: `source_class:"uploaded_document"`, `verdict:"pass"`,
    citation → `source_id 5754ac23…`, `field_or_chunk_id:"chief_concern"`, page 1.
- The flat top-level `citations[]` (grouped, what the current UI chips render from) carried
  **35** CitationV2 objects: patient_record 6, uploaded_document 25, guideline 4.
- **Correlation ids** for captured answers: `ec568f017da744c3ad2d7c3186b029a9` (35 citations),
  `6de4e50d80d5483b938f68f6d01015bd` (22-claim capture).

**Nondeterminism / safety note (honest):** the identical question sometimes returns a
**deterministic critic refusal** (`source:"deterministic_refusal"`,
`verdicts:["refused:critic_rejected"]`, `citations:[]`, and **no `claims` key**) instead of a
composed answer. This is a *safe* failure — nothing ungrounded is flushed — but it means the
claims[] lane is only present on the pass path. The UI beat itself rendered a full answer.

**AF-P0-03 deployed-half verdict: CONFIRMED** — per-claim `claims[]` with per-claim CitationV2
and per-claim overlay bbox is live on the deployed SHA.

**Citation click → bbox:** clicked the uploaded-document chip "results.0.value: 92" in the
answer; the **Source page** dialog opened ("Uploaded document · page 1 · verified grounded
field") with the page image and boxed value.

Screenshots: `o01-step5a-cited-answer.jpeg`, `o01-step5b-answer-chip-bbox.jpeg`
JSON: `o01-step5-chat-claims-structure.sanitized.json`

**Verdict: WORKS** (claims[] lane live; UI chips still grouped-per-source, not yet inline
per-claim — consistent with recording-kit note that inline per-claim chips are an R01 UI item).

## Step 6 — Follow-up question + missing-data question (safe behavior)

Both asked in the same **Cited answer** panel, same session/patient.

- **Follow-up:** `hypertension; blood pressure` → **safe deterministic refusal**. UI rendered a
  "patient record" card: *"This request cannot be served automatically; please review the
  chart manually."* No ungrounded claim was flushed.
- **Missing-data:** `thyroid disorder; TSH` → **honest no-data behavior** (captured JSON):
  - `source: "deterministic_refusal"`, `verdicts: ["refused:no_claim"]`, `citations: []`,
    no `claims` key, HTTP 200.
  - brief: *"No verified evidence matched this question. Ask about a condition or test, for
    example Magnesium."*
  - correlation id `858a1e88ef7d4c10bc0648ea20b2fc2d`.
  - No fabrication, no invented values — the system states the absence of evidence rather than
    guessing. This is the required REQ-safe-refusal / missing-data behavior.

Screenshot: `o01-step6-followup-missingdata-safe.jpeg` (shows the missing-data no-evidence card).

**Verdict: WORKS** (safe refusal + honest no-data, both fail safe).

## Step 7 — Negative identity / mismatched-session cases (curl, no browser)

All requests to `https://agent-production-9f62.up.railway.app`. No secrets recorded — a valid
session token, where used, is shown as `<valid-session-redacted>`; the bogus token is a
non-secret literal.

| # | Request (sanitized) | Result | Fail-safe? |
|---|---|---|---|
| A | `GET /documents/49bf83f3…/status?session_id=bogus_session_token_…` (real doc + bogus session) | **404** `{"detail":"session not found"}` | YES |
| B | `GET /documents/00000000-…-000000000000/status?session_id=bogus…` (bogus doc + bogus session) | **404** `{"detail":"session not found"}` | YES |
| C | `GET /documents/49bf83f3…/pages/1?session_id=bogus…` (real doc + bogus session) | **404** `{"detail":"session not found"}` | YES |
| D | `GET /documents/49bf83f3…/status` (no session_id param) | **422** `{"detail":[{"type":"missing",..."session_id"...}]}` | YES |
| E | `GET /documents/49bf83f3…/extraction-report?session_id=bogus…` | **404** `{"detail":"session not found"}` | YES |
| F | `GET /documents/00000000-…/status?session_id=<valid-session-redacted>` (valid session + bogus doc) | **500** `Internal Server Error` | **NO — defect** |
| G | `GET /documents/00000000-…/pages/1?session_id=<valid-session-redacted>` (valid session + bogus doc) | **404** `{"detail":"page not found"}` | YES |

- Session is validated **before** document lookup: a bogus/absent session yields `404 session
  not found` (or `422` when the param is missing) and never reveals whether the document
  exists or to whom it belongs — no cross-patient existence leak.
- **PRODUCTION DEFECT (reported, not fixed):** case F — the **`/status`** endpoint returns
  **HTTP 500 (generic "Internal Server Error")** for an *unknown document id on a valid
  session*, instead of a clean `404`. It is not a data leak (generic body, no content), but it
  is not a clean fail-safe 4xx. The sibling **`/pages`** endpoint handles the same unknown id
  correctly with `404 page not found` (case G), so the fix is localized to the `/status`
  handler's not-found path.

**Verdict: WORKS (mostly)** — all mismatched/bogus-session requests fail safe with 4xx and no
leak; one `/status` not-found path returns 500 rather than 404 (defect logged).

## Step 8 — R08 beat: degraded scan → visible-unverified fields, zero ungrounded writes

Fixture: `agent/evals/fixtures/golden/lab-degraded-scan-unreadable-value.pdf` (Doc type **Lab PDF**).

- **Status:** complete; **5 grounded / 1 UNSUPPORTED**.
- **Document id:** `4a240b48-037f-469f-a7ef-a7d25e1bfd76`
- **Correlation id:** `w2.111002f4a74acd388f67d4dffc9bb97a`
- **Banner:** *"1 field(s) are UNSUPPORTED and were not written as facts."*
- **Extraction report:** the unreadable field renders **visibly but unverified** —
  `result 0 · value` = **"UNSUPPORTED — verify against source document"** (no fabricated value),
  while legible neighbors ground normally (`result 0 · unit = U/L`, plus a page-1 citation on
  the grounded fields). The UNSUPPORTED value carries **no citation chip** and **no clinical
  write** — extraction persisted, but the ungrounded field was not written as a fact.
- **OpenEMR readback:** Source **Verified byte-for-byte** `sha256 · 2b8a082917f4…`; Grounded
  artifact **Verified byte-for-byte** `sha256 · 91a5d3614f2b…` (the artifact stores what was
  actually grounded; the unreadable value is excluded).
- This satisfies R08: **degraded input → extraction still persists, ungrounded/unreadable
  fields are surfaced as visible-unverified and produce zero clinical writes.**

Screenshot: `o01-step8-degraded-unsupported.jpeg`

**Verdict: WORKS.**

---

## Per-step correlation-ID table (for joining to the exported event-lane chains)

Deployed SHA `293f18bb9a8203af58c0159f02e218e74ee1edd1`; patient Daron260 Windler79
(`patient_id a234b772-e1b4-4944-90f1-90d01f440155`, pinned chart badge `a234b772…0155`).
The single authenticated session (token redacted) carried every step below.

| Step | Action | Document id | Correlation id | Digest pair (source / artifact) |
|---|---|---|---|---|
| 2 | Lab PDF `lab-clean-hba1c-high.pdf` | `49bf83f3-794a-4af3-ba96-938a2f5fb6b6` | `w2.23278219a6f02daf83305170e92ccf85` | `c58142bd7530…` / `103823e31309…` |
| 3 | Intake `intake-full-valid.png` upload #1 | `c592d7b6-8801-46ad-8202-d0201c632691` | `w2.e46ddda6e8359ff5def824ed49efd180` | (extraction failed — vlm_unavailable) |
| 3 | Intake `intake-full-valid.png` upload #2 (dedup) | `c592d7b6-8801-46ad-8202-d0201c632691` (same) | `w2.e46ddda6e8359ff5def824ed49efd180` (same) | — |
| 4 | Medication list `med-list-clean-grounded.pdf` | `ec713543-1194-4fa2-9c7e-1da64d7bdb53` | `w2.710d40a49af595a8d8687a3b9fcfc193` | `253d60acc695…` / `ade03b609205…` |
| 5 | Cited answer "type 2 diabetes; glucose" (35-citation capture) | — | `ec568f017da744c3ad2d7c3186b029a9` | — |
| 5 | Cited answer "type 2 diabetes; glucose" (22-claim capture) | — | `6de4e50d80d5483b938f68f6d01015bd` | — |
| 6 | Missing-data "thyroid disorder; TSH" (refused:no_claim) | — | `858a1e88ef7d4c10bc0648ea20b2fc2d` | — |
| 8 | Degraded `lab-degraded-scan-unreadable-value.pdf` | `4a240b48-037f-469f-a7ef-a7d25e1bfd76` | `w2.111002f4a74acd388f67d4dffc9bb97a` | `2b8a082917f4…` / `91a5d3614f2b…` |

Notes: Step 6 follow-up ("hypertension; blood pressure") returned a deterministic-refusal card
without a surfaced correlation id in the UI. Cited-answer (`/chat`) correlation ids are the
hex form (no `w2.` prefix); document-job correlation ids carry the `w2.` prefix.

## REQ-96 (exactly-once) justification note

Per the plan, **no write-timeouts were induced against the production OpenEMR instance.** The
deployed positive evidence gathered here is: (a) the duplicate intake upload returned **HTTP
200 on both attempts with the identical document id** (`c592d7b6…`) and identical correlation
id — i.e., same bytes → one permanent document identity, no second document/write (Step 3, with
network confirmation of two `POST /documents` → 200/200); and (b) the `readback-verification`
endpoint re-reads **byte-for-byte sha256 digests from OpenEMR** for every completed document
(Steps 2, 4, 8), confirming the stored source and grounded artifact rather than echoing
request bytes. The **crash-safe timeout/reconcile half** of exactly-once (write intent ledger,
reconcile-on-restart) is deliberately **cited from the intent-ledger test suite** rather than
exercised against production. The Daron260 intake extraction could not complete on the deployed
SHA (`vlm_unavailable`, Step 3), so the eligible-vitals write itself was not demonstrated live;
idempotency was proven at the document-identity layer instead.

## Langfuse trace half — owner action

This bundle captures the client-visible correlation ids for each step so the orchestrator can
join them to the separately-exported event-lane chains. The **Langfuse dashboard walk**
(supervisor → worker → critic hops rendered in the Langfuse UI, filtered by these correlation
ids) requires **owner Langfuse project access** and is an **owner action** — not performable
from this browser-driven run. The `/ready` `langfuse` soft probe was green at run time.

## Honest per-step verdict table

| Step | What it proves | Verdict |
|---|---|---|
| 0 | `/health` SHA = release SHA; `/ready` 9/9 green incl. `graph_state` | **WORKS** |
| 1 | SMART launch → sign-in (attempt 1) → authorize → workbench; pinned synthetic patient | **WORKS** |
| 2 | Lab PDF: status→complete, extraction, dual verified digests, citation→bbox preview, unit-safe trends | **WORKS** |
| 3 | Intake double-upload idempotency (same doc id, 200/200) | **PARTIAL** — upload idempotency proven; intake **extraction failed (`vlm_unavailable`)**, so grounded-vitals write + fresh-digest re-verify not shown |
| 4 | Medication list = source + grounded artifact only, no MedicationRequest | **WORKS** |
| 5 | Cited answer; **R01 `claims[]` lane live** with per-claim CitationV2 + overlay bbox (AF-P0-03 deployed half); chip→bbox | **WORKS** (UI chips grouped-per-source; inline per-claim is an R01 UI item) |
| 6 | Follow-up safe refusal + missing-data honest no-evidence | **WORKS** |
| 7 | Negative identity/mismatched-session cases fail safe | **PARTIAL** — all bogus-session cases 4xx/no-leak; **`/status` returns 500 (not 404) for unknown doc id on a valid session** (defect logged) |
| 8 | R08 degraded scan → visible-unverified UNSUPPORTED field, zero ungrounded writes | **WORKS** |

### Production defects found (reported, NOT fixed here)

1. **Intake image (VLM/OCR) path fails on the deployed SHA** — `Processing stopped:
   vlm_unavailable`; 0 grounded, `Retry failed job` offered. No dedicated VLM readiness probe,
   so `/ready` stays green. Reproducible (matches prior run's `o01-04`). Blocks the live
   intake-vitals write demonstration.
2. **`GET /documents/{id}/status` returns HTTP 500** for an unknown document id on a valid
   session (should be 404; the `/pages` sibling correctly 404s). Not a data leak, but not a
   clean fail-safe.

### Evidence inventory (this directory)

- `W2_O01_JOURNEY_BUNDLE.md` (this file)
- `o01-step5-chat-claims-structure.sanitized.json` — sanitized `/chat` claims[] structure
- Fresh screenshots: `o01-step1-workbench`, `o01-step2a-lab-extract-readback`,
  `o01-step2b-citation-bbox`, `o01-step3-intake-idempotency-vlm-fail`,
  `o01-step4-medlist-artifact-only`, `o01-step5a-cited-answer`, `o01-step5b-answer-chip-bbox`,
  `o01-step6-followup-missingdata-safe`, `o01-step8-degraded-unsupported` (9 total, ≤10 limit)
- Supplementary (prior crashed run, retained): `o01-01…o01-09-*.jpeg`,
  `o01-chat-claims-structure.sanitized.json`

**All data synthetic (Daron260 Windler79). No secrets recorded. No writes outside the synthetic patient.**
