# W2_AUDIT_REVIEW_RAW — verbatim output of the read-only adversarial audit review

> Provenance: produced 2026-07-13 by a SEPARATE read-only agent (static analysis only;
> no files/git/HTTP; live assertions assessed for code-consistency only). This is the
> raw evidence record. It is distilled into W2_AUDIT.md (findings W2-F12..F23 + imprecise
> corrections) and W2_DECISIONS.md (W2-D9). Kept for the file:line citations and the
> paper trail. Not itself a decision doc.

## BOTTOM LINE
The W2-D1 transport decision survives: this repository has no client-supplied FHIR
create/update/upload path for DocumentReference, Observation, or Binary. The standard
documents and vitals APIs are the applicable write paths. However, four numbered findings
are materially imprecise, including W2-F4. Newly missed findings change the security and
provisioning assumptions:
- Registered scopes are immutable in the UI/DB-facing service, but they are NOT an
  effective permission ceiling — a read-registered client can statically pass validation
  for same-resource write scopes.
- Standard document and vital writes are not bound to the token's launch patient and do
  not validate patient/encounter ownership on create.
- Vital range validation is bypassed by the REST route; vital clinical attribution is
  caller-controlled or absent.
- Disabling a client does not revoke existing access tokens.
- "Read-only" FHIR GETs, including metadata construction, can conditionally mutate the DB.
- The specific hypothesis that PDF/vital request bodies are copied into api_log is FALSE.
  api_log copies JSON responses into both request_body and response instead.

## VERDICT TABLE (W2-F1..F11)
- W2-F1 — IMPRECISE — no client-supplied target CRUD exists, but "no FHIR write path" is
  too broad. $docref, $export, expired Binary reads, and UUID backfills can mutate state.
  Evidence: _rest_routes_fhir_r4_us_core_3_1_0.inc.php:247-295,493-503;
  FhirServiceBaseEmptyTrait.php:37-50.
- W2-F2 — CONFIRMED — POST /api/patient/:pid/document reaches
  DocumentRestController::postWithPath and DocumentService::insertAtPath:
  _rest_routes_standard.inc.php:496-502; DocumentRestController.php:120-124;
  DocumentService.php:127-160.
- W2-F3 — CONFIRMED — POST vital at _rest_routes_standard.inc.php:140-145 reaches
  EncounterRestController.php:471-489. No lab-result/Observation write route exists.
- W2-F4 — IMPRECISE — no supported client-scope edit path exists, so the W1 client still
  needs replacement for api:oemr, user/document, user/vital. BUT same-resource scope
  escalation is permitted by ScopeEntity::containsScope: ScopeEntity.php:140-178;
  ScopeRepository.php:137-168. Missing api:oemr produces 403, not the audit's
  unconditional 401: BearerTokenAuthorizationStrategy.php:365-378.
- W2-F5 — CONFIRMED — uploads untrusted; native handling does only a configurable MIME
  allowlist before ingesting raw bytes: DocumentService.php:127-153;
  library/sanitize.inc.php:105-149. Schema/prompt/page-count/content controls are agent
  design requirements, not OpenEMR behavior.
- W2-F6 — CONFIRMED — $docref is generation not upload; fresh CCD-generation event each
  successful branch: FhirDocRefService.php:86-93,152-218. Non-idempotent.
- W2-F7 — CONFIRMED — capability generation mechanically maps every POST except $export
  to create: RestControllerHelper.php:445-471 (esp. 458-459). Only DocumentReference POST
  is $docref: _rest_routes_fhir_r4_us_core_3_1_0.inc.php:247-283. No real create impl.
- W2-F8 — CONFIRMED — $docref persists a Document and ccda row:
  FhirDocRefService.php:152-218; CCDAEventsSubscriber.php:57-94; CcdaGenerator.php:117-168;
  EncounterccdadispatchTable.php:3940-3976.
- W2-F9 — IMPRECISE — upload returning 200 true with no ID CONFIRMED:
  DocumentRestController.php:120-124; DocumentService.php:127-160. The observed download
  500 is code-consistent, but "CSRF-key defect" is WRONG: the controller authorizes raw
  retrieval and passes raw bytes as BinaryFileResponse's filename:
  DocumentService.php:162-176; DocumentRestController.php:156-171;
  C_Document.class.php:574-637,859-876.
