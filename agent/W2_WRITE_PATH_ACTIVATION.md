# Week 2 write-path activation

This runbook activates the real deployed
`upload → extract → ground → write → cite → answer` path without weakening a control.
The default remains fail-closed. The automation pins both services to
`W2_DOCUMENT_RUNTIME_ENABLED=false` and `W2_GRAPH_ENABLED=0`, proves the live
prerequisites, enables the worker, then enables the attested graph/web path, and rolls
both flags back to disabled on any failure.

After confirming that the OpenEMR deployment contains synthetic/demo charts only, the
single activation/verification command is:

```bash
W2_VERIFY_SYNTHETIC_ONLY_ACK=synthetic-patient-and-documents \
  python agent/scripts/activate_w2_write_path.py
```

It enrolls every current synthetic patient and encounter in an immutable attested route
registry, then uses only committed synthetic PDFs and the canonical synthetic Week 1
patient UUID for the live verification sample. It never accepts secrets as arguments,
calls Railway's variable-list command, prints an opaque session or route identifier, or
returns document bytes.

## Owner checklist — only these three provisioning actions

- [ ] **1. Register and enable the replacement SMART client.** Use the exact name,
  callback, client type, grants, and 16 scopes in the next section. Do not give the
  registration secret to the activation script.
- [ ] **2. Create `/AI-Source-Documents` and `/AI-Extractions` and grant the ACLs.** Make
  both immediate root categories with `aco_spec=patients|docs`; grant the launching
  synthetic-demo staff role `patients/docs` write/read and `encounters/notes` access.
- [ ] **3. Set exactly two owner-managed secret variables on both Railway services.** Set
  `SMART_CLIENT_SECRET` and `DOCUMENT_CREDENTIAL_KEY` on `agent` and
  `document-worker`. Use the replacement client's secret and the same stable valid Fernet
  `DOCUMENT_CREDENTIAL_KEY` on both services. Never paste either value into source or a
  command transcript.

If `document-worker` does not yet exist, run the single command once after steps 1–2. It
creates the empty service, pins it disabled, and stops safely at the first unmet
prerequisite. Complete checklist item 3 in Railway, then rerun the same command.

## 1. Exact SMART registration

Register and enable one replacement client named exactly
`AgentForge Week 2 Write Client`:

```json
{
  "application_type": "private",
  "client_name": "AgentForge Week 2 Write Client",
  "initiate_login_uri": "https://agent-production-9f62.up.railway.app/week2/launch",
  "redirect_uris": ["https://agent-production-9f62.up.railway.app/callback"],
  "token_endpoint_auth_method": "client_secret_post",
  "grant_types": ["authorization_code", "refresh_token"],
  "scope": "openid offline_access launch launch/patient api:oemr user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/Encounter.read user/document.crs user/DocumentReference.rs user/Binary.read user/vital.crus user/Observation.rs"
}
```

The exact case-sensitive scope set is:

1. `openid`
2. `offline_access`
3. `launch`
4. `launch/patient`
5. `api:oemr`
6. `user/Patient.read`
7. `user/Condition.read`
8. `user/MedicationRequest.read`
9. `user/AllergyIntolerance.read`
10. `user/Observation.read`
11. `user/Encounter.read`
12. `user/document.crs`
13. `user/DocumentReference.rs`
14. `user/Binary.read`
15. `user/vital.crus`
16. `user/Observation.rs`

