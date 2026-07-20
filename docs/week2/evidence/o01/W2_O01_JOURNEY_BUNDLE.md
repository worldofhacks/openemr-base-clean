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
