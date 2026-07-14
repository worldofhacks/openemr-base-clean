# W2_IMPLEMENTATION_PLAN.md — Week 2 build plan (living document)

> Decomposed 2026-07-13 by /tasks-gen from the **binding repo-root `W2_ARCHITECTURE.md`**
> (15 §-anchors; finalized 2026-07-13). Supporting inputs: `docs/week2/W2_DECISIONS.md`
> (W2-D1..D10 + dated revisions), `docs/week2/W2_AUDIT.md` (W2-F1..F23 — F7..F11 added by
> the 2026-07-13 W2-F1 live verification and F12..F23 by the adversarial review),
> `docs/week2/W2_RESEARCH.md` (W2-R1..R6), `docs/week2/W2_USERS.md` (UC-W2-1..4),
> `docs/week2/W2_gap-audit.md` (99-req coverage), `docs/week2/Week_2_AgentForge.pdf` (PRD).
>
> **Conventions.** Checkboxes are the plan's state — update as work lands, never rewrite
> from scratch. Every task carries `Files:` (NEW vs extended), `Anchors:` (§/ADR/finding),
> `Accept:` (behavior incl. edge/error cases), and `Test:` with a `guards:` failure-mode
> annotation (W1 convention; eval cases tagged boundary / invariant / regression). Task
> IDs: **W2-M#** (MVP), **W2-E#** (Early), **W2-#** (Final — `W2-F#` is taken by audit
> findings), **W2-OA#** (owner actions). This plan never extends the architecture: work
> without §/ADR backing is flagged in **Needs architecture**, not written as a task.
>
> **Build model (this week).** Claude Code only — no Codex. Verification-touching code —
> the **grounding verifier (W2-M10)**, the **citation contract (W2-M15)**, and the **eval
> gate (W2-M18/M19/M20)** — is built via the **tdd-swarm skill** (frozen tests first,
> independent verification agents); noted on those tasks.
>
> **Week-scoped rule.** Nothing under `docs/week1/` or any W1 root doc is edited.
> After this docs-only remediation is committed, `W2_ARCHITECTURE.md` is the read-only
> binding contract for implementation; build tasks may not silently rewrite it.

---

## Adversarial audit-review integration (2026-07-13)

A read-only adversarial re-audit of the write/upload surface (W2_AUDIT.md "Adversarial
audit review"; W2-F12..F23; owner decision **W2-D9**) confirmed the **W2-D1 transport
survives** but **retired the "no finding blocks the architecture" verdict**. The OpenEMR
write surface enforces none of the following server-side on create, so they are
**MANDATORY agent-side controls that gate the write path** (not defense-in-depth) and are
threaded into the tasks below:

| Blocking control | Finding | Task(s) |
|---|---|---|
| Exact-scope provisioning + agent asserts granted scopes, rejects unexpected | W2-F12 | W2-OA3, W2-M11 |
| Patient-pin + encounter-ownership preflight before any write (cross-patient/mismatched-encounter negative tests) | W2-F13 | W2-M8, W2-M11 |
| Canonical source/artifact paths + expected category-ID/ACL preflight; send only the path | W2-F14 | W2-OA3, W2-M8, W2-M11 |
| Vital range sanity in the agent (server range validator is bypassed by REST) | W2-F15 | W2-M11 |
| Never send caller `user`/`group`; decide provenance representation | W2-F16 | W2-M11 |
| Token-revoking cutover (client-disable does not revoke live 1h tokens) | W2-F17 | W2-OA3 |
| Idempotency ledger is load-bearing (native upload is non-idempotent) | W2-F18 | W2-M8, W2-M11 |
| Upload validation: size/page/exact-MIME/`$_FILES`-error, controlled 4xx (native returns empty 404 on reject) | W2-F19 | W2-M8 |
| Confirm `system_error_logging != DEBUG` before FHIR-Binary readback | W2-F20 | W2-M11, W2-OA5 |
| "read-only" ≠ "no DB mutation" — a metadata GET can backfill UUIDs | W2-F21 | W2-M2 note, W2-1 |

Precision corrections adopted (no task redesign): missing `api:oemr` → **403 not 401**
(W2-F4); the document-download **500 is raw-bytes-as-filename, not a CSRF defect** — FHIR
DocumentReference→Binary stays the read-back (W2-F9); `api_log` logs the **response** into
both request/response columns, so inbound PDF/vital bodies are NOT logged (W2-F20).
**W2-F23** (soap_note PUT IDOR) is adjacent-not-D1 — the agent touches no soap_note route;
recorded so no build agent adds one.

---

## Post-review remediation (2026-07-13) — binding execution overlay

This dated overlay implements locked **W2-D10** and supersedes only the conflicting clauses
called out below; it does not erase the audit trail or restructure the plan. Checkpoints are
**build-order milestones, not scope ceilings**: MVP is the first fully correct vertical
slice, Early closes the measured/operational surfaces, and every non-stretch PRD and
engineering requirement is complete, integrated, and evidenced by Final. Source document,
grounded extraction artifact, and eligible grounded intake vitals all ship under the same
contained exactly-once contract; build order may land source+artifact first, but no leg is
cut, stubbed, or waived.

| Review closure | Owner task(s) | Required checkpoint and acceptance evidence |
|---|---|---|
| 1. PHI-check scope | W2-M7/M18/M20, W2-2 | MVP scanner excludes canonical input fixtures, scans only generated outputs/logs/traces/reports/recordings/results, and a generated known-leak self-test turns red; Final drill linked |
| 2. Gate arithmetic | W2-M18/M20, W2-2 | MVP deterministic categories are 100%-required; factual denominator/delta emitted; Final factual drill flips `floor(0.05 × applicable)+1` cases or enough to cross its absolute threshold |
| 3. Frozen contracts | W2-M6, W2-E8 | MVP field-for-field schema snapshot; Early cross-component compatibility snapshot; Final no drift |
| 4. Path category control | W2-OA3, W2-M8/M11 | Each source/artifact path resolves to the recorded expected ID+ACL before POST; only the path is sent; wrong/ambiguous path, ID, or ACL refuses |
| 5. Remote exactly-once | W2-M8/M11, W2-E8/W2-1 | Durable `{pending,unknown,complete}` intents, remote marker/fingerprint, list/re-read reconciliation, and commit-then-timeout tests for all three legs; never blind retry |
| 6. Permanent ledger + requeue | W2-M8/M11 | Permanent patient-safe dedup/lineage survives attempt purge; failed logical job requeues atomically; cross-patient same-bytes and purge tests pass |
| 7. Exact provisioning | W2-OA3 | Exact 16-scope manifest/payload from W2_AUDIT, enabled confidential client, no password grant/unadvertised write scopes, and old access+refresh retirement evidence |
| 8. Named D9 negatives | W2-M17/M18/M19/M20, W2-1 | Golden and lower-level tests explicitly cover F12/F13/F14/F15/F16/F17/F18/F19; deployed operational checks close F17 |
| 9. Superseded facts | W2-M2/OA3/E4, W2-6 | 403, raw-bytes-as-filename, access+refresh retirement, and Early SLO closure are the only current claims |
| 10. UC/decision trace | W2-6 | W2_USERS capability matrix through D8/D9/D10 reviewed against shipped evidence |
| 11. Typed leased queue | W2-M8/M21, W2-E3 | Transactional claim, lease/heartbeat, bounded backoff, explicit worker topology, graceful shutdown, stale recovery, and queue-age evidence |
| 12. Delegated-job credential | W2-M5, W2-E8 | Encrypted patient/principal-bound credential and refresh lifecycle independent of interactive idle expiry; restart/expiry/revocation tests |
| 13. One-ID correlation | W2-M6/M12/M14, W2-E8 | Required integration trace resolves inbound→job→worker→VLM→retrieval→each write/read-back→terminal event from one ID |
| 14. Backup + PHI rows | W2-OA5, W2-4 | Agent Postgres encrypted backup/restore, named source custody, key recovery, RPO/RTO; patient-linked job/dedup/ledger rows classified PHI |
| 15. Log envelope + migrations | W2-M5/M6/M8, W2-E8 | One owned typed event envelope; 002/003 ordered expand/contract, clean-upgrade, compatibility, rollback/roll-forward evidence |
| 16. F20 deploy guard | W2-OA6, W2-M11/W2-1 | Admin-recorded non-DEBUG value; unknown or DEBUG fails closed before Binary read-back |
| 17. Tier-2 capacity/policy | W2-M24/M20 | Measured runtime/cost/quota for `50 × (VLM extract + answer + judge)` and safe fork-PR secret policy before gate required |
| 18. Full-stack RSS | W2-M1 | bge-small + local reranker + one OCR page concurrently stay below the recorded Railway ceiling with headroom |
| 19. Only stretch cuttable | Cut section, W2-6 | Exactly five sanctioned stretch items may be cut; all core/engineering/D9/D10/write/gate/GitLab work blocks Final if incomplete |
| 20. Readiness semantics | W2-M21/E3/W2-1 | Worker heartbeat+oldest-queue-age signals; `/health` remains healthy during soft degradation; stale worker/unsafe queue is unready |

**STOP conditions:** do not enable a write while any D9/D10 precondition is unknown; do
not ship a gate whose known-leak or threshold-crossing drill remains green; do not claim
readiness with a stale worker or unknown F20 setting; do not defer or cut a non-stretch
requirement. These are execution gates baked into the tasks below, not new product choices.

---

## Checkpoints (Central time — phase boundaries are the real gates)

| Checkpoint | Deadline | Must be true |
|---|---|---|
| **MVP — first fully correct vertical slice** | **Tue 2026-07-14 11:59 PM** | Both document types traverse strict extraction/grounding; source+artifact use the real D9/D10 path and exactly-once protocol; supervisor + 2 workers, hybrid RAG, 50-case two-tier PR-blocking gate, deployed source-grounded UI, initial report, and walkthrough are real—not stubs. Any not-yet-landed non-stretch work remains scheduled below, never reclassified out of scope. |
| **Early — operational and full-write closure** | **Thu 2026-07-16 11:59 PM** | Complete grounded intake-vitals leg under D10; overlay/follow-up; dashboards/alerts/runbooks; full baselines vs W1 and the **single numeric SLO closure**; OpenAPI+Bruno; migrations/log-envelope/one-ID integration closure. |
| **Final — complete robust submission** | **Sun 2026-07-19 12:00 PM** | Every non-stretch PRD and engineering requirement complete; all three write legs and D9/D10 controls hardened; deterministic+factual regression drills red; full report; OpenEMR+agent-Postgres backup/restore; six-part video; final live E2E; GitLab SHA-bound gate green. |

**Dependency spine:** W2-OA3 → W2-M8 → W2-M11 (W2-M2 ✅ verified-by-audit 2026-07-13 —
its outputs now bind M8/M11 directly); W2-M5 → {W2-M8, W2-M11} (jobs carry only
`credential_ref`; writes use the separately encrypted delegated-job credential); W2-M1 → W2-M4 (reader spike
needs the container's tesseract/pdfium unless run host-only); W2-M7 → {W2-M9, W2-M17};
W2-M6 → nearly everything; W2-M8 → W2-M16 (page renders fetch the OpenEMR-stored source
with the delegated token); W2-M14 → W2-M12 *final acceptance only* (see Track C note);
W2-M17+W2-M18 → W2-M19 → W2-M20; W2-M24 → W2-M20; W2-OA2 → W2-M20;
W2-OA6 → {W2-M11, W2-M21}; W2-OA5 → W2-4; W2-OA4 → {W2-M19, W2-M20}
(GH jobs need the remote); W2-OA1 decides W2-M14's shipped `RERANKER` value.

**Parallel tracks after Wave 0** (independent until the named merge points):
- **Track A — ingestion/writes:** W2-M5 → W2-M8 → W2-M9 → W2-M10 → W2-M11 → W2-E8
- **Track B — retrieval:** W2-M13 → W2-M14 (owns `agent/app/routes/evidence.py`; never
  touches `documents.py`)
- **Track C — graph/composer:** W2-M12 → W2-M15 → W2-M16. W2-M12 builds
  `evidence_retriever` against the **W2-M3 stub seam**; the real retrieval module
  (W2-M14) swaps in at integration — W2-M12's final acceptance (encounter.summary
  retrieval hits) closes only after that swap.
- **Track D — evals:** W2-M7 → W2-M17 → W2-M18 → W2-M19; Wave-0 W2-M24 runs in
  parallel, and `{W2-M19, W2-M24} → W2-M20` (needs A/B/C merged for recordings and the
  live Tier-2 run; case + scorer authoring starts Monday in parallel).
  **Track D owns `.github/workflows/agent-eval-gate.yml`** — W2-M13 contributes its
  index↔manifest hash assertion as a standalone script the workflow calls, not a yml edit.
- Shared-file merge points made explicit: `agent/app/routes/documents.py` (A creates, C
  extends for page renders — one merge), `agent/app/orchestrator/workers/__init__.py`
  (W2-M9 registers the real extractor over C's stub — named in W2-M9 Files).

---

## Resolved during this pass — O-new: intake-vitals field mapping

Per the architecture's Open items, O-new ("exact vitals-API field mapping for intake-form
vitals fields — resolve during /tasks-gen, before the writeback task") is resolved here,
code-verified in this fork:

- **Endpoint:** `POST /api/patient/:pid/encounter/:eid/vital` —
  route `apis/routes/_rest_routes_standard.inc.php:140` →
  `EncounterRestController::postVital()` (`src/RestControllers/EncounterRestController.php:471`)
  → validation `EncounterService::validateVital()` (`src/Services/EncounterService.php:657`)
  → `VitalsService::save()` (`src/Services/VitalsService.php:314`).
- **`VitalsWrite` model maps exactly these intake-form vitals-class fields** (all optional
  in the API; numeric values sent as strings): `bps` (systolic, mmHg), `bpd` (diastolic,
  mmHg), `weight`, `height`, `temperature`, `pulse`, `respiration`, `oxygen_saturation`,
  plus `note` (provenance/lineage sentence, ≤255 chars) and `date` (measurement timestamp,
  `YYYYMMDDHHmmss`-tolerant; server defaults to now if omitted).
- **Units:** the API performs **no conversion on POST** — values persist as-is and are
  interpreted per the instance's `units_of_measurement` global (this deployment: US units —
  weight lb, height in, temperature °F). `VitalsWrite` therefore carries values in the
  instance's configured unit system and records the unit assumption in the artifact lineage.
- **Never sent:** `id`/`uuid`/`pid`/`eid` (URL/server-assigned), `BMI`/`BMI_status`
  (not auto-computed server-side; the agent does not derive new clinical values — W2-D1
  append-only posture), lab-class values of any kind (W2-F3: labs are not vitals).
- **Unit-mismatch rule (closes the mapping honestly):** when the grounded on-page unit
  token is absent or differs from the instance's configured unit system (e.g. a metric
  intake form on this US-units instance), the field's vitals leg is **skipped** — the
  value persists in the `ExtractionArtifact` only, verbatim with its on-page unit. The
  agent **never converts** (a converted number is a derived value not on the page —
  W2-D1/W2-D3). Typed as `writeback.skipped(unit_mismatch)` — added to the §6a event
  inventory 2026-07-13 with a dated W2-D1 note (the pre-authorized addendum this plan
  flagged; see Needs architecture item 1, resolved).

