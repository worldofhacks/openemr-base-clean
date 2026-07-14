# W2_gap-audit.md — /arch-finalize coverage table & findings register (Week 2)

> Produced 2026-07-13 by the adversarial /arch-finalize pass over
> `docs/week2/W2_ARCHITECTURE_DRAFT.md` + all W2 planning artifacts, judged against
> `docs/week2/Week_2_AgentForge.pdf` (ground truth) at **production-grade** posture.
> Method: multi-agent workflow (53 agents) — seven dimension auditors (flows/lifecycle,
> failure/deploy/observability, interfaces/authority, research/consistency, scope/trust,
> testing/evals, completeness critic) plus an **independent PRD coverage re-derivation**
> (the draft's §10 matrix was not trusted or copied); every critical/important finding and
> every non-covered coverage row was adversarially re-verified by a second agent
> instructed to refute it. Outcome: 3 critical, 31 important, 2 owner questions (4 asked
> in total), 3 downgraded, 1 refuted, 29 minor. All critical/important findings were
> resolved with the owner on 2026-07-13 and are folded into the binding repo-root
> **`W2_ARCHITECTURE.md`**. Section references below (§N) resolve identically in the
> draft and the binding doc (stable anchors).

## Verdict

The draft was structurally sound — no locked decision was invalidated — but the eval gate
was **not buildable as specified** (3 critical findings), the async ingestion flow had
undesigned states (durability, write principal, partial writes), and the bbox overlay was
an unowned PHI surface. After resolution: **99 PRD requirements re-derived, 94 covered,
5 out-of-scope (stretch tier, PRD-sanctioned), 0 uncovered, 0 blank cells.**

## Owner decisions at the gate (2026-07-13)

| # | Question | Decision |
|---|---|---|
| 1 | May the graded CI eval gate make live Anthropic calls? | **Yes — W2-D8 (owner-recorded ADR):** Tier 1 offline — every PR + local hook (unit, integration-on-stubs, deterministic rubric subset; the PRD's no-live-API clause satisfied verbatim). Tier 2, the graded gate itself, is a **PR-blocking GH Actions job running the full 50 cases against live Anthropic** (real turns + pinned judge). The initial gate selection (offline PR / live-on-merge) was refined by the owner's hand-written ADR to make the live gate itself PR-blocking |
| 2 | Reranker: Cohere (locked W2-D4) vs W2-R3's local-primary recommendation | **Cohere + dated trigger** (W2-D4 rev): `RERANKER=cohere\|local` seam; mxbai implemented + tested as shipping fallback; if the production `COHERE_API_KEY` is not in Railway by **Mon 2026-07-14 EOD**, MVP ships local |
| 3 | Front-desk/MA upload actor with no designed auth path | **Narrative-only**: "uploaded by the front desk" is document provenance, not an agent principal; upload rides the pinned SMART session; W2_USERS revised (dated) |
| 4 | Async extraction job durability + write principal | **Durable Postgres job rows** + boot reconciliation; job writes under the uploader's delegated token via the persisted session store (W1 token-persistence debt pulled into MVP) |

## Findings register

Every finding below was independently verified (verifier tried to refute it) before
resolution. "Resolved →" points into the binding `W2_ARCHITECTURE.md`.

### Critical (3) — all in the eval gate (the graded hard gate)

| # | Finding | Resolved → |
|---|---|---|
| C1 | Regression-gate arithmetic unimplementable: no per-category thresholds, no committed baseline artifact, no case allocation (at ≤20 cases/category one flip is ≥5% — effectively zero-tolerance, unstated) | §7: threshold table (no_phi + safe_refusal 100%, others ≥90%), committed `agent/evals/w2_baseline.json` updated only by explicit PR step, case-allocation table, zero-tolerance stated plainly |
| C2 | Fully-stubbed gate is blind to prompt/model-config regressions — the exact class graders can inject; defense-prep's own regression #3 would NOT be caught as claimed | §7 two-tier gate (W2-D8): Tier 2 — the full 50-case **live-Anthropic run — is itself PR-blocking** in GH Actions, so prompt/behavior regressions fail CI behaviorally; regression-#3 story corrected honestly (templater rule → deterministic catch; genuine prompt drift → Tier-2 live catch) |
| C3 | Internal contradiction: "no live APIs" in the same gate that requires a pinned LLM judge; the "two judged checks" never named; factually_consistent not buildable | §7: deterministic/judged split named — factually_consistent free-text is the **single** judged check; `judge_config.yaml` committed (pinned model, temp 0, boolean prompts quoting evidence spans); judge runs Tier-2 only; fail-closed flake policy |

### Important (31) — grouped by theme

| # | Finding | Resolved → |
|---|---|---|
| I1 | Extraction-job state in-process only; restart/deploy orphans jobs; status endpoint behavior undefined | §3 durable job rows + boot reconciliation → failed(worker_restart), retriable; §5 row; queue depth from durable rows |
| I2 | Async job's writeback credentials undesigned (job outlives the ~1h delegated token; client_credentials forbidden) | §3 write principal: uploader's delegated token via persisted session store; refresh grant; failed(auth_expired) terminal; W1 debt #1 pulled into MVP |
| I3 | EHR write failure after successful extraction undesigned; two-leg partial-write states; vitals retry idempotency (vitals POST has server-assigned IDs) | §3 write ordering + per-leg state + write ledger keyed (content_hash, field_id); §5 rows incl. 401-scope (W2-F4 runbook) |
| I4 | Idempotency enforcement point undesigned: concurrent duplicate uploads race check-then-create | §3 atomic insert-or-return on UNIQUE(patient_id, content_hash); §5 row; concurrent-duplicate integration test (§7a) |
| I5 | UC-W2-4 graph-state vs session-state boundary undesigned (checkpointer = a new un-inventoried PHI store) | §2 graph-state lifecycle: per-turn ephemeral, rebuilt from session row + persisted artifacts (refs not values); **no LangGraph checkpointer**; §4a row |
| I6 | Upload-flow actor contradiction (front-desk actor vs physician-only SMART launch) | Owner decision 3; §1 non-goals + §3; W2_USERS revised (dated) |
| I7 | No W2 extension of W1 §3a lifecycle/retention table; words+boxes layer "ephemeral" with no location/lifetime/purge rule | §3a table: one row per new stateful entity, PHI stores marked |
| I8 | Tesseract/OCR process failure (crash, hang on huge/adversarial page) undesigned | §5 row: per-page subprocess timeout → UNSUPPORTED page; all-fail → failed(ocr_failed); `doc.ocr.failed` event; caps rejected pre-queue |
| I9 | Vector index missing/corrupt at boot undefined; "retrieval returns nothing" conflates no-evidence with retrieval-down (a false statement to the physician) | §5/§6: image-build index + startup integrity check (manifest hash); split rows — empty hit ≠ retrieval_unavailable; BM25-only degraded leg |
| I10 | Supervisor routing error has no runtime recovery; no graph step/recursion budget anywhere | §2 step budget (recursion limit, working value 8); §5: retry-once-then-canonical-refusal; step_budget_exceeded terminal handoff |
| I11 | No canonical coordinate space for bounding boxes — the two reading paths emit incompatible geometries | §2 NormBBox: normalized page-relative [0,1], origin top-left; conversion rules per path; DPI recorded; dual-path unit fixture |
| I12 | Page-image serving contract missing — contradicts "document images live in OpenEMR only" | §2a `GET /documents/{id}/pages/{n}`: pinned session + patient match, on-demand render, bounded in-memory TTL cache, never disk/logged; W2-D7 rev |
| I13 | Upload endpoint has no typed request contract (MIME per doc_type — is intake_form an image? caps unstated) | §2a UploadRequest: MIME whitelist per doc_type (intake_form accepts images → straight to OCR), ≤10 MB / ≤20 pages pre-queue, typed 422 |
| I14 | LabPdfExtraction single-analyte; GroundedField/CitationV2 composition undefined | §2: LabPdfExtraction{results: list[LabResult]}; composition rule — every leaf is GroundedField[T], citation iff grounded |
| I15 | W1→W2 citation schema change has no actual migration note (a named PRD requirement) | §2a Migration notes: field-for-field W1 evidence_id → CitationV2 mapping; composer-side adapter; W1 pipeline unchanged; pinned by regression test |
| I16 | EHR write payloads untyped; vitals route's mandatory :eid has no sourcing rule (creating encounters would be a third write capability) | §2 ExtractionArtifact + VitalsWrite models; §3 rule: vitals only with explicit encounter_id, else artifact-only + writeback.skipped(no_encounter); agent never creates encounters |
| I17 | Extraction-job state has no named authority; §4a ledger missing rows (job state, words+boxes, page renders, graph state) | §4a four new rows + write ledger row |
| I18 | PDF text-layer + page-render library never named or researched; license-sensitive in a GPL-3 fork (PyMuPDF is AGPL) | W2-R6 added (dated): pypdfium2 (Apache/BSD) as the single PDF dep; pdfplumber (MIT) alternative; PyMuPDF rejected (AGPL), pdf2image rejected (poppler system dep); named in §1/§2 |
| I19 | W2-D4 (Cohere) vs W2-R3 (local-primary) tension not carried; MVP deliverable hinges on an undated owner action; un-reranked-degraded ≠ "or equivalent" | Owner decision 2; §2 reranker seam + dated trigger; W2-D4 rev (dated) |
| I20 | Eval-gate stubbing posture ambiguous/contradictory (also C2/C3 substrate) | §7 two-tier posture stated explicitly; integration tests stay fully stubbed (PRD); W2-D8 |
| I21 | New endpoints (POST /documents, status) have no named authn/authz enforcement point | §2a: pinned session + patient-match rule on every W2 endpoint; invariant eval cases |
| I22 | Write attribution unspecified (whose token executes the async job's writes) | §3 write principal (= I2); attribution = clinician per OpenEMR, machine authorship in the record (W2-O3) |
| I23 | Page-image overlay is an unnamed PHI endpoint (no auth/session binding/leak analysis) | §2a endpoint contract + cross-patient 403 leak test (§7a); W2-D7 rev |
| I24 | Cohere PHI-free-query contract asserted everywhere, enforced nowhere | §4: deterministic query builder + outbound screen, fail-closed to local path; unit-tested + injection eval case |
| I25 | Stub mechanism/fidelity undesigned (what the stub IS, staleness policy, is the answer LLM stubbed?) | §7 Tier-1: recorded real-model responses committed under `agent/evals/recordings/` (`make record-evals`, PR-diff reviewed); VLM + answer LLM stubbed in Tier 1, live in the PR-blocking Tier 2 (W2-D8) |
| I26 | no_phi_in_logs judging mechanics undesigned (no per-case log capture, no defined detector) | §7 canary harness: unique canary tokens per fixture + correlation-ID-scoped capture; 100% threshold; global CI grep; defense-prep regression #4 now deterministically caught |
| I27 | No scorer self-tests, no pre-submission regression drill (permanently-green-gate risk) | §7/§7a: known-fail fixture per scorer; §9 Final regression drill — four injected regressions → four red runs linked in CI Evidence |
| I28 | Unbypassable-enforcement ambiguous: GH Actions can't block on the GitLab mirror; hooks opt-in; "fast subset" undefined | §6a: hook runs the FULL Tier-1 gate; branch protection requires **both** eval jobs; `.gitlab-ci.yml` runs Tier 1 on the mirror; GitHub is the canonical remote for the live Tier-2 gate; grader path documented |
| I29 | Only 2 of 5 documented W1 debt items in the draft (PRD: documented AND resolved) | §8 five-row W1 debt ledger with resolutions + waves; ordering deviation owned |
| I30 | Screenshots (PRD-named sensitive artifact) homeless; page PNGs an unowned PHI class | §4 sensitive-artifact inventory (W2-D7 rev): screenshots, prompts, page renders added |
| I31 | Per-encounter log line (core req 7) half-homed: token usage, cost estimate, per-encounter eval outcome missing | §6 `encounter.summary` terminal event carrying all seven fields; ingestion-count panel added |

### Downgraded on verification (3 — applied as edits)

| # | Finding | Resolved → |
|---|---|---|
| D1 | Alerts had no in-doc thresholds/response actions (deferred to a not-yet-existing runbook file) | §6 W1-§7-style alert table (4 alerts, working thresholds, actions); runbooks stay as the expanded copy |
| D2 | Graded gate had no execution path on the GitLab submission host | §6a `.gitlab-ci.yml` + canonical-remote statement (= I28) |
| D3 | Core req 7 log line half-homed | §6 (= I31) |

### Refuted on verification (1 — no action)

- "Demo video's six required contents are homeless" — refuted: the walkthrough video is
  scheduled in §9 (MVP row 5 deliverable); the binding doc still adds the explicit
  six-element shot list at §9 Final as cheap insurance.

### Minor (29 — applied silently, one line each)

1. Index build timing/rebuild trigger → §6 (image build; CI asserts index↔manifest hash).
2. Vitals encounter anchor → §3 rule + writeback.skipped(no_encounter) (= I16).
3. Wrong-doc-type / wrong-patient flows → §5 rows (doc_type_mismatch; void-and-reupload runbook).
4. VLM-down split rows (question vs ingestion) → §5.
5. Deploy/rollback promoted into the binding doc → §6.
6. "Event retries" panel extended to W2 dependencies → §6 metrics.
7. Baseline method + four PRD-named flows stated → §6.
8. /ready hard-vs-soft classification per new dep; ingestion SLO alert → §5 /ready row + §6 alert table.
9. Round-trip write-then-re-read verification promoted from DEFENSE_PREP → §3.
10. W2 endpoint inventory (retrieval + full-flow contracts) → §2a.
11. DocumentStatus typed failure reasons → §2.
12. HandoffRecord closed enums (supervisor_decision, reason_code) → §2.
13. EvidenceSnippet/CitationV2 corpus-version pinning → §2.
14. W2-O1 carried as designed open item (budget + measurement point + fallback ladder) → §6.
15. V2 spike (LangGraph + SSE streaming) carried visibly with fallback → §2a, §9, Open items.
16. Lab-vs-vitals routing ambiguity ("lab-adjacent") eliminated → §3 rule; O-new renamed.
17. W2-R2 figure-strip license condition carried into the corpus bullet → §2.
18. Tesseract packaging verification → §9 day-1 container spike.
19. Corpus-size claim hedged as estimate; W2-R3 anchor noted → §2.
20. Document-injection T1-style enforcement paragraph (raw OCR text never enters an answer prompt) → §4.
21. W1 G9-1 scope-trace exemption note carried → §1.
22. ColQwen2/multi-vector added to the explicit deferral list → §8.
23. Potassium-trend disambiguation (textual answer core; widget stretch) → §2 composer.
24. W1 eval suite stays green as an explicit CI step → §6a.
25. Scenario promise (3 degraded axes) stated as a behavior contract → §3.
26. Capability→W1-user mapping referenced from the binding doc → §1.
27. Front-desk narrative sentence in the binding doc → §1 non-goals (per owner decision 3).
28. README branch + service disambiguation → §8.
29. CPU/memory named for the four baseline flows → §6.

## Artifact updates made at the gate (all dated 2026-07-13)

- `W2_ARCHITECTURE.md` (repo root) — the binding contract, supersedes the draft.
- `docs/week2/W2_DECISIONS.md` — W2-D1 addendum (durable jobs, write principal, ledger,
  re-read); W2-D4 revision (reranker seam + dated trigger); W2-D7 revision (page renders,
  screenshots, prompts); **W2-D8 new, owner-recorded** (two-tier eval gate: Tier 1
  offline, Tier 2 live PR-blocking); W2-O1 resolved; O-new renamed.
- `docs/week2/W2_USERS.md` — front-desk actor demoted to provenance narrative (dated).
- `docs/week2/W2_RESEARCH.md` — **W2-R6 new** (PDF stack licensing); V-item statuses.
- `docs/week2/W2_ARCHITECTURE_DRAFT.md` — superseded pointer added.
- `docs/week1/**` — untouched (frozen), per the standing rule.

---

## PRD coverage table (re-derived from the PRD; 99 rows, zero blank cells)

Statuses reflect the **post-finalize** state (binding `W2_ARCHITECTURE.md`). Rows
corrected at the gate: W2-REQ-52 (verifier correction), W2-REQ-81/89/95 (partial at draft
time, resolved by the finalize edits — see notes). `out-of-scope` rows cite the PRD's own
sanction. §N references resolve in both the draft and the binding doc.

| Req | PRD anchor | Requirement | Coverage | Where | Notes |
|---|---|---|---|---|---|
| W2-REQ-01 | p.2 GATE box | Eval-driven CI is non-negotiable; a working demo that cannot block regressions fails Week 2. | covered | draft §7 + §6a CI; W2-D5 | PR-blocking hook + GH Actions; §7 explicitly designed 'where the graders will strike'; defense prep §2 treats it as THE gate. |
| W2-REQ-02 | p.3 MVP table row 1 | Ingest two document types: upload and extract a lab PDF and an intake form using strict schemas. | covered | draft §2 (attach_and_extract, Pydantic models) + §3 ingestion; W2-D3; UC-W2-1/2 | Both doc types enumerated with strict Pydantic v2 schemas and hard-reject on violation. |
| W2-REQ-03 | p.3 MVP table row 2 | Small guideline corpus indexed with keyword+dense retrieval and Cohere rerank or equivalent. | covered | draft §2 corpus/retriever; W2-D4; W2-R2/R3 | BM25 + bge-small + Cohere Rerank (owner-locked, production key); degraded fallback defined. |
| W2-REQ-04 | p.3 MVP table row 3 | Supervisor routes to intake-extractor and evidence-retriever with logged handoffs. | covered | draft §2 LangGraph graph + HandoffRecord; W2-D2; W2-R1 | Handoff record schema fully specified {correlation_id, turn, decision, reason_code, worker, refs, ts}. |
| W2-REQ-05 | p.3 MVP table row 4 | Gate with eval-driven CI: 50-case golden set, boolean rubrics, PR-blocking Git Hook. | covered | draft §7; W2-D5 | All three elements present; hook committed via hooksPath + setup doc. |
| W2-REQ-06 | p.3 MVP table row 5 | Integrate and demo: deployed app, source-grounded UI, latency/cost report, walkthrough video. | covered | draft §1 (Railway), §2 composer/overlay UI, §8a cost report, §9 | UI substance designed (citation classes, bbox overlay, preview). Open UI details: W2-O3 pending-review treatment and the upload affordance. Video content plan is the one gap — see W2-REQ-52. |
| W2-REQ-07 | p.3 Stage 1 | Ingestion flow: accept a file, associate with a patient, store source in OpenEMR, extract structured JSON, link every derived fact back to the source. | covered | draft §3 ingestion lifecycle; W2-D1; W2-F2 (POST /api/patient/:pid/document, code-verified) | All five obligations designed with lineage per field. Note: W2_USERS names a front-desk upload-only actor but only physician SMART auth exists — the secondary actor's auth path is undesigned (flag for binding doc; demo can be physician-upload). |
| W2-REQ-08 | p.4 Stage 2 | Create a small self-sourced clinical-guideline corpus of agreed clinical practices relevant to the user profile (documents not provided). | covered | draft §2 corpus; W2-D4; W2-R2 (deep-researched licensing) | VA/DoD CPG trio matches the W1 PCP panel; license-verified with manifest, do-not-ingest list, and sizing/curation rule. Self-sourcing obligation fully discharged by W2-R2. |
| W2-REQ-09 | p.4 Stage 2 | Keyword plus vector retrieval, rerank candidate chunks, return evidence snippets with source metadata. | covered | draft §2 hybrid retriever + EvidenceSnippet{source_id, section, chunk_id, quote, score} | Snippet shape typed; verbatim chunks align with the citation contract. |
| W2-REQ-10 | p.4 Stage 2 stretch line | ColQwen2 and multi-vector indexing. | out-of-scope | PRD sanction: p.4 Stage 2 — 'ColQwen2 and multi-vector indexing are stretch; the core requirement is a reliable hybrid retriever' | Explicitly rejected in W2-D3 rejected-list and draft §8 stretch positioning. |
| W2-REQ-11 | p.4 Stage 3 | Supervisor decides when extraction is needed, when evidence retrieval is needed, and when the final answer is ready; handoffs explicit. | covered | draft §2 graph + §3 question lifecycle; W2-D2 | Per-turn routing decisions logged as records with reason_code; termination decision is the composer handoff. |
| W2-REQ-12 | p.4 Stage 4 | 50 synthetic/demo cases exercising extraction, evidence retrieval, citations, refusals, and missing-data behavior; boolean rubrics not 1-10; CI fails on meaningful regression. | covered | draft §7 case mix; W2-D5 | Case mix explicitly covers all five required behaviors plus duplicate-upload and injection cases. |
| W2-REQ-13 | p.4 Stage 5 | Expose the W2 flow in the deployed app, capture observability traces, record a demo, and explain why each capability maps back to the W1 user and workflow. | covered | draft §1/§6/§9; W2_USERS (user carried from W1 D1 + capability→UC→decision traceability table) | W2_USERS was written specifically to discharge the capability→W1-user mapping. |
| W2-REQ-14 | p.4 Core req 1 (tool) | Implement attach_and_extract(patient_id, file_path, doc_type) or an equivalent tool. | covered | draft §2 attach_and_extract(patient_id, file, doc_type) | Exact prescribed signature adopted. |
| W2-REQ-15 | p.4 Core req 1 (doc types) | The tool must support lab_pdf and intake_form. | covered | draft §2 (doc_type ∈ {lab_pdf, intake_form}) | Enum-constrained at the tool boundary. |
| W2-REQ-16 | p.4 Core req 1 (store source) | Store the source document in OpenEMR. | covered | draft §3; W2-D1; W2-F2 (route code-verified at _rest_routes_standard.inc.php:496) | Content-hashed for idempotency; documents-API transport verified before any code. |
| W2-REQ-17 | p.4 Core req 1 (strict JSON) | Return strict-schema JSON. | covered | draft §2 Pydantic v2 models; W2-D3 (malformed = hard reject) | Raw VLM output never bypasses schema — pitfall 2 also answered. |
| W2-REQ-18 | p.4 Core req 1 (persist facts) | Persist derived facts as appropriate FHIR resources or OpenEMR records. | covered | draft §3 write-interface discrepancy note; W2-D1; W2-R5; W2-F1/F3 | Fork has no FHIR write for the targets (3-way verified); the PRD's own 'or OpenEMR records' clause sanctions documents/vitals API. Labs persist as structured lineage-linked artifacts, never shoehorned into vitals (W2-F3). |
| W2-REQ-19 | p.4-5 Core req 2 (schema tooling) | Use Pydantic, Zod, or equivalent strict schemas. | covered | draft §2 — Pydantic v2, named explicitly, with full model inventory | Same stack as W1 D3; six models enumerated. |
| W2-REQ-20 | p.4-5 Core req 2 (lab fields) | Lab schema includes at least test name, value, unit, reference range, collection date, abnormal flag, source citation. | covered | draft §2 LabPdfExtraction field list | All seven required fields present verbatim. |
| W2-REQ-21 | p.4-5 Core req 2 (intake fields) | Intake schema includes demographics, chief concern, current medications, allergies, family history, source citation. | covered | draft §2 IntakeFormExtraction field list | All six required fields present verbatim. |
| W2-REQ-22 | p.4 Core req 3 (index + sparse+dense) | Index a small clinical-guideline corpus; retrieve with sparse+dense search. | covered | draft §2 (rank-bm25 + bge-small-en-v1.5 ONNX); W2-R3; W2-O1 | W2-O1 open: confirm in-process index memory fit alongside ONNX models on Railway — flagged, not blocking. |
| W2-REQ-23 | p.4 Core req 3 (rerank) | Rerank candidate chunks with Cohere Rerank or an equivalent reranker. | covered | draft §2; W2-D4 (Cohere, production key — owner decision); W2-R3 | Production key is a named owner action; mxbai local alternative documented; reranker-down degradation defined. |
| W2-REQ-24 | p.4 Core req 3 (top evidence only) | Feed only the top grounded evidence to the answer model. | covered | W2-D4 ('snippets are the only guideline content the model sees'); draft §2 composer | Exact top-k is a build detail; the containment contract is designed. |
| W2-REQ-25 | p.4 Core req 4 (framework) | Use LangGraph, OpenAI Agents SDK, or another inspectable orchestration framework. | covered | W2-D2 (LangGraph, locked); W2-R1 (pattern + Langfuse integration verified) | W1 D6's own invalidation clause fired — consistency story pre-built in defense prep §4B. |
| W2-REQ-26 | p.4 Core req 4 (named workers) | Required workers are intake-extractor and evidence-retriever. | covered | draft §1/§2 — both workers by the PRD's names | W1 direct loop survives inside the workers. |
| W2-REQ-27 | p.5 Core req 5 (per-claim citation) | Every clinical claim in the final response includes machine-readable citation metadata. | covered | draft §2 answer composer; W2-D6 | Enforcement is structural: incomplete citation = claim does not render. |
| W2-REQ-28 | p.5 Core req 5 (citation shape) | Minimum citation shape {source_type, source_id, page_or_section, field_or_chunk_id, quote_or_value}. | covered | draft §2 CitationV2 (typed Pydantic model); W2-D6 | Prescribed shape adopted field-for-field. |
| W2-REQ-29 | p.5 Core req 5 (bbox overlay) | A visual PDF bounding-box overlay is required. | covered | draft §2 composer (server-rendered page PNG + scaled coordinate divs); W2-D3 (OCR/text-layer coordinates) | Defense prep §2 correctly flags this as core-not-stretch; boxes only drawn where grounding justifies them. |
| W2-REQ-30 | p.5 Core req 6 (golden set) | Build a 50-case golden set. | covered | draft §7; W2-D5 | In-repo, Synthea-authored fixtures + degraded variants. |
| W2-REQ-31 | p.5 Core req 6 (PR-blocking hook) | A PR-blocking Git Hook. | covered | draft §7/§6a (committed hooksPath + setup doc, plus GH Actions) | Client hooks are bypassable; draft owns this — GH Actions named as the enforcement graders cannot bypass. |
| W2-REQ-32 | p.5 Core req 6 (categories) | Boolean rubric categories must include schema_valid, citation_present, factually_consistent, safe_refusal, no_phi_in_logs. | covered | draft §7; W2-D5 | All five verbatim; no_phi_in_logs backed by a CI PHI-detection check. |
| W2-REQ-33 | p.5 Core req 6 (thresholds) | Build fails if any category regresses by more than 5% or drops below the pass threshold. | covered | draft §7; W2-D5 | Both failure conditions stated; also wired to an alert (§6). |
| W2-REQ-34 | p.5 Core req 7 (per-encounter logging) | Each encounter logs tool sequence, latency by step, token usage, cost estimate, retrieval hits, extraction confidence, and eval outcome. | covered | draft §6 metrics + §6a event inventory + W1 Langfuse tracing carried | Extraction confidence is defined (grounding agreement, binary) rather than VLM self-report — stronger than the PRD asks. |
| W2-REQ-35 | p.5 Core req 7 (no raw PHI) | Logs must not contain raw PHI. | covered | draft §6a privacy scrubbing; W2-D7; CI PHI-detection check | extraction.field.outcome logs field NAME + boolean only, never the value. |
| W2-REQ-36 | p.5 HARD GATE box | Graders introduce a small regression; if the CI gate does not block it, Week 2 does not pass. | covered | draft §7; W2-D5; DEFENSE_PREP §8 (named regression per category) | Each rubric category maps to a concrete one-line break it catches — designed for the graded strike, with a fourth spare. |
| W2-REQ-37 | p.5 Core Deliverables bullet 1 | Two document types: lab PDF and intake form. | covered | draft §2/§3; UC-W2-1/2 | Duplicate anchor of REQ-02/15; consistent everywhere. |
| W2-REQ-38 | p.5 Core Deliverables bullet 2 | One supervisor and two workers: intake-extractor and evidence-retriever. | covered | draft §2; W2-D2 | Duplicate anchor of REQ-04/26. |
| W2-REQ-39 | p.5 Core Deliverables bullet 3 | Basic hybrid RAG plus rerank over a small guideline corpus. | covered | draft §2; W2-D4 | Duplicate anchor of REQ-03/22-24. |
| W2-REQ-40 | p.5 Core Deliverables bullet 4 | 50-case golden dataset with boolean rubrics. | covered | draft §7; W2-D5 | Duplicate anchor of REQ-30/32. |
| W2-REQ-41 | p.5 Core Deliverables bullet 5 | PR-blocking eval CI and an observable deployed demo. | covered | draft §6a CI + §7 + §1/§6 (deployed + Langfuse dashboards) | 'Observable' discharged by traces, dashboard panels, and alerts extending W1 §7. |
| W2-REQ-42 | p.5 Core Deliverables bullet 6 | Critic agent that rejects uncited claims or unsafe action suggestions. | out-of-scope | PRD sanction: p.4 Core req 4 — 'A critic agent is extension work, not core' + p.3 MVP table defining core as five items | Deferral recorded in draft §8, W2-D2 rejected-list, DEFENSE_PREP §3/§7 (dated cut entry promised). Note the composer's incomplete-citation-blocks-render rule delivers the uncited-claim rejection deterministically. |
| W2-REQ-43 | p.5 Core Deliverables bullet 7 | Click-to-source UI for citation snippets, with a simple document preview. | covered | draft §8 stretch positioning + §2 composer | Stretch-tier by the five-item-core reading (defense prep §3), but substantively delivered by core work: W1 citation popovers + required bbox overlay + server-rendered page preview; only polish beyond that is deferred. |
| W2-REQ-44 | p.5 Core Deliverables bullet 8 | A third document type such as referral fax or medication list. | out-of-scope | PRD sanction: p.3 MVP table defining core as five items/two doc types + p.7 pitfall 1 (don't add doc types before two work) | Draft §8 defers with dated cut entry unless Final-core is green early. |
| W2-REQ-45 | p.5 Core Deliverables bullet 9 | Lab trend chart widget using extracted Observation data. | out-of-scope | PRD sanction: p.3 MVP table defining core as five items | Draft §8 defers. Additional fact: the fork has no FHIR Observation write (W2-F1), so this stretch item would need the artifact-store read path — worth one line in the binding doc if ever picked up. |
| W2-REQ-46 | p.5 Core Deliverables bullet 10 | Contextual retrieval improvements: better chunking, query rewriting, or domain-specific filters. | out-of-scope | PRD sanction: p.3 MVP table defining core as five items + p.4 Stage 2 ('core requirement is a reliable hybrid retriever') | Draft §8 defers with dated cut entry; W2-R3 notes query phrasing is the highest-leverage lever if picked up later. |
| W2-REQ-47 | p.5 deliverable table: GitLab Repository | Week 1 fork with Week 2 changes, setup guide, deployed link, and clear environment-variable documentation. | covered | draft §8 (GitLab mirror current at every checkpoint; README env-var list incl. COHERE_API_KEY) + §9 MVP | W1 GitLab-mirror/RAILWAY_TOKEN residual debt is named and closes in W2 CI work (DEFENSE_PREP §6.5). |
| W2-REQ-48 | p.5 deliverable table: Week 2 Architecture Doc | ./W2_ARCHITECTURE.md explaining ingestion flow, worker graph, RAG design, eval gate, risks, and tradeoffs. | covered | the draft itself: §3 ingestion, §2 graph/RAG, §7 gate, §8 risks/tradeoffs (+ §5 failure modes, §7a testing per eng reqs); this /arch-finalize pass produces the binding repo-root file | Must land at repo ROOT as ./W2_ARCHITECTURE.md — draft header already commits to that. |
| W2-REQ-49 | p.5 deliverable table: Schemas | Pydantic/Zod schemas for lab_pdf and intake_form including source citation fields and validation tests. | covered | draft §2 ('each with validation tests — a named PRD deliverable') + §6a CI schema-validation tests | source_citation is a field in both models; CitationV2 typed. |
| W2-REQ-50 | p.5 deliverable table: Eval Dataset | 50 synthetic/demo cases with expected behavior, boolean rubrics, judge configuration, and results. | covered | draft §8a 'Eval-artifact deliverables named' + §7 | All four artifact elements explicit: expected behavior per case, rubrics, pinned judge config (model + boolean prompts), committed results per run. |
| W2-REQ-51 | p.5 deliverable table: CI Evidence | Git Hook or equivalent that runs the eval suite and blocks regressions. | covered | draft §6a (hook runs fast subset; GH Actions full gate) + §7 | Two-layer delivery matches 'or equivalent' generously. |
| W2-REQ-52 | p.5 deliverable table: Demo Video | 3-5 minute video showing document upload, extraction, evidence retrieval, citations, eval results, and observability. | covered | draft/binding §9 Final (demo video) — finalize adds the explicit six-element shot list | Verifier corrected the auditor: the walkthrough video was scheduled in §9; the binding doc now scripts the six PRD-required contents so the recording provably hits all six. |
| W2-REQ-53 | p.5 deliverable table: Cost and Latency Report | Actual dev spend, projected production cost, p50/p95 latency, and bottleneck analysis. | covered | draft §8a cost & latency report contents | All four elements named; sources are traces + Railway billing; expected bottleneck hypothesis pre-stated for verification. |
| W2-REQ-54 | p.5 deliverable table: Deployed Application | Publicly accessible deployed app with the Week 2 core flow working. | covered | draft §1 (Railway project unchanged) + §9 MVP deploy | Deployment posture inherited from W1; new container deps (Tesseract, ONNX) named. |
| W2-REQ-55 | p.6 eng req: API/event contracts | Every interface between W2 components (ingestion, RAG, handoffs, FHIR/EHR writes) has a typed contract. | covered | draft §2 Pydantic model inventory (LabPdfExtraction, IntakeFormExtraction, CitationV2, EvidenceSnippet, HandoffRecord, GroundedField) + §4a | One model per interface, enumerated by name. |
| W2-REQ-56 | p.6 eng req: schema evolution/migration | Any schema change from Week 1 must be accompanied by a migration note. | covered | draft §2 (commitment stated) | Practice-level requirement; CitationV2 extends W1 evidence IDs, so the first migration note is already identifiable — binding doc should name it. |
| W2-REQ-57 | p.6 eng req: data authority | Data authority explicit: one source of truth per data type, no silent overwrites. | covered | draft §4a authority ledger (7 artifact types); W2-D1 (append-only, content-hash dedupe) | 'Every write is a create' makes no-silent-overwrites structural, not procedural. |
| W2-REQ-58 | p.6 eng req: extend observability to W2 flows | Cover document ingestion latency, extraction confidence per document, RAG retrieval hit rate, supervisor routing decisions, per-worker latency. | covered | draft §6 new-metrics list | All five named plus grounding-agreement rate and rerank score/version. |
| W2-REQ-59 | p.6 eng req: SLOs | Add SLOs for document ingestion (p95 < X seconds) and evidence retrieval. | covered | draft §6 (working targets: ingestion p95 ≤ 30s/doc, retrieval p95 ≤ 2s); W2-O2 | PRD leaves X to the builder; method is baseline-then-set (defense prep §3 names this reading). W2-O2 must close at MVP with measured numbers. |
| W2-REQ-60 | p.6 eng req: timeouts/retries/queues/circuit breakers | All outbound LLM and retrieval calls have timeouts and retry logic; queues and circuit breakers per the requirement heading. | covered | draft §6 (timeouts+retries on all outbound calls) + §3 (async in-process queue, depth as metric) + §5 (per-dependency breaker with half-open probe) | Breaker state machine is even unit-tested (§7a). Retry budgets unnumbered — build detail. |
| W2-REQ-61 | p.6 eng req: canonical contracts | Extraction schemas (lab_pdf, intake_form) are the canonical contracts; raw VLM output never bypasses schema validation; the schema is the source of truth. | covered | draft §2 ('canonical contracts' verbatim; hard reject); W2-D3 | Grounding adds a second gate beyond what the PRD requires. |
| W2-REQ-62 | p.6 eng req: correlation ID across boundaries | W1 correlation ID propagates into ingestion flows, worker handoffs, and EHR writes; full multi-agent trace reconstructable from the correlation ID alone. | covered | draft §6 + §2 HandoffRecord (correlation_id first field) + §4a lineage; W2-D2 | 'Reconstructable from the ID alone' asserted and mechanized via handoff records + span nesting. |
| W2-REQ-63 | p.6 eng req: structured logs searchable + W2 events | Logs searchable by case ID, event ID, correlation ID; W2 events covered: ingestion start/complete, extraction outcome per field, retrieval hit/miss, worker handoff, eval run outcome; all PHI-free. | covered | draft §6a log-event inventory | Every PRD-named event has a named log event; searchability keys stated; PHI-free enforced by CI check. |
| W2-REQ-64 | p.6 eng req: dashboards | Dashboard shows request count, error count, latency, queue depth, event retries, decision outcomes + W2 panels (ingestion count, field-level pass rate, retrieval hit rate, routing decisions, eval rate per category); grader-readable health. | covered | draft §6 (W2 panels) + §3 (queue depth exported) + W1 §7 dashboard carried (request/error/latency/retry counts) | Two panels ('document ingestion count', 'event retries') ride on the W1 dashboard carry-over rather than the W2 additions list — name them explicitly in the binding doc's panel list. |
| W2-REQ-65 | p.6 eng req: CI pipeline | CI: build, lint/typecheck, tests, coverage, dependency audit, security scan — dep audit and security scan on every PR. | covered | draft §6a CI pipeline (ruff, mypy, pytest+coverage, pip-audit, semgrep, per-PR) | Concrete tools named for every stage. |
| W2-REQ-66 | p.6 eng req: CI pipeline (eval-gate extension) | Extend the W1 eval gate with schema-validation tests, supervisor-worker contract tests, and extraction regression tests in the PR-blocking suite. | covered | draft §6a (all three named in pipeline order) + §7a | Extraction regression tests run on fixtures + stubbed VLM. |
| W2-REQ-67 | p.6 eng req: testing strategy | Document in W2_ARCHITECTURE.md: what is unit-tested, integration-tested, golden-set evaluated, and what is not tested and why. | covered | draft §7a four-way split incl. an explicit not-tested-and-why list | The not-tested list gives reasons per item — the part most drafts omit. |
| W2-REQ-68 | p.6 eng req: testing strategy (failure modes) | Every test has a documented failure mode it guards against. | covered | draft §7a ('guards:' annotation per test, W1 convention carried) | Enforced convention from W1 §8 EvalCase schema. |
| W2-REQ-69 | p.6 eng req: observability/debugging/incident response | Cover W2 failure modes — ingestion failures, extraction schema violations, RAG returning no results, supervisor routing errors — each with how to identify in logs and the recovery action. | covered | draft §5 failure table + named log event per failure + W2 runbook entries in docs/observability/runbooks.md | All four PRD-named failures present plus seven more (junk text layer, duplicate, injection, breaker, /ready). |
| W2-REQ-70 | p.6 eng req: runnable API collection | Update the W1 Bruno/Postman collection with W2 endpoints: document upload, extraction status, evidence retrieval, full W2 agent flow; graders can run any workflow. | covered | draft §6 (Bruno extended: upload, status, retrieval, full flow) + §9 Early | W1's token-mint helper (W1 §7) carries so authenticated flows stay grader-runnable. |
| W2-REQ-71 | p.6 eng req: baseline profiles | Record baseline CPU/memory/latency/throughput for W2 flows (ingestion, extraction, retrieval, full multi-agent run); compare against W1 baselines for shared-path regressions. | covered | draft §6 (W2 baselines recorded + W1 comparison, 'shared-path regression check') + §9 Early | Method inherits W1 §7 (Railway metrics + k6). CPU/mem not re-named for W2 flows in the draft text — make explicit in binding doc; substance is designed. |
| W2-REQ-72 | p.6 eng req: consistent structured logging | W2 logging follows the W1 structured format; no plain-text output; extend the log schema, no parallel convention. | covered | draft §6a ('same structured format, no parallel convention') | Extension-not-fork stated verbatim. |
| W2-REQ-73 | p.6 eng req: correlation/request IDs across services | Propagate the correlation ID into all W2 worker invocations, VLM calls, retrieval calls, and EHR writes; grader reconstructs a full W2 trace from the ID only. | covered | draft §6 + §4a (write lineage carries correlation_id); W2-D1/W2-D2 | Companion of REQ-62; write-side propagation explicitly in the upload-event lineage tuple. |
| W2-REQ-74 | p.7 eng req: distributed tracing | Extend W1 tracing to the supervisor/worker graph: each worker invocation is a child span of the supervisor span; extraction/retrieval sub-calls traceable within worker spans. | covered | draft §6 (span nesting stated); W2-R1 (Langfuse LangChain handler produces exactly this nesting — verified) | Mechanism research-verified, not assumed. |
| W2-REQ-75 | p.7 eng req: /health and /ready | /ready validates W2 dependencies — document storage, vector index, reranker API — returning degraded status, not binary up/down. | covered | draft §5 /ready row (extended deps, degraded-not-binary); W1 §7 hard/soft classification carried; PRESEARCH §2 | W1 /ready 50-VU saturation debt acknowledged; knee re-measure scheduled (§8). |
| W2-REQ-76 | p.7 eng req: dashboard and alert definitions | W2 alerts: extraction failure rate, RAG retrieval latency, eval regression >5% in any category; alerts documented with expected response actions. | covered | draft §6 (all three alerts) + §5 (runbook entry per recovery action, appended to docs/observability/runbooks.md) | Response-action documentation rides the runbook mechanism. |
| W2-REQ-77 | p.7 eng req: OpenAPI 3.0 | Publish an OpenAPI 3.0 spec for all W2 HTTP endpoints, committed to the repo, kept in sync, with contract tests verifying implementation matches spec. | covered | draft §6 (spec published) + §6a (OpenAPI contract tests: spec ↔ implementation) + §9 Early | Sync enforcement is the contract test in CI. |
| W2-REQ-78 | p.7 eng req: integration tests with fixtures and stubs | Integration tests exercise the full ingestion-to-answer path using fixture documents (stored PDFs and form images) and stubbed LLM/VLM responses; pass in CI without live API access. | covered | draft §7a integration list (6 fixture classes, stubbed VLM/reranker); W2-D4 (CI never calls Cohere live) | PRESEARCH §7 commits form-image fixtures to repo. |
| W2-REQ-79 | p.7 eng req: data modeling/lineage/access | Document the data model for extracted lab observations, intake facts, guideline chunks, and citation records — each with defined owner, lineage, access control, and validation rules. | covered | draft §4a ledger | All four required types present plus source documents, handoff records, and the golden set; every cell filled. |
| W2-REQ-80 | p.7 eng req: privacy of analytics workflows | Audit W2 observability data for PHI leakage; traces, logs, eval datasets, cost reports contain no patient identifiers, raw document text, or extracted clinical values; document scrubbing; verify in CI with a PHI-detection check. | covered | draft §6a privacy scrubbing (incl. cost-report clause); W2-D7 | Scrubbing approach stated (ids/hashes/counts/booleans only) and CI-enforced; doubles as the no_phi_in_logs rubric backstop. |
| W2-REQ-81 | p.7 eng req: backup and recovery | Document backup (automatic AND manual) for extracted documents, derived records, and the eval golden set; manual recovery procedure if automated backup fails; RPO/RTO estimates. | covered | binding §8a + Open items (Railway backup verification = named deploy action before Final) | Partial at draft time: the AUTOMATIC leg rested on an asserted, unverified Railway backup posture. Resolved: verification + evidence in DEPLOYMENT.md is a dated owner checklist item; manual leg + RPO/RTO were already designed. |
| W2-REQ-82 | p.7 eng req: backup (golden set clause) | The eval golden set must be reproducible from the repo alone — never living only in a database without a recovery path. | covered | draft §7 + §8a + §4a (golden set owner = Repo, RPO 0); W2-D5 | In-repo fixtures by design. |
| W2-REQ-83 | p.7 pitfall 1 | Do not attempt five document types before two work reliably. | covered | draft §8 core-first posture; PRESEARCH owner decision 5 (core only until green) | Third doc type explicitly deferred with dated cut entry. |
| W2-REQ-84 | p.7 pitfall 2 | Never use a VLM answer directly without schema validation or source metadata. | covered | W2-D3; draft §2 (hard reject; grounding per field) | Double gate: Pydantic proves shape, grounding proves content is on the page. |
| W2-REQ-85 | p.7 pitfall 3 | Supervisor must not become a black box; handoffs logged and explainable. | covered | W2-D2 (handoff record w/ reason_code); draft §2/§5 | Routing errors reconstructable from the correlation ID; DEFENSE_PREP §8 Q6 rehearsed. |
| W2-REQ-86 | p.7 pitfall 4 | No LLM-as-judge without a clear rubric; boolean rubrics so failures are actionable. | covered | W2-D5 (deterministic first; unavoidable LLM judgments pinned to boolean questions quoting the evidence span); draft §8a judge config | No 1-10 anywhere; judge model + prompts pinned. |
| W2-REQ-87 | p.7 pitfall 5 | Never log raw document text, patient identifiers, or screenshots to SaaS observability tools. | covered | W2-D7 (Langfuse content switch stays OFF, W1 D16); draft §6a | Document images confined to OpenEMR; Langfuse receives ids/hashes/booleans only. |
| W2-REQ-88 | p.3 Codebase prose | Build on the Week 1 fork, auth flow, tool layer, verification strategy, observability, and eval harness. | covered | draft header + §2 'Unchanged W1 components' + §3 (W1 EvidencePacket path, verify-then-flush carried) | W1 thesis explicitly extended, not replaced; W1 docs treated as frozen. |
| W2-REQ-89 | p.3 Codebase prose | Week 1 technical debt documented AND resolved before adding new surface area. | covered | binding §8 W1 debt ledger (all 5 items + wave) + §9 (token persistence pulled into MVP) | Partial at draft time: only 2 of 5 debt items carried; ordering deviation unowned. Resolved: full ledger; item 1 pulled into MVP (the job write principal depends on it); Early/Final items are an owned, argued deviation. |
| W2-REQ-90 | p.3 Codebase prose | README clearly separates W1 baseline behavior from W2 multimodal behavior; graders run the core flow without guessing branch, env var, or service. | covered | draft §8 (README env-var inventory + W1/W2 split) + §9 MVP | Env-var list enumerated (COHERE_API_KEY, Langfuse, SMART client, OE_*). |
| W2-REQ-91 | p.2 Scenario prose | The answer stays useful even if the document scan is imperfect. | covered | W2-D3 (UNSUPPORTED render, never guessed); draft §5 degraded-scan rows; §7 degraded fixtures; UC-W2-1 failure states | 'Honest and slow-path' degraded-scan-day flow in W2_USERS ops section. |
| W2-REQ-92 | p.2 Scenario prose | The answer stays useful even if the patient record is incomplete. | covered | UC-W2-2 (W1 F-D.5 absence discipline carried: empty allergy ≠ NKDA); draft §7 missing-data cases | Missing-data behavior is also a required Stage-4 eval dimension — present in the case mix. |
| W2-REQ-93 | p.2 Scenario prose | The answer stays useful when the user asks a follow-up question. | covered | UC-W2-4 (grounding never degrades across turns); draft §3 question lifecycle (session context reuse) | Selective re-retrieval without re-extraction designed; session-expiry failure named. |
| W2-REQ-94 | p.2-3 HIPAA-minded prose | Use only demo or synthetic data. | covered | draft §7 (Synthea-authored fixtures); W2-D7; W1 demo-data-only rule carried | Eval fixtures synthetic by construction. |
| W2-REQ-95 | p.2-3 HIPAA-minded prose | Treat prompts, extracted fields, document images, traces, AND screenshots as sensitive. | covered | binding §4 sensitive-artifact inventory (W2-D7 rev 2026-07-13: + screenshots, prompts, page renders) | Partial at draft time: screenshots and prompts were absent from the W2-D7 inventory. Resolved by dated W2-D7 revision. |
| W2-REQ-96 | p.3 hard problem: FHIR and OpenEMR integrity | Uploaded documents and derived observations round-trip through OpenEMR without creating duplicate or untraceable records. | covered | W2-D1 (content-hash idempotency; deterministic fact IDs; lineage on every create); draft §4a; §5 duplicate-upload row; §7 duplicate eval case | DEFENSE_PREP §8 Q9 rehearses the exact grader question. |
| W2-REQ-97 | p.2 hard problem: vision extraction without invention | Schema, source links, and verification strategy must make unsupported extracted facts visible. | covered | W2-D3 (per-field grounding; ungrounded → UNSUPPORTED + 'verify against source document'); draft §2 grounding verifier | Confidence = binary grounding agreement, never VLM self-report — directly answers the confidence-overstatement half. |
| W2-REQ-98 | p.2 hard problem: evidence grounding | Every answer separates patient-record facts from guideline evidence; medication/lab claims must point back to a source. | covered | W2-D6 (source_type ∈ {patient_record, uploaded_document, guideline}, rendered as visually distinct classes); draft §2 composer | Separation is structural (typed source_type) plus visual; unsourced claims cannot render. |
| W2-REQ-99 | p.2 intro prose | Route work across a small multi-agent graph WITHOUT losing grounding. | covered | W2-R1 (custom typed LangGraph state carrying extracted facts, citations, partial answers between workers); UC-W2-4 | Grounding survives handoffs because citations travel in typed graph state, not prose; verify-then-flush runs at the composer regardless of which worker produced the material. |