- W2-F10 — CONFIRMED (code-consistent; probe not re-run) — standard POST writes
  form_vitals via EncounterService/VitalsService; FHIR vitals mapper queries the same
  service/table: EncounterService.php:580-595; VitalsService.php:40,85-202,330-405;
  FhirObservationVitalsService.php:335-450. Exact live count of 15 not reverified.
- W2-F11 — IMPRECISE — drift confirmed and broader; discovery advertises legacy .read
  PLUS v2 .rs, not "only .rs." Validator can accept same-key .write/.cud/.cruds and
  ignore constraints: ServerScopeListEntity.php:72-203,337-347; ScopeEntity.php:140-178.

Refuted subclaims (no full finding killed): (1) "No FHIR endpoint ever writes." (2) "An
existing client cannot gain scopes post-registration." (3) "A skipped provisioning step
always yields 401." (4) "The download 500 is a CSRF-key defect." (5) "Discovery advertises
only .rs." (6) "Full logging copies inbound PDF/vital request bodies into api_log."

## INDEPENDENT-VERIFICATION (static assessment of the earlier live-probe claims)
- Plain POST DocumentReference/Observation → 404: code-consistent; unmatched paths reach
  HttpRestRouteHandler.php:54-60,98-112; POST /_search is separate search normalization.
- All mapped target persistence methods empty: CONFIRMED — FhirDocumentReferenceService
  .php:32-50; FhirObservationService.php:41-73; FhirServiceBaseEmptyTrait.php:37-50.
- No runtime module routes: confirmed for this repo; a deployed-only module cannot be
  excluded statically.
- Local/deployed metadata byte-identical: not statically verifiable.
- Upload returns 200 true: confirmed from code.
- FHIR readback byte-exact: code-consistent — DocumentReference→Binary same UUID; Binary
  streams Document::get_data() unchanged: FhirDocumentReferenceTrait.php:106-126;
  BaseDocumentDownloader.php:58-79.
- Standard download 500: code-consistent; cause corrected above.
- Vital → 15 FHIR resources: same-table mapping confirmed; exact count not re-run.
- Registration disabled/admin-enable: code-consistent; persisted scope editing absent, but
  the effective scope ceiling is defective.

## ROUTE MATRIX (literal, exhaustive)
FHIR (73-868): GET 72, POST 4, PUT 3, PATCH 0, DELETE 1 = 80.
Standard (51-716): GET 55, POST 20, PUT 15, PATCH 0, DELETE 8 = 98.
Portal (29-45): GET 5, others 0 = 5. TOTAL 183.
FHIR POST: DocumentReference/$docref, Organization, Patient, Practitioner.
FHIR PUT: Organization/:uuid, Patient/:uuid, Practitioner/:uuid. FHIR PATCH: none.
FHIR DELETE: $bulkdata-status.
Standard POST incl.: patient/:pid/encounter/:eid/vital, patient/:pid/document (+ others).
Standard PUT incl.: patient/:pid/encounter/:eid/vital/:vid, .../soap_note/:sid (+ others).
Target-surface conclusions: DocumentReference = GET + POST $docref only; Observation =
GET only; Binary = GET instance only; standard document = POST upload + GET list/download
(no PUT/PATCH/DELETE); standard vital = POST/GET/PUT (PUT requires existing vid owned by
pid/eid, EncounterService.php:560-577 — not an upsert). Organization/Patient/Practitioner
PUT have no insert fallback; metadata declares updateCreate=false
(RestControllerHelper.php:517-519). No portal write surface.
Effective-method nuance: POST /fhir/<resource>/_search is normalized to GET search before
routing (HttpRestRequest.php:641-646; RoutesExtensionListener.php:51-54;
SearchRequestNormalizer.php:15-52) — searches, not writes. Plain POST DocumentReference/
Observation are not normalized.
Runtime extensions: RestApiCreateEvent is real (RestApiCreateEvent.php:25-70; dispatched by
StandardRouteFinder.php:28-39, FhirRouteFinder.php:21-33, PortalRouteFinder.php:25-35), but
0 active listeners add routes (claimrev-connect Bootstrap.php:323-333 empty; dorn
Bootstrap.php:239-247 empty; test fixture only). Repo runtime matrix complete; a
deployed-only module is statically unknowable.

