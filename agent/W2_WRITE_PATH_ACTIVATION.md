# Week 2 deployed write-path activation

This runbook activates the real `upload → extract → ground → write → cite → answer`
path. The default remains fail-closed: leave `W2_DOCUMENT_RUNTIME_ENABLED=false` until
all five owner items below are complete. Do not substitute a local token, fake worker,
disabled Binary check, or relaxed scope/category setting.

Production agent callback: `https://agent-production-9f62.up.railway.app/callback`.

## Owner checklist

- [ ] Register and enable one replacement **private** SMART client with the exact 16
  scopes and sole redirect URI below; place its new ID/secret in Railway.
- [ ] Create the two exact root document categories, record their live IDs, and grant the
  launching staff role document-write/readback plus intake-vitals access.
- [ ] Observe `system_error_logging=WARNING` on the deployed OpenEMR instance and record
  the environment and timestamp.
- [ ] Put the complete web/worker variable set in Railway, including one stable Fernet
  `DOCUMENT_CREDENTIAL_KEY`; do not expose any value in logs or source.
- [ ] Create and deploy the dedicated `document-worker` service from the same image, then
  enable the web runtime and run the single verifier command at the end.

## 1. Replace the SMART client

Register a new client with this exact payload, replacing only the callback placeholder
at registration time. The redirect is an HTTPS web callback (not a native-app URI, not a
wildcard, and no trailing slash).

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

Set `${SMART_REDIRECT_URI}` to exactly
`https://agent-production-9f62.up.railway.app/callback`. The 16 case-sensitive scopes
are:

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

Do not add `user/DocumentReference.write`, `user/Observation.write`, `password`,
`client_credentials`, or `private_key_jwt`. In OpenEMR, go to **Administration →
System → API Clients** and enable the newly registered client (new registrations start
disabled). Store the returned values directly under `SMART_CLIENT_ID` and
`SMART_CLIENT_SECRET` in Railway; never paste them into a command, ticket, or this file.

The deployed launch uses authorization code + PKCE(S256), and the background credential
may use only the corresponding delegated refresh token. On callback, the agent requires
the granted set to equal all 16 scopes—missing *or extra* scopes refuse the session before
write access. After the new client passes verification, disable the old W1 client and
revoke both its access and refresh tokens. If revocation is unavailable, wait out and
record both maximum lifetimes before declaring the old client retired.

## 2. Create categories and grant ACLs

In **Administration → Practice → Document Categories**, create two immediate children of
the root `Categories` node:

| Name | Canonical path | Required `aco_spec` | Railway ID variable |
|---|---|---|---|
| `AI-Source-Documents` | `/AI-Source-Documents` | `patients|docs` | `SOURCE_DOCUMENT_CATEGORY_ID` |
| `AI-Extractions` | `/AI-Extractions` | `patients|docs` | `ARTIFACT_DOCUMENT_CATEGORY_ID` |

Do not nest them and do not create them from the agent. Using an administrator's database
console, run this read-only check and retain its two-row result as deployment evidence:

```sql
SELECT id, name, parent, aco_spec
FROM categories
WHERE parent = 1
  AND name IN ('AI-Source-Documents', 'AI-Extractions')
ORDER BY name;
```

Require exactly one row per name and `aco_spec=patients|docs`; copy each instance-assigned
`id` to its named Railway variable. Also set the non-secret runtime attestations
`SOURCE_DOCUMENT_CATEGORY_ACL=patients|docs` and
`ARTIFACT_DOCUMENT_CATEGORY_ACL=patients|docs`.

Grant the clinician/staff role used for the SMART launch:

- `patients/docs` with `write` (the POST route also accepts `addonly`, but the live
  reconciliation/readback path additionally needs collection read access); and
- `encounters/notes` access for the intake-vitals write/readback leg.

The runtime accepts only the two canonical paths, the recorded IDs, and the exact ACL
attestations. It also performs an authorized collection read before write/reconciliation.
Unknown, duplicate, nested, wrong-ID, wrong-ACL, or unreadable paths fail closed.

## 3. Attest non-DEBUG Binary readback

On the deployed OpenEMR instance, open **Administration → Config → Logging → System Error
Logging Options** and observe **Standard Error Logging**, whose stored value is `WARNING`.
Record the production environment, observed value, verifier, and timestamp. Repeat this
check after each OpenEMR deploy or logging-setting change.

Only after that observation, set `OPENEMR_BINARY_READBACK_SAFE=true` on both web and
worker. Leave it false if the value is unknown, unreadable, or `DEBUG`.

This attestation is a confidentiality control: at `DEBUG`, OpenEMR's FHIR document
downloader can place decrypted document bytes in a debug log context. At `WARNING`, that
debug event is not emitted. The agent checks the attestation before every FHIR Binary
read. The final verifier then performs fresh `DocumentReference → Binary` reads for both
the source document and grounded extraction artifact, compares SHA-256 digests, and
returns no bytes, token, or clinical content.

## 4. Set the Railway variable contract