The **App Launch URI** (OpenEMR's `initiate_login_uri`) and callback are different
by design:

- App Launch URI: `https://agent-production-9f62.up.railway.app/week2/launch`
- Redirect URI: `https://agent-production-9f62.up.railway.app/callback`

Do not use `/launch`: that route intentionally remains the separate Week 1 pre-visit
application and returns to `/app`. The Week 2 launch binds its one-time OAuth state to
`/week2` on the server; neither OpenEMR nor a callback query chooses that destination.

In OpenEMR, enable the client after registration; manual-approval scopes cause new
registrations to start disabled. Do not add `user/DocumentReference.write`,
`user/Observation.write`, `password`, `client_credentials`, or `private_key_jwt`.

The automation discovers the public `client_id` from the exact live registration. It
requires one unrevoked record with that exact name, enabled/private/secret-backed state,
the sole callback above, the exact Week 2 App Launch URI, the two exact grants, no skipped
EHR authorization, and exactly the 16 scopes. It selects only a boolean that a stored
secret exists, never the secret.

## 2. Exact OpenEMR categories, grants, and DEBUG-off state

In **Administration → Practice → Practice Settings → Document Categories**, create these
immediate children of the root `Categories` node:

| Name | Canonical path | Required `aco_spec` |
|---|---|---|
| `AI-Source-Documents` | `/AI-Source-Documents` | `patients|docs` |
| `AI-Extractions` | `/AI-Extractions` | `patients|docs` |

Grant the launching synthetic-demo staff role:

- `patients/docs` write plus collection/readback access; and
- `encounters/notes` access for the intake-vitals write/readback leg.

OpenEMR does not expose internal category IDs or `aco_spec` through REST/FHIR, and it
does not expose the global `system_error_logging` value there. The script therefore uses
Railway's authenticated SSH transport for a narrow configuration/attestation transaction
inside the deployed MySQL service. It requires `secure_upload=1`, idempotently inserts or
reactivates only `application/json` in `files_white_list`, and independently confirms that
the entry is active. It never disables secure upload or permits a MIME wildcard. This is
required because the frozen grounded extraction artifact is an `application/json`
document. The script also requires exactly two root category rows, distinct positive IDs,
and exact ACLs, then sets `SOURCE_DOCUMENT_CATEGORY_ID` and
`ARTIFACT_DOCUMENT_CATEGORY_ID` itself. No owner copies an ID, and no OpenEMR schema or
source code is changed.

OpenEMR's SMART/FHIR context identifies patients and encounters with UUIDs, while its
legacy document and vital routes require numeric `pid`/`eid` values. The frozen exact-16
scope set deliberately does not add the separate lowercase standard-patient lookup
scope. During the same database attestation, the script therefore deterministically
enumerates every current non-null patient UUID/PID pair and every current non-null
encounter UUID/EID pair together with its owning patient UUID.

The script validates canonical UUIDs, positive canonical decimal IDs, UUID and numeric-ID
uniqueness, and encounter ownership. It sorts the arrays, calculates a SHA-256 hash over
their canonical JSON, and sends the payload through stdin—not command arguments—to
`python -m app.writeback.route_attestations import-stdin` inside the disabled Agent web
service. The importer validates the supplied counts/hash and atomically activates the
snapshot in Agent Postgres. Re-importing the same snapshot is idempotent; additions are
allowed, changing an existing UUID-to-numeric binding is a hard conflict, and omitted
existing bindings remain preserved rather than being deleted.
Only aggregate counts/hash status may be logged.

Runtime keeps UUIDs for the SMART patient pin, delegated authorization, ledgers, FHIR
reads, and citations. It resolves numeric IDs only for legacy routes and still requires
the encounter mapping to belong to the pinned patient before HTTP. The retired four
`OPENEMR_LEGACY_*` singleton variables are removed from web and worker while both are
disabled. The canonical Week 1 synthetic patient remains only the end-to-end verification
target; every patient in the attested snapshot is supported by its own pinned SMART
session. A patient or encounter created after the snapshot remains fail-closed until this
activation command is rerun.

The same read-only attestation requires `system_error_logging=WARNING`. If it is not
already `WARNING`, the script stops fail-closed and emits this owner-admin remediation:

**Administration → Config → Logging → System Error Logging Options → Standard Error
Logging (`WARNING`)**, save, then rerun the single command.

This setting is an OpenEMR global, not an agent/Railway variable, so the automation does
not fabricate a Railway substitute. Once the query proves `WARNING`, it sets the agent
attestation `OPENEMR_BINARY_READBACK_SAFE=true` on web and worker.

The non-DEBUG attestation prevents OpenEMR's FHIR Binary downloader from placing
decrypted document bytes in DEBUG log context. The final verifier then performs fresh
`DocumentReference → Binary` reads for both source and grounded-artifact documents,
compares SHA-256 expected/observed digests byte-for-byte, and exposes only digests and
boolean results—not bytes, OAuth material, or clinical content.

### Railway SSH tooling fallback

This Railway account must have a registered local SSH key for that narrow provisioning
and attestation transaction.
If none is available, the script prints exactly this command and stops with both services
disabled:

```bash
railway ssh keys add
```

Run it once, then rerun the activation command. This is only Railway transport setup; it
does not create categories, change ACLs, or read a secret. The activation transaction's
only OpenEMR mutation remains the exact `application/json` whitelist entry described
above.

## 3. Owner-managed secrets

The script intentionally never reads the following from the local environment or from
Railway, and never takes them as arguments:

- `SMART_CLIENT_SECRET`
- `DOCUMENT_CREDENTIAL_KEY`

Set both directly in Railway's secret UI on `agent` and `document-worker`. The document
key must be a URL-safe base64 Fernet key and must match across web and worker. Enabled
process construction validates that both are present and usable; a missing/malformed key
or missing SMART secret prevents startup. The script then restores
`W2_DOCUMENT_RUNTIME_ENABLED=false` on both services and reports only a sanitized stage
failure.

The synthetic browser launch also needs the existing synthetic-demo OpenEMR login. Make
`OE_ADMIN_PASS` available only in the activation process environment (and optionally
`OE_USERNAME`, default `admin`). Do not put the password on a command line. The script
does not retain or print it.

## What the script automates

On every run, `agent/scripts/activate_w2_write_path.py` performs this idempotent sequence:

1. Verifies authenticated Railway CLI access and finds or creates exactly one
   `document-worker` service in the pinned project/environment.
2. Sets the full non-secret baseline on web and worker with
   `W2_DOCUMENT_RUNTIME_ENABLED=false` and `W2_GRAPH_ENABLED=0` before inspecting
   mutable prerequisites.
3. Requires the exact synthetic-only acknowledgment, OpenEMR secure upload to remain
   enabled, idempotently enables only the
   architecture-required `application/json` whitelist entry, and attests it is active.
   It then validates the SMART registration, both category paths/IDs/ACLs,
   `system_error_logging=WARNING`, every current synthetic patient UUID/PID mapping, and
   every current encounter UUID/EID/owner mapping required by legacy write routes.
4. Sets the discovered public SMART client ID and both category IDs with both services
   disabled; removes the retired singleton `OPENEMR_LEGACY_*` variables.
5. Deploys the web service disabled so migrations and the registry importer are present,
   then imports the validated deterministic snapshot over stdin. Any conflict or import
   failure leaves both services disabled.
6. Deploys the worker from a temporary context in which the committed
   `agent/railway.worker.json` is the active `railway.json`. Its real start command is
   `python -m app.ingestion.worker`; there is no fake HTTP health endpoint.
7. Sets worker `W2_DOCUMENT_RUNTIME_ENABLED=true`, deploys it, and requires one running,
   non-crashed replica before touching web.
8. Sets the attested `W2_GRAPH_ENABLED=1` configuration and web
   `W2_DOCUMENT_RUNTIME_ENABLED=true` last, deploys it, and requires `/ready` to report
   overall `ready` plus `document_runtime: {ok: true, kind: hard, detail: ready}`.
9. Starts the repository Selenium service automatically when the configured loopback
   endpoint is absent, opens `/week2/launch`, performs authorization-code + PKCE, and
   requires the callback to land on the distinct `/week2` workbench before selecting the
   exact canonical synthetic UUID from OpenEMR's `data-patient-id`. It refuses an absent,
   ambiguous, or defaulted patient. Only the opaque agent session remains in memory; the
   token never leaves the web service.
10. Runs `agent/scripts/verify_w2_write_path.py` in a clean child process containing only
   the five `W2_VERIFY_*` synthetic context variables; the OpenEMR admin password and
   owner-managed secrets are never inherited. The intake form is uploaded first, so
   delegated encounter ownership fails before any document write. It then runs intake +
   lab through extract, ground, exactly-once write, fresh Binary readback, cite, and
   answer, and requires `/ready` green again.
11. On any failure after an enable attempt, pins both services disabled and redeploys the
    disabled configuration. Re-running is safe: patient + content hash drive the durable
    exactly-once ledger.

## Exact non-secret Railway variable contract

The script sets these values itself on both services as applicable:

```text
W2_DOCUMENT_RUNTIME_ENABLED
W2_GRAPH_ENABLED
OPENEMR_FHIR_BASE_URL
OPENEMR_OAUTH_BASE_URL
OPENEMR_REST_BASE_URL
AGENT_CALLBACK_URL
SMART_CLIENT_ID
SOURCE_DOCUMENT_PATH
SOURCE_DOCUMENT_CATEGORY_ID
SOURCE_DOCUMENT_CATEGORY_ACL
ARTIFACT_DOCUMENT_PATH
ARTIFACT_DOCUMENT_CATEGORY_ID
ARTIFACT_DOCUMENT_CATEGORY_ACL
OPENEMR_BINARY_READBACK_SAFE
DOCUMENT_WORKER_ID
DOCUMENT_WORKER_POLL_SECONDS
DOCUMENT_WORKER_LEASE_SECONDS
DOCUMENT_WORKER_MAX_ATTEMPTS
DOCUMENT_WORKER_BASE_BACKOFF_SECONDS
RERANKER
```

The worker also receives Railway project references—not fetched values—for the already
managed web variables `ANTHROPIC_API_KEY`, `SESSION_STORE_DSN`, `LANGFUSE_HOST`,
`LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY`. `RERANKER=local`, so no Cohere secret is
needed for activation. Langfuse receives aggregate accountability, latency, usage, cost, and
verdict metadata only; prompts, transcripts, tool payloads, claims, and answers are always
redacted and have no deployment override.

Optional non-secret selector overrides exist for a non-production clone:

```text
W2_ACTIVATE_RAILWAY_PROJECT_ID
W2_ACTIVATE_RAILWAY_ENVIRONMENT
W2_ACTIVATE_WEB_SERVICE
W2_ACTIVATE_WORKER_SERVICE
W2_ACTIVATE_MYSQL_SERVICE
W2_ACTIVATE_OPENEMR_BASE_URL
W2_VERIFY_AGENT_BASE_URL
SELENIUM_URL
OE_USERNAME
W2_VERIFY_PATIENT_ID
W2_VERIFY_ENCOUNTER_ID
```

`W2_VERIFY_PATIENT_ID` and `W2_VERIFY_ENCOUNTER_ID` select only the synthetic live
verification sample; they do not limit registry enrollment. The patient must exist in the
snapshot and the encounter must be attested beneath that patient. If the encounter is
omitted, activation chooses a deterministic attested encounter for that patient.

The three OpenEMR bases and callback are pinned to their exact HTTPS deployed-origin
shapes; divergent values fail before activation.

## Run and interpret the result

From the repository root, with the owner checklist complete and `OE_ADMIN_PASS` supplied
through the process environment, first confirm the target OpenEMR contains synthetic/demo
charts only, then run only:

```bash
W2_VERIFY_SYNTHETIC_ONLY_ACK=synthetic-patient-and-documents \
  python agent/scripts/activate_w2_write_path.py
```

Success prints aggregate evidence only: activation complete plus the verifier's counts
for synthetic documents, source Binaries, artifact Binaries, and grounded citations.
Failure starts with `FAIL-CLOSED:` and names only the missing prerequisite or failed
stage. Do not troubleshoot by setting the runtime true manually, loosening scopes/ACLs,
disabling readback, adding a stub worker, or calling the verifier with fabricated IDs.