## SCOPE SURFACE
Advertised FHIR write scopes: legacy v1 user/Patient.write, user/Practitioner.write,
user/Organization.write; SMART v2 FHIR = none (.rs only); operation scope
DocumentReference.$docref; api:fhir gates FHIR generally.
Standard v2 write-bearing: user/document.crs, user/vital.crus, user/patient.crus,
user/encounter.crus, ... (+ .cruds/.cud/.crds variants). Standard routes also require
api:oemr. Evidence: ServerScopeListEntity.php:72-347; ScopePermissionObject.php:40-80.
Advertised-but-unrouted: appointment.cruds update (no PUT), transaction.cuds delete (no
DELETE), legacy .write overstates for document/encounter/facility/insurance/patient/
practitioner/prescription/soap_note/transaction/vital/appointment; FHIR
Patient/Practitioner/Organization .write implies delete (no FHIR DELETE).
Routed-but-unadvertised: background_service GET/POST (_rest_routes_standard.inc.php:681-716)
has no resource scope in ServerScopeListEntity.php:206-314.

## MISSED FINDINGS (new candidates)
- **W2-F12 — HIGH — Effective same-resource OAuth scope escalation + constraint
  stripping.** ScopeEntity::containsScope returns the REQUESTED read/write flag without
  checking the registered/server permission: ScopeEntity.php:140-178 (esp. 155-160);
  ScopeRepository.php:93-127,137-168; ScopePermissionObject.php:40-80. A client registered
  for user/Patient.read can statically pass finalization for user/Patient.write/.cud/
  .cruds; constrained scopes can be requested unconstrained (constraints never compared);
  negative test undiscovered (ScopeRepositoryTest.php:225-246). W1→W2 replacement still
  holds (exact context/resource lookup keys, ScopeEntity.php:93-102; W1 lacks api:oemr,
  lowercase document, lowercase vital) BUT registration scope is not a trustworthy ceiling.
  Affects W2-F4, W2-F11, least-privilege model, provisioning.
- **W2-F13 — HIGH — Native document/vital creates trust caller-supplied patient/encounter
  ids.** Routes accept pid/eid directly (_rest_routes_standard.inc.php:140-152,496-502);
  authz checks resource permission not launch-patient equality
  (AuthorizationListener.php:169-194); server patient-access check is a stub
  (BearerTokenAuthorizationStrategy.php:473-485); DocumentService passes pid/eid to
  Document (DocumentService.php:127-159; Document.class.php:1109-1125); vital create stamps
  supplied pid/eid without validating the encounter (EncounterService.php:580-595;
  VitalsService.php:341-380); documents.encounter_id has no FK (sql/database.sql:1391-1432).
  A sufficiently scoped delegated token can target another pid and associate a
  document/vital with a nonexistent/other-patient encounter. Affects W2-D1 and the
  patient-pin/encounter-preflight controls (W2_ARCHITECTURE.md:239-258;
  W2_IMPLEMENTATION_PLAN.md:334-370) — those agent controls are load-bearing.
- **W2-F14 — HIGH — Document category validation can produce uncategorized documents /
  bypass category ACL intent.** Route authz checks only generic patients/docs ACL
  (_rest_routes_standard.inc.php:496-502); DocumentService::isValidPath can return true for
  an unresolved one-component path (DocumentService.php:52-80); getLastIdOfPath can return a
  missing ID (:83-94); category-aware Document::can_access not called on upload
  (Document.class.php:348-372); createDocument inserts category linkage only when IDs
  numeric (:1122-1125); documents with no categories considered accessible (:361-364);
  seeded categories have differing aco_spec (sql/database.sql:287-335). Affects W2-D1 fixed
  category, provisioning, access controls.
- **W2-F15 — HIGH — REST vital creation bypasses the physiological range validator.** REST
  validator checks only numeric/string shape (EncounterService.php:657-676); the real range
  validator (FormVitals.php:470-510; VitalsFieldRanges.php:22-90; native UI invocation
  C_FormVitals.class.php:463-473) is NOT called; REST persists directly via
  EncounterRestController.php:471-489 and VitalsService.php:314-322,330-405. Negative/
  impossible values can be stored. Affects W2-D1 vital mapping, extraction verifier,
  bounded-write policy.
