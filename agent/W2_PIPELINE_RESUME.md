# Week 2 Pipeline Resume Checkpoint

Checkpoint recorded: 2026-07-14 (America/New_York)

This file is the restart point for the Week 2 pipeline task. Do not redo B2, B3, B4, or the committed serving integration. Resume only the executable document-runtime unit, then integration/deploy/push.

## Hard scope still in force

- Work under `agent/` only.
- Do not touch `.github/`, `agent/evals/golden/cases.json` entries 41–50, binding architecture/decision documents, `docs/week1/`, OpenEMR PHP/routes/schema, or the other agent's CI/adversarial work.
- Frozen tests and frozen Pydantic schemas must not be weakened.
- Synthetic data only; secrets come from environment/platform variables and never enter code, logs, fixtures, or this file.
- No force pushes or history rewrites.
- The production web process enqueues document jobs; it must not execute clinical jobs inline. The worker is a distinct process/service and uses a durable encrypted delegated-job credential.
- Keep W2 runtime enablement fail-closed until every deployment attestation is real.

## Canonical committed state

Canonical worktree:

```text
/Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-integration
```

Branch and last production-code head (the resume document itself is a later docs-only
checkpoint commit):

```text
feat/w2-integration
02fe39643d56b969540063db00406c0ba77b9210  merge(W2): delegated OpenEMR write and readback gateway
```

The production-code head is 26 commits ahead of `origin/swarm/w2-wave0` and has no
committed dependency on the unfinished worktrees below.

Important committed checkpoints:

```text
4498f35 feat(W2): integrate graph retrieval serving path
80eecd1 feat(W2): mount documents and render verified graph evidence
8064b6b feat(agent): execute durable document extraction runtime
181eb84 feat(W2): add strict Anthropic VLM extraction adapter
233214e feat(W2): hydrate persisted document refs into graph turns
811d7f0 test(W2): prove lab PDF full path
32631df feat(agent): wire delegated OpenEMR live gateway
9b1ece8 feat(W2): bootstrap attested document runtime schema
eed8299 feat(W2): enforce replacement delegated-client scopes
02fe396 merge(W2): delegated OpenEMR write and readback gateway
```

Already complete and committed:

- B2 ingestion, grounding, durable job queue, exactly-once writeback machinery.
- B3 supervisor/workers, real retrieval worker, verified composer, CitationV2/source-class/bbox rendering.
- B4 manifest-driven harness and five boolean scorers.
- Evidence and document routers mounted; graph serving integration mounted.
- VA/DoD corpus/retrieval lane, non-adversarial 40-case golden data, OpenAPI/Bruno/retrieval docs.
- Strict Anthropic VLM adapter, persistent artifact refs, live delegated OpenEMR document/vital gateway.

Last committed full-suite validation:

```text
577 passed, 7 skipped, 1 pre-existing warning in 7.06s
```

Command and environment used:

```bash
cd /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-integration/agent
/Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-swarm-w2-wave0/agent/.venv/bin/pytest -q tests corpus/tests evals
```

The shared test venv already has the declared `python-multipart` and `rank-bm25` dependencies installed.

The golden manifest remains at 40 non-adversarial cases and was not changed during this checkpoint:

```text
boundary=14, invariant=15, regression=11
SHA-256 329392e339393ada34991e9cfc9159b2c8a17d73b9aa96597fb61432fa1186a2
```

## Paused test-first worktrees

All active subagents were interrupted before this checkpoint. No worker is still editing files.

### 1. Runtime composition

```text
Worktree: /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-runtime-composition
Branch:   feat/w2-runtime-composition
Base:     02fe396
State:    one untracked RED test, no production edits
File:     agent/tests/test_runtime_composition.py
```

The test freezes these expectations:

- `AgentServices` exposes durable document repository/artifact store/pipeline/processor/document operations when W2 is enabled.
- Uvicorn does not start an inline clinical worker.
- The worker loop pulses a durable heartbeat, accepts a stop event, and stops before another claim.
- app lifespan calls `shutdown()`.
- document readiness is hard-failed for missing schema/crypto or stale/missing worker heartbeat.

### 2. Encrypted job credentials

```text
Worktree: /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-job-credentials
Branch:   feat/w2-job-credentials
Base:     02fe396
State:    one untracked RED test, no production edits
File:     agent/tests/test_job_credentials.py
```

The test proposes these public interfaces (adjust the runtime-composition branch to the final names only once):

```text
app.auth.job_credentials.CredentialCipher
app.auth.job_credentials.JobCredentialVault
app.auth.job_credentials.InMemoryJobCredentialRepository
app.auth.job_credentials.PostgresJobCredentialRepository
app.auth.job_credentials.JobCredentialUnavailable
app.auth.job_credentials.JobCredentialBindingError
app.auth.job_credentials.JobCredentialAuthExpired
SmartClient.refresh_token(refresh_token=SecretStr(...))
TokenResponse.refresh_expires_in
```

Required behavior:

