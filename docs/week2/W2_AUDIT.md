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
be re-enabled by an admin (one-time provisioning step, runbook item). A silent 401 on
first write is the expected first-run failure if skipped. **Build-blocking checklist
item, not an unknown.**

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
  Binary path is the reliable read-back.
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
  patients/docs write. **Critical constraint: existing clients cannot gain scopes
  post-registration and the admin screen cannot edit scope sets — W1's client cannot be
  extended; W2 requires a REPLACEMENT registration** (union of W1+W2 scopes,
  auth-code+refresh grants, swap SMART_CLIENT_ID/SECRET, then disable the old client —
  W1's E9 duplicate-launcher lesson applies to the cutover).

**MVP design corrections fed to the binding doc/plan:** upload success = 200 `true`; id
via list-by-hash; FHIR DocumentReference→Binary is the round-trip read-back (dodges the
500); `$docref` described as "server-generated CCD persistence"; vitals unchanged.
Verification client: local-only, password-grant (pre-enabled locally; NOT for
production), to be disabled post-audit.

## Gate verdict

The W2 integration mechanism is sound with the corrected transport: uploads and
artifacts via the documents API, vitals via the vitals API, everything append-only
with lineage, scopes verified at provisioning. No finding blocks the architecture;
W2-F4 is the one build-time checklist item. All findings feed W2_ARCHITECTURE
(§3 discrepancy note, §4a ledger) and W2_DECISIONS (W2-D1, W2-D3, W2-D5).
