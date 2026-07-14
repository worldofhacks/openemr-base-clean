# W2_AUDIT.md — Write-Surface & Upload-Surface Audit (Week 2 gate artifact)

> Scope note: W1's AUDIT.md (docs/week1/, frozen) forensically audited the READ
> surface this agent consumes; nothing on that surface changed and it is not
> re-audited. Week 2 adds two new surfaces — EHR WRITES and DOCUMENT UPLOADS — and
> this audit covers them, code-verified in this fork on 2026-07-13 before any W2
> code. Findings W2-F#; all evidence is file:line in this repo.

## W2-F1 — No FHIR write path exists for the W2 targets (high, confirmed 3-way)

*Evidence:* exhaustive write-method enumeration of
`apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php` — POST/PUT exist only for
Organization (:546/:553), Patient (:560/:569), Practitioner (:677/:684), plus
`DELETE /fhir/$bulkdata-status` (:868). `POST /fhir/DocumentReference/$docref`
(:259) is the US Core document-GENERATION operation: its handler calls
`FhirOperationDocRefRestController->getAll(...)`; FHIR_README.md documents it as
"Generate Clinical Summary (CCD)"; `Documentation/api/FHIR_API.md:728` states "The
`$docref` operation creates clinical summary documents (C-CDA)." There is no
`POST /fhir/Observation` and no FHIR upload for DocumentReference.
*Impact:* W2-D1's write mechanism cannot be FHIR create. The PRD's own core
requirement sanctions the alternative ("as appropriate FHIR resources **or OpenEMR
records**"). Any design claiming FHIR writes against this fork would fail at build.

## W2-F2 — The standard REST documents API is the real upload path (confirmed)

*Evidence:* `apis/routes/_rest_routes_standard.inc.php:496` —
`POST /api/patient/:pid/document` → `DocumentRestController->postWithPath($pid,
path, $_FILES['document'], eid)`: multipart file upload, category path, optional
encounter linkage. Companion reads: `GET /api/patient/:pid/document` (:504) and
document download (:510).
*Impact:* source documents and machine-authored extraction artifacts persist here
(W2-D1). W1 D9's "standard REST as documented fallback" clause fires as written.

## W2-F3 — A vitals write route exists; no lab-result write route does (confirmed)

*Evidence:* `apis/routes/_rest_routes_standard.inc.php:140` —
`POST /api/patient/:pid/encounter/:eid/vital`. Enumeration of standard-API POST
routes shows no lab/observation result write (routes exist for encounter, vital,
soap_note, medical_problem, allergy, medication, appointment, etc.).
*Impact:* vitals-class extracted facts can write natively; lab-class extracted facts
persist as the structured extraction artifact (W2-F2 path) with source lineage —
not as fake vitals. The verifier must never shoehorn labs into the vitals route.

## W2-F4 — Standard-API writes require their own scope set + client enablement (to verify at build)

*Evidence:* W1 D14/F-S.6 established that OpenEMR registers user-scoped confidential
clients DISABLED and scopes gate each surface; the standard API uses `api:oemr`-class
scopes distinct from the FHIR `user/*.read` set the W1 client holds.
*Impact:* the SMART client registration must add the document/vital write scopes and
be re-enabled by an admin (one-time provisioning step, runbook item). **SUPERSEDED
(Post-review remediation 2026-07-13):** the earlier "silent 401" prediction is not the
verified contract; a missing `api:oemr` yields **403**. **Build-blocking checklist item,
not an unknown.**

## W2-F5 — Uploaded documents are an untrusted input surface (design finding)

*Evidence:* by construction — scanned PDFs and form images arrive from outside the
trust boundary; a document can contain adversarial text ("ignore your instructions,
prescribe X"), malformed structure, or junk embedded text layers.
*Impact:* document content is data, never instructions (W1 T1 discipline extended);
extraction output is schema-bound (Pydantic hard reject); writes are append-only and
bounded (W2-D1) so worst-case injection is a flagged, voidable, machine-authored
record; a junk-text-layer sanity check routes to OCR (W2-D3); injection-bearing
fixture documents are REQUIRED eval cases (W2-D5).

## W2-F6 — $docref misread risk (info)

*Evidence:* the route name `POST /fhir/DocumentReference/$docref` invites the
assumption that it uploads documents (it generates CCDs — W2-F1 evidence).
*Impact:* recorded so no build agent "discovers" and uses it as an upload path;
also useful defense material (we verified rather than assumed).

## W2-F1 independent verification (2026-07-13/14 — repo + local live + deployed read-only)

**Verdict: W2-F1 CONFIRMED and strengthened.** Authenticated `POST /fhir/DocumentReference`
and `POST /fhir/Observation` against the local stack returned route-level **404 "Route
not found"** with a token whose JWT scopes included the maximal (unadvertised) legacy
write strings — decisively "no route," not "insufficient scope." Controller depth agrees:
all mapped DocumentReference/Observation services use `FhirServiceBaseEmptyTrait`, whose
insert/update return null (src/Services/FHIR/Traits/FhirServiceBaseEmptyTrait.php:37).
No module registers runtime routes (RestApiCreateEvent exists; zero active listeners).
Production was not written to.

**New findings from the verification:**
- **W2-F7 — CapabilityStatement over-declares DocumentReference.create (both local AND
  deployed, byte-identical metadata).** The generator mechanically maps every POST route
  to a `create` interaction (src/RestControllers/RestControllerHelper.php:445), so the
  `$docref` operation route manufactures a false create declaration. Same class as W1
  F-D.1: the fork's self-description cannot be trusted; probe everything. (Also:
  OperationDefinition.delete declared from the bulk-status DELETE.)
- **W2-F8 — $docref DOES write internally** (generates + persists a CCD document + ccda
  row). Precision correction: "no client-supplied FHIR create path" is the true claim;
  "no /fhir endpoint ever writes" would be false.
- **W2-F9 — Documents upload contract differs from assumption:** POST returns **200 with
  body `true`** (not 201) and **no document id**
  (src/RestControllers/DocumentRestController.php:120); id discovery = collection GET by
  unique filename/content-hash. Upload/list verified; **FHIR read-back verified
  byte-exact** (DocumentReference/uuid → Binary/uuid, SHA-256 match on a 603-byte test
  PDF). The standard REST download companion **returns 500** in this stack (CSRF-key
  defect via DocumentService::getFile → C_Document) — known local defect; the FHIR
  Binary path is the reliable read-back. **SUPERSEDED (Post-review remediation
  2026-07-13):** the 500 remains code-consistent, but the cause is raw document bytes
  being passed as `BinaryFileResponse`'s filename, **not** a CSRF-key defect; see the
  W2-F9 correction below.
- **W2-F10 — Vitals path fully validated end-to-end:** POST vital → 201 {vid} → standard
  GET returns values → **FHIR `Observation?category=vital-signs` surfaces 15 Observation
  resources** (BP panel, HR, SpO2, temp, height, weight, respiration). The PRD's
  "derived observations round-trip through OpenEMR" is proven across both API surfaces.
- **W2-F11 — Scope/discovery drift (multiple instances):** SMART v2 discovery advertises
  only `.rs` for our targets; a validator defect accepts unadvertised legacy
  `user/DocumentReference.write`/`user/Observation.write` at registration; several
  advertised scope letters have no matching routes (appointment update, transaction
  delete); repo docs claim write scopes that conflict with discovery AND routing.
  Pattern finding: registration/discovery/docs/routes are four surfaces that disagree.
- **W2-F4 RESOLVED — provisioning verified live.** Minimum W2 write/read scope surface:
  `api:oemr user/document.crs user/vital.crus user/Observation.rs` (+
  `user/DocumentReference.rs user/Binary.read` for document read-back verification).
  Registration → created DISABLED → admin enable via Administration → System → API
  Clients (verified sequence recorded). Staff ACLs must independently permit
  patients/docs write. **Precision (W2-F4/F12): there is no supported persisted-scope edit
  path or admin scope-set editor — W1's client cannot be extended through a supported
  operation, so W2 requires a REPLACEMENT registration** (union of W1+W2 scopes,
  auth-code+refresh grants, swap SMART_CLIENT_ID/SECRET, then disable the old client —
  W1's E9 duplicate-launcher lesson applies to the cutover). **SUPERSEDED
  (Post-review remediation 2026-07-13):** disable-only is insufficient; the cutover must
  disable the old client **and** retire its access and refresh tokens by revocation or by
  waiting out both token lifetimes before treating the old client as retired (W2-F17).

**MVP design corrections fed to the binding doc/plan:** upload success = 200 `true`; id
via list-by-hash; FHIR DocumentReference→Binary is the round-trip read-back (dodges the
500); `$docref` described as "server-generated CCD persistence"; vitals unchanged.
Verification client: local-only, password-grant (pre-enabled locally; NOT for
production), to be disabled post-audit. This is historical probe provenance only:
**password grant is prohibited for the W2 write client** specified below.

### Post-review remediation (2026-07-13) — exact W2 write-client registration manifest

This block is the provisioning source of truth for the replacement confidential client.
The carried W1 read set is copied exactly from the executable W1 policy at
`agent/app/auth/scopes.py:23-30` and its freezing test at
`agent/tests/test_scopes.py:23-35`; no read scope is inferred from discovery metadata.
The W2 verification established the standard-write and read-back additions at
W2-F4/W2-F10 above. The exact registered scope manifest is:

```text
openid
offline_access
launch
launch/patient
api:oemr
user/Patient.read
user/Condition.read
user/MedicationRequest.read
user/AllergyIntolerance.read
user/Observation.read
user/Encounter.read
user/document.crs
user/DocumentReference.rs
user/Binary.read
user/vital.crus
user/Observation.rs
```

`user/Observation.rs` is the verified FHIR
`Observation?category=vital-signs` read-back scope; the carried
`user/Observation.read` remains because the replacement registration is the exact union
of W1 and W2. The manifest deliberately does **not** register the unadvertised
`user/DocumentReference.write` or `user/Observation.write` strings. Scope order is not
semantic, but membership is exact.

Use this dynamic-registration payload, substituting only the deployment callback URI
placeholder from the secret/environment runbook; do not put a client secret in the
payload or in this repository:

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

The accepted registration fields and array handling are code-backed at
`src/RestControllers/AuthorizationController.php:268-291,311-336`; the confidential
client selection is `application_type=private` at `:312-315`; and the repository's
auth-code exchange posts the secret in the form body (`client_secret_post`) at
`agent/app/auth/smart_client.py:148-163`. The W2 client permits **only** delegated
`authorization_code` plus `refresh_token`; it never registers or uses `password`,
`client_credentials`, or `private_key_jwt`.

Provisioning sequence: register with the exact payload; capture the returned client ID
and secret directly into the approved secret store without logging either value; enable
the initially disabled client in **Administration → System → API Clients**; retain
the fixed scope manifest as non-secret evidence; and validate the first delegated token
against the exact scopes requested for that launch, rejecting missing or unexpected
grants before writes are enabled (W2-F12). A skipped `api:oemr` grant is a **403**, not
401. At cutover, swap the deployment credentials, prove the new client flow, then disable
the old client and revoke both access and refresh tokens; if revocation is unavailable,
wait out **both** lifetimes before declaring retirement. Disable-only is not retirement.

## Adversarial audit review (2026-07-13 — read-only static analysis; findings W2-F12..F23)

> A SEPARATE read-only agent adversarially re-checked the whole write/upload surface
> against the code (static analysis only; no live probes — live claims assessed for
> code-consistency). Full evidence with file:line is in `W2_AUDIT_REVIEW_RAW.md`; this
> section is the distilled record. **Verdict counts:** W2-F1..F11 → 7 CONFIRMED, 0
> false-positive, 4 IMPRECISE; 12 new findings (W2-F12..F23); 183 literal routes checked;
> 0 active module route listeners. Four of the new findings were spot-verified against the
> code in this pass (W2-F12 ScopeEntity.php:156-161; W2-F13/F16 EncounterService.php:580-595;
> W2-F15 EncounterService.php:657-676; W2-F20 — the listener lives at
> `src/RestControllers/Subscriber/ApiResponseLoggerListener.php:83-85`, a directory
> correction to the raw review's path; line content matches). **Owner's two load-bearing
> calls on this review (see W2-D9):** (1) the **W2-D1 transport decision survives** — no
> client-supplied FHIR CRUD exists; standard documents + vitals APIs remain the sanctioned
> transport, not reopened; (2) the earlier **"no finding blocks the architecture" gate
> verdict is retired** — the blocking items below are now MANDATORY agent-side controls
> that must land before writes are enabled (transport still sound).

**Imprecise corrections to earlier findings (dated 2026-07-13; originals stand, refined):**
- **W2-F1 → IMPRECISE (thrust holds).** "No client-supplied target CRUD" is exact; "no
  FHIR write path" is too broad — `$docref`, `$export`, expired-Binary reads, and UUID
  backfills can mutate DB state (see W2-F8, W2-F21). All mapped DocumentReference/
  Observation persistence is empty (`FhirServiceBaseEmptyTrait.php:37-50`).
- **W2-F4 → IMPRECISE (replacement still required).** No supported client-scope edit path
  exists → the W1 client still needs replacement for `api:oemr`, `user/document`,
  `user/vital`. But a missing `api:oemr` yields **403, not the unconditional 401** the
  earlier note implied (`BearerTokenAuthorizationStrategy.php:365-378`); and registered
  scope is **not an effective ceiling** (W2-F12).
- **W2-F9 → IMPRECISE (contract holds; cause wrong).** Upload 200 `true`/no-id CONFIRMED;
  the download **500 is real but NOT a "CSRF-key defect"** — the controller authorizes raw
  retrieval and passes raw bytes as `BinaryFileResponse`'s filename
  (`DocumentRestController.php:156-171`, `C_Document.class.php:574-637`). FHIR
  DocumentReference→Binary read-back remains the reliable byte-exact path.
- **W2-F11 → IMPRECISE (broader than stated).** Discovery advertises legacy `.read` **plus**
  v2 `.rs` (not "only `.rs`"); the validator can accept same-key `.write/.cud/.cruds` and
  ignore constraints (`ServerScopeListEntity.php:72-347`, `ScopeEntity.php:140-178`).

**New findings (W2-F12..F23):**
- **W2-F12 — HIGH — Same-resource OAuth scope escalation + constraint stripping.**
  `ScopeEntity::containsScope` returns the *requested* scope's legacy read/write flag
  without consulting the registered permission (`ScopeEntity.php:140-178`, esp. 155-161);
  constrained scopes can be requested unconstrained. A read-registered client can
  statically pass finalization for same-resource write scopes. **Registration scope is not
  a trustworthy ceiling.** W1→W2 replacement still holds (W1 lacks `api:oemr`, lowercase
  `document`/`vital`). *Affects: W2-F4, W2-F11, least-privilege, provisioning (W2-OA3).*
- **W2-F13 — HIGH — Native document/vital creates trust caller-supplied pid/eid.** Routes
  accept `pid`/`eid` directly (`_rest_routes_standard.inc.php:140-152,496-502`); authz
  checks resource permission, not launch-patient equality (`AuthorizationListener.php:169-194`);
  the server patient-access check is a stub (`BearerTokenAuthorizationStrategy.php:473-485`);
  vital create stamps supplied pid/eid without validating the encounter
  (`EncounterService.php:580-595`); `documents.encounter_id` has no FK. A sufficiently
  scoped token can target another patient / a nonexistent encounter. **The agent's
  patient-pin + encounter-ownership preflight is load-bearing, not defense-in-depth.**
  *Affects: W2-D1, W2-M8, W2-M11.*
- **W2-F14 — HIGH — Document category validation can yield uncategorized docs / bypass
  category ACL.** Route authz checks only the generic patients/docs ACL;
  `DocumentService::isValidPath` can return true for an unresolved one-component path
  (`DocumentService.php:52-94`); category-aware `Document::can_access` is not called on
  upload; docs with no categories are treated accessible (`Document.class.php:361-364`).
  *Affects: W2-D9 canonical source/artifact path → expected category-ID/ACL preflight,
  provisioning, and runtime attestation (W2-OA3, W2-M8, W2-M11).*
- **W2-F15 — HIGH — REST vital creation bypasses the physiological range validator.** The
  REST validator checks only numeric/string shape (`EncounterService.php:657-676`); the
  real range validator (`FormVitals.php:470-510`, `VitalsFieldRanges.php:22-90`) is **not
  called** — negative/impossible values persist. **Range sanity is the agent's job.**
  *Affects: W2-D1 vital mapping, extraction verifier, bounded-write policy (W2-M11).*
- **W2-F16 — HIGH — Vital clinical author absent or caller-controlled.** `insertVital`
  overwrites id/eid/pid/authorized but **not** user/group (`EncounterService.php:580-588`);
  `FormVitals` accepts arbitrary user/groupname (`FormVitals.php:173-192`); FHIR performer
  emitted only when user joins to a UUID+NPI. Omitting user → author absent; supplying
  another username → spoofed performer. **The agent must never send caller user/group and
  must decide provenance representation.** *Affects: W1 F-S.5, W2 lineage/attribution
  (W2-M11).*
- **W2-F17 — HIGH — Client disable is weaker than the runbook assumes.** Existing access
  tokens survive client disable (only `is_enabled` flips — `ClientAdminController.php:360-371`
  TODO-to-revoke; never re-checked at `BearerTokenAuthorizationStrategy.php:141-211`);
  token lifetime 1h; JWT/`client_credentials` paths bypass the enablement check. **"Disable
  the old client" is not immediate retirement.** *Affects: W2-OA3 cutover, W2-F4.*
- **W2-F18 — MEDIUM — Native document upload is non-idempotent.** Every successful POST
  allocates a new UUID; the content hash is computed after creation and never used for
  dedup; no patient+hash uniqueness (`DocumentService.php:151-159`,
  `Document.class.php:1109-1125`, `sql/database.sql:1391-1432`). **The agent-side
  UNIQUE(patient_id, content_hash) ledger is mandatory, not defense-in-depth.**
  *Affects: W2-D1 idempotency (W2-M8, W2-M11).*
- **W2-F19 — MEDIUM/HIGH — Native upload controls + failure contract incomplete.**
  `DocumentService` does not check `$_FILES` error, declared size, `is_uploaded_file`, PDF
  page count, W2's 10 MB limit, or exact type (`DocumentService.php:127-160`); a rejected
  upload returns false → an empty **404, not the documented 400**
  (`RestControllerHelper.php:156-168`). **The agent must enforce size/page/MIME/error
  validation before the native upload.** *Affects: W2-D3 upload policy (W2-M8).*
- **W2-F20 — MEDIUM (conditional HIGH leak) — W2 write logging differs from the assumed
  model.** `api_log` copies the JSON **response** into both `request_body` and `response`
  (`src/RestControllers/Subscriber/ApiResponseLoggerListener.php:83-85`) — so the uploaded
  PDF is **NOT** duplicated into `api_log` and posted vital values are **NOT** stored as a
  request body (the earlier F-S.4-style hypothesis is FALSE). But FHIR Observation JSON
  readback still hits W1 F-S.4, and **FHIR Binary readback passes decrypted document bytes
  to a debug logger when `system_error_logging=DEBUG`** (`BaseDocumentDownloader.php:58-69`;
  default WARNING). *Affects: W1 F-S.4, log config, document readback (W2-M11, W2-M8).*
- **W2-F21 — MEDIUM — HTTP GET does not imply DB-read-only.** `GET $export` creates
  export_job + Document/Binary artifacts; a `GET` on an expired Binary can soft-delete the
  document; DocumentReference/Observation **service construction and capability-metadata
  GETs invoke UUID backfills** that do table UPDATEs + `uuid_registry` INSERTs
  (`UuidRegistry.php:411-426`, `RestControllerHelper.php:504-510`). **A metadata GET can
  conditionally write the production DB.** *Affects: any "read-only" wording (W2-M2 note,
  Phase-3 W2-1).*
- **W2-F22 — MEDIUM — Self-description ≠ complete runtime/auth surface.** CapabilityStatement
  reads the static route file, not the route finder (`FhirMetaDataRestController.php:49-77`);
  SMART discovery disagrees with OAuth discovery on grants/auth methods. **Metadata/discovery
  cannot independently prove route or provisioning behavior.** *Affects: W2-F7, W2-F11.*
- **W2-F23 — HIGH, adjacent (not part of W2-D1) — SOAP-note PUT ownership gap.** PUT
  `.../soap_note/:sid` updates solely by `sid`, ignoring eid and overwriting pid, and
  lacks the pid/eid ownership check present in vital update (`EncounterService.php:535-557`
  vs `:560-577`). Broader standard-REST write-surface risk; **the agent touches no
  soap_note route** — recorded so no build agent adds one; merits a separate synthetic
  IDOR probe.

**W1 carry-over findings in W2 context:**
- **F-D.5 — CONFIRMED** — allergy mapper maps stored records only, no NKDA synthesis
  (`FhirAllergyIntoleranceService.php:101-255`); empty bundle = ambiguous absence (carried
  as UC-W2-2's "confirm with patient", never "NKDA").
- **F-S.5 — CONFIRMED with a W2 provenance caveat** — delegated auth-code tokens establish
  the actual clinician; the standard API rejects the system role. Caveat = W2-F16
  (`form_vitals.user`/performer absent or caller-controlled).
- **F-S.4 — CONFIRMED for JSON responses; REFUTED for inbound W2 write bodies** — full JSON
  FHIR responses remain plaintext `api_log` by default, but the logger never captures the
  multipart PDF or the vital request JSON.

**Blocking items (reviewer's list — MANDATORY per W2-D9 before writes are enabled; transport
remains sound):**
1. **W2-F12** — provision exact scopes; the agent asserts the granted scope set and rejects
   any unexpected granted scope; document residual server-side escalation.
2. **W2-F13** — patient-pin + encounter-ownership preflight mandatory; cross-patient and
   mismatched-encounter negative tests must pass before writes are enabled.
3. **W2-F14** — canonical source/artifact paths, each preflighted to its provisioned
   expected category ID+ACL; the request sends the path and mismatch fails closed.
4. **W2-F15/F16** — bind vital range + attribution policy; never send caller user/group;
   decide clinician-provenance representation.
5. **W2-F17** — cutover: revoke both access and refresh tokens or wait out each token
   class's independently recorded maximum lifetime; disable-only/one-hour-only is not
   retirement. Do not substitute `private_key_jwt` without resolving its enablement bypass.
6. **W2-F18** — D10's patient-bound permanent dedup/lineage, durable intents, and
   reconcile-before-retry protocol are load-bearing; 30-day attempts are not the ledger.
7. **W2-F19** — size/page/exact-MIME/upload-error/controlled-4xx validation before the
   native upload.
8. **W2-F20** — confirm `system_error_logging != DEBUG` before using FHIR Binary for
   document verification.
9. **W2-F21** — Phase-3 must distinguish "no HTTP write method" from "no DB mutation"
   (a metadata GET can backfill UUIDs).

## Gate verdict

The W2 **transport** is sound: uploads and artifacts via the documents API, vitals via the
vitals API, everything append-only with lineage — the W2-D1 decision survives the
adversarial review (owner call 1, W2-D9). **The earlier "no finding blocks the
architecture" verdict is retired (owner call 2, W2-D9):** the adversarial review surfaced
HIGH findings (W2-F12..F17) showing the OpenEMR write surface does **not** enforce
patient/encounter ownership, scope ceilings, category ACLs, vital ranges, attribution, or
idempotency on create — so those controls are **mandatory agent-side** and must land
before writes are enabled (blocking items 1–9 above; threaded into W2-OA3, W2-M8, W2-M11).
W2-F4 remains the provisioning checklist item (replacement client). All findings feed
W2_ARCHITECTURE (§3 discrepancy note, §4a ledger), W2_DECISIONS (W2-D1, W2-D3, W2-D5,
**W2-D9/W2-D10**), and W2_IMPLEMENTATION_PLAN.