- Fernet/authenticated ciphertext at rest; access/refresh tokens absent from DB repr, errors, and logs.
- Durable credential is bound to session, clinician, and patient; job stores only `credential_ref`.
- Resolution works after UI idle without consulting the interactive session.
- Expired access token refreshes only with the delegated `refresh_token` grant, rotates ciphertext, and preserves the old refresh token if OpenEMR omits a replacement.
- Wrong patient, tampering, missing credential, refresh expiry/revocation, or decrypt failure fails closed with typed errors.
- Add migration `005` for credential storage, an env-sourced `SecretStr` Fernet key, and the `cryptography` dependency.
- Never implement or call `client_credentials`.

### 3. Mounted route-to-answer E2E

```text
Worktree: /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-runtime-route-e2e
Branch:   feat/w2-runtime-route-e2e
Base:     02fe396
State:    one untracked test, no production edits
File:     agent/tests/test_document_route_runtime_e2e.py
```

This test exercises the real mounted path with external boundaries faked:

```text
POST /documents multipart
  -> queued status
  -> DocumentProcessor
  -> PDF words/boxes
  -> local grounding
  -> artifact persistence and verified source/artifact writes
  -> POST /chat graph turn
  -> uploaded-document page+bbox citation + VA/DoD guideline section citation
```

It uses the committed synthetic `lab-clean-glucose.pdf` and committed corpus vectors. Run it after adapting it to the final runtime facade. It has not been executed yet and may need formatting or small API corrections; do not weaken its assertions.

## Safe resume order

1. Confirm all worktrees still match this checkpoint:

   ```bash
   git -C /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-integration status --short --branch
   git -C /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-runtime-composition status --short --branch
   git -C /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-job-credentials status --short --branch
   git -C /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-runtime-route-e2e status --short --branch
   ```

2. Finish `feat/w2-job-credentials` first. Implement only the encrypted credential substrate, migration/config/dependency, and delegated refresh seam. Run focused tests, then the full suite. Commit once with W2-D1/D9/D10 and §3 traceability.

3. Cherry-pick/merge that credential commit into `feat/w2-runtime-composition`. Then implement:

   - Shared `PostgresDocumentRepository`, `PostgresIntentRepository`, and `PostgresArtifactStore`.
   - `AnthropicVlmExtractor`.
   - Per-session/per-job `OpenEMRLiveGateway`; never a global patient/token-bound gateway.
   - Source chain: live gateway -> source document backend -> source transport -> exactly-once writer -> document operations facade.
   - Artifact chain using the artifact category.
   - Encounter-pinned vital chain using `OpenEMRVitalBackend`.
   - Production encounter ownership check through delegated FHIR `Encounter` search, pinned to the session patient.
   - Dynamic pipeline facade that resolves the job credential and constructs gateway/transports per record/encounter.
   - A separately runnable worker factory/entrypoint with heartbeat and graceful stop/drain. Web only enqueues.
   - `AgentServices.complete_callback()` persistence of the encrypted job credential.
   - Hard `document_runtime` readiness: schema, credential vault/crypto, category/Binary attestation, and fresh worker heartbeat. Disabled W2 remains bootable.
   - app lifespan shutdown.

   Do not reopen already committed B2 internals unless a frozen test proves an integration break.

4. Finish and commit the route-level E2E test on `feat/w2-runtime-route-e2e`, then bring it into runtime composition or integration after the production interfaces exist.

5. Run the full suite and changed-file quality checks in runtime composition. Merge credential, runtime composition, and E2E commits into `feat/w2-integration`. Run the full suite again.

6. Build and inspect the final image before touching Railway:

   ```bash
   ROOT=/Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-w2-integration

   docker build --pull \
     -f "$ROOT/agent/Dockerfile" \
     -t clinical-copilot-agent:w2-integration \
     "$ROOT/agent"

   docker run --rm --entrypoint python \
     clinical-copilot-agent:w2-integration \
     -m corpus.check_index_manifest --corpus-dir /app/corpus

   docker run --rm --entrypoint sh \
     clinical-copilot-agent:w2-integration \
     -c 'tesseract --version | head -1 && python -c "import app, cryptography, pypdfium2, pytesseract, fastembed, rank_bm25"'
   ```

7. Fetch both remotes. If `origin/swarm/w2-wave0` has advanced, preserve it and integrate normally; never force. Merge `feat/w2-integration` into the clean `swarm/w2-wave0` worktree, run tests/image verification again, and only then deploy/push.

## Git/remotes at pause

```text
origin = git@github.com:worldofhacks/openemr-base-clean.git
gitlab = https://labs.gauntletai.com/alexander.miller/openemr-base-clean.git
```

Remote heads observed at checkpoint:

```text
origin/swarm/w2-wave0  f762225d3d0d5d1a41bf76c35e4bcbac7f63e0ed
gitlab/swarm/w2-wave0  aa3ea5bd33e69200bdecc297e0fd322e9e1f2da7  (ancestor of origin)
origin/feat/w2-retrieval-corpus      78bb40d6584060392a66439a9209ce9a37eb923d
gitlab/feat/w2-retrieval-corpus      78bb40d6584060392a66439a9209ce9a37eb923d
origin/feat/w2-b3-graph-skeleton     39ff2d3c5e3f7e0e8b636fab2585c8a4cf616cf3
gitlab/feat/w2-b3-graph-skeleton     39ff2d3c5e3f7e0e8b636fab2585c8a4cf616cf3
```

`feat/w2-integration` and the three paused feature branches have not been pushed. Final requested pushes are plain, non-force pushes of `swarm/w2-wave0` to both `origin` and `gitlab`, followed by SHA parity verification.

## Railway state and deployment gate

Project/environment/services:

```text
Project:        openemr / 1bddbc72-6307-4ec9-b6dd-8184310fbdcf
Environment:    production / 056473db-d0da-44ab-997b-d491f0e2720b
Agent service:  dc00b075-c031-42e4-97a6-4a08b26bc7d2
OpenEMR:        1021be9f-480a-41fb-b172-84b1da7a27b1
Agent URL:      https://agent-production-9f62.up.railway.app
OpenEMR URL:    https://openemr-production-cc95.up.railway.app
```

Live baseline at pause:

```text
GET /health -> 200 {"status":"alive"}
GET /ready  -> 200 ready (OpenEMR FHIR, Anthropic, session Postgres, Langfuse all green)
```

The live image predates the new W2 integration (its readiness payload does not yet include retrieval index or document runtime).

W2 deployment variables/attestations are currently absent:

```text
W2_DOCUMENT_RUNTIME_ENABLED
OPENEMR_REST_BASE_URL
SOURCE_DOCUMENT_CATEGORY_ID
ARTIFACT_DOCUMENT_CATEGORY_ID
OPENEMR_BINARY_READBACK_SAFE
DOCUMENT_CREDENTIAL_KEY (final name may follow implementation)
```

Do not set `W2_DOCUMENT_RUNTIME_ENABLED=true` until all of these external prerequisites are real:

- Replacement confidential SMART client registered and admin-enabled with the exact frozen 16-scope W1+W2 manifest and the deployed callback URI.
- New client proves a delegated authorization-code + PKCE + refresh flow and exact granted scopes. No password or client-credentials grant.
- Canonical `/AI-Source-Documents` and `/AI-Extractions` categories exist with recorded expected IDs and `patients|docs`-equivalent write ACLs.
- Administrator-recorded proof that deployed `system_error_logging` is known and not `DEBUG`.
- Encrypted credential key is generated into Railway secret storage, not source.
- Dedicated worker service/process is deployed from the same image with its service-specific start command, database variables, and heartbeat visible to web readiness.
- First-use embedding/reranker model download and Railway RSS/cold-start capacity are verified, or models are baked in.

Railway's current guidance for web + worker from one repository is to use separate services with per-service start commands rather than one shared `startCommand`: <https://docs.railway.com/guides/rails#using-the-same-railwayjson-for-multiple-services>. Build/start command behavior: <https://docs.railway.com/builds/build-and-start-commands>.

No Railway SSH key is currently registered. A read-only category/Binary-setting probe was attempted but not completed: SSH refused for lack of a key, and the public MySQL route was not reachable from the local shell. Do not treat those controls as attested.

Safe web deploy command after merge/image verification (run from `agent/` so the agent `railway.json` is used):

```bash
cd /Users/quietguy/Documents/Dev/Gauntlet/OpenEMR-Custom-Build/openemr-wt-swarm-w2-wave0/agent

railway up . \
  --project 1bddbc72-6307-4ec9-b6dd-8184310fbdcf \
  --environment production \
  --service dc00b075-c031-42e4-97a6-4a08b26bc7d2 \
  --detach --yes --json \
  --message "W2 integrated pipeline"
```

Then poll deployment status/logs and verify:

```bash
/usr/bin/curl --fail-with-body -sS https://agent-production-9f62.up.railway.app/health
/usr/bin/curl --fail-with-body -sS https://agent-production-9f62.up.railway.app/ready
```

Deploying the final image with W2 disabled is safe, but it does not satisfy the requested deployed upload-to-answer proof. If the replacement SMART client, categories, Binary guard, or worker service remains unavailable, report the precise external blocker and do not claim the full path is green.

## Final completion condition

Stop only after all of the following are true, or report the exact external blocker:

- Full local suite green.
- Route-level synthetic PDF upload -> extract -> ground -> write/re-read -> cite -> answer green.
- Final Docker image builds and validates corpus/native/runtime imports.
- `swarm/w2-wave0` contains all integration commits without altering the other agent's `.github` or adversarial cases.
- Railway web and dedicated worker are deployed.
- Deployed `/ready` is green across hard dependencies and reports worker/document runtime.
- A fresh deployed synthetic SMART session proves the full upload-to-answer path.
- `swarm/w2-wave0` is pushed without force to `origin` and `gitlab`, and both remote SHAs match.