---

## Phase 0 — Owner actions + Wave 0 de-risking spikes

**Deadline:** before feature work; all inside the MVP window (Mon 2026-07-13 → Tue 2026-07-14).
**Spec anchors:** §9 (build order), §2, §2a, §4, W2-F4, W2-R1, W2-R6, W2-D4 rev.
**Goal:** kill the four unknowns that can sink MVP (container deps, write path + scopes,
LangGraph+SSE, PDF/OCR geometry) and put the dated owner dependencies on the clock.
**Exit criteria:** all four spikes green (or fallbacks invoked and named); owner actions
done or their dated triggers fired; no feature task starts blocked on an unknown.

### Owner actions (blocking — dated)

- [ ] **W2-OA1 — Cohere production `COHERE_API_KEY` into Railway env.**
  **Trigger: Monday 2026-07-13 EOD.** If absent at trigger, **MVP ships `RERANKER=local`**
  (mxbai) per W2-D4 rev; Cohere becomes the Early upgrade (W2-E6). Blocks nothing except
  the shipped seam value — W2-M14 builds both paths regardless.
  Anchors: §2 (decision trigger), W2-D4 rev 2026-07-13.
  *Date note (resolved 2026-07-13):* the binding docs briefly printed
  "Monday 2026-07-14 EOD" (2026-07-14 is a Tuesday); the owner-approved correction to
  **Monday 2026-07-13 EOD** has landed in the architecture, W2-D4 rev, and the
  gap-audit — all sources now agree with this task's trigger.
- [ ] **W2-OA2 — `ANTHROPIC_API_KEY` into GitHub Actions repo secrets.**
  **Trigger: Monday 2026-07-13, before the first Tier-2 CI run.**
  **Blocking: Tier 2 (W2-M20) cannot run without it.** Anchors: §7 Tier 2, W2-D8.
- [ ] **W2-OA3 — REPLACEMENT SMART client registration (W1+W2 scope union) + verified
  cutover.** **Trigger: Monday 2026-07-13, before the first deployed write (W2-M11).**
  **Blocking: build-blocking provisioning checklist item.** The W2-F1 live verification
  proved (W2-F4 refined) that there is **no supported persisted-scope edit path and the
  admin screen cannot edit scope sets** — W1's client therefore still requires replacement,
  while registered scope is not an effective server-side ceiling (W2-F12). Steps
  (verified sequence recorded in the W2_AUDIT.md
  verification section, incl. the registration payload):
  1. Register this exact confidential-client payload; `${SMART_REDIRECT_URI}` is replaced
     from the approved environment/runbook and no secret is written to source control:

     ```json
     {
       "application_type": "private",
       "client_name": "AgentForge Week 2 Write Client",
       "redirect_uris": ["${SMART_REDIRECT_URI}"],
       "token_endpoint_auth_method": "client_secret_post",
       "grant_types": ["authorization_code", "refresh_token"],
       "scope": "openid offline_access launch launch/patient api:oemr user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/Encounter.read user/document.crs user/DocumentReference.rs user/Binary.read user/vital.crus user/Observation.rs"
     }
     ```

     The exact 16-scope manifest is `openid`, `offline_access`, `launch`,
     `launch/patient`, `api:oemr`, the executable W1 read set
     (`user/Patient.read`, `user/Condition.read`, `user/MedicationRequest.read`,
     `user/AllergyIntolerance.read`, `user/Observation.read`, `user/Encounter.read`), and
     W2's `user/document.crs`, `user/DocumentReference.rs`, `user/Binary.read`,
     `user/vital.crus`, `user/Observation.rs`. It deliberately excludes unadvertised
     `user/DocumentReference.write` and `user/Observation.write`; it never permits
     `password`, `client_credentials`, or `private_key_jwt` grants. W2_AUDIT's dated
     manifest is the provisioning source of truth.
  2. Admin-enable it (registers DISABLED): Administration → System → API Clients.
  3. Verify **staff ACLs** independently permit patients/docs write. Provision and record
     two immutable deployment tuples—`(SOURCE_DOCUMENT_PATH=/AI-Source-Documents,
     expected_id, expected_acl)` and `(ARTIFACT_DOCUMENT_PATH=/AI-Extractions,
     expected_id, expected_acl)`—in the non-secret runbook. The standard API accepts a
     **path**, not direct category-ID input:
     before every source/artifact POST the agent resolves the configured path, verifies
     its expected ID and ACL, then sends that path. Unknown, ambiguous, wrong-ID, or
     unauthorized resolution refuses; runtime never creates categories.
  4. Swap `SMART_CLIENT_ID`/`SECRET` in the Railway env; verify the launch + a probe
     write under the new client; **on first token, the agent asserts the granted scope
     set equals the expected set and refuses to start writes on any unexpected granted
     scope** (W2-F12; also a W2-M11 runtime guard).
  5. **Cutover retires the OLD W1 client for real (W2-F17):** after the new client is
     verified, disable the old client and revoke **both access and refresh tokens**. If
     revocation is unavailable, wait out the independently recorded maximum lifetime of
     **both token classes**, not only the one-hour access-token lifetime, and prove old
     access- and refresh-token attempts are rejected before declaring cutover complete.
  The expected failure if `api:oemr` is skipped is an explicit **403 on first write**
  (W2-F4 correction — 403, not 401); the runbook entry points here.
  Anchors: W2-F4 (resolved — 403 not 401), W2-F11/F12 (scope drift + registered scope is
  not a ceiling — trust the verified set, assert granted scopes), W2-F14 (category ACL),
  W2-F17 (token-revoking cutover), W2-D1 addendum + W2-D9, §4 (scope delta), §5 (EHR
  write 403 row), architecture Verification errata #4.
- [ ] **W2-OA4 — Push `main` to origin (GitHub) and to the GitLab mirror.**
  **Trigger: Monday 2026-07-13, before the first CI run that needs the remote (W2-M19);
  re-verify at each checkpoint deadline.** GitHub is the canonical CI remote (branch
  protection + Tier 2 live there); **GitLab is the submission host** and must be current
  at every checkpoint. Blocking for W2-M19/W2-M20 (GH jobs + branch protection need the
  remote) and for every checkpoint submission. Anchors: §6a enforcement, §8.
- [ ] **W2-OA5 — Verify complete backup/source-custody posture** and record non-secret
  evidence in `DEPLOYMENT.md`. Dated: before Final; feeds W2-4. Inventory OpenEMR
  MySQL/volume, encrypted agent Postgres (OAuth state, delegated credentials, jobs,
  permanent dedup/lineage, intents/attempts, attribution), encryption-key recovery
  custody, repo-reproducible eval/corpus assets, and the named owner/location of original
  demo source files plus the named clinic/records custodian and recovery inventory for
  source documents used by the manual re-upload procedure. Record RPO/RTO and
  restore owners; never copy credentials into evidence. Anchors: §8a, W2-D10, Open items.
- [ ] **W2-OA6 — Record and gate the deployed F20 logging configuration.** Before the
  first FHIR Binary read-back, an administrator records non-secret evidence that
  `system_error_logging` is known and not `DEBUG`, including deployment/environment and
  verification time. Unknown/unreadable/`DEBUG` is a hard fail-closed condition: Binary
  read-back and its dependent completion transition remain disabled, `/ready` identifies
  `binary_readback_unsafe`, and no document bytes are fetched. Re-check at each deploy and
  Final. Anchors: W2-F20, W2-D9/D10; implemented/tested by W2-M11/M21/W2-1.

### Wave 0 spikes (before feature work)