- **W2-F16 — HIGH — Vital clinical author absent or caller-controlled.** EncounterService
  overwrites id/eid/pid/authorized but not user/group (EncounterService.php:580-588);
  FormVitals accepts arbitrary recognized fields incl. user/groupname
  (FormVitals.php:173-192,526-540); VitalsService persists them (:314-321,365-380); FHIR
  performer emitted only when user joins to a user UUID+NPI (VitalsService.php:132-178;
  FhirObservationVitalsService.php:664-668). Omitting user leaves author/performer absent;
  supplying another valid username spoofs the performer. Forms wrapper is session-attributed
  (FormService.php:62-102) but that does not repair form_vitals provenance. Affects W1 F-S.5,
  W2 lineage/attribution/auditability.
- **W2-F17 — HIGH — Client disable/admin-enable weaker than the runbook assumes.** Existing
  access tokens survive client disable (ClientAdminController.php:360-371 TODO to revoke,
  only flips flag; ClientRepository.php:231-243 only updates is_enabled;
  BearerTokenAuthorizationStrategy.php:141-211 never rechecks is_enabled); access-token
  lifetime 1h (AuthorizationController.php:104-111). JWT paths bypass enablement
  (private_key_jwt auth-code branch returns before the common enabled check,
  CustomAuthCodeGrant.php:175-238; client_credentials no isEnabled check,
  CustomClientCredentialsGrant.php:189-242; ClientRepository::validateClient secret check
  only for authorization_code, :179-221). Affects W2-OA3 cutover, W2-F4 enable assumption.
  Current client_secret auth-code provisioning still reaches the enable check, but "disable
  old client" is not immediate retirement.
- **W2-F18 — MEDIUM — Native document upload is non-idempotent.** Every successful POST
  allocates a new document/file UUID (DocumentService.php:151-159;
  Document.class.php:1054-1088); hash computed after creation, never used for dedup
  (:1109-1125); no patient+hash uniqueness (sql/database.sql:1391-1432). The agent-side
  UNIQUE(patient_id, content_hash) ledger is mandatory, not defense-in-depth.
- **W2-F19 — MEDIUM/HIGH — Native upload controls + failure contract incomplete.**
  DocumentService does not check $_FILES error, declared size, is_uploaded_file, PDF page
  count, W2's 10MB limit, or exact type policy (DocumentService.php:127-160); secure_upload
  configurable (library/globals.inc.php:2125-2129) with broad default allowlist incl. ZIP/
  text/images (sql/database.sql:5741-5749). A rejected/empty upload returns false →
  responseHandler converts to empty 404, not the documented 400
  (RestControllerHelper.php:156-168; DocumentRestController.php:113-123). Affects W2-D3
  upload policy, error handling, provisioning tests.
- **W2-F20 — MEDIUM (conditional HIGH leak) — W2 write logging differs from the assumed
  model.** api_log copies the JSON RESPONSE into both request_body and response
  (ApiResponseLoggerListener.php:50-86); full logging defaults to 2
  (library/globals.inc.php:2893-2902); plaintext longtext (sql/database.sql:91-105).
  Therefore the uploaded PDF is NOT duplicated into api_log and posted vital values are NOT
  stored as request body; but document-true/vital-IDs may be stored twice, pid/eid remain
  in request_url, staff-token patient_id is generally 0. FHIR Observation JSON readback
  still triggers W1 F-S.4. Separate conditional leak: FHIR Binary decrypts/reads document
  bytes and passes the payload to a debug logger (BaseDocumentDownloader.php:58-69) — active
  only when system_error_logging=DEBUG (SystemLogger.php:38-67; default WARNING,
  library/globals.inc.php:2925-2932). Affects W1 F-S.4, D5/D10 accountability, log config,
  document readback.
