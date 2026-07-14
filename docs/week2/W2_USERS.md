# W2_USERS.md — Users, Flows, Traceability (Week 2: Multimodal Evidence Agent)

> New file per week-scoped convention. The user is CARRIED from W1 (D1: PCP with a
> 20-patient day) — the W2 PRD explicitly requires explaining "why each capability
> maps back to the Week 1 user and workflow." W1 USERS.md (root) remains the user
> contract; this file adds the W2 use cases and their traceability.

## The user (carried, not re-chosen)

Primary-care physician, outpatient clinic, ~20 patients/day, 90-second gaps between
rooms (D1, W1 USERS.md). Week 2 moment: **the follow-up visit** where the chart's
structured data is stale and the important recent information is buried in two
uploaded documents — a scanned lab PDF and a front-desk intake form.

> **Revision (2026-07-13, /arch-finalize — owner decision):** the front-desk staff /
> medical assistant is **document provenance, not an agent principal**. The PRD's
> "uploaded by the front desk" describes how the paper arrived; the agent's upload
> surface accepts only the W1 authenticated pinned SMART session (in the demo, the
> physician uploads). A front-desk-authenticated path is an explicit W2 non-goal
> (binding W2_ARCHITECTURE.md §1/§2a). UC actor lists below read accordingly: the
> uploader is whoever holds the pinned session.

## Use cases

### UC-W2-1 — Ingest a scanned lab PDF
- **Actor:** front-desk staff or the physician. **Trigger:** an outside lab result
  arrives as a scanned PDF.
- **Steps:** upload from the chart → `POST /documents` (patient-bound) → source
  stored in OpenEMR → async extraction job: text-layer/OCR read → VLM proposes
  `LabPdfExtraction` fields → Pydantic validation → per-field grounding → append-only
  writeback with lineage → status complete.
- **Success:** every lab value visible as a grounded field with citation + page/bbox;
  derived records queryable in OpenEMR; duplicate re-upload creates nothing.
- **Failure states:** degraded scan → affected fields render UNSUPPORTED ("verify
  against source document"), never guessed; schema violation → extraction failed
  status, explicit message; storage failure → job failed, source retained, idempotent
  retry.
- **Data touched:** source PDF (OpenEMR), words+boxes layer (ephemeral), extraction
  artifact + vitals records (OpenEMR, lineage-linked).
- **Why an agent:** unstructured pixels → verified structured facts requires
  vision + judgment; the deterministic grounding gate is what makes the judgment
  safe to persist.

### UC-W2-2 — Ingest a patient intake form
Same shape as UC-W2-1 with `IntakeFormExtraction` (demographics, chief concern,
current medications, allergies, family history). Distinct failure emphasis: allergy
and medication fields inherit W1's absence discipline — an empty allergy section
renders "no allergy information captured on this form; confirm with patient," never
"no known allergies" (W1 F-D.5 rule carried).

### UC-W2-3 — Grounded follow-up-visit answer
- **Actor:** physician. **Trigger:** "What changed? What should I pay attention to?
  What evidence supports the recommendation?"
- **Steps:** question → LangGraph supervisor decides per turn → chart facts (W1
  EvidencePacket), document facts (extracted, grounded), guideline evidence
  (hybrid retrieval + rerank over the VA/DoD corpus) → composer merges with patient
  facts and guideline evidence as visually distinct classes → every claim carries
  CitationV2; document claims render the bbox overlay → verify-then-flush.
- **Success:** an answer the physician can trust and click through — every clinical
  claim resolves to its source; guideline citations quote the actual CPG text.
- **Failure states:** no guideline evidence retrieved → stated explicitly, never
  invented; ambiguous question → W1 canonical refusal; VLM/LLM down → D13-style
  deterministic degradation (facts, no synthesis, banner); reranker down →
  un-reranked hybrid scores, flagged degraded.
- **Data touched:** read: EvidencePacket, extraction artifacts, guideline chunks.
  Written: none (answers never write).
- **Why an agent:** the routing judgment (which sources this question needs) and the
  synthesis are model work; every fact it emits is deterministically checked.

### UC-W2-4 — Follow-up continuity
- **Actor:** physician, same session. **Trigger:** any follow-up ("show me the
  potassium trend," "what does the guideline say about that?").
- **Steps:** session context + prior grounding persist; supervisor may re-route to
  the retriever without re-extracting; citations remain live.
- **Success:** grounding never degrades across turns; the second answer is as cited
  as the first. **Failure:** session expiry → explicit re-launch prompt (W1
  behavior); context overflow → bounded evidence selection, truncation named.
- **Why an agent:** multi-turn state + selective re-retrieval is the agent shape;
  the alternative (re-running the full pipeline per question) is cost and latency.

## Non-happy and ops flows (production-grade posture)

- **Degraded-scan day:** a batch of poor scans yields many UNSUPPORTED fields — the
  system stays honest and slow-path work goes to the physician as flagged fields,
  not invented values. Alert: extraction failure rate.
- **Dependency loss:** reranker or VLM outage → degraded modes above; /ready reports
  degraded per dependency; breaker short-circuits repeated failures; runbook entry
  per alert (docs/observability/runbooks.md, W2 additions).
- **Deploy/rollback:** push → CI (lint, types, tests, contracts, PHI check, eval
  gate) → deploy on green; rollback = Railway one-click previous deployment; a >5%
  eval-category regression blocks before deploy and alerts if seen live.
- **Duplicate/replay:** re-upload of any document is idempotent (content hash) —
  operational recovery is "re-run ingestion," always safe.

## Capability → use case → decision traceability

| Capability | UC | Decisions |
|---|---|---|
| attach_and_extract (upload, store, hash) | UC-W2-1/2 | W2-D1, W2-D3 |
| Text-layer/OCR words+boxes read | UC-W2-1/2 | W2-D3 |
| Pydantic extraction schemas + validation tests | UC-W2-1/2 | W2-D3, PRD req 2 |
| Per-field grounding + UNSUPPORTED render | UC-W2-1/2 | W2-D3 |
| Append-only writeback with lineage | UC-W2-1/2 | W2-D1, W2-R5 |
| LangGraph supervisor + logged handoffs | UC-W2-3/4 | W2-D2 |
| Hybrid retrieval + Cohere rerank over VA/DoD corpus | UC-W2-3/4 | W2-D4, W2-R2/R3 |
| CitationV2 + source-class separation + bbox overlay | UC-W2-1/2/3 | W2-D6, W2-D3 |
| 50-case boolean eval gate + PR-blocking hook | all (quality gate) | W2-D5 |
| PHI-free logging + CI PHI check | all (compliance) | W2-D7 |
| Session continuity (W1 store) | UC-W2-4 | W1 D-O2 carried |

Rule enforced: every capability above traces to a UC; anything without a row does
not get built (stretch items are cut entries, not capabilities).
