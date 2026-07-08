# AUDIT.md — OpenEMR Fork Pre-Agent Audit (Stage 3 hard gate)

> Read-only forensic audit of this OpenEMR fork (v8.2.0-dev) performed **before any agent/AI code exists**, on the live local stack (25 Synthea patients) and the deployed Railway instance. Every finding is tagged with an ID, severity, confidence, concrete evidence (file:line / query result / endpoint response), and its impact on the agent architecture (D#/§ from `docs/planning/`).
>
> **Method note (graded thinking):** each critical/high finding was independently re-checked by an adversarial verifier instructed to *refute* it. That pass changed several verdicts — one "high" security finding rested on a grep claim that was **factually wrong** (ACL restrictions *are* registered), and four findings were downgraded or refuted as demo-data artifacts or unbuilt-design. Post-verification severities are used throughout; where a first-pass rating was corrected, it is stated. Provenance was checked too: the FHIR/auth/mapper code discussed is **stock upstream OpenEMR imported verbatim** (commit `ef3d490`); the fork's own commits touch no clinical code. That doesn't make the behaviors less real for the agent — it means they are inherited constraints to design around, not fork bugs to fix.

---

## Summary — the findings that change what the agent must do

The detail below reduces to **seven things the agent design must absorb**, plus a broad confirmation that the integration boundary is sound.

**1. FHIR data is not trustworthy field-by-field — one field is inverted (F-D.1, critical).** Every completed immunization renders as `status: not-done` + `statusReason: patient objection`, caused by a case-sensitive `"completed" == "Completed"` in the stock mapper; verified 67/67 vaccines for the canonical patient come back "patient refused." A naïve agent would tell a physician the patient declined every vaccine. This is the strongest justification for the D7/§5 verification layer: **the agent must never surface a FHIR status verbatim as clinical fact.** It compounds with AllergyIntolerance `criticality` being null across the whole dataset (a real label-vs-numeric key bug, F-D.4) and Encounter.status hardcoded `finished` (F-D.6).

**2. Absence is unsafe and indistinguishable from a negative (F-D.5).** OpenEMR has no "No Known Allergies" record type; an empty AllergyIntolerance bundle means *either* "no allergies" *or* "never asked." This forces the §5 forbidden-phrasing rule verbatim: an empty allergy result renders **"no allergy records returned; confirm with patient,"** never "NKDA." (Verification downgraded this as a *data* defect — a known upstream US-Core gap, first-pass evidence cited the wrong dump — but its weight on §5 stands.)

**3. OpenEMR's audit log can't attribute an agent's call, or be joined by a shared id (F-C.1 + F-C.2, high, confirmed).** `api_log` records `user_id`/`patient_id`/URL/body but **no OAuth `client_id`, no granted scopes, no correlation-id column**, and there is no code path to persist an inbound `X-Copilot-Request-Id`. This **challenges D10/§7's** promise to "join agent traces to api_log via a shared id" — that join point doesn't exist. Revise §7: make the agent's Langfuse trace (D5) the **system of record** for client_id + scopes + correlation id, and correlate to api_log only fuzzily on `(user, patient, url, timestamp)`.

**4. PHI is already stored plaintext at rest, by default (F-S.4, high, confirmed).** `api_log_option` defaults to `2` (Full Logging); every FHIR response body is written unencrypted to `api_log.response` (verified a 32KB clinical bundle at rest). The 6-read fan-out (D10) multiplies this. The compliance boundary must inventory api_log as a PHI store alongside Langfuse (D5), not just the LLM channel.

**5. Authorization is inherited correctly, but D2's wording overstates it (F-S.1 corrected to low; F-A.2 confirms D2).** D2 is **sound** — a real SMART-on-FHIR/OAuth2 EHR-launch surface with S256-enforced PKCE, and delegated `authorization_code` attributes calls to the actual clinician (verified). But for `patient/*` tokens the enforcement is **scope + single-patient compartment binding, not "scopes AND GACL ACL."** Refine D2's sentence; keep the agent-side pin (D12) as the real clinician↔patient guarantee, since OpenEMR's own `checkUserHasAccessToPatient()` is a stub returning `true` (F-S.2).

**6. Latency floor is service-construction-bound, and small next to the LLM (F-P.1/F-P.5).** Every service instantiation runs 3 uncached schema queries (measured **26 metadata round-trips for one `GET /Patient`**); live per-read floor ~0.39s. D10 fan-out is validated — it collapses ~2.3s sequential into ~0.4–0.6s — confirming §9's "LLM dominates wall-clock."

**7. Two safety paths can't be tested on demo data (F-S.7).** Zero Synthea patients are deceased, so D12's deceased hard-stop ships unexercised unless the eval suite injects a synthetic fixture — same gap for the empty-allergy path.

**Net verdict on the gate:** the integration architecture (D2 sidecar, D9 FHIR-only, delegated auth-code) is **confirmed correct and defensible**. The audit's real payload is that the *data layer lies in specific, enumerable ways* and the *audit layer can't attribute the agent* — both move work onto the agent's verification (§5) and observability (§7, D5) layers, and one (the D10 api_log join) needs an explicit revision.

---

## 1. Security Audit

**Section summary.** The deployed instance is well-hardened (F-S.9 confirms DEPLOYMENT.md §4: default creds rejected, no phpMyAdmin/setup/admin/Xdebug, clean 401 JSON, secrets absent from git). The consequential findings are architectural: for `patient/*` SMART tokens authorization is scope + compartment-binding rather than scope∧ACL (F-S.1, corrected); the server-side patient-access check is a stub (F-S.2); a same-session local-API path skips OAuth scope enforcement (F-S.3, corrected — but authenticated, CSRF-protected, and still SQL-audited); and OpenEMR persists full PHI response bodies in plaintext by default (F-S.4). Delegated `authorization_code` attributes calls to the real clinician (F-S.5) — confirming the D9 grant choice.

| ID | Severity | Conf. | Finding |
|----|----------|-------|---------|
| **F-S.4** | **high** | verified | **Full PHI FHIR response bodies stored plaintext at rest in `api_log`, on by default.** |
| F-S.2 | medium | verified | Server-side `checkUserHasAccessToPatient()` is a stub returning `true`. |
| F-S.6 | medium | verified | User-scoped OAuth apps require manual admin approval before they can mint tokens. |
| F-S.7 | medium | verified | Deceased indicator is plain data, not an enforcement point — and untestable on demo data. |
| F-S.1 | low *(was high)* | verified | For `patient/*` tokens, GACL ACL is not the enforcement layer — compartment binding is. |
| F-S.3 | low *(was high)* | verified | Same-session local-API path skips OAuth *scope* checks (but is authenticated, CSRF-guarded, SQL-audited). |
| F-S.5 | info | verified | Provider attribution correct under `authorization_code`; collapses to `oe-system` under `client_credentials`. |
| F-S.8 | info | verified | The "26 Observations" anomaly is benign — count is complete and correct. |
| F-S.9 | info | verified | Deployed security baseline (DEPLOYMENT.md §4) independently verified. |

**F-S.4 — Full PHI bodies plaintext at rest in `api_log` (default).**
*Evidence:* `ApiResponseLoggerListener.php:62-64,83-85` writes `$response->getContent()` into `api_log.request_body` and `.response` whenever `api_log_option >= 2`. `library/globals.inc.php:2900` sets the shipped **default to `2`** (Full Logging). Verified at rest: `SELECT LENGTH(response), SUBSTRING(response,1,120) FROM api_log ORDER BY id DESC` → a 32,171-byte row `{"resourceType":"Bundle",...,"total":19,...}` (full Condition bundle for the canonical patient). Columns are `longtext`, unencrypted; `EventAuditLogger.php:660-661` notes the encryption path was removed. Demo data is synthetic, so no real PHI is exposed *today* — but the mechanism is identical for real PHI.
*Architecture impact:* OpenEMR itself is a PHI-at-rest store the compliance boundary must inventory alongside Langfuse (**D5**). Every agent FHIR read (**D9/D10**, 6 per turn) duplicates clinical data into `api_log`. Recommend documenting a retention/`api_log_option` decision for the deployment, or accepting api_log as an in-boundary PHI store with the deployment's encryption-at-rest obligations. Ties to **F-C.3**.

**F-S.1 — Authorization is scope + compartment-binding for patient tokens, not scope∧GACL (first pass overstated this).**
*Evidence:* `FhirGenericRestController.php:94-102` — for `isPatientRequest()` the controller binds the patient UUID and skips the GACL `aclCheckCore` loop; the ACL check runs only in the `else` (non-patient) branch via `RestConfig::request_authorization_check`. **Correction to the first-pass claim:** ACL restrictions *are* registered — `git grep addAclRestrictions` returns six calls in `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:169,174,495,500,716,721` (Condition/Observation/RelatedPerson), and user-scoped reads *do* run `AclMain::aclCheckCore` (verified live: a `user/Condition.read` admin token returns a bundle through the ACL path). A "patient request" is set only for `patient/<resource>` SMART scopes (`HttpRestRouteHandler.php:65-66`), and such tokens are hard-locked to one patient compartment — the service unconditionally overwrites the `patient` param with the server-derived puuid (`FhirConditionService.php:88`, `FhirServiceBase.php:227-231`), so no cross-patient read is possible.
*Architecture impact:* **D2 remains viable but its wording — "authorization inherited from OpenEMR's OAuth2/SMART scopes + ACL" — should be refined.** For the patient-app model the agent inherits its ceiling from the *granted scope set + compartment binding*, with GACL adding nothing for that bound patient. Request the narrowest scopes possible; do not assume ACL is a second containment layer under patient scopes. No cross-patient exposure was demonstrated.

**F-S.2 — `checkUserHasAccessToPatient()` is a stub.**
*Evidence:* `src/RestControllers/Authorization/BearerTokenAuthorizationStrategy.php:479-485` body is `// TODO … return true;`, used at line 443 before binding the SMART launch patient. No facility/provider scoping on which patient a user token may bind to.
*Architecture impact:* The "(clinician, patient)" pin is honored only insofar as the SMART launch context set it — OpenEMR does not independently verify the clinician is authorized for that patient. **D12's agent-side session pin is the real enforcement, not an inherited server guarantee** — do not weaken it on the assumption OpenEMR re-checks.

**F-S.3 — Same-session local-API path skips OAuth scope enforcement (downgraded from high).**
*Evidence:* an `APICSRFTOKEN` header flips to local-API mode (`HttpRestRequest.php:153-155`; `LocalApiAuthorizationController.php:29-34,111` set `skipAuthorization=true`); `AuthorizationListener.php:154` early-returns before the scope check (lines 189-193); `ApiResponseLoggerListener.php:53` skips the api_log body write. **Corrections:** the path requires a valid authenticated core session + a session-bound CSRF token (`LocalApiAuthorizationController.php:56`, `CsrfUtils::verifyCsrfToken(...,'api')`) — not reachable unauthenticated; and PHI access is *still* audited to the standard `log` table via `EventAuditLogger::auditSQLEvent()` in `library/ADODB_mysqli_log.php:50` (no `isLocalApi` gate). Only the api_log *body* log is skipped. Files are byte-identical to upstream import.
*Architecture impact:* This is exactly the shortcut **D9** forbids the serving agent from using. The agent must **always present a Bearer token, never `APICSRFTOKEN`, and never be co-hosted inside an OpenEMR browser session.** The D10 trace-join only holds on the Bearer path.

**F-S.5 — Grant-type attribution (confirms D9).** `authorization_code` → `api_log.user_id` resolves to the delegated clinician (verified `user_id=1`). `client_credentials` → the synthetic `oe-system` user (`UserService.php:37,119`), erasing the human actor. *Impact:* keep **D9** locked to auth-code+PKCE; add a guardrail/test asserting the agent never negotiates `client_credentials`. Note `api_log.patient_id` was `0` even on the patient read — the agent must carry patient identity in its own correlation metadata.

**F-S.6 — User-scoped apps need admin approval.** `ClientRepository.php:78-88` + `ScopeRepository.php:334+`: a confidential client requesting `user/*` scopes is registered **disabled** (`is_client_enabled=0`) pending admin action; `patient/*`-only clients auto-enable. *Impact:* the agent (D2, `user/*` scopes) needs a one-time "enable app in Administration" step in its deployment runbook — registration ≠ usable. Mild defense-in-depth positive.

**F-S.7 — Deceased indicator is data, not a guardrail; untestable on demo data.** `FhirPatientService.php:741-751` maps `deceased_date`→`deceasedDateTime` else `deceasedBoolean(false)`; nothing blocks reads on a deceased patient. `SELECT COUNT(*) FROM patient_data WHERE deceased_date IS NOT NULL AND deceased_date != '0000-00-00…'` = **0**. *Impact:* **D12's deceased hard-stop is 100% the agent's job** (read `Patient.deceasedDateTime`/`deceasedBoolean` and refuse deterministically). **Testing gap:** with zero deceased patients, the eval suite must inject a synthetic deceased fixture (audit-only DB edit) or mock the FHIR payload, or the safety-critical path ships unverified.

**F-S.9 — Deployed baseline verified.** Live `admin/pass` → rejected (login-error redirect, no frameset); `/phpmyadmin/` `/setup.php` `/admin.php` `/sql_patch.php` → 404; unauthenticated `/fhir/Patient` → clean `401` JSON, no stack trace; git secret scan finds only stock CI/dev fixtures, no production secrets, no tracked `.env`. *Two caveats:* TLS is **edge-only** (no app-side HSTS) — the agent must pin `https://` and reject downgrade; and DEPLOYMENT.md §4.5's still-open Railway MySQL TCP proxy is a direct-DB path outside the D9 boundary — recommend closing it.

---

## 2. Performance Audit

**Section summary.** The dominant cost is **schema-introspection on every service construction** (F-P.1) — uncached, and empirically ~26 metadata queries for a single `GET /Patient`. The unfiltered Observation read fans out to 10 sub-services (F-P.2) and the lab path is a textbook N+1 (F-P.3). Indexing is **healthy** — this is a query-*count* problem, not an index-*missing* one (F-P.4). Measured live floor is ~0.39s/read; `metadata` is a pathological ~3.97s (CapabilityStatement generation, not network) (F-P.5). `api_log` has no correlation column (F-P.6, = F-C.2/F-A.5). The Observation anomaly is a red herring (F-P.7).

| ID | Severity | Conf. | Finding |
|----|----------|-------|---------|
| **F-P.1** | **high** | verified | Every `BaseService` construction runs 3 uncached introspection queries (incl. `SHOW TABLES` over 283 tables); ~26 metadata round-trips per `GET /Patient`. |
| **F-P.2** | **high** | verified | No-category Observation search eagerly builds 10 sub-services (4 of which each re-pay introspection). |
| **F-P.3** | **high** | verified | `ProcedureService` lab search is N+1: `1 + O*(2+R)` queries + 8 UUID-backfill scans/construction (~1445 queries for the heaviest patient). |
| F-P.5 | medium | verified | Measured wall-clock: local 0.12–0.25s/read; live Railway floor ~0.39s/read; `metadata` ~3.97s. |
| F-P.6 | medium | verified | `api_log` has no correlation-id column — D10/§7 join needs a schema change OpenEMR can't get (read-only). |
| F-P.4 | info | verified | Hot FHIR tables are adequately indexed on pid/uuid/date — **not** an index problem. |
| F-P.7 | info | verified | The "26 vs 164 Observation" gap is a per-patient-vs-average artifact, not data hiding. |

**F-P.1 — Uncached schema introspection per construction.** `BaseService.php:69-70` unconditionally calls `QueryUtils::listTableFields()` (which runs `SHOW COLUMNS` + a full `SHOW TABLES` whitelist check, `QueryUtils.php:35,50`) and `getAutoIncrements()` (a second `SHOW COLUMNS`, `BaseService.php:317`) — 3 metadata round-trips, no memoization, 53 services extend it. **Empirical (verifier enabled MariaDB general_log, audit-only, restored after):** one `GET /fhir/Patient/{id}` → **8 `SHOW TABLES` + 18 `SHOW COLUMNS` = 26** metadata queries; the *same* table is re-introspected on every instantiation. *Impact on §9:* each fanned-out read pays a fixed introspection tax independent of payload; the cost model must budget it per fanned read. Doesn't challenge D10 (parallelism amortizes it) — sets the per-call floor.

**F-P.2 — Unfiltered Observation fan-out (corrected count).** `FhirObservationService.php:60-74` eagerly instantiates all 10 mapped sub-services; with no `category`/`code`, `getAll()` searches all of them (`:148-150`). **Correction:** exactly **4** (not 5) sub-services eagerly build an underlying BaseService — Vitals, SocialHistory, HistorySdoh, ObservationForm; Laboratory's ProcedureService is lazy. Measured construction ~15ms cold / ~6.6ms warm. *Impact:* the agent should pass an explicit `category` (e.g. `laboratory,vital-signs`) on its Observation call to prune the fan-out — an agent-side optimization the **D9** FHIR-client seam and **§9** model should encode.

**F-P.3 — Lab N+1.** `ProcedureService.php:681` outer loop, `:711` per-order codes, `:728` per-order reports, `:745` per-report results (nested) → `1 + O*(2+R)`; constructor `:44-55` runs `createMissingUuidsForTables` over 8 tables every instantiation. DB: 3588 orders / 267 reports / 4101 results; heaviest patient `pid=7` = 692 orders → **~1445 sequential queries** for one call. *Impact:* the Observation lab path is the highest-variance read — **D10's per-call timeout must be sized for this worst case, not the median.** Reinforces D10's per-call-timeout + total-turn-budget design.

**F-P.5 — Measured wall-clock (feeds §9).** Local medians (3 samples): Patient 0.137s, Condition 0.128s, MedicationRequest 0.248s, Observation 0.185s, Encounter 0.165s, Allergy 0.122s → sequential ≈ 0.99s, D10 parallel floor ≈ 0.25s. Live Railway: authenticated reads weren't runnable (the live token-mint returned an authorization-server key error), so the floor was measured via an unauth read that 401s *after* the auth check: ~**0.39s** median. Live `metadata` ~3.97s (generation, not network; local metadata 0.37s). Responses are **uncompressed** (no `content-encoding`). *Impact:* the sidecar pays ~0.15–0.25s network tax/call (a D2 consequence). Sequential 6-read ≈ 2.3s live; **D10 fan-out collapses it to ~0.4–0.6s — worth ~1.7–1.9s/brief.** Confirms §9's "LLM ≈ 85% of wall-clock" — the FHIR block (<1s parallel) is small next to ~28s p50.

**F-P.4 — Indexes are fine.** `SHOW INDEX` confirms pid/uuid/date/FK coverage on `procedure_result/report/order`, `lists`, `form_encounter`, `prescriptions`, `patient_data`, `immunizations`. The N+1 join column (`procedure_result.procedure_report_id`) is indexed, so each N+1 query is cheap — cost is round-trip **count**, not scan. *Impact:* attribute FHIR latency to query-count + PHP object construction, not table scans.

---

## 3. Architecture Audit

**Section summary.** The FHIR request path is a full Symfony HttpKernel + EventDispatcher pipeline (F-A.1). **D2 is confirmed** — a real SMART-on-FHIR/OAuth2 EHR-launch surface with S256-enforced PKCE (F-A.2), a near-zero-diff launch affordance already exists (F-A.3), and the embedded-module alternative is heavier and reinforces the sidecar choice (F-A.4). The one architectural challenge is **D10's api_log join** (F-A.5, = F-C.2/F-P.6). Data lives in legacy tables served by modern namespaced FHIR services (F-A.7).

| ID | Severity | Conf. | Finding |
|----|----------|-------|---------|
| **F-A.2** | high *(positive)* | verified | **D2 CONFIRMED:** SMART-on-FHIR/OAuth2 auth server with external EHR-launch + S256-enforced PKCE. |
| F-A.3 | medium | verified | Smallest-diff EHR-launch affordance already exists (PatientDemographics `RenderEvent`). |
| F-A.5 | medium | verified | **D10 CHALLENGED:** `api_log` has no correlation/request-id column, no inbound header capture. |
| F-A.1 | info | verified | FHIR path = Symfony HttpKernel + EventDispatcher (the hook surface modules would use). |
| F-A.4 | info | verified | Embedded-module alternative is real but Laminas+Symfony-coupled — reinforces D2. |
| F-A.7 | info | verified | Clinical data in legacy tables, served by modern `src/Services/FHIR/*`. |

**Pipeline (traced).** `apis/dispatch.php:20-30` → `ApiApplication.run()` (`ApiApplication.php:71-123` builds a Symfony EventDispatcher + subscribers, runs `OEHttpKernel.handle` dispatching kernel.request/controller/view/response) → route table `_rest_routes.inc.php:34` (80 FHIR routes) → `src/RestControllers/FHIR/*` → `src/Services/FHIR/*` → FHIR mappers. There *is* an EventDispatcher step in the request path (the hook surface an embedded module would use).

**F-A.2 — D2 confirmed (wording corrected).** Live `/.well-known/smart-configuration` (HTTP 200): capabilities `launch-ehr, launch-standalone, context-ehr-patient, context-ehr-encounter, permission-user, permission-patient, authorize-post`; `grant_types_supported [client_credentials, authorization_code]`; `code_challenge_methods_supported [S256]`. **PKCE is S256-*enforced*, not just advertised** — `CustomAuthCodeGrant.php:53` (`verifiers=['S256'=>true]`) and `:253-270` reject non-S256. Source: `Capability.php:27-52`, `SMARTConfigurationController.php:74,78`, `ScopeRepository.php:210`. **Correction:** all imported verbatim in `ef3d490` — this is certification-*capable* stock upstream OpenEMR, **not** fork-authored or independently certified; and the one enabled OAuth client is a password-grant audit client, so no auth-code/PKCE EHR-launch client is *actually deployed* yet. *Impact:* **CONFIRMS D2 and D9.** The federally-mandated integration surface (R5) is present and real. Provisioning a real auth-code/PKCE client is a Stage-5 step.

**F-A.3 — Launch affordance exists.** `demographics.php:99-100` instantiates `SMARTLaunchController` and calls `registerContextEvents` on chart render; `SmartLaunchController.php:49` listens on `RenderEvent::EVENT_SECTION_LIST_RENDER_AFTER`, `getSMARTClients:183` lists launch-opted clients, `renderLaunchButton:101` emits a Launch button. Entry `interface/smart/ehr-launch-client.php`; registration UI `register-app.php`. *Impact:* the "launch Co-Pilot from chart" affordance (**D2**, §2) is a near-zero-diff attach point — an opted-in SMART client appears automatically, no core patch needed.

**F-A.5 / D10 challenge — see F-C.2** (same root cause; the join key OpenEMR would need does not exist).

---

## 4. Data Quality Audit

**Section summary — this section is the agent's failure-mode catalog.** One **critical inversion** (immunization status, F-D.1) and one **real mapper defect** (allergy criticality null across the dataset, F-D.4) mean the agent cannot trust FHIR fields at face value. One **patient-safety semantic gap** (empty allergy = NKDA ambiguity, F-D.5) forces a specific phrasing rule. The remaining items (medication dose, resolved-condition labeling, hardcoded encounter status, stale/valueless observations, F-D.2/F-D.6) were **downgraded by verification** to demo-data-fidelity or interop nits — real, but not defects that drop data on a default read. Each finding below names the **§5 verifier/phrasing rule it forces.**

| ID | Severity | Conf. | Finding |
|----|----------|-------|---------|
| **F-D.1** | **critical** | verified | **Immunization status inverted:** all completed vaccines render `not-done` / patient-objection (case bug). |
| **F-D.4** | **high** | verified | AllergyIntolerance `criticality` null across the whole dataset (label-vs-numeric key bug); `type` never set; statuses constant. |
| F-D.5 | low *(was critical; safety-forcing)* | verified | Empty AllergyIntolerance bundle is indistinguishable from affirmed NKDA; no NKDA record type exists. |
| F-D.6 | low *(was high)* | verified | Resolved conditions labeled `inactive` (not dropped) on default read; broken `clinical-status` filter; Encounter.status hardcoded `finished`; stale/valueless observations faithfully mapped. |
| F-D.2 | low *(was high)* | verified | MedicationRequest carries no usable dose (seed `dosage='1.00'`); order+plan dual-representation is intended US-Core, not duplication. |

**F-D.1 — Immunization status inversion (critical, confirmed).**
*Evidence:* `FhirImmunizationService.php:100-105` checks `$dataRecord['completion_status'] == "Completed"` (capital C). PHP `==` is case-sensitive (`"completed" == "Completed"` → `false`), so the completed branch is never taken; execution falls to `status="not-done"` + `statusReason` PATOBJ (patient objection). DB: all 369 immunization rows store `completion_status='completed'` **lowercase** (`SUM(BINARY completion_status='Completed')`=0). Verified against both `/tmp/r_Immunization.json` **and** a fresh live FHIR call: **67/67** entries return `status:"not-done"` + patient-objection. 100% of administered vaccines render as patient-refused.
*§5 rule forced:* the agent must **never surface FHIR `status` verbatim**; the EvidencePacket normalizer must treat immunization status as unreliable, and the templater must never render "patient declined/refused [vaccine]" from this field. This is the concrete proof that **D7/§5's reject-on-contradiction + deterministic re-render is load-bearing, not theater** — and it challenges the naïve reading of **D9** ("FHIR is ground truth"): FHIR is the *source*, but field-level correctness is not guaranteed.

**F-D.4 — Allergy criticality null across dataset (high, confirmed).**
*Evidence:* `FhirAllergyIntoleranceService.php:138-151` builds a criticality map keyed on string labels (`"mild"`,`"severe"`,…), but the DB stores `severity_al` as a numeric seq (`DISTINCT severity_al='1'` for all **41** allergy rows system-wide), and the underlying SQL (`AllergyIntoleranceService.php:52`) passes it through raw with no `list_options` join to translate — so `$criticalityCode['1']` is undefined and `criticality` is omitted. Live endpoint: `criticality=None` ×12. `type` is never set (`grep -c setType`=0). `category` hardcoded `"medication"` (`:135`). `clinicalStatus`/`verificationStatus` effectively constant (`active`/`unconfirmed`).
*§5 rule forced:* **criticality is unreliable — never infer allergy risk from it, and never drop/deprioritize an allergy on absent criticality.** The verifier must reject any `AllergyCriticality` claim (no supporting evidence field exists). Constant fields (type/category/status) must not be asserted as clinically meaningful.

**F-D.5 — Empty allergy = NKDA ambiguity (safety-forcing; downgraded as a data defect).**
*Evidence:* `FhirAllergyIntoleranceService.php` has **no NKDA emission path** (`parseOpenEMRRecord:101-244`; grep `no known|nkda|716186003`=0 hits). The `lists` table (type=allergy, 41 rows) has no NKDA record type. The CCDA exporter (`CarecoordinationController.php:450`) emits "No Known Allergies" only as an `else`-branch when count==0 — confirming OpenEMR models "no allergies" as **absence**, not a record. So an empty `total=0` bundle is indistinguishable from affirmed NKDA. **Correction:** the first-pass evidence cited `/tmp/r_Patient.json` (a Patient bundle, total=0) — wrong file; the canonical patient actually has 12 allergies. The empty-bundle case wasn't reproduced on this patient, and this is a known upstream US-Core limitation, not a fork defect — hence the severity downgrade *as a data defect*. Its **design-forcing weight is undiminished.**
*§5 rule forced (verbatim):* an empty AllergyIntolerance result must render **"no allergy records returned; confirm with patient,"** never "NKDA" / "no known allergies." A dedicated empty-result phrasing rule is mandatory — **absence is the hazard.**

**F-D.6 — Conditions/encounters/observations (downgraded; several real sub-points).**
*Evidence & corrections:* Conditions — all 19 return on a default read (16 `inactive`, 3 `active`); **nothing is dropped.** The first-pass "active-only hides 16/19" is **false**: the `clinical-status` search filter maps to a non-existent SQL column and returns **0** for both `active` and `inactive` (`FhirConditionProblemListItemService.php:196` computes status in PHP post-query) — the real defect is a **broken filter**, not selective hiding. Resolved conditions are labeled `inactive` via `enddate` (`FhirConditionTrait.php:102-134`). Encounter.status is unconditionally hardcoded `finished` (`FhirEncounterService.php:139-140`, 37/37) — benign here (all encounters past). Observations: labs all dated `2021-07-20`; `procedure_result`'s 4101 rows carry `0000-00-00` dates; sparse vitals map to `dataAbsentReason` (valid FHIR), only 1 truly valueless.
*§5 rules forced:* (a) **consume ALL conditions** and never pass `clinical-status=active` (it silently returns nothing) — reject a "no history of X" claim if an inactive/resolved match exists; (b) treat hardcoded `Encounter.status=finished` as **non-informative** — don't assert encounter state from it; (c) a "recent labs" read must **flag decade-stale dates** rather than imply currency; (d) valueless observations need a non-null value or must be rejected, not rendered as a finding.

**F-D.2 — Medication dose (downgraded to demo-data fidelity).** DB `prescriptions.dosage='1.00'` for all 152 rows (a free-text SIG field, seeded); `FhirMedicationRequestService.php:265` correctly suppresses the bare-numeric value (`!is_numeric` guard). MedicationRequest returns 18 = 9 `order` (from `prescriptions`) + 9 `plan` (from `lists`, same drugs) — the intended US-Core dual representation, not duplication. *§5 rules still forced:* an empty-dose claim must render "dose not specified — confirm before dosing," never invent a dose; the EvidencePacket should de-duplicate order+plan to one stable ID per drug; a `MedicationDose` claim requires a non-empty evidence field or is rejected.

---

## 5. Compliance & Regulatory Audit (HIPAA)

**Section summary.** The headline is an **accountability gap**: `api_log` — OpenEMR's audit trail for every FHIR call — records `user_id`/`patient_id`/URL/body but **no OAuth `client_id`, no granted scopes, and no correlation-id column** (F-C.1), and there is **no code path to persist an inbound correlation header** (F-C.2). OpenEMR cannot answer "which app, under which grant, made this call," and the D10 shared-id trace-join is not achievable. `api_log` also defaults to Full Logging with no retention control (F-C.3). The LLM-egress breach surface (F-C.4) and the BAA/minimum-necessary distinction (F-C.5) are **forward-looking design notes, not current-system findings** — verification confirmed **no agent code exists yet**, so nothing forwards PHI to any LLM today.

| ID | Severity | Conf. | Finding |
|----|----------|-------|---------|
| **F-C.1** | **high** | verified | `api_log` omits OAuth `client_id` + granted scopes — §164.312(b) accountability gap. |
| **F-C.2** | **high** | verified | No mechanism to persist `X-Copilot-Request-Id` — D10 shared-id join is unachievable through OpenEMR. |
| F-C.3 | medium | verified | `api_log` defaults to Full Logging (PHI bodies), no retention/purge, ATNA forwarding off. |
| F-C.4 | info *(was high; refuted as current-system)* | verified | LLM-provider PHI egress **will** expand the breach surface — once the agent exists (it doesn't yet). |
| F-C.5 | info *(was high hypothesis)* | hypothesis | BAA covers permitted processing/breach terms, not minimum-necessary — the CE's duty stays on the agent. |
| F-C.6 | info | verified | "Hidden Observations" anomaly is a false positive. |

**F-C.1 — `api_log` omits client_id + scopes (high, confirmed).**
*Evidence:* `DESCRIBE api_log` → 11 columns (`id, log_id, user_id, patient_id, ip_address, method, request, request_url, request_body, response, created_time`), **no client_id, no scope.** Write path `LogTablesSink.php:71-82,98` and builder `ApiResponseLoggerListener.php:77-86` populate only those fields; `recordLogItem()` (`EventAuditLogger.php:642-694`) passes them through unchanged. The accessors **exist but are never used by the audit path** — `HttpRestRequest::getClientId()` (`:470`) and `getAccessTokenScopes()` (`:328`) are only read into the error logger. Granted scopes live in `oauth_clients.scope` but are never joined. **Adversarial re-check:** attribution *cannot* be recovered by joining to `api_token`/`jwt_grant_history` — `api_log.user_id` is a bigint (`users.id`, 0/1) while `api_token.user_id` is a varchar UUID; different identity spaces, no shared token/client id. No join path exists.
*Architecture impact:* **hardens D10/§7 and elevates D5.** The agent's Langfuse trace becomes the **system of record** for app-level attribution — the agent MUST log `{client_id, exact scopes exercised, correlation_id}` per FHIR call, because OpenEMR's §164.312(b) audit controls omit them. D5 (self-hosted Langfuse in-boundary) is therefore a **HIPAA accountability control, not just observability.**

**F-C.2 — No correlation-id persistence; D10 join unachievable (high, confirmed).**
*Evidence:* `api_log` has no header/request-id column (`sql/database.sql:92-105`, only `PRIMARY(id)`); `recordLogItem()` has no correlation parameter; `ApiResponseLoggerListener.php:77-86` builds the log array purely from session + request line and **never reads request headers**; all three `SinkInterface` impls consume a fixed 7-key DTO. Broad grep for `X-Copilot`/`correlation`/`request-id` across src/library/sql/interface → zero persistence code.
*Architecture impact:* **CHALLENGES D10/§7's** "join agent traces to OpenEMR's api_log via a shared id" — the join point does not exist, and adding one is disallowed (read-only, no app modification). **Revise §7:** cross-system audit correlation is **best-effort/fuzzy** on `(user_id, patient_id, request_url, utc_timestamp)`, weakened further because every agent call logs the same delegated `user_id`. Langfuse (D5) is the authoritative agent-side trace; api_log is corroborating evidence at best. Have the agent record `{correlation_id, client_id, scopes, user, patient, request_url, utc_timestamp}` per call so a compliance query can re-derive the api_log row by URL+timestamp.

**F-C.3 — Full-Logging default, no retention (medium).** `api_log_option=2` ("Full includes requests and responses," `library/globals.inc.php:2892-2903`) stores full PHI bundles indefinitely; **no retention/purge global exists** (grep found only password-expiry; no cron deletes from `log`/`api_log`); `enable_atna_audit=0` so tamper-evident external syslog is off. *Impact:* D10's fan-out writes 6+ Full-Logging PHI rows/turn, accumulating unbounded. The deployment should set `api_log_option` per a documented retention decision; the compliance posture must list api_log as a second at-rest PHI store (ties to **F-S.4**). No agent code change (D9 read-only).

**F-C.4 — LLM PHI egress (refuted as current-system; kept as forward design note).** *Verification refuted the "high" rating:* D4/D5 are planning decisions, not shipped code — `composer.json`/`package.json` contain no anthropic/langfuse package; no Python/FastAPI agent exists; every "anthropic" hit in the tree is an AI-attribution comment; live `GET /chat` → 404. **No PHI leaves the OpenEMR boundary today.** *Kept as forward-looking impact:* once the agent is built, the architecture must inventory four PHI stores — (a) Anthropic (prompts/completions), (b) self-hosted Langfuse (D5, in-boundary but breach-subject), (c) Railway platform logs (confirm no PHI leaks), (d) OpenEMR api_log (F-C.3) — each with an incident-response owner. D5's in-boundary choice is correct precisely because a SaaS observability tool would add a fifth external disclosure point needing its own BAA.

**F-C.5 — BAA ≠ minimum-necessary (info, hypothesis; no documentary error).** The regulatory principle is accurate: a BAA (§164.502(e)/§164.504(e)) governs permitted uses/safeguards/breach reporting and does **not** discharge the covered entity's §164.502(b) minimum-necessary duty. **Verification correction:** the planning docs do **not** conflate the two — minimum-necessary is separately addressed (least-privilege read-only scopes, single-patient pinning, category/lookback-filtered tools; DEFENSE.md:230-233,140), and no agent code exists to forward PHI. So this is a **design-review reminder, not a gap:** keep the EvidencePacket (§5) as the minimum-necessary boundary (only fields needed for the question reach the prompt), and reconcile prompt-caching on the full-patient prefix (D4) with minimum-necessary explicitly — a cached full-patient prefix maximizes PHI sent per turn.

---

## Audit → Architecture Impact (top findings → D#/§ they affect)

| Finding | Sev. | Decision / § affected | Effect |
|---------|------|----------------------|--------|
| **F-D.1** immunization status inverted | critical | **D7 / §5**, D9 | Proves the verification layer is load-bearing; EvidencePacket must treat FHIR `status` as unreliable; agent never renders "patient declined" from this field. Nuances D9 ("FHIR source ≠ field-correct"). |
| **F-D.5** empty allergy = NKDA ambiguity | (safety) | **§5 phrasing rule** | Forces the forbidden-phrasing rule verbatim: empty allergy → "no allergy records returned; confirm with patient," never "NKDA." |
| **F-D.4** allergy criticality null | high | **D7 / §5** | Verifier must reject any criticality-based claim; never infer/deprioritize allergy risk from criticality; constant fields not asserted. |
| **F-C.1** api_log omits client_id+scopes | high | **D5, D10 / §7** | Langfuse trace becomes the HIPAA system-of-record for app attribution; agent must log client_id+scopes+correlation per call. |
| **F-C.2 / F-A.5 / F-P.6** no correlation column | high | **D10 / §7 (revise)** | Shared-id api_log join is unachievable; downgrade to fuzzy `(user,patient,url,timestamp)` correlation; Langfuse authoritative. |
| **F-S.4** PHI plaintext in api_log | high | **D5, compliance** | api_log is a second in-boundary PHI store; inventory it; set `api_log_option`/retention per a documented decision. |
| **F-P.1 / F-P.3 / F-P.5** introspection + N+1 + latency | high/med | **§9, D10** | Per-read introspection tax + lab N+1 set the latency floor; size D10 timeouts for the worst (pid=7-class) patient; parallel fan-out worth ~1.7–1.9s/brief. |
| **F-A.2** SMART/OAuth2 surface real | (positive) | **D2, D9 — confirmed** | Sidecar integration boundary validated (S256-enforced PKCE, EHR-launch, delegated attribution). |
| **F-S.1** scope + compartment, not scope∧ACL | low | **D2 (reword)** | Refine D2's "scopes + ACL" phrasing; agent ceiling is the granted scope set; keep D12 pin as the real clinician↔patient guarantee. |
| **F-S.5** grant-type attribution | info | **D9 — confirmed** | Lock auth-code+PKCE; guardrail-test that the agent never uses client_credentials. |
| **F-S.2** patient-access check is a stub | med | **D12** | Agent-side session pin is the real enforcement — do not weaken it. |
| **F-S.7** deceased untestable on demo data | med | **D12 (test gap)** | Eval suite must inject a synthetic deceased fixture, or the D12 hard-stop ships unverified. |
| **F-S.6** user-scoped apps need admin approval | med | **D2 (runbook)** | One-time "enable app in Administration" provisioning step. |
| **F-C.4 / F-C.5** LLM egress / BAA scope | info | **D4, D5 (forward)** | No PHI egress today; when built, inventory 4 PHI stores; EvidencePacket = minimum-necessary boundary. |

---

## Decisions that need revisiting before Stage 5 (flagged, not rewritten)

1. **D10 / §7 — REQUIRED revision.** Drop the claim that the correlation ID "joins OpenEMR's api_log via a shared id." OpenEMR has no column or capture path for it (F-C.2). Restate as: Langfuse is authoritative; api_log correlation is best-effort fuzzy `(user, patient, url, timestamp)`.
2. **D2 — wording refinement.** "Authorization inherited from OAuth2/SMART scopes **+ ACL**" → for patient-scoped tokens it is scopes + single-patient compartment binding; GACL adds nothing there (F-S.1). D2's *substance* (external sidecar, inherited authz) is **confirmed** (F-A.2).
3. **D5 — elevate role.** Self-hosted Langfuse is now also a **HIPAA accountability control** (system of record for client_id + scopes), because OpenEMR's audit trail omits them (F-C.1) — not merely observability.
4. **D12 — close the test gap.** Add a synthetic deceased fixture so the deceased hard-stop is exercised end-to-end (F-S.7); the "empty allergy" path (F-D.5) needs the same.
5. **§5 verifier rules — now concrete.** F-D.1 (status unreliable), F-D.4 (criticality unreliable), F-D.5 (empty-allergy phrasing), F-D.6 (consume all conditions; never `clinical-status=active`; flag stale labs), F-D.2 (empty-dose phrasing + order/plan de-dup) are the specific rules the verification layer must encode.

*Gate status: complete. No agent/AI code written. Stopping here per the Stage 3 hard gate.*