Use Railway's variable/secret UI so values are never echoed into a shell transcript.
Apply the same shared values to the web service and the dedicated worker unless a row is
marked worker-specific.

| Variable name | Required setting |
|---|---|
| `OPENEMR_FHIR_BASE_URL` | Deployed HTTPS `/apis/default/fhir` base |
| `OPENEMR_OAUTH_BASE_URL` | Deployed HTTPS `/oauth2/default` base |
| `OPENEMR_REST_BASE_URL` | Deployed HTTPS `/apis/default` base |
| `SMART_CLIENT_ID` | New replacement-client ID |
| `SMART_CLIENT_SECRET` | New replacement-client secret |
| `AGENT_CALLBACK_URL` | `https://agent-production-9f62.up.railway.app/callback` |
| `ANTHROPIC_API_KEY` | Owner-managed provider secret |
| `SESSION_STORE_DSN` | Same restricted Postgres DSN on web and worker |
| `DOCUMENT_CREDENTIAL_KEY` | Same stable URL-safe base64 Fernet key on web and worker |
| `SOURCE_DOCUMENT_PATH` | `/AI-Source-Documents` |
| `SOURCE_DOCUMENT_CATEGORY_ID` | Live ID recorded in step 2 |
| `SOURCE_DOCUMENT_CATEGORY_ACL` | `patients|docs` |
| `ARTIFACT_DOCUMENT_PATH` | `/AI-Extractions` |
| `ARTIFACT_DOCUMENT_CATEGORY_ID` | Live ID recorded in step 2 |
| `ARTIFACT_DOCUMENT_CATEGORY_ACL` | `patients|docs` |
| `OPENEMR_BINARY_READBACK_SAFE` | `true` only after step 3 |
| `W2_DOCUMENT_RUNTIME_ENABLED` | Keep `false` until step 5 |
| `DOCUMENT_WORKER_ID` | Unique stable worker name, for example `document-worker-production` |
| `DOCUMENT_WORKER_POLL_SECONDS` | `1.0` |
| `DOCUMENT_WORKER_LEASE_SECONDS` | `60` |
| `DOCUMENT_WORKER_MAX_ATTEMPTS` | `3` |
| `DOCUMENT_WORKER_BASE_BACKOFF_SECONDS` | `5` |
| `RERANKER` | `local` (or `cohere` only with `COHERE_API_KEY`) |
| `COHERE_API_KEY` | Required only for `RERANKER=cohere`; otherwise leave unset |
| `LANGFUSE_LOG_CONTENT` | `false` |

`DOCUMENT_CREDENTIAL_KEY` is the Railway credential-encryption key—not a SMART token.
Generate it in the owner's approved secret manager as a valid Fernet key and keep it
stable. Web encrypts the patient/principal-bound delegated access+refresh material into
the shared Postgres store; worker must use the identical key to decrypt and refresh it.
Neither process prints the key or credential.

The configuration validator rejects activation when the callback is not the exact HTTPS
`/callback` shape, either path/ACL drifts, either category ID is absent, the Binary flag is
not true, the REST base is absent/plaintext, or the Fernet key is missing/malformed.

## 5. Create the worker, activate, and verify

1. Push/deploy the `feat/w2-write-path-activation` HEAD that contains this runbook.
2. In the same Railway project/environment as the web service, create one service named
   `document-worker` from the same repository and branch. Set its root directory to
   `agent` and its config file to `/agent/railway.worker.json`. If Railway asks for an
   explicit start command, use exactly `python -m app.ingestion.worker`.
3. Give the worker no public domain and no synthetic HTTP health endpoint. Use one
   replica. Its real health signal is the durable heartbeat that the web service checks.
4. Attach the exact shared variables from step 4. Set
   `W2_DOCUMENT_RUNTIME_ENABLED=true` on the worker and deploy it. Confirm it remains
   running; do not treat restart-looping as ready.
5. Set `W2_DOCUMENT_RUNTIME_ENABLED=true` on web and redeploy the same image. `/ready`
   must report overall `ready` and `document_runtime` with `ok=true`, `kind=hard`,
   `detail=ready`—`detail=disabled` is a failed activation.
6. From OpenEMR, launch the replacement SMART app in a dedicated synthetic patient's
   chart with an existing synthetic encounter. Put the resulting opaque context only in
   local environment variables named `W2_VERIFY_AGENT_BASE_URL`,
   `W2_VERIFY_SESSION_ID`, `W2_VERIFY_PATIENT_ID`, and `W2_VERIFY_ENCOUNTER_ID`. Set
   `W2_VERIFY_SYNTHETIC_ONLY_ACK=synthetic-patient-and-documents`. Do not paste these
   values into source or logs.
7. Run the command below from the repository root. It is idempotent: repeated uploads use
   the patient-bound content-hash ledger. It uploads only the committed synthetic lab and
   intake PDFs, requires grounded fields and a completed worker job, freshly hashes both
   source and artifact FHIR Binaries, requires grounded citations for both documents in
   the answer, and checks `/ready` before and after. Success prints aggregate counts only.

```bash
python agent/scripts/verify_w2_write_path.py
```