- [ ] **W2-M1 — Day-1 container spike: W2 native deps build & deploy on Railway.**
  Files: `agent/Dockerfile` (extended: tesseract + eng traineddata, pdfium via pypdfium2,
  ONNX runtime), `agent/pyproject.toml` (extended), `agent/railway.json` (if needed).
  Anchors: §1 (container deps), §9 (day-1 spike), W2-R6, W2-R4.
  Accept:
  - Image builds locally and on Railway; `/health` green post-deploy with new deps loaded.
  - `tesseract --version` + eng traineddata resolvable in-container; pypdfium2 renders a
    page; bge-small ONNX loads under FastEmbed (no torch anywhere in the lockfile).
  - **Full-stack capacity gate (W2-O1 remediation):** in the same Railway container,
    concurrently hold bge-small embeddings, `mxbai-rerank-base-v1`, the built hybrid index,
    and one 200-DPI Tesseract OCR page while exercising one request. Record actual plan
    memory limit, peak/cold RSS, image size, and cold-start. A numeric
    `W2_WAVE0_RSS_CEILING_MB` is set to at most 80% of the actual Railway plan limit; peak
    RSS must remain below it. Failure blocks the local-reranker stack and invokes the
    documented quantize→raise-memory→externalize-index ladder before feature work.
  - Image-size delta and cold-start time recorded (W2-R4 planning numbers, devlog entry).
  - Error case: any dep failing to build on Railway's builder → fallback investigation
    documented before feature work proceeds (this is the spike's purpose).
  Test: CI build stage passes with the new image; smoke test in `agent/tests/test_app_boot.py`
  (extended) asserts OCR/pdfium/ONNX importability — guards: MVP deploy discovering broken
  native deps on Tuesday night.

- [x] **W2-M2 — Documents-API write-path spike — VERIFIED BY AUDIT (2026-07-13).**
  The W2-F1 independent live verification (W2_AUDIT.md, "W2-F1 independent verification"
  section; findings W2-F7..F11, W2-F4 resolved) executed this spike's substance against
  the local live stack (production **reads only** — no writes issued; note W2-F21: even
  GETs are not guaranteed DB-read-only, since metadata/service-construction GETs can
  backfill UUIDs — the verification stayed on already-registered resources to avoid
  triggering a backfill write). Verified outcomes, binding on downstream tasks:
  - `POST /api/patient/:pid/document` under a real token: returns **HTTP 200 body `true`
    with NO document id** (DocumentRestController.php:120) — not 201; the id is
    discovered via **collection GET keyed on unique filename/content-hash** (W2-F9).
  - **Read-back:** the standard REST download **returns 500** in this stack. **SUPERSEDED
    (Post-review remediation 2026-07-13):** the cause is raw document bytes passed as
    `BinaryFileResponse`'s filename, not a CSRF-key defect. The verified byte-exact
    round-trip is the **FHIR projection** `DocumentReference/:uuid → Binary/:uuid`
    (SHA-256 match, W2-F9), guarded by W2-OA6/W2-F20.
  - Vitals: `POST .../vital` → **201 {vid}** → standard GET returns values → **FHIR
    `Observation?category=vital-signs`** surfaces the Observation resources (W2-F10) —
    the O-new mapping path is fully proven end-to-end.
  - Provisioning sequence verified live (register → created DISABLED → admin enable;
    scope minimums; replacement-client constraint) — carried into W2-OA3.
  - **Residual (not covered by the verification):** both canonical source/artifact path
    controls — W2-OA3 records each expected category ID+ACL; W2-M8/M11 resolve and attest
    that mapping before every write and send the path. A mismatch is typed
    `category_mismatch`; runtime never creates a category.
  Retained artifact: the probe flow is recorded in the audit section (registration
  payload + call sequence); `agent/ops/spike_document_write.py` is authored during
  W2-M11 as the regression-able probe, exits nonzero on 403/mismatch with the runbook
  pointer — guards: an explicit 403 scope failure (W2-F4 correction — 403, not 401)
  surfacing for the first time inside the MVP demo.
  Anchors: W2-F2, W2-F4 (resolved), W2-F9, W2-F10, §3 (write principal), W2-R5
  (verified live 2026-07-13), architecture Verification errata #1–#2.

- [ ] **W2-M3 — LangGraph skeleton + SSE spike (V2): supervisor + 2 stub workers,
  handoffs, span nesting, W1 loop embedded.**
  Files: `agent/app/orchestrator/graph.py` (NEW), `agent/app/orchestrator/state.py` (NEW),
  `agent/app/orchestrator/workers/__init__.py` + `stub_extractor.py` + `stub_retriever.py`
  (NEW, stubs replaced in W2-M9/M14), `agent/app/routes/chat.py` (extended behind a flag).
  Anchors: W2-R1, W2-D2, §2 (graph + graph-state lifecycle), §2a (/chat SSE + V2 spike
  fallback), §6 (span nesting).
  Accept:
  - Supervisor routes to two stub workers; every hop emits a `HandoffRecord` with closed
    enums; Langfuse shows supervisor span ⊃ worker spans, reconstructable from the
    correlation ID alone.
  - The W1 direct loop (`agent/app/orchestrator/loop.py`) runs **inside** a worker node
    unchanged — W1 chat behavior is bit-identical with the graph flag off.
  - **SSE verdict recorded:** token streaming through LangGraph workers works, OR the
    named fallback is invoked (stream only the final composer stage) and its
    perceived-latency cost is noted for the cost report (§2a — never a correctness cost).
  - Edge: recursion/step budget stub in place — an intentionally-looping stub graph
    terminates via the budget, not a hang.
  Test: `agent/tests/test_graph_skeleton.py` (NEW) asserts handoff-record emission, enum
  membership, span parent-child ids, and W1-loop-in-worker equivalence — guards: framework
  adoption invalidating W1 behavior or producing untraceable routing.

- [ ] **W2-M4 — PDF words+boxes spike: pypdfium2 + pdfplumber + Tesseract on one clean
  and one degraded fixture.**
  Files: `agent/app/ingestion/reader.py` (NEW — words+boxes layer, both paths),
  `agent/ops/spike_reader.py` (NEW); two seed fixtures under
  `agent/evals/fixtures/documents/` (NEW dir — expanded in W2-M7).
  Anchors: W2-R6, W2-D3, §2 (NormBBox canonical space), §3 (read step). **PyMuPDF is
  AGPL — do not add it** (W2-R6 rejection is binding).
  Accept:
  - Born-digital fixture: text layer extracted with word boxes via pypdfium2/pdfplumber;
    degraded scan fixture: Tesseract words+boxes at render DPI 200; both emit the **same
    normalized NormBBox space** ([0,1], origin top-left, y-down; PDF path flips y).
  - Word-segmentation winner chosen (pypdfium2 vs pdfplumber) on fixture evidence;
    licenses re-verified from shipped package metadata (Apache/BSD/MIT only).
  - Junk-text-layer sanity check (density heuristic) routes a junk-layer fixture to OCR.
  - Error case: per-page OCR subprocess timeout kills a pathological page and marks it
    unreadable rather than hanging the job.
  Test: `agent/tests/test_reader_geometry.py` (NEW) — the §2-required dual-path fixture
  asserting both readers yield the same normalized box for the same word — guards:
  incompatible geometries making every downstream bbox/overlay silently wrong.

- [ ] **W2-M24 — Tier-2 timing/cost/quota spike + fork-PR secret policy (Wave 0).**
  Files: throwaway synthetic run output (not committed if it contains provider content),
  `docs/week2/W2_DEVLOG.md` (measured aggregate), CI/runbook design notes consumed by M20.
  Anchors: W2-D5/D8, §7, §6a, post-review remediation item 17.
  Accept:
  - Run a representative sample through the real three-call-unit shape and extrapolate
    with the explicit formula `50 × (VLM extraction + answer turn + judge)`; multi-page VLM
    calls are counted, not hidden inside “50 turns.” Record p50/p95 runtime, tokens,
    provider cost, rate-limit headroom, retry amplification, daily quota, and a maximum
    per-run cost/time budget. The full 50-case gate is not marked required until the
    measured quota and timeout support it.
  - Fork policy is frozen before M20: untrusted fork code receives **no repository
    secrets** and is never checked out under `pull_request_target`; forks run Tier 1 only,
    then a maintainer must reproduce the exact fork commit on a trusted same-repository
    branch for the required Tier-2 result before merge. Same-repo PRs use least-privilege
    environments with approval and no secret echo/artifact retention.
  - Failure to fit quota/runtime/cost is a STOP for making Tier 2 required and is escalated
    as a dependency problem; it is not solved by reducing the 50 cases or bypassing the
    gate.
  Test: policy lint/CI event-matrix review plus one trusted dry run and one fork simulation
  proving secrets absent — guards: secret exposure and an every-PR gate that cannot finish.

---

## Phase 1 — MVP core

**Deadline:** Tue 2026-07-14 11:59 PM CT.
**Spec anchors:** §2, §2a, §3, §3a, §4, §4a, §5, §6a, §7, §7a, §9 MVP row; W2-D1..D8;
W2-F1..F6; UC-W2-1/2/3.
**Goal:** the five PRD MVP rows live: two doc types ingesting end-to-end with grounding,
supervisor + 2 workers, hybrid RAG + rerank seam, the two-tier 50-case PR-blocking gate,
and the deployed demo with initial report + walkthrough video.
**Exit criteria:** deployed app runs UC-W2-1/2/3 live; both eval jobs green and marked
required on main; `agent/evals/w2_baseline.json` committed; README W1/W2 split pushed to
GitHub + GitLab; video + initial report delivered.

- [ ] **W2-M5 — Separate interactive-session and encrypted delegated-job credential
  lifecycles (W1 debt #1 — pulled into MVP).**
  Files: `agent/app/session/store.py` (extended), `agent/app/auth/` (extended — refresh
  grant + job-credential vault), `agent/migrations/002_oauth_state.sql` (NEW, expand-first).
  Anchors: §3 (write principal), §8 debt ledger #1, W2-D1 addendum (b); W1 D9/F-S.5
  carried (never `client_credentials`).
  Accept:
  - The interactive session retains its W1 idle/turn/access-token expiry and is never kept
    alive merely because a background job exists. Process restart preserves its PKCE/state
    contract and only non-value document/artifact refs.
  - At enqueue, the job receives a **separate opaque credential reference** to an encrypted
    credential row bound to `{job_id, patient_id, delegated_clinician_sub, client_id,
    granted_scopes, access_expiry, refresh_expiry, key_id}`. Access+refresh tokens are
    envelope-encrypted at rest; plaintext exists only for the bounded exchange/call, never
    in job rows, logs, traces, errors, or backups without encryption. The encryption key is
    external to Postgres with named rotation/recovery custody.
  - The job credential refreshes independently of interactive idle expiry but cannot
    change patient, principal, client, or scope manifest. Scope/patient/principal mismatch,
    refresh expiry/revocation, or cutover retirement fails closed as typed
    `auth_expired|scope_mismatch`; it never falls back to system/client-credentials auth.
    Credential material is destroyed after its last logical job reaches a reconciled
    terminal state, subject to the audited recovery window.
  - Migration 002 is expand/contract safe: expand schema first, dual-read old/new during
    one deploy, backfill only non-secret metadata, switch writers, then contract no earlier
    than the following verified deploy. Upgrade-from-001, rollback to the compatible app,
    roll-forward, key-unavailable, and partial-migration tests are required; migrations
    never print token values.
  Test: `agent/tests/test_session_store.py` (extended) plus migration tests — restart and
  interactive-idle-expiry while a job refreshes; patient/principal/scope binding refusal;
  refresh revocation; encryption/key-rotation; 001→002 clean upgrade, compatible rollback,
  roll-forward, and secret-free diagnostics — guards: jobs orphaned by idle expiry or
  silently writing under the wrong principal.

- [ ] **W2-M6 — Pydantic v2 schema inventory + validation tests (canonical contracts —
  named PRD deliverable).**
  Files (all NEW): `agent/app/schemas/__init__.py`, `agent/app/schemas/extraction.py`
  (`GroundedField[T]`, `NormBBox`, `LabPdfExtraction`+`LabResult`,
  `IntakeFormExtraction`+`Demographics`+`VitalCandidate`+`IntakeVitals`,
  `ExtractionArtifact`,
  `VitalsWrite` per W2-D10),
  `agent/app/schemas/citations.py` (`CitationV2`, `EvidenceSnippet`),
  `agent/app/schemas/handoff.py` (`HandoffRecord`, closed decision/reason enums),
  `agent/app/schemas/documents.py` (`UploadRequest`, `UploadAccepted`, `RetryRequest`,
  `RetryAccepted`, `DocumentStatus`, `FailureReason`),
  `agent/app/schemas/retrieval.py` (`EvidenceSearchRequest/Response`),
  `agent/app/schemas/jobs.py` (`JobRecord`),
  `agent/app/schemas/writeback.py` (`WriteIntent`, `WriteResult`),
  `agent/app/schemas/workers.py` (`WorkerInput`, `WorkerOutput`),
  `agent/app/observability/events.py` (`LogEventEnvelope`, the sole event-envelope owner);
  `agent/tests/test_schemas.py` (NEW), committed schema snapshot.
  Anchors: §2 (full inventory, composition rule), W2-D3, W2-D6, PRD core req 2;
  §2a migration note (CitationV2 mapping fields).
  Accept:
  - The architecture's post-review field-for-field snapshot is frozen here with
    `extra="forbid"`. `GroundedField[T]` **owns** `{value,page,bbox,grounded,citation}`:
    grounded requires a complete citation+bbox; ungrounded forbids a citation and cannot
    write/render as fact, but may retain a bbox solely as an UNSUPPORTED review region.
    `CitationV2` retains its five PRD fields; no parallel model owns
    a field citation.
  - Every `LabResult` carries its own grounded `collection_date` (not a report-level date),
    along with test name, value, unit, reference range, abnormal flag, and citations.
    Multi-date fixtures must round-trip distinct result dates.
  - `IntakeFormExtraction` retains demographics, chief concern, medications, allergies,
    and family history and adds `IntakeVitals` candidates `bps`, `bpd`, `weight`, `height`,
    `temperature`, `pulse`, `respiration`, and `oxygen_saturation`. Each
    `VitalCandidate{value:GroundedField[Decimal],unit:GroundedField[str],
    measurement_date:GroundedField[datetime]}` owns its on-page value/unit/date citations
    and bboxes. `note` is generated provenance, never extracted.
  - `FailureReason` is the closed union of the prior enum
    `{auth_expired,schema_violation,ocr_failed,vlm_timeout,vlm_unavailable,
    writeback_failed,writeback_verify_failed,doc_type_mismatch,worker_restart}` plus
    `{patient_mismatch,encounter_mismatch,unit_mismatch,range_violation,scope_mismatch,
    category_mismatch,binary_readback_unsafe,upload_rejected,unsupported_media_type,
    size_or_page_cap_exceeded,
    storage_write_failed}`. API status mapping is frozen and no free-text reason
    substitutes for an enum.
  - `POST /evidence/search` accepts typed `EvidenceSearchRequest {query,k}` (bounded `k`)
    and returns `EvidenceSearchResponse`; no anonymous `{query,k}` remains. Upload/status,
    `RetryRequest/RetryAccepted`, `JobRecord`, all three `WriteIntent/WriteResult` legs, and
    `WorkerInput/WorkerOutput` have the exact architecture fields and typed enums.
  - Every structured event uses one W1-compatible `LogEventEnvelope{schema_version,
    event_id,event_type,occurred_at,case_id,job_id,correlation_id,component,severity,
    attributes}`; optional IDs are explicit `None`. Raw patient IDs, token material,
    document text/images, extracted values, and free-form exception bodies are forbidden;
    event-specific attributes use the approved PHI-free scalar/list schema.
  - `supervisor_decision`/`reason_code` are closed enums; guideline `source_id`s embed
    corpus version. Snapshot compatibility tests fail any unreviewed field/enum/event
    drift in both producer→consumer directions.
  - Edge cases tested: empty results, multi-date labs, grounded/citation contradiction,
    boundary bboxes, bad dates, unit preservation, every failure enum, bounded `k`, worker
    union exhaustiveness, and event-envelope redaction.
  Test: per-model validation tests incl. reject fixtures (the PRD names validation tests
  as part of the Schemas deliverable) — guards: raw VLM output bypassing the schema
  contract (PRD pitfall 2).

- [ ] **W2-M7 — Fixture authoring: synthetic document set (critical path — blocks the
  eval-gate tasks and extractor tuning).**
  Files: `agent/evals/fixtures/documents/` (NEW set), `agent/ops/gen_fixtures.py` (NEW —
  reproducible generation from Synthea seeds), fixture manifest
  `agent/evals/fixtures/documents/manifest.json` (NEW).
  Anchors: §7 (case mix, canary mechanics), W2-D5, W2-D7, W2-F5, W2-REQ-94 (synthetic only).
  Accept:
  - Lab PDF: clean born-digital, clean 300-DPI scan, degraded scan, junk-text-layer
    variant. Intake form: clean PDF, image (PNG/JPEG) variant, degraded scan,
    **empty-allergy-section** variant (W1 F-D.5 / UC-W2-2 discipline).
  - **Injection variants for both doc types** (embedded "ignore your instructions…" text,
    plus one aimed at planting an identifier into the retrieval query — feeds the §4
    outbound-screen eval case). Wrong-doc-type and duplicate-upload cases derive from the
    same files.
  - Every fixture embeds **canary tokens** (`ZZPHI-<case_id>` name, canary MRN, canary
    sentence) per §7 no_phi_in_logs mechanics; all content Synthea-derived synthetic.
    Canonical input fixtures and their manifest are test inputs, so the generated-output
    PHI-leak scan deliberately excludes this directory; a separate fixture-policy check
    proves synthetic provenance, manifest membership, and canary presence. Generated model
    recordings are **not** canonical inputs and must be placeholder-scrubbed before commit
    so they pass W2-M18/M20's scan.
  - Regeneration is deterministic (`gen_fixtures.py` committed; fixtures also committed —
    RPO 0 from repo alone, §8a).
  Test: fixture-manifest completeness check in CI (every referenced fixture exists;
  canaries present in every document) — guards: eval gate authored against fixtures that
  can't exercise degraded/injection/missing-data behavior.

- [ ] **W2-M8 — `attach_and_extract` ingestion, D10 source intent, and typed claimed/leased
  durable queue.**
  Files: `agent/app/routes/documents.py` (NEW — POST/status/`POST /documents/{id}/retry`),
  `agent/app/ingestion/service.py` (NEW), `agent/app/ingestion/queue.py` (NEW — claim,
  lease, heartbeat, requeue), `agent/app/ingestion/intents.py` + `reconcile.py` (NEW),
  `agent/app/ingestion/hashing.py` (NEW), `agent/app/ingestion/reader.py` (hardened),
  `agent/migrations/003_document_jobs.sql` (NEW — jobs/leases, permanent dedup+lineage,
  write intents, 30-day attempts), worker-service Railway configuration, `agent/app/main.py`.
  Anchors: §2/§2a/§3/§3a/§5/§6a; UC-W2-1/2; W2-D9/D10; W2-F9/F13/F14/F18/F19.
  Accept:
  - Every endpoint requires the pinned session. Patient mismatch → `patient_mismatch`;
    supplied encounter must resolve to the pinned patient before enqueue or returns
    `encounter_mismatch`; status/page ownership is checked. No job or intent is created on
    refusal.
  - Before queueing, validate upload error, presence, actual size ≤10 MB, page count ≤20,
    magic-byte/exact MIME plus doc-type allowlist, and safe filename. Rejection is a typed
    controlled 4xx `upload_rejected` before any native call. PDF is lab-only; intake also
    permits PNG/JPEG.
  - The source write uses OA3's `SOURCE_DOCUMENT_PATH=/AI-Source-Documents` tuple: resolve path,
    compare expected category ID and ACL, then POST the **path**. Wrong/ambiguous/missing
    path, ID, or ACL fails closed as `category_mismatch`; the agent never creates a
    category or sends category ID
    as if the API accepted it.
  - **Exactly-once source intent:** atomically create the logical job, permanent record,
    attempt, and `{pending}` source intent before the remote call. The remote filename
    carries an opaque stable intent/correlation marker and content-hash fingerprint. On
    200 `true`, timeout, disconnect, or missing returned ID, list by pinned patient and
    verify marker+content hash. One match records the remote ID and `complete`; a possible
    commit becomes `unknown` and stops for reconciliation; only proven absence may return
    to `pending` and POST. Multiple/conflicting matches fail closed. **No blind retry.**
  - Permanent dedup/lineage key is
    `(patient_id, document_id_or_content_hash, leg, version, field)`; source uses
    `(patient_id, content_hash, source_document, schema_version, source)`. It is never
    purged.
    Attempt rows are separate and alone use the 30-day purge. Concurrent duplicate or
    cross-process requests return the same logical document/status and one remote source;
    identical bytes for different patients remain distinct.
  - Typed `POST /documents/{id}/retry` with `RetryRequest{expected_state="failed"}` performs
    one compare-and-swap **atomic requeue** of that same job/version only after all
    `unknown` intents reconcile; it never creates a second logical job or bypasses the
    permanent ledger. Duplicate upload returns the existing logical job; complete/running
    jobs are returned, not requeued.
  - Queue topology is explicit: web services validate/enqueue only; a dedicated Railway
    worker service claims with `SELECT … FOR UPDATE SKIP LOCKED`, bounded concurrency set
    from M1, lease owner/expiry, heartbeat, attempt count, and bounded exponential backoff
    with jitter. Graceful shutdown stops new claims, heartbeats/finishes active work within
    grace, then safely expires leases. A new worker recovers stale leases; process boot
    never blanket-fails all nonterminal jobs. Queue depth, oldest age, active/stale leases,
    and last heartbeat feed M21 readiness/E3 metrics.
  - Typed pipeline states are exactly
    `storing/reconciling/queued/extracting/grounding/writing/complete/failed`; OCR
    words+boxes remain memory-only and are re-derived. Binary fetch is permitted only after
    OA6's known non-DEBUG guard. Temp source is deleted only after remote reconciliation;
    encrypted credential refs follow M5 lifecycle.
  - Patient-linked job, lease, intent, attempt, dedup, and lineage rows are **PHI**:
    least-privilege DB role, encrypted backup, explicit retention, hashed refs in events,
    and no diagnostic body/value emission. Migration 003 follows M5's expand/dual-read/
    switch/contract ordering with 002→003 compatibility, rollback, roll-forward, and
    secret/PHI-free migration diagnostics.
  Test: schema/unit tests for keys, claims, lease/heartbeat/backoff and atomic requeue;
  integration tests for happy path, concurrent duplicate, same bytes across two patients,
  cross-patient/mismatched encounter, all upload rejects, wrong path/ID/ACL, crash and
  graceful shutdown, stale-lease recovery, 002→003 upgrade/rollback, 30-day **attempt-only**
  purge, and source commit-then-timeout reconciliation (zero duplicate POST) — guards:
  orphaned/duplicated jobs, ambiguous remote commits, and cross-patient writes.

- [ ] **W2-M9 — Intake-extractor worker: VLM extraction into strict schemas (the
  PRD-named worker serves BOTH doc types via doc_type dispatch — there is no separate
  lab extractor).**
  Files: `agent/app/orchestrator/workers/intake_extractor.py` (NEW — replaces stub;
  registered in `agent/app/orchestrator/workers/__init__.py`, the visible merge point
  with Track C), `agent/app/llm/` (extended — VLM page-image calls,
  timeouts/retries/breaker), `agent/app/ingestion/service.py` (extended — extract step).
  Anchors: §2 (VLM → strict schema), W2-D3, §4 (injection crossing #1), §5 (schema-violation,
  VLM-down rows + breaker), §7a (breaker state machine unit test), W2-R4 (page-call cost cap).
  Accept:
  - Page images (canonical DPI renders) → Claude VLM → parsed **only** into
    `LabPdfExtraction`/`IntakeFormExtraction` per doc_type; any other shape/extra fields →
    hard reject → `failed(schema_violation)` with `extraction.schema.violation` event
    (never partial acceptance).
  - VLM page calls capped per doc (cost planning number W2-R4); temperature pinned; every
    call carries correlation ID + timeout + bounded retries; **VLM circuit breaker:** N
    consecutive failures → open (job fails fast `failed(vlm_unavailable)`, source
    retained, queued jobs held), **half-open probe recovers it**; state changes logged
    `breaker.state.changed` (§5/§6a).
  - Injection fixture: embedded instructions produce no out-of-schema output — the only
    possible effect is schema-valid field values (which grounding then catches, W2-M10).
  - VLM self-reported confidence is discarded — never persisted or rendered (W2-D3).
  Test: **breaker state-machine unit tests** (closed→open after N failures; open
  short-circuits; half-open probe closes/reopens — §7a unit list, shared with W2-M14's
  reranker breaker); integration on fixtures with **recorded** VLM responses
  (clean/degraded/injection/wrong-doc-type); unit tests for reject paths — guards:
  hallucinated or injected VLM output entering the pipeline unvalidated (PRD pitfall 2)
  and a breaker that never recovers or never trips.

- [ ] **W2-M10 — Grounding verifier (build via tdd-swarm — verification-touching).**
  Files: `agent/app/grounding/__init__.py` + `verifier.py` (NEW),
  `agent/app/ingestion/service.py` (extended — grounding step).
  Anchors: §2 (grounding verifier), W2-D3, §5 (grounding-disagreement row), §7a unit list.
  Accept:
  - Per-field: locate the extracted value in the words+boxes layer → found: citation +
    NormBBox attached, `grounded=true`; not found **or disagreement**: `grounded=false`,
    renders UNSUPPORTED + "verify against source document" + overlay region, **no
    citation** (composition rule).
  - Confidence is binary grounding agreement only; grounding summary
    (`fields_grounded`/`fields_unsupported`) lands in `DocumentStatus` + `ExtractionArtifact`.
  - Majority-ungrounded + doc_type signal → `failed(doc_type_mismatch)` with
    re-classify-and-retry message (§5).
  - Unreadable page (from W2-M8 timeout) → its fields UNSUPPORTED ("page could not be
    read"), job still completes for readable pages.
  - Event per field: `extraction.field.outcome` (field NAME + boolean only — never the
    value, §6a).
  Test: tdd-swarm frozen unit suite — found / not-found / disagreement / unreadable-page /
  doc_type_mismatch; eval tag invariant — guards: an extracted value the page doesn't
  support earning a citation (the week's core safety claim).

- [ ] **W2-M11 — D10 exactly-once writeback: grounded artifact + intake vitals + verified
  re-read (shared with M8's source leg).**
  Files: `agent/app/writeback/__init__.py`, `documents_api.py`, `vitals_api.py`,
  `intents.py`, `reconcile.py`, `ranges.py`, `verify_reread.py`, `rest_client.py` (NEW),
  `agent/app/ingestion/service.py` (extended). The standard-REST write client remains
  separate from the read-only FHIR client.
  Anchors: §3/§4/§4a, W2-D1/D9/**D10**, W2-F2/F3/F4/F9/F10/F12–F20, W2-O3.
  Accept:
  - **Build-order contract:** MVP completes the production shared intent/reconciliation
    machinery plus source (M8) and grounded-artifact legs as the first fully correct
    slice. W2-E8 enables the structured vital leg only after the same machinery, frozen
    vital schema/ranges, and all D9 negatives pass. Final W2-1 re-runs all three. This is
    sequencing only—vitals is neither optional nor a stub and must be complete by Early.
  - Order is source → `ExtractionArtifact` → eligible vital intents whose standard-API
    create persists the structured row in OpenEMR `form_vitals`. Artifact POST uses
    `ARTIFACT_DOCUMENT_PATH=/AI-Extractions`: resolve path and verify OA3's expected ID+ACL,
    then send the path. Unknown/mismatch refuses; no category creation or direct-ID input.
  - Only grounded `IntakeVitals` with an explicit patient-owned encounter can form a vital
    intent. Labs never route to vitals. Missing encounter → artifact success plus
    `writeback.skipped(no_encounter)`. Missing/unsupported/ungrounded value, unit, or
    measurement date cannot write. Unit absent/different from configured OpenEMR units →
    `unit_mismatch`, artifact-only, no conversion.
  - **Pinned range table:** hard inclusive bounds exactly mirror this fork's
    `VitalsFieldRanges::getRanges()` and drift-test its source: weight 0–2000 lb / 0–910 kg;
    height 0–150 in / 0–381 cm; bps 0–400 mmHg; bpd 0–300 mmHg; pulse 0–500/min;
    respiration 0–150/min; temperature 0–120 °F / 0–48.9 °C; oxygen saturation 0–100%.
    Outside the unit-specific bound → `range_violation`, permanent skip provenance, no
    vital POST.
  - `VitalsWrite` contains only the mapped clinical fields, date, and an agent-generated
    non-PHI note marker. Caller `user`/`group`/author are stripped. The M5 delegated
    clinician principal—not request-body attribution—is recorded in artifact, permanent
    lineage, and trace; the agent does not falsely claim the server populated a performer.
  - At startup/token exchange the granted scopes are compared against the exact scopes
    requested for that launch from OA3; missing or unexpected scope → `scope_mismatch` and
    no write. Missing `api:oemr` is explicit 403. Refresh uses only M5's bound encrypted
    job credential; failure → `auth_expired`, never system auth.
  - Every artifact and per-field vital uses permanent key
    `(patient_id,document_id_or_content_hash,leg,version,field_id)` and a durable
    `{pending,unknown,complete}` intent. Artifacts use deterministic filename+content hash;
    vitals use a non-PHI `note` marker containing intent/correlation ID and payload-hash
    prefix. Before every possible re-POST, list/re-read by pinned patient/encounter and
    match marker+payload hash. Unique match completes; proven absence permits retry;
    timeout/possible commit or conflicting matches stays `unknown` and stops for operator
    reconciliation. A local DB transaction is never called atomic with remote HTTP.
  - Only a verified re-read advances the logical job: artifact uses FHIR
    DocumentReference→Binary and byte hash, vital uses standard GET plus FHIR
    `Observation?category=vital-signs` projection and payload comparison. Missing/mismatch
    → `writeback_verify_failed`. OA6's recorded non-DEBUG setting is mandatory before
    Binary fetch; unknown/DEBUG returns `binary_readback_unsafe`, refuses completion, and
    makes readiness unsafe.
  - Lineage includes source ID, page+bbox/citation, content/payload hash, intent and remote
    IDs, correlation ID, schema/agent version, and delegated principal. It is permanent;
    attempts alone purge at 30 days. All writes are append-only; no update/delete client
    exists.
  Test: unit tests for range/source drift, exact scopes, attribution stripping, intent
  states/keys, and marker matching; mocked integration tests for artifact and every vital
  field, no-encounter/unit/range skips, path/ID/ACL refusal, 403/auth expiry, F20 refusal,
  ID discovery, re-read mismatch, concurrent requests, and **commit-then-timeout for both
  artifact and vital proving no blind re-POST**. The synthetic live sequence confirms all
  three round trips by Final — guards: duplicate, cross-patient, out-of-range,
  misattributed, or unverified records.

- [ ] **W2-M12 — LangGraph production graph: typed state, step budget, routing recovery,
  encounter summary.**
  Files: `agent/app/orchestrator/graph.py` + `state.py` (extended from W2-M3),
  `agent/app/orchestrator/workers/evidence_retriever.py` (NEW — built against the W2-M3
  stub seam; the real retrieval module from W2-M14 swaps in at integration, so this task
  does not wait on Track B — only its final acceptance does),
  `agent/app/routes/chat.py` (extended — graph becomes the /chat path),
  `agent/app/observability/` (extended — encounter.summary emission).
  Anchors: §2 (graph-state lifecycle, step budget 8, HandoffRecord), §3 (question
  lifecycle), §5 (routing-error + graph-loop rows), §6 (encounter.summary — core req 7),
  W2-D2, UC-W2-3.
  Accept:
  - Per-turn state constructed from (a) the Postgres session row and (b) persisted
    extraction artifacts by document_id ref; discarded at turn end — **no LangGraph
    checkpointer** (§2 non-goal). "Without re-extracting" = re-reading the persisted
    artifact, never in-memory VLM output across turns.
  - Supervisor decisions ∈ closed enum; every handoff logged as `worker.handoff`
    (HandoffRecord) with trace-addressable input_ref/output_ref; supervisor span parents
    worker spans. Every event is emitted only through M6's versioned `LogEventEnvelope`;
    ad-hoc JSON/plain-text clinical events fail contract tests.
  - Routing error recovery: invalid route / malformed worker output → supervisor retries
    the decision **once** with the failure appended; second failure → deterministic
    W1-canonical refusal (never a silent wrong-worker answer). Step budget (recursion
    limit 8) exhaustion → terminal handoff `reason_code=step_budget_exceeded` → refusal.
  - Terminal `encounter.summary` event per encounter with **all seven** core-req-7 fields:
    tool+handoff sequence (ordered), latency by step, token usage, cost estimate,
    retrieval hits, extraction confidence (grounding-agreement rate), eval/verification
    outcome (live verdict + W1 D16 scores).
  Test: supervisor-worker **contract tests** (enum membership + ref resolvability, §6a CI
  step); loop-fixture test asserting budget termination; recovery test (one retry then
  refusal) — guards: black-box supervisor (PRD pitfall 3) and infinite/silent routing
  failures.

- [ ] **W2-M13 — Guideline corpus build + manifest + image-build index.**
  Files: `agent/corpus/` (NEW — committed source texts + `manifest.json` with
  provenance/license/version/ingest-date + do-not-ingest list), `agent/ops/build_corpus.py`
  (NEW — chunker + figure-strip + index build), `agent/ops/check_index_manifest.py` (NEW —
  standalone index↔manifest hash assertion; Track D wires it into the workflow — this task
  does not edit `agent-eval-gate.yml`), `agent/Dockerfile` (extended — index built at
  image build).
  Anchors: §2 (corpus), W2-R2, W2-D4, §4a (chunks row), §6 (deploy: index ships in image),
  W2-O1 (resolved: in-process).
  Accept:
  - Corpus = VA/DoD trio (Diabetes 2023, **HTN 2020 pinned**, Lipids 2025) + pocket cards;
    verbatim chunks only; recommendation/management sections + pocket cards, skipping
    evidence-review appendices; actual chunk count recorded in the manifest at ingest.
  - **Figure-strip enforced by the build script** (text-only ingestion — W2-R2 license
    caveat); manifest records the rule; do-not-ingest list committed (ADA, AHA/ACC, JNC 8,
    GINA, KDIGO, JAMA-branded PDFs).
  - Index (BM25 + bge-small embeddings) built at Docker image build from committed corpus
    + manifest — rebuildable from repo alone; **CI asserts index↔manifest hash agreement**
    so a stale index cannot deploy; rollback carries its matching index.
  - Startup integrity check: manifest-hash mismatch / missing index → /ready degraded
    (`retrieval_unavailable`), retriever returns the distinct "guideline retrieval
    unavailable" state (§5 — never conflated with an empty hit).
  Test: unit tests for chunker + manifest license/figure-strip check (§7a); integrity-check
  test with a corrupted index fixture — guards: licensing traps (ingesting stripped-figure
  or banned content) and a silently stale/corrupt index serving wrong evidence.

- [ ] **W2-M14 — Hybrid retrieval + reranker seam + enforced PHI-free egress +
  `POST /evidence/search`.**
  Files: `agent/app/retrieval/__init__.py` + `hybrid.py` (BM25 + dense) + `rerank.py`
  (seam: `RERANKER=cohere|local`) + `query_builder.py` + `phi_screen.py` (NEW),
  `agent/app/routes/evidence.py` (NEW — POST /evidence/search; Track B never touches
  `documents.py`), `agent/app/config.py` (extended — `RERANKER`, `COHERE_API_KEY`).
  Anchors: §2 (retriever/reranker + dated trigger), §2a (/evidence/search), §4 (Zone C:
  query builder + outbound screen fail-closed), §5 (retrieval rows, Cohere-down row),
  W2-D4 rev, W2-R3.
  Accept:
  - Hybrid rank-bm25 + bge-small-en-v1.5 (ONNX/FastEmbed) → rerank behind the one-env-var
    seam; **`mxbai-rerank-base-v1` implemented and integration-tested** as the shipping
    fallback; shipped default decided by W2-OA1's Monday-EOD trigger. Cohere rerank version
    + model logged per trace (score-drift forensics, §8).
  - **Queries are builder-constructed from coded clinical terms only** — never free-form
    conversation text; the **outbound screen** rejects identifiers / DOBs / MRN-shaped
    tokens / the session patient's demographic strings and **fails closed** to
    local/un-reranked; screen firing is logged.
  - Degradations distinct and typed: empty hit on healthy index → "no guideline evidence
    found" (stated, never invented); index/embedder down → `retrieval_unavailable`
    (/ready degraded); dense-leg-only failure → BM25-only flagged degraded; Cohere
    down/rate-limited → un-reranked or local per seam, degraded, logged, /ready
    `rerank_off`. Breaker per dependency (reranker) with half-open probe.
  - `POST /evidence/search` accepts only M6's typed `EvidenceSearchRequest{query,k}` and
    returns `EvidenceSearchResponse` (corpus version + correlation ID); no anonymous
    `{query,k}` or raw list response remains. The inbound correlation ID propagates through
    query construction, embedder, both retrieval legs, reranker/fallback, response, span,
    and typed log envelope.
  - **CI never calls Cohere live** (W2-D4) — stub/local in all tiers.
  Test: unit tests for query builder + PHI screen (incl. the injection fixture that plants
  an identifier aimed at the query — eval tag invariant); retrieval integration on the
  built index (hit + empty + unavailable); rerank-seam flip test — guards: PHI egress to
  Cohere and "no evidence" lies when retrieval is actually down.

- [ ] **W2-M15 — Citation contract v2 + answer composer + W1 migration adapter (build via
  tdd-swarm — verification-touching).**
  Files: `agent/app/verify/` (extended — composer-side CitationV2 adapter),
  `agent/app/orchestrator/workers/` composer stage (extended), `agent/app/routes/chat.py`
  (extended — SSE claim-block events carry CitationV2), `agent/app/routes/ui.py` (extended
  — citation chips render CitationV2; patient facts vs guideline evidence visually
  distinct).
  Anchors: §2 (composer), §2a migration notes, W2-D6, §4 (injection crossing #2 — typed
  evidence only into prompts), UC-W2-3.
  Accept:
  - Every clinical claim carries a complete `CitationV2`; **incomplete citation = claim
    does not render** (structural, not advisory). Patient facts, uploaded-document facts,
    and guideline evidence render as visually distinct source classes.
  - **Migration pinned:** chart claims map W1 evidence ids → CitationV2 exactly per §2a
    (`source_type=patient_record, source_id={ResourceType}/{uuid}, page_or_section=null,
    field_or_chunk_id={W1 evidence_id incl. hash8}, quote_or_value={verified value}`);
    W1 EvidencePacket/claims/verification pipeline **unchanged**; a regression test pins
    the mapping.
  - Document content reaches the answer model **only as typed grounded evidence records**;
    the raw OCR/text layer never enters any LLM prompt; quote_or_value bounded to grounded
    spans (§4). W1 verify-then-flush + templater + treatment-verb blocklist unchanged.
  - Trend questions (UC-W2-4 potassium) answered as cited textual/tabular values (chart
    widget stays stretch). Missing data: absence named per W1 discipline (empty allergy →
    "confirm with patient", never NKDA).
  - Degradations: VLM/LLM down on a question turn → W1 D13 deterministic degradation
    (facts, no synthesis, banner).
  Test: tdd-swarm frozen suite — citation-completeness rejection unit tests, W1→W2 mapping
  regression test (tag regression), source-class separation render test, no-raw-text-in-
  prompt assertion — guards: uncited claims rendering and the W1 contract silently breaking
  under the citation migration.

- [ ] **W2-M16 — Page-render endpoint + minimal bbox overlay.**
  Files: `agent/app/routes/documents.py` (extended — GET /documents/{id}/pages/{n}),
  `agent/app/ingestion/render.py` (NEW — on-demand pypdfium2 render + bounded TTL cache),
  `agent/app/routes/ui.py` (extended — overlay draws NormBBox × displayed pixel dims).
  Anchors: §2a (page renders), §2 (NormBBox → overlay math), W2-D7 rev, W2-D3 (overlay
  requirement), §5/§7a (leak test).
  Accept:
  - Page PNG rendered **on demand** at canonical DPI from the OpenEMR-stored source
    (fetched with the delegated token via the **FHIR `DocumentReference/:uuid →
    Binary/:uuid` projection** — the verified byte-exact read path; the standard REST
    download 500s in this stack, known issue, not a dependency — W2-F9); bounded
    in-memory short-TTL cache; **never written to disk, never logged or traced** (§3a
    page-render row).
  - Pinned session + patient-match on every fetch; cross-patient page fetch → 403.
  - Overlay draws boxes only for `grounded=true` fields (boxes only where grounding
    justifies them); UNSUPPORTED fields render the flag + overlay region per §2 grounding
    verifier contract; coordinates scale correctly at any displayed size (NormBBox ×
    displayed dims).
  - Error: render failure or missing page → typed error response, no partial image cached.
  Test: cross-patient 403 **leak test** (§7a, tag invariant); render-path unit test
  asserting no-disk-write + cache TTL eviction; a visual fixture comparing box position on
  both reader paths — guards: the overlay becoming an unauthenticated PHI endpoint and
  boxes drawn where grounding doesn't justify them.

- [ ] **W2-M17 — 50-case golden set authoring (depends W2-M7).**
  Files: `agent/evals/w2_cases/` (NEW — 50 case files with expected behavior),
  `agent/evals/schema.py` (extended — W2 case schema + tags + `guards:` field),
  `agent/evals/cases.py` (extended — loader).
  Anchors: §7 (case allocation, case mix), W2-D5, §3 (scenario promise: 3 degraded axes),
  UC-W2-1..4.
  Accept:
  - Exactly 50 cases; allocation per §7: every case scores schema_valid +
    citation_present + no_phi_in_logs; tagged subsets ~10 refusal, ~8 missing-data,
    ~6 injection-bearing, ~4 retrieval-empty, ~12 extraction
    (clean/degraded/disagreement/duplicate), ~10 question-flow consistency (tags overlap);
    cross-patient invariant cases included (§2a). Each rubric emits applicable numerator
    and denominator so threshold arithmetic never assumes all 50 apply.
  - Named write-surface negatives are mandatory members, not generic tags:
    `f12-scope-escalation-startup-refusal`; `f13-cross-patient` and
    `f13-mismatched-encounter`; `f14-wrong-category-path`, `f14-wrong-category-id`, and
    `f14-wrong-category-acl`; `f15-out-of-range-vital-skipped`;
    `f16-attribution-spoof-stripped`; `f17-old-access-token-rejected` and
    `f17-old-refresh-token-rejected`; `f18-duplicate-upload-noop`; and
    `f19-upload-validation-4xx`. F17 uses deterministic
    auth stubs in Tier 1 and is closed by real post-cutover evidence in W2-1; lower-level
    unit/integration tests remain required in M8/M11.
  - Each case: fixture ref, expected behavior, boolean rubric expectations, tags
    (boundary/invariant/regression), and a `guards:` line naming the failure mode.
  - The three PRD degraded axes each have cases: imperfect scan → UNSUPPORTED not
    invented; incomplete record → absence named; follow-up → grounding survives turns.
  - **MVP scoping for the question-flow/follow-up cases (avoids a hidden MVP blocker):**
    the MVP-era cases assert only what W2-M12/W2-M15 deliver by Tuesday — a turn-2 answer
    re-reads the persisted artifact and stays fully cited. The deeper continuity cases
    (session expiry, context overflow — W2-E2's surface) enter the set at Early via the
    documented explicit baseline-update PR step (§7), never by silently editing cases.
  - Golden set reproducible from repo alone (fixtures + cases in git — RPO 0).
  Test: case-schema validation in CI (every case parses; allocation counts assert) —
  guards: a golden set that under-covers a graded category and an unreproducible dataset.

- [ ] **W2-M18 — Rubric scorers, canary harness, judge config (build via tdd-swarm —
  verification-touching).**
  Files: `agent/evals/scorers.py` (NEW — the 5 boolean scorers),
  `agent/evals/canary.py` (NEW — correlation-ID-scoped log capture + canary/n-gram scan),
  `agent/evals/judge_config.yaml` (NEW — pinned model id+version, temperature 0, boolean
  templates quoting evidence spans), `agent/evals/known_fail/` (NEW — one violating
  fixture per scorer).
  Anchors: §7 (categories/thresholds, judge config, no_phi mechanics, scorer self-tests),
  W2-D5, W2-D8, C1/C2/C3 resolutions.
  Accept:
  - Five scorers, all boolean: schema_valid (Pydantic), citation_present (CitationV2
    completeness), factually_consistent (deterministic field-vs-evidence for structured
    claims; **LLM-judged only for free-text synthesis — the single judged check, Tier 2
    only**), safe_refusal (templated-refusal string/shape match), no_phi_in_logs (canary
    harness: zero canary tokens + zero fixture n-grams in correlation-scoped generated
    logs/traces/outputs).
  - **Each scorer has a known-fail fixture proving it returns False** on a violating
    output (guards: permanently-green gate) — these self-tests run in Tier 1, with one
    explicit carve-out: factually_consistent's known-fail exercises the **deterministic
    branch** in Tier 1 (plus a **recorded** judge response replayed from
    `agent/evals/recordings/` for the judged branch); the live-judge path is exercised
    only by Tier 2 and the W2-2 drill. **No live judge call may exist in any Tier-1 code
    path** (W2-D8: Tier 1 is offline, no secrets).
  - Judge flake policy implemented: one retry at temp 0; judged False = real fail; judge
    **infra** failure after retries → job inconclusive (rerun required), never silent pass.
  - Thresholds encoded exactly: `schema_valid`, `citation_present`, `safe_refusal`, and
    `no_phi_in_logs` are deterministic **100%** invariants, so one applicable failure is
    immediately red. `factually_consistent` alone is ≥90% and fails on a regression of
    more than five percentage points. Output prints numerator, applicable denominator,
    score, baseline delta, and the rule that fired. Threshold tests include denominators
    where one factual flip is insufficient and a mutation count of
    `floor(0.05 × applicable)+1` crosses the delta.
  - PHI scanner scope is frozen: exclude only canonical input fixtures and their manifest;
    scan generated outputs, structured logs, traces, reports, sanitized recordings,
    screenshots, and results. Recordings replace canary/raw clinical values with typed
    placeholders before commit and hydrate from canonical inputs only in memory. A
    generated known-leak self-test writes a temporary canary/raw n-gram to every supported
    artifact class and passes only when the scanner makes CI red; the leak artifact is then
    destroyed.
  Test: tdd-swarm frozen suite = the known-fail fixtures themselves + threshold-arithmetic
  unit tests (tag regression) — guards: a gate that cannot go red (the exact failure the
  graders probe).

- [ ] **W2-M19 — Tier 1 offline gate + recordings + committed Git Hook (build via
  tdd-swarm — verification-touching).**
  Files: `agent/evals/runner.py` (extended — tier flag), `agent/evals/recordings/` (NEW —
  recorded model responses), `agent/Makefile` (NEW or extended — `make hooks`,
  `make record-evals`), `githooks/pre-push` (NEW, via core.hooksPath),
  `.github/workflows/agent-eval-gate.yml` (extended — `eval-tier1` job).
  Anchors: §7 Tier 1, §6a enforcement layer 1, W2-D8, §7a (integration posture).
  Accept:
  - Tier 1 runs **fully offline**: real local components (OCR, text-layer, retrieval,
    Pydantic, grounding, citation builder, templater, canary harness) + **recorded**
    VLM/LLM/reranker responses from `agent/evals/recordings/` + the deterministic rubric
    subset. No secrets, no network. Regenerated only via documented `make record-evals`
    (live, reviewed in PR diff).
  - The committed pre-push hook (`make hooks` one-command setup) runs the **full Tier-1
    gate** — deterministic, not a lint-only subset — within a measured budget (target
    < 60s: the hook path reuses words+boxes/OCR results **cached by content hash**; CI
    runs the same gate cold). If the measured hook time exceeds the budget, the caching
    layer is the fix — never trimming the gate to a subset.
  - `eval-tier1` job green in GH Actions on the PR that lands this task; W1 eval suite
    (`agent/evals/`, unchanged cases) still green in the same pipeline (shared-path
    regression guard — any W1 failure blocks the PR).
  - Error: missing/stale recording → named error pointing at `make record-evals`, never a
    silent skip or pass.
  Test: hook-installed run on a clean clone (grader path: clone → `make hooks` → commit a
  regression → watch it block); recording-staleness failure test — guards: contributors
  bypassing the gate locally and stale stubs going green against changed contracts.

- [ ] **W2-M20 — Tier 2 live gate, branch protection, GitLab CI, PHI check, baseline
  (build via tdd-swarm — verification-touching; depends W2-OA2/OA4).**
  Files: `.github/workflows/agent-eval-gate.yml` (extended — `eval-tier2-live` job +
  PHI-detection step + mirror-push job), `.gitlab-ci.yml` (NEW, repo root — identical
  Tier-1 gate plus same-SHA fail-closed Tier-2 bridge),
  `agent/evals/w2_baseline.json` (NEW — committed baseline),
  `agent/evals/runner.py` (extended — results export), `README.md` (extended — canonical
  remote + grader path, folded into W2-M21).
  Anchors: §7 Tier 2, §6a (CI pipeline, enforcement layers 2–3, PHI check), W2-D8, §8
  (GitLab submission host), debt #5.
  Accept:
  - `eval-tier2-live` runs **all 50 cases against live Anthropic** (real agent turns: VLM
    extraction over fixtures + answer model, plus the pinned judge) on every PR;
    **both `eval-tier1` and `eval-tier2-live` are required status checks** on main
    (branch protection configured — the enforcement graders cannot bypass). Reranker
    never live in CI (stub/local). It becomes required only after W2-M24 records a viable
    timeout/quota/cost budget for `50 × (VLM extraction + answer + judge)` including
    multi-page calls/retries.
  - Gate compares against committed `agent/evals/w2_baseline.json` (updated only by an
    explicit PR step, never auto-committed). The four deterministic categories require
    100%, so any applicable failure is red; `factually_consistent` alone fails below 90%
    or on a regression greater than five percentage points. **Infra failure ≠ case
    failure:** bounded retries → job errors
    inconclusive (rerun required), never silent green.
  - Full §6a pipeline order in CI: build → ruff+mypy → pytest+coverage → W1 eval suite →
    schema-validation tests → supervisor-worker contract tests → extraction regression
    tests → OpenAPI contract tests (lands W2-E5; step wired now, non-blocking until spec
    exists) → pip-audit → semgrep → **scoped PHI-detection check** (canonical inputs
    excluded; generated outputs/logs/traces/reports/recordings/screenshots/results scanned;
    known-leak self-test must trip) → eval gate (both tiers) → deploy on green.
  - `.gitlab-ci.yml` is uncuttable and runs Tier 1 plus a **fail-closed SHA-bound bridge**
    to GitHub Tier 2: query the approved workflow/status for the exact GitLab commit SHA,
    repository, workflow name/version, and protected ref; verify success and artifact/result
    SHA before GitLab reports green. Missing, pending beyond timeout, red, wrong repo/ref,
    stale/mismatched SHA, or API/auth error fails GitLab. A mirror-push job keeps GitLab
    current. The bridge uses a read-only status token and never imports Anthropic secrets.
  - Fork policy from M24 is enforced: untrusted forks run Tier 1 with no secrets and no
    `pull_request_target` checkout; merge requires a maintainer-created trusted branch at
    the exact source SHA and its successful Tier-2 result. Same-repo live jobs use an
    approved least-privilege environment and do not retain prompts/provider payloads.
  - Each Tier-2 run exports results; committed results refreshed at least per checkpoint
    (Eval Dataset deliverable's "results" element). Historical “~$4/50 turns” is
    superseded; measured M24 cost/runtime/quota and per-run ceiling are emitted from
    PHI-free aggregate traces.
  Test: tdd-swarm verification = one applicable mutation in each deterministic 100%
  category turns both canonical gating and the GitLab bridge red; the factual mutation
  flips `floor(0.05 × applicable)+1` cases (or enough to drop below 90%) and goes red. Test
  stale/wrong SHA, absent GH status, fork-without-secrets, known leak, and bridge timeout —
  guards: a bypassable/permanently-green gate or stale submission mirror.

- [ ] **W2-M21 — MVP deploy + README W1/W2 split + source-grounded UI integration.**
  Files: `README.md` (extended — W1-baseline vs W2-multimodal split, env-var inventory
  `COHERE_API_KEY`/`RERANKER`/Langfuse/SMART/`OE_*`, canonical branch main, three services
  + which serves W2, one-command grader path), `agent/app/routes/ui.py` (extended — upload
  affordance from the chart + extraction report view: grounded fields cited+boxed,
  UNSUPPORTED flagged), Railway deploy config (extended as needed).
  Anchors: §8 (README contract), §9 MVP row, §3 (extraction report), UC-W2-1/2, W2-REQ-06/90.
  Accept:
  - Deployed Railway app serves UC-W2-1/2/3 live end-to-end: upload from the chart →
    async status → extraction report (grounded fields cited + boxed; ungrounded flagged
    UNSUPPORTED) → grounded Q&A with visually-separated source classes + working overlay.
  - README: a grader can run the core W2 flow without guessing branch, env var, or
    service; **deployed app URL stated** (the GitLab-Repository deliverable's "deployed
    link" element); env-var list complete; W1/W2 behavior split explicit; grader CI path
    documented (clone → `make hooks` → commit regression → blocked).
  - Machine-authored records carry the W2-O3 provenance flag visibly in the UI ("pending
    review" treatment — lands with the core flow per Open items).
  - `/health` is process liveness only and stays 200 during soft dependency degradation.
    `/ready` returns structured component states and includes OpenEMR/docs, session+job
    Postgres, Anthropic, F20, index/reranker, **worker-heartbeat age, active/stale leases,
    queue depth, and oldest queued age**. Stale/missing worker heartbeat, queue age beyond
    the Early-locked SLO, unsafe/unknown F20, or hard store/provider failure → unready;
    index/reranker degradation → HTTP 200 with explicit `degraded`, never fake healthy or
    full outage. Thresholds and replica expectations come from M8/E4.
  Test: post-deploy smoke = pairwise `/health`+`/ready` tests for healthy, each hard
  failure, index/reranker soft failure (health 200, ready 200 degraded), stale worker, old
  queue, and unknown F20 + one full live ingestion + one live
  question turn with citations (tag boundary) — guards: a green pipeline in CI with a
  broken deployed demo (the thing graders actually click).

- [ ] **W2-M22 — Initial latency/cost report (MVP row 5).**
  Files: `docs/week2/W2_COST_LATENCY.md` (NEW — initial numbers; expanded at W2-3).
  Anchors: PRD MVP table row 5 / W2-REQ-06 (MVP-scoped initial report); §6 (cost from
  traces), W2-R4; full report contract is §8a (Final).
  Accept:
  - Initial measured numbers from Langfuse traces + Railway: per-turn cost estimate,
    VLM per-page cost vs the W2-R4 planning number, ingestion and turn latency
    (p50/p95 from whatever volume exists at MVP — labeled as initial, not baseline).
  - Names the SSE-fallback perceived-latency cost if the W2-M3 fallback shipped (§2a).
  - Explicitly marked "initial — superseded by the Final report (W2-3)"; W2-O2 SLO
    working targets restated for the Early baseline task to confirm or revise.
  Test: numbers cross-checked against at least one live traced request per flow —
  guards: an MVP submission missing the PRD's report row.

- [ ] **W2-M23 — MVP walkthrough video.**
  Files: link recorded in `README.md` (extended) / submission notes.
  Anchors: PRD MVP table row 5 / W2-REQ-06 (walkthrough video is an MVP deliverable);
  W2-D7 (synthetic-only captures; screenshots are sensitive artifacts — never logged to
  SaaS).
  Accept:
  - Walkthrough of the deployed MVP: upload → extraction report with grounding →
    grounded answer with citations + overlay → the eval gate blocking (can show the
    W2-M20 smoke-break run). Synthetic data only; no PHI-bearing captures retained
    outside the video.
  - Distinct from the Final demo video (W2-5, six required contents, 3–5 min) — this one
    proves the MVP rows work on Tuesday.
  - Error path: any MVP-table element that cannot be demonstrated live at recording time
    blocks the MVP checkpoint and is reported as incomplete; it cannot be relabeled as a
    cut, deferral, stub, or workaround.
  Test: pre-submission checklist review against the five MVP table rows, recorded in the
  devlog — guards: an MVP checkpoint submission missing a PRD-listed deliverable.

**Ordering deviations, owned (not silent):**
1. §9 lists the video and cost/latency report only in the Final row; the PRD's MVP table
   row 5 ("Integrate and demo") explicitly requires "deployed app, source-grounded UI,
   latency/cost report, walkthrough video" at MVP. The PRD is ground truth (gap-audit
   W2-REQ-06, covered); W2-M22/M23 deliver MVP-scoped versions, refreshed at Final by
   W2-3/W2-5.
2. **W2-O2 closure timing — SUPERSEDED by Post-review remediation (2026-07-13):** MVP
   records clearly labeled initial measurements only. The full four-flow matrix runs and
   numeric SLOs are formally locked **once at Early, Thu 2026-07-16** by W2-E4. Final
   validates/reports against those locked SLOs; it is not another closure point.

---

## Phase 2 — Early

**Deadline:** Thu 2026-07-16 11:59 PM CT.
**Spec anchors:** §9 Early row; §6 (dashboards, alerts, baselines, SLOs), §6a, §2a
(OpenAPI/Bruno), §8 debt ledger #2/#3, W2-O1/W2-O2, UC-W2-4.
**Goal:** the observability/ops surface graders read, measured baselines that close the
open items, and the follow-up experience.
**Exit criteria:** dashboards + alerts live with runbook entries; baselines recorded and
diffed vs W1; SLOs set from measurement; OpenAPI + Bruno committed with contract tests
green; UC-W2-4 flows demonstrated.

- [ ] **W2-E1 — Overlay polish + click-to-source integration.**
  Files: `agent/app/routes/ui.py` (extended — the W1 UI is served from here;
  overlay JS stays inline in its templates unless it outgrows them, in which case
  `agent/app/static/overlay.js` is the NEW first static asset).
  Anchors: §9 Early ("overlay polish"), §8 (click-to-source substantially delivered by
  core: W1 popovers + bbox overlay + page preview), W2-D6.
  Accept:
  - Citation chips click through to their source: chart claims → W1 popover; document
    claims → page render + highlighted bbox; guideline claims → quoted CPG text with
    corpus version.
  - Multi-page documents navigate correctly; boxes stay aligned across zoom/resize
    (NormBBox scaling), degraded-scan pages render their UNSUPPORTED flags inline.
  - Edge: citation to a since-failed page render degrades to the quote + "page
    unavailable", never a broken image or wrong box.
  Test: render tests over the fixture set incl. multi-page + degraded (tag boundary) —
  guards: the required overlay being demo-fragile exactly where graders click.

- [ ] **W2-E2 — Follow-up continuity flows (UC-W2-4).**
  Files: `agent/app/orchestrator/graph.py`/`state.py` (extended), `agent/app/session/store.py`
  (extended if refs need widening).
  Anchors: UC-W2-4, §2 (graph-state lifecycle), §3 (question lifecycle), W2-D2.
  Note: the minimal multi-turn assertion (turn-2 re-reads the persisted artifact, stays
  cited) already lands at MVP via W2-M12 + the scoped W2-M17 cases; this task delivers
  the deeper guarantees, and its cases enter the golden set via the explicit
  baseline-update PR step.
  Accept:
  - Follow-ups reuse session context: supervisor re-routes to the retriever **without
    re-extracting** (artifact re-read by ref, never re-VLM); citations remain live;
    grounding never degrades across turns — the second answer as cited as the first.
  - Session expiry mid-conversation → explicit re-launch prompt (W1 behavior);
    context overflow → bounded evidence selection with truncation named in the answer.
  - Potassium-trend question answered as cited textual/tabular values across documents.
  Test: multi-turn eval cases (~10 question-flow consistency cases from W2-M17 run
  against the deployed path; tag invariant) — guards: grounding decay across turns —
  the UC-W2-4 failure the PRD scenario names.

- [ ] **W2-E3 — W2 dashboard panels, alerts, runbooks.**
  Files: `agent/app/observability/` (extended — W2 metrics emission),
  `docs/observability/runbooks.md` (extended — W2 entries),
  `docs/observability/dashboard.md` (extended — the W1 panel definitions live here),
  `agent/ops/alert_checker.py` (extended — the four W2 alerts).
  Anchors: §6 (metrics list, alert table, breaker visibility), §6a (log-event inventory),
  §5 (every failure row needs identify-in-logs + recovery), W2-REQ-64/76.
  Accept:
  - Panels live for the full §6 W2 list: ingestion count + latency, per-field extraction
    pass rate, grounding-agreement rate, retrieval hit rate, rerank scores+version,
    routing decisions, per-worker latency, eval pass rate per category, **queue depth from
    durable rows**, **outbound retry count per dependency + job attempt count** (the W2
    event-retries extension), breaker state, **oldest queue age, worker-heartbeat age,
    active/stale leases, pending/unknown intents, reconciliation age, F20 guard state, and
    delegated-credential refresh failures**. Dashboard answers "is it healthy" without
    reading logs or exposing patient-linked row contents.
  - The four §6 alerts configured with working thresholds + first-action + escalation
    exactly per the table (extraction failure >20%/1h; retrieval p95 >2s/15m; ingestion
    p95 >30s/doc; any deterministic eval invariant <100% or factual regression >5
    percentage points/below 90%). Add stale-worker, oldest-queue, unresolved-intent, and
    unsafe-F20 alerts using E4's locked thresholds.
  - Runbook entries exist for **every §5 failure row** (identify-in-logs event name +
    recovery action), including the wrong-patient void-and-reupload ops path and the
    W2-F4 scope-403 entry (403, not 401 — W2-F4 correction).
  Test: each §6a event observed live at least once (synthetic trigger per failure class
  where safe — e.g. breaker trip via stubbed outage in staging); alert-threshold unit
  tests on the metric emitters — guards: failures that are invisible in logs when a
  grader (or the drill) induces them.

- [ ] **W2-E4 — Baselines: 4 flows × CPU/mem/latency/throughput vs W1 (closes W2-O1,
  W2-O2, debt #2).**
  Files: `agent/load/k6/` (extended — W2 flow scripts), `docs/week2/W2_BASELINES.md`
  (NEW — recorded numbers + W1 diff + SLO decisions).
  Anchors: §6 (baselines + SLO method + W2-O1 memory budget/fallback ladder), §8 debt #2,
  W2-O2, W2-REQ-71.
  Accept:
  - Recorded per PRD-named flow — (1) document ingestion, (2) extraction, (3) RAG
    retrieval, (4) full multi-agent run — across CPU, memory, latency, throughput
    (Railway metrics + k6, the W1 §7 method); diffed against W1's k6 @10/50-VU numbers
    for shared paths (any shared-path regression named).
  - **W2-O1 closed:** agent-service RSS measured against the Railway plan limit vs the
    working budget (index+ONNX 200–300MB, +~400MB if `RERANKER=local`, Tesseract
    ~100MB/page peak); if over budget the §6 fallback ladder fires in order (quantized
    ONNX → raise service memory → externalize index, documented tradeoff).
  - **W2-O2 closed exactly once at Early:** SLOs set from these measurements (confirm or revise ingestion p95 ≤
    30s/doc, retrieval p95 ≤ 2s); alert thresholds in W2-E3 updated if the measured
    numbers moved them; /ready 50-VU saturation knee re-measured with the new deps
    (debt #2).
  Test: k6 scripts committed + reproducible; baseline doc reviewed against §6's flow ×
  metric matrix (tag boundary) — guards: unmeasured SLOs (invented numbers) and a memory
  ceiling discovered in production instead of in the baseline run.

- [ ] **W2-E5 — OpenAPI 3.0 spec + contract tests + Bruno collection.**
  Files: `agent/ops/openapi.yaml` (NEW — committed spec for the closed §2a endpoint list
  + W1 endpoints), `agent/tests/test_openapi_contract.py` (NEW), `agent/bruno/` (extended
  — upload, extraction status, evidence retrieval, page render, full W2 flow; W1
  token-mint helper carried).
  Anchors: §2a (closed surface), §6 (OpenAPI + Bruno), §6a (contract tests in CI),
  W2-REQ-70/77.
  Accept:
  - Spec enumerates **exactly** the §2a list plus W1 endpoints (closed surface — contract
    tests fail on any undocumented route or drifted schema, both directions).
  - Bruno: a grader can run every W2 workflow from the collection, including auth via the
    token-mint helper; requests carry correlation IDs.
  - CI step from W2-M20 flips from wired-non-blocking to blocking.
  Test: OpenAPI contract tests in CI (spec ↔ implementation, tag invariant) — guards:
  spec drift and grader-unrunnable workflows.

- [ ] **W2-E6 — Cohere upgrade path (conditional — only if W2-OA1 missed Monday EOD and
  MVP shipped `RERANKER=local`).**
  Files: Railway env (RERANKER=cohere), `docs/week2/W2_DEVLOG.md` (dated entry).
  Anchors: §2 (dated trigger: "Cohere becomes the Early-checkpoint upgrade"), W2-D4 rev.
  Accept:
  - Seam flipped in Railway env only — zero code change (that was the seam's promise);
    rerank model+version appears in traces; retrieval quality spot-checked on the eval
    retrieval cases; /ready reflects the live reranker.
  - Cohere runtime failure still degrades per §5 (un-reranked, logged, /ready degraded).
  - If the key never arrives: this task is closed as "not fired" with a dated note —
    local reranker is the shipped, PRD-compliant path.
  Test: Tier-1/Tier-2 unchanged (CI never calls Cohere); one live deployed
  /evidence/search trace showing the Cohere version — guards: a mid-week vendor flip
  silently changing retrieval behavior without trace evidence.

- [ ] **W2-E7 — W1 debt #3 residual audit (verification-v2 / UC2 delta tool).**
  Files: `docs/week2/W2_DEVLOG.md` (dated entries), any residual rule files in
  `agent/app/verify/` (extended only if a residual rule is real).
  Anchors: §8 debt ledger #3 (absorbed and every residual rule closed by Early).
  Accept:
  - Confirms the absorption claims against the shipped code: extraction grounding
    (W2-M10) covers the verification-v2 surface; supervisor per-turn routing (W2-M12)
    subsumes the delta-tool trigger.
  - Any residual required W1 verification rule not actually covered is implemented and
    tested by Final; it cannot become a sixth cut. If it requires a product decision beyond
    existing W1/W2 contracts, STOP and escalate rather than inventing one.
  Test: W2-M10 + W2-M12 suites re-run green and the dated closure evidence exists in the
  devlog — guards: a "documented AND resolved" debt claim graders can falsify.

- [ ] **W2-E8 — D10 full-write + cross-boundary platform-contract closure.**
  Files: W2-M5/M6/M8/M11 modules (extended), migration compatibility tests, one-ID E2E
  test, schema/event snapshots, CI integration suite.
  Anchors: W2-D9/D10, §2/§2a/§3/§6/§7a, post-review items 3/5/11/12/13/15.
  Accept:
  - Enable and prove the complete grounded intake-vitals leg from M11 under the same
    source/artifact intent protocol; all three legs survive commit-timeout reconciliation,
    re-read verification, attempt purge, stale lease, and long-job credential refresh.
  - The single-correlation integration test reconstructs inbound request → permanent job
    and source write/read-back → queue claim/lease → extractor/VLM → retrieval/embedder/
    reranker → artifact write/read-back → every eligible vital write and standard/FHIR
    read-back → terminal typed event from one ID, with resolvable parent/child spans and no
    raw PHI.
  - One owned `LogEventEnvelope` snapshot covers every event; migrations 002 then 003 pass
    clean upgrade, old-code/new-schema and new-code/old-schema compatibility, documented
    expand/contract order, compatible rollback/backup restore, roll-forward, and secret/
    PHI-free failure diagnostics.
  - All F12/F13/F14/F15/F16/F17/F18/F19 named golden cases and lower-level tests are green;
    F20 unknown/DEBUG remains fail-closed. No “provisional” control reaches Final.
  Test: the cross-boundary integration/migration/reconciliation suite itself — guards:
  interface drift, trace gaps, unsafe schema rollout, and an incompletely enabled D10 leg.

---

## Phase 3 — Final

**Deadline:** Sun 2026-07-19 12:00 PM CT.
**Spec anchors:** §9 Final row; §7 (regression drill), §8a (cost report, backup), §8
(uncuttable/GitLab), W2-D9/D10, and all non-stretch W2-REQ rows.
**Goal:** complete and harden every non-stretch PRD/engineering requirement, all three
contained exactly-once write legs, the real gate, operational evidence, reports, and demo;
then freeze the identical green submission SHA on GitHub and GitLab.
**Exit criteria:** every deterministic/factual drill has linked red evidence; every D9/D10
negative and live E2E is green; reports and DEPLOYMENT backup/restore evidence are committed;
video delivered; GitLab same-SHA graded gate green and current at deadline.

- [ ] **W2-1 — Hardening + final live E2E.**
  Files: fixes as found (no new surface — hardening only).
  Anchors: §9 Final row; §5 (every failure row re-verified); W2-F21, W2-D9.
  Accept:
  - Full UC-W2-1..4 pass on the deployed app with real (synthetic-data) documents,
    including one deliberate degraded-scan and one duplicate re-upload live.
  - Every §5 failure row spot-verified: induced where safe (breaker trip, Cohere-off,
    restart mid-job → stale-lease recovery + intent reconciliation of the same logical
    job) — each identifiable in logs by its named event.
  - **"Read-only" claims are stated precisely (W2-F21):** any doc/log wording that calls
    a GET-only path "read-only" distinguishes "no HTTP write method" from "no DB
    mutation" — the agent issues only its enumerated reads, and the writeback DEBUG-log
    guard (W2-F20) plus the reads-only-on-registered-resources posture (avoids UUID
    backfill writes) are confirmed on the deployed stack.
  - The W2-D9/D10 controls are re-verified green end-to-end: cross-patient and
    mismatched-encounter rejection (W2-F13); exact scope assertion (W2-F12); both canonical
    paths resolving to expected category ID+ACL (W2-F14); range and attribution controls
    (W2-F15/F16); permanent patient-bound lineage, commit-timeout reconciliation, and no
    blind retry on source/artifact/vitals (W2-F18); F20 fail-closed; and rejection of the
    old access **and refresh** tokens after cutover (W2-F17).
  - No new features land after this task starts. An incomplete non-stretch item blocks
    Final; only one of the five named stretch items can appear in the Cut section.
  Test: the deployed-app E2E checklist itself (tag boundary) — guards: submission-day
  surprises in flows that only ever ran locally.

- [ ] **W2-2 — Regression drill: inject known breaks, confirm the gate goes red
  (explicit graded-gate rehearsal).**
  Files: throwaway branch(es); `docs/week2/W2_CI_EVIDENCE.md` (NEW — the defense and
  category-threshold red-run matrix lives here; README links to it).
  Anchors: §7 (regression drill + defense-prep §8 correction), W2-D5/D8, W2-REQ-36.
  Accept:
  - Each of the four W2_DEFENSE_PREP §8 regressions injected on a throwaway branch;
    **the gate goes red for the mapped category in all four runs**; the four red CI runs
    are linked in the CI Evidence deliverable.
  - The regression-#3 correction honored: the templater-rule loosening is caught
    deterministically by safe_refusal; a genuine prompt-level behavior change is shown
    caught by the Tier-2 live run (W2-D8's purpose).
  - In addition to those four defense scenarios, the drill matrix proves the actual gate
    arithmetic: one applicable violation makes each of `schema_valid`,
    `citation_present`, `safe_refusal`, and `no_phi_in_logs` red immediately; the PHI case
    uses the generated known-leak self-test; and `factually_consistent` flips
    `floor(0.05 × applicable)+1` cases or enough to drop below 90%, whichever is needed to
    cross the real rule. Evidence records numerator, denominator, score, delta, and rule.
  - Any drill failure (a regression NOT caught) is treated as a gate bug: fixed, scorer
    self-test added, drill re-run — the drill does not pass by narrowing the claim.
  Test: this task IS the test (tag regression) — guards: the graded hard gate failing
  exactly when graders inject their break.

- [ ] **W2-3 — Cost & latency report (Final, supersedes W2-M22).**
  Files: `docs/week2/W2_COST_LATENCY.md` (extended to the full §8a contract).
  Anchors: §8a (report contents), §6 (trace-measured cost), §8 debt #4, W2-REQ-53.
  Accept:
  - Actual dev spend from Langfuse traces + Railway billing; projected production cost at
    the W1 ARCHITECTURE §9 scale tiers; measured p50/p95 for ingestion / extraction /
    retrieval / full-turn; bottleneck analysis verified against traces (expected: VLM
    page calls dominate ingestion, LLM dominates turns — confirmed or corrected with
    evidence).
  - Closes debt #4 (R12 latency anchor superseded by measured p50/p95 — dated ledger note).
  - Aggregates spend without clinical content (§6a privacy rule).
  Test: every number traceable to a trace/billing query documented in the report —
  guards: a report graders can falsify against our own traces.

- [ ] **W2-4 — OpenEMR + encrypted agent-Postgres backup/restore and source-custody
  verification (with W2-OA5).**
  Files: `DEPLOYMENT.md` (extended — backup posture evidence, RPO/RTO), restore-drill
  notes.
  Anchors: §8a (automatic + manual legs, RPO/RTO), Open items owner action.
  Accept:
  - Automatic OpenEMR leg: Railway MySQL + document volume backup verified/enabled with
    evidence recorded; RPO/RTO stated as the scheduled-backup interval and measured restore
    plus re-ingestion time.
  - Automatic agent-Postgres leg: encrypted backup covers OAuth state, encrypted delegated
    credentials, PHI-bearing jobs/leases/attempts, permanent dedup/lineage/intents, remote
    IDs, correlation markers, and attribution. A restore into an isolated target proves key
    recovery, 002→003 compatibility, referential integrity, startup, stale-lease handling,
    and reconciliation without duplicate remote POSTs. Its measured RPO/RTO is recorded.
  - Retention is verified after restore: 30-day purge affects attempts only; permanent
    dedup/lineage/intents are never purged. Backup/restore logs and diagnostics pass the
    scoped PHI scanner and expose no credential material.
  - Manual source-custody leg names the clinic/records custodian and recovery inventory
    mapping original source files to patient-safe hashes. Re-upload + D10 reconciliation of
    a synthetic sample proves recovery cannot duplicate; demo fixtures remain repo-owned.
  - Golden set + corpus manifest confirmed repo-reproducible (clone → rebuild index →
    Tier-1 green) — RPO 0 leg.
  Test: the restore drill itself (tag boundary) — guards: an untested backup claim in a
  graded document.

- [ ] **W2-5 — Demo video (3–5 min, six required contents).**
  Files: video artifact; shot list checked into `docs/week2/W2_DEVLOG.md`.
  Anchors: §9 Final (six-element shot list), W2-REQ-52, W2-D7 (synthetic captures only).
  Accept:
  - All six contents shown: document upload, extraction, evidence retrieval, citations,
    eval results, observability — within 3–5 minutes.
  - Synthetic data only; frames treated as sensitive artifacts (no PHI-bearing captures
    to SaaS tools).
  - Error path: any of the six required contents that cannot be shown working live blocks
    Final; it cannot be relabeled as a cut/deferral or replaced with a fake demonstration.
  Test: shot-list review against the six contents before recording ships — guards: a
  video missing a PRD-enumerated element.

- [ ] **W2-6 — Submission freeze: mirror current, results refreshed, checkpoints filed.**
  Files: GitLab mirror state; `agent/evals/` committed results (refreshed);
  `README.md` final pass.
  Anchors: §8 (GitLab submission host + README contract), §6a enforcement layer 3, §7
  (results per checkpoint).
  Accept:
  - GitLab mirror current at the deadline; `.gitlab-ci.yml` Tier 1 **and its fail-closed
    identical-SHA bridge to the required live Tier 2** are green; committed Tier-2 results
    refreshed for Final and bound to that SHA.
  - README final: env vars, branch, service map, **deployed app URL**, grader paths (run
    flow + break the gate) all verified by a clean-clone walkthrough.
  - Deviations/cuts all recorded with dates in this plan's Cut section.
  - Error path: a red or stale mirror state at freeze time **blocks submission** and is
    escalated to the owner — never annotated around.
  Test: clean-clone grader-path walkthrough (tag boundary) — guards: a submission where
  the graded host is stale or the grader path has a missing step.

---

## Needs architecture

**Post-review remediation (2026-07-13): none.** The earlier decomposition notes are
superseded: unit mismatch and the vitals mapping are locked by W2-D10; the Cohere date is
corrected; the chart upload affordance is already bounded by UC-W2-1/W2-M21; MVP report/video
and Early SLO closure are now explicit in binding §9; and every W2-F12..F21 control is folded
into binding §4/§5 under W2-D9/D10. No owner decision is outstanding and no task below relies
on a future architecture pass. If implementation discovers a genuine product/decision choice
beyond W2-D1..D10, **STOP and report it rather than inventing W2-D11**.

---

## Deliverables map (graded item → producing task)

**PRD deliverable table (all eight rows):**

| Deliverable | Producing task(s) |
|---|---|
| GitLab Repository (fork + setup guide + deployed link + env-var docs) | W2-OA4, W2-M20 (mirror job), W2-M21 (README), W2-6 (freeze) |
| Week 2 Architecture Doc (`./W2_ARCHITECTURE.md`) | Updated by the dated 2026-07-13 post-review planning remediation; implementation treats the committed result as binding |
| Schemas (Pydantic, both doc types, citation fields, validation tests) | W2-M6 |
| Eval Dataset (50 cases, expected behavior, boolean rubrics, judge config, results) | W2-M17 (cases), W2-M18 (rubrics + judge config), W2-M20 + W2-6 (committed results per checkpoint) |
| CI Evidence (Git Hook or equivalent that runs the eval suite and blocks) | W2-M19 (hook + Tier 1), W2-M20 (Tier 2 + branch protection + GitLab CI), W2-2 (four linked red runs) |
| Demo Video (3–5 min, six contents) | W2-M23 (MVP walkthrough), W2-5 (Final) |
| Cost & Latency Report (dev spend, projection, p50/p95, bottlenecks) | W2-M22 (initial), W2-3 (Final; closes debt #4) |
| Deployed Application (public, W2 core flow working) | W2-M1 (container), W2-M21 (deploy + UI), W2-1 (final E2E) |

**Five core deliverables (PRD p.5, the graded core):**

| Core deliverable | Producing task(s) |
|---|---|
| Two document types (lab PDF + intake form) | W2-M6, W2-M7, W2-M8, W2-M9, W2-M10, W2-M11 |
| One supervisor + two workers with logged handoffs | W2-M3 (spike), W2-M12 |
| Basic hybrid RAG + rerank over a small guideline corpus | W2-M13, W2-M14 (+ W2-E6 conditional) |
| 50-case golden dataset with boolean rubrics | W2-M17, W2-M18 |
| PR-blocking eval CI + observable deployed demo | W2-M19, W2-M20, W2-M21, W2-E3 (observable), W2-2 (proven) |

**W1 debt ledger (PRD: documented AND resolved) — all five items:**

| Debt | Task |
|---|---|
| #1 token/PKCE persistence | W2-M5 (MVP) |
| #2 50-VU /ready knee | W2-E4 |
| #3 verification-v2 residual | W2-M10/W2-M12 (absorbed) + W2-E7 (audit) |
| #4 R12 latency anchor | W2-3 |
| #5 GitLab mirror + RAILWAY_TOKEN residual | W2-M20 |

**Engineering-requirement clusters (PRD pp.6–7 → §10 matrix):** typed contracts +
migration safety (W2-M5, W2-M6, W2-M8, W2-M15, W2-E8); data authority/lineage
(W2-M8, W2-M11, W2-M13); observability/
SLO/queues/retries/breakers (W2-M8, W2-M9, W2-M12, W2-E3, W2-E4); correlation ID + tracing
(W2-M3, W2-M12, W2-E8); structured logs + owned typed envelope + PHI-free CI check
(W2-M6, all tasks' events, W2-M18,
W2-M20); CI pipeline stages (W2-M20); testing strategy four-way split (each task's Test:
hook; §7a is the contract); Bruno/OpenAPI (W2-E5); baselines (W2-E4); /health //ready
(W2-M21); alerts+runbooks (W2-E3); privacy scrubbing (W2-M18, W2-M20); backup/recovery
(W2-OA5, W2-4); scenario promise 3 degraded axes (W2-M10, W2-M15, W2-E2 + W2-M17 cases);
capability→W1-user mapping (done — W2_USERS traceability table; demo narrative in W2-5).

**§-anchor coverage check (every architecture § → implementing tasks):**
§1 → W2-M1/M21 (+ non-goals enforced across all tasks); §2 → W2-M3/M4/M6/M9/M10/M12/M13/M14/M15;
§2a → W2-M8/M14/M15/M16/E5; §3 → W2-M5/M8/M11/M12; §3a → W2-M5/M8/M16 (retention
behaviors); §4 → W2-M9/M14/M15/M16 (+ W2-M7 canaries); §4a → W2-M11/M13 (+ W2-M8 job
authority); §5 → W2-M8/M9/M11/M12/M13/M14 (behaviors) + W2-E3 (runbooks) + W2-1
(verification); §6 → W2-M12 (encounter.summary)/W2-E3/W2-E4; §6a → W2-M19/M20 (+ every
task's named events); §7 → W2-M17/M18/M19/M20/W2-2; §7a → each task's Test: hook; §8 →
W2-M21/W2-6 + Cut section; §8a → W2-M22/W2-3/W2-4; §9 → this plan's phase ordering; §10 →
this deliverables map. **Not-this-week:** §8 stretch tier (see Cut — PRD-
sanctioned out-of-scope rows W2-REQ-10/42/44/45/46).

---

## Cut — stretch tier only

Dated entries; cuts are decisions with a paper trail. **Post-review remediation
(2026-07-13): only the five PRD-sanctioned stretch items below are cuttable.** No
contingency may move, weaken, stub, or silently omit a non-stretch item. If the complete
robust Final cannot fit, STOP and escalate rather than inventing a sixth cut.

- **2026-07-13 — Critic agent** (PRD bullet 6): **cut as stretch** — PRD p.4 "A critic agent is
  extension work, not core." First pickup per above.
- **2026-07-13 — Third document type** (referral fax / med list, PRD bullet 8): **cut as stretch** —
  PRD pitfall 1 (two must work first).
- **2026-07-13 — Lab trend chart widget** (PRD bullet 9): **cut as stretch** — trend questions
  answered as cited textual/tabular values in core (W2-M15); a widget would need the
  artifact-store read path (no FHIR Observation exists, W2-F1).
- **2026-07-13 — Contextual retrieval improvements** (PRD bullet 10): **cut as stretch** — if picked
  up, query phrasing is the highest-leverage lever (W2-R3).
- **2026-07-13 — ColQwen2 / multi-vector indexing**: **cut as stretch** — PRD's own Stage-2 stretch
  language.

~~**Superseded 2026-07-13:** click-to-source polish beyond core was previously listed as
deferred.~~ It is not a cut: W2-M15/M16 plus W2-E1 complete the PRD citation/overlay and
click-to-source experience by Early. Likewise, the former contingency order proposing to
cut GitLab CI, corpus breadth, or the vitals leg is retired in full.

**Explicitly UNCUTTABLE through Final:** every PRD core and engineering requirement; both
document types; source document + grounded artifact + eligible intake vitals; every
W2-D9/D10 containment and exactly-once control; the 50-case dataset; Tier 1 and live Tier 2;
the deterministic/factual regression drills; GitHub required checks; GitLab Tier 1 and its
SHA-bound Tier-2 bridge; full corpus promised by D4; schemas, queue/credentials,
observability, `/health`/`/ready`, OpenAPI, Bruno, PHI detection, migration safety,
correlation test, backups/RPO/RTO, reports, deployed app, and video. A missing item blocks
the checkpoint/Final—it never becomes a workaround or dated non-stretch deferral.

The standing architecture non-goals (LangGraph checkpointer persistence, a front-desk
principal, agent-created encounters, and update/delete surfaces) are boundary exclusions,
not cuts from a PRD requirement and not candidates for pickup.

*(Any later cut entry must be one of the same five stretch items and include date/reason.)*