- **W2-F21 — MEDIUM/HIGH — HTTP GET does not imply DB-read-only.** GET $export creates
  export_job rows + Document/Binary artifacts (FhirOperationExportRestController.php:196-547;
  FhirExportJobService.php:87-116); GET expired Binary can soft-delete the document
  (FhirDocumentRestController.php:111-125; Document.class.php:384-391); DocumentReference /
  Observation service construction invokes UUID backfills (FhirPatientDocumentReferenceService
  .php:56-60; DocumentService.php:31-35; FhirObservationService.php:60-73;
  VitalsService.php:50-54; SocialHistoryService.php:34-38); capability metadata instantiates
  service classes (RestControllerHelper.php:504-510); UUID backfill does table UPDATEs +
  uuid_registry INSERTs (UuidRegistry.php:411-426,536-554). Affects Phase-3 "read-only"
  wording — a metadata GET can conditionally write the production DB when UUIDs are missing.
- **W2-F22 — MEDIUM — Self-description does not represent the complete runtime/auth
  surface.** CapabilityStatement reads the static route file, not FhirRouteFinder
  (FhirMetaDataRestController.php:49-53,77) — a future module-added FHIR route could be
  operational but absent from metadata. SMART discovery emits scopes_supported as a nested
  array and disagrees with OAuth discovery on grant/auth methods
  (SMARTConfigurationController.php:69-89; OAuth2DiscoveryController.php:65-109). Affects
  W2-F7, W2-F11 — metadata/discovery cannot independently prove route/provisioning behavior.
- **W2-F23 — HIGH, adjacent (not part of D1) — SOAP-note PUT ownership gap.** PUT
  .../soap_note/:sid calls updateSoapNote which ignores eid, overwrites pid, updates solely
  by sid (EncounterService.php:535-557) — lacks the pid/eid ownership check present in vital
  update (:560-577). Broader standard REST write-surface security; does not change W2-D1;
  merits a separate synthetic IDOR probe.

## SECONDARY W1 FINDINGS IN W2 CONTEXT
- F-D.5 — CONFIRMED — FhirAllergyIntoleranceService maps stored records only, no NKDA
  synthesis (FhirAllergyIntoleranceService.php:101-255); empty bundle = ambiguous absence.
- F-S.5 — CONFIRMED with a W2 provenance caveat — delegated auth-code tokens establish the
  actual clinician; client_credentials uses oe-system; standard API rejects system-role
  (BearerTokenAuthorizationStrategy.php:238-395; AuthorizationListener.php:169-175). api_log
  + document attribution are clinician-based. Caveat = W2-F16 (form_vitals.user/performer
  absent or caller-controlled).
- F-S.4 — CONFIRMED for JSON responses; REFUTED for inbound W2 write bodies — full JSON FHIR
  responses remain plaintext api_log by default, but the logger never captures the multipart
  PDF or vital JSON request (mirrors the response).

## BLOCKING ITEMS (the reviewer's list — the gate statement "no finding blocks the
architecture" is no longer supportable without these; transport remains sound)
1. W2-F12: registered scope set is not the effective ceiling — provision exact scopes,
   reject unexpected granted scopes in the agent, document residual server-side escalation.
2. W2-F13: patient pin + encounter-ownership preflight mandatory; cross-patient and
   mismatched-encounter negative tests must pass before writes are enabled.
3. W2-F14: provisioned fixed category + explicit category-ID/ACL validation.
4. W2-F15/F16: bind vital range + attribution policy; do not permit caller-supplied
   user/group; decide clinician provenance representation.
5. W2-F17: cutover — revoke access/refresh tokens + trusted-user authorization, or wait out
   token expiry; do not substitute private_key_jwt without resolving its enablement bypass.
6. W2-F18: agent idempotency ledger load-bearing.
7. W2-F19: size, page, exact MIME/content, upload-error, controlled-4xx validation before
   the native upload.
8. Confirm system_error_logging != DEBUG before using FHIR Binary for document verification.
9. Phase 3 must distinguish "no HTTP write method" from "no DB mutation" (metadata GET can
   backfill UUIDs).

## SUMMARY COUNTS
W2-F1..F11: CONFIRMED 7, FALSE-POSITIVE 0, IMPRECISE 4. Newly found: 12 (W2-F12..F23).
Literal routes checked: 183. Active module route listeners: 0. W1 carryovers: F-D.5
confirmed; F-S.5 confirmed with provenance caveat; F-S.4 confirmed for JSON responses,
refuted for inbound W2 write bodies.
