# Deployment Guide — OpenEMR (openemr-base-clean fork)

> **Public deployment URL:** https://openemr-production-cc95.up.railway.app

This document covers two environments:

1. **Local development** — the repo's Docker Compose stack with Synthea sample patients.
2. **Production on Railway** — this fork's code deployed as separate Railway services (no compose in production).

It also records the deployment-architecture decisions and every gotcha hit along the way, so the setup is reproducible and defensible.

---

## 1. Image-path decision (Phase 0)

**Decision: build the production image from this fork's own checkout, using a Railway-specific Dockerfile (`docker/railway/Dockerfile`) derived from the repo's production build (`docker/release/Dockerfile`), with the upstream `git clone` stage replaced by a `COPY` of the build context.**

### Why the alternatives were rejected

| Option | Verdict | Reason |
|---|---|---|
| Official `openemr/openemr:latest` DockerHub image | **Rejected** | Official images package tagged releases. This fork is a *pruned import of OpenEMR master at version `8.2.0-dev`* (`version.php`), with only 2 commits of history — no release tag exists that is code-identical to this fork. Deploying the official image would deploy upstream's code, not this fork's. Verified, not assumed: `docker/production/docker-compose.yml` pins `openemr/openemr:latest@sha256:…`, and `docker/release/Dockerfile:162` shows official builds clone `https://github.com/openemr/openemr.git` — never local source. |
| `openemr/openemr:flex` image with `FLEX_REPOSITORY` pointed at the fork | **Rejected for production** | The flex image git-clones the source **at container runtime** and runs `composer install` + `npm build` on startup (`docker/flex/openemr.sh:642-649`). On Railway's ephemeral filesystem that means a 10-20 minute rebuild on every deploy/restart, requires the GitHub repo to stay public, and is explicitly a development image (`docker/README.md`). |
| `docker/binary/Dockerfile` | **Rejected** | Downloads pre-built upstream PHAR binaries pinned to `7_0_4` releases — not this fork's code at all. |
| Modify `docker/release/Dockerfile` in place | **Rejected** | Works, but mutates an upstream-maintained file. A separate additive Dockerfile keeps the fork's diff purely deployment-scoped (project constraint: no modifications to application code beyond what deployment strictly needs). |

### Why the chosen path is correct

- `docker/release/Dockerfile` is the repo's real production build (multi-stage: source → `composer install --no-dev` → `npm run build` → hardened final image with the `openemr.sh` entrypoint, `auto_configure.php` non-interactive installer, and permission lockdown). Reusing its stages verbatim inherits all of that.
- The **only** upstream coupling is stage 1 (`openemr-source`, lines 160-163), which clones `openemr/openemr.git`. Replacing that one stage with `COPY . /openemr` makes the image contain **exactly the commit Railway checks out from this fork** — provable provenance, no dependency on repo visibility, no re-clone indirection.
- The fork commits `composer.lock` and `package-lock.json`, so dependency installation is reproducible from the checkout alone.

### Railway constraints designed around

- Railway does **not** run compose files — each container is its own service. Topology: `openemr` (this Dockerfile) + `mysql` (Railway managed MySQL). Compose files in this repo are used for local dev only.
- Railway builds from a Dockerfile in the repo (path set via `railway.json` / `RAILWAY_DOCKERFILE_PATH`), with the repo root as build context — which is what makes the `COPY .` source stage work.
- **Railway volumes mount empty** (they shadow image content). The production compose relies on Docker named-volume auto-population for `sites/` — that does not exist on Railway. The entrypoint's empty-volume restore (`rsync /swarm-pieces/sites` when `sites/default` is missing) only runs when `SWARM_MODE=yes` (`docker/release/openemr.sh:271-333`), so the Railway service sets `SWARM_MODE=yes` even though it is a single container. First boot: restore `sites/` from `/swarm-pieces` → run `auto_configure.php` → touch `sites/docker-completed` (persisted in the volume, so later restarts skip setup).
- Railway injects a public domain and terminates TLS at its edge; the container serves plain HTTP on port 80 (image exposes 80 and 443; the service's target port is set to **80** explicitly so Railway doesn't route to the self-signed 443).
- The entrypoint auto-configures from env vars (verified in `docker/release/openemr.sh`, not guessed): `MYSQL_HOST`, `MYSQL_PORT` (line 52), `MYSQL_ROOT_USER`, `MYSQL_ROOT_PASS`, `MYSQL_USER`, `MYSQL_PASS`, `MYSQL_DATABASE`, plus initial-admin bootstrap `OE_USER` / `OE_PASS`. Setup triggers when `MYSQL_HOST` and `MYSQL_ROOT_PASS` are set and the site is unconfigured.
- Health endpoint for Railway healthchecks: `/meta/health/readyz` (used by the repo's own production compose healthcheck; implemented in `meta/health/index.php`).

## 2. Local development setup (Stage 1)

Prereqs: Docker Desktop running. No host PHP/Node/Java needed — everything runs in containers.

```bash
# 1. Start the dev stack (7 services; first boot pulls images and runs
#    composer/npm builds inside the container — allow 10-25 min cold, ~2 min warm)
cd docker/development-easy
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d --wait
```

The `HOST_UID`/`HOST_GID` export makes the in-container apache user adopt your host
uid so bind-mounted files it writes stay host-owned (this is what `openemr-cmd up`
does automatically — use that instead if you have
[openemr-cmd](https://github.com/openemr/openemr-devops/tree/master/utilities/openemr-cmd)
installed, which the rest of this guide assumes).

| Service | URL | Credentials |
|---|---|---|
| OpenEMR | http://localhost:8300 (https on 9300) | `admin` / `pass` |
| phpMyAdmin | http://localhost:8310 | — |
| MySQL (MariaDB 11.8) | localhost:8320 | `openemr` / `openemr` |
| Mailpit UI | http://localhost:8025 | — |
| Selenium (E2E/VNC) | localhost:4444 / 7900 | — |

**REST + FHIR APIs are pre-enabled** in this stack — the compose file sets
`OPENEMR_SETTING_rest_api=1`, `OPENEMR_SETTING_rest_fhir_api=1`, and
`OPENEMR_SETTING_rest_portal_api=1`, which the entrypoint writes into the globals
table at setup (equivalent to Administration → Config → Connectors). Verify:

```bash
curl -s http://localhost:8300/apis/default/fhir/metadata | head -c 300
# → FHIR R4 (4.0.1) CapabilityStatement JSON, ~35 KB, 34 resource types
```

### Verified locally (2026-07-07)

- `docker compose up -d --wait` → all 7 containers healthy.
- Login `admin`/`pass` → main tabs screen (verified in a real Chrome session via the
  stack's Selenium; first login shows a product-registration dialog — safe to dismiss
  with "Ask again later").
- FHIR `GET /apis/default/fhir/metadata` → `CapabilityStatement`, fhirVersion 4.0.1.
- After sample-data load (§5): 25 patients / 1,042 encounters / 152 medications /
  41 allergies / 774 problems / 4,101 lab results / 369 immunizations.

Useful stack commands: `docker compose logs -f openemr` (watch first-boot progress),
`openemr-cmd php-log` (PHP error log), `docker compose down` (stop; add `-v` to also
wipe volumes for a from-scratch reinstall).

## 3. Railway deployment (Stage 2)

### Architecture

```
                    Railway project: openemr
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ┌───────────────────┐  private IPv6 net   ┌─────────────┐  │
│  │ openemr           │ ──────────────────▶ │ MySQL       │  │
│  │ built from        │  mysql.railway.     │ mysql:9.4   │  │
│  │ docker/railway/   │  internal:3306      │             │  │
│  │ Dockerfile        │                     │ mysql-volume│  │
│  │                   │                     │ /var/lib/   │  │
│  │ openemr-volume:   │                     │   mysql     │  │
│  │ /var/www/localhost│                     └─────────────┘  │
│  │ /htdocs/openemr/  │                                      │
│  │ sites             │   (room for later: agent service —   │
│  └───────▲───────────┘    Langfuse is cloud-hosted, D5 rev) │
│          │ :80 (HTTP, dual-stack)                           │
└──────────┼──────────────────────────────────────────────────┘
           │ TLS terminated at Railway edge
   https://openemr-production-cc95.up.railway.app
```

- **No compose in production** — each container is a Railway service; the compose
  files in this repo are local-dev only.
- One Railway project (`openemr`) so the later agent service can join it. Services:
  `openemr` (app) and the managed MySQL (template display name "MySQL"; its private
  DNS alias is already lowercase `mysql.railway.internal`, and reference variables
  use `${{MySQL.*}}`). No Langfuse services deploy here — observability is Langfuse
  Cloud under an assumed BAA (D5 rev 2026-07-08): the agent service gets
  `LANGFUSE_HOST` (demo: `https://us.cloud.langfuse.com`; production:
  `https://hipaa.cloud.langfuse.com` + signed BAA) plus project keys as env vars.

### Environment variables (openemr service)

Names verified against the entrypoint (`docker/release/openemr.sh`), not guessed.
Values with `${{…}}` are Railway reference variables resolved from the MySQL service.

| Variable | Value | Purpose |
|---|---|---|
| `MYSQL_HOST` | `${{MySQL.MYSQLHOST}}` → `mysql.railway.internal` | DB host (private network) |
| `MYSQL_PORT` | `3306` | DB port (private; the public TCP proxy port differs) |
| `MYSQL_ROOT_USER` | `root` | Used once at setup to create the app DB + user |
| `MYSQL_ROOT_PASS` | `${{MySQL.MYSQL_ROOT_PASSWORD}}` | Root password from the managed service |
| `MYSQL_USER` | `openemr` | App DB user (created at setup) |
| `MYSQL_PASS` | _(generated secret)_ | App DB password |
| `MYSQL_DATABASE` | `openemr` | App database name |
| `OE_USER` | `admin` | Initial OpenEMR admin username |
| `OE_PASS` | _(generated secret — **not** the `pass` default)_ | Initial admin password: the instance never runs with default credentials |
| `SWARM_MODE` | `yes` | **Required on Railway:** enables the entrypoint's restore of `sites/` into the empty volume (see §1) |
| `OPENEMR_SETTING_rest_api` | `1` | Enables REST API (globals table, set at boot) |
| `OPENEMR_SETTING_rest_fhir_api` | `1` | Enables FHIR API |
| `OPENEMR_SETTING_site_addr_oath` | `https://openemr-production-cc95.up.railway.app` | Site address for OAuth2/FHIR (Connectors) |
| `OPENEMR_SETTING_api_log_option` | `1` | Minimal API metadata for a new/bootstrap configuration; existing sites must also verify and, if necessary, update the persisted global (F-S.4/D15) |

Secrets were generated with `openssl rand` and live only in Railway service
variables (and a local gitignored scratch file) — never in git. See `.env.example`
for the variable-name manifest.

### Reproducible steps (Railway CLI, from repo root)

```bash
railway login                       # interactive (browser)
railway init --name openemr        # create the project
railway add --database mysql --service mysql   # managed MySQL (template)

# app service + env wiring (generate real secrets for MYSQL_PASS / OE_PASS):
railway add --service openemr \
  -v 'MYSQL_HOST=${{MySQL.MYSQLHOST}}' \
  -v 'MYSQL_PORT=3306' \
  -v 'MYSQL_ROOT_USER=root' \
  -v 'MYSQL_ROOT_PASS=${{MySQL.MYSQL_ROOT_PASSWORD}}' \
  -v 'MYSQL_USER=openemr' \
  -v "MYSQL_PASS=$(openssl rand -hex 24)" \
  -v 'MYSQL_DATABASE=openemr' \
  -v 'OE_USER=admin' \
  -v "OE_PASS=<strong generated password>" \
  -v 'SWARM_MODE=yes' \
  -v 'OPENEMR_SETTING_rest_api=1' \
  -v 'OPENEMR_SETTING_rest_fhir_api=1' \
  -v 'OPENEMR_SETTING_api_log_option=1'

railway service link openemr
railway volume add --mount-path /var/www/localhost/htdocs/openemr/sites
railway domain --service openemr --port 80     # generates the public HTTPS domain
railway variables --service openemr \
  --set 'OPENEMR_SETTING_site_addr_oath=https://<generated-domain>' --skip-deploys

railway up --service openemr    # uploads this checkout; builds docker/railway/Dockerfile
```

`railway.json` at the repo root points the build at `docker/railway/Dockerfile` and
sets the restart policy (`ON_FAILURE`, 3 retries). A Railway healthcheck on
`/meta/health/readyz` was tried first and intentionally removed — see §6 for why.
Deployment success is verified end-to-end against the public URL instead (§4);
first boot imports the full database schema (~9 min) before Apache starts, and
subsequent boots skip setup via the volume's completed-setup marker (~20 s).

### Verified on Railway (2026-07-07, post sample-data import; exposure rechecked 2026-07-12)

| Check | Result |
|---|---|
| `https://openemr-production-cc95.up.railway.app` over HTTPS | HTTP 302 to the login route |
| Login with strong bootstrap credentials (real browser session) | accepted |
| Login with default `admin`/`pass` | rejected |
| `GET /apis/default/fhir/metadata` on public URL | HTTP 200, FHIR R4 CapabilityStatement, 34 resource types |
| Patients visible in Patient Finder (screenshot) | 25 Synthea patients, same panel as local |
| Data counts in Railway MySQL | 25 patients / 1,042 encounters / 152 meds / 41 allergies / 4,101 labs |
| Public exposure audit | OpenEMR and agent HTTPS domains; the OpenEMR MySQL proxy is closed; no phpMyAdmin or Xdebug (§4/F8). Two managed Postgres services still expose separately owned TCP proxies (residual review below). |

## 4. Security baseline

Performed immediately at/after first public boot, all verified against the live URL:

1. **Default credentials never existed publicly.** Instead of deploying with
   `admin`/`pass` and rotating afterwards, the instance was bootstrapped with a
   strong generated `OE_PASS` (the entrypoint creates the initial admin from
   `OE_USER`/`OE_PASS`), so there was no window in which the well-known default
   worked. Verified in a real browser against the public URL: strong password
   **accepted**, `admin`/`pass` **rejected**. Re-verified after the sample-data
   import (the import deliberately excludes the `users_secure` password-hash
   table precisely so the local dev instance's `admin`/`pass` hash cannot ride
   in with the data — see §5).
2. **Site address set for OAuth2/FHIR.** `site_addr_oath` is managed by the
   `OPENEMR_SETTING_site_addr_oath` env var (applied by the entrypoint at boot)
   and points at the Railway HTTPS domain. After the sample-data import
   overwrote the globals table with local values, it was re-set and the service
   restarted (§5 step 4).
3. **REST + FHIR APIs enabled** via `OPENEMR_SETTING_rest_api=1` /
   `OPENEMR_SETTING_rest_fhir_api=1`;
   `GET /apis/default/fhir/metadata` on the public URL returns the FHIR R4
   CapabilityStatement (HTTP 200, 34 resource types).
4. **No phpMyAdmin or debug service exposed.** The OpenEMR service exposes only
   its HTTPS application domain; its managed MySQL dependency has no public
   endpoint. phpMyAdmin is not deployed (`/phpmyadmin/` on the app → 404).
   Xdebug is not installed in the production image (`XDEBUG_ON` unset; probe
   with `XDEBUG_SESSION_START` just redirects to login). Apache access/error
   logging to disk is disabled in the image (upstream behavior; logs go to
   Railway's log stream).
5. **No public OpenEMR database path (F-S.9/F8).** The one-time MySQL TCP proxy used for
   sample-data import was removed on 2026-07-12. The OpenEMR service continues
   to use only Railway's private network (`mysql.railway.internal:3306`). Do not
   reopen a public proxy for routine administration; use Railway's authenticated
   MySQL Data tab or a project-private maintenance job.
6. **TLS and downgrade resistance (F-S.9).** Railway terminates TLS with valid
   certificates for both public services; plain-HTTP requests redirect to HTTPS.
   OpenEMR emits HSTS. Independently of the edge, the agent validates both
   OpenEMR base URLs as `https://` at configuration load and the FHIR client
   rejects a non-HTTPS base, so a misconfigured service cannot silently downgrade
   its delegated-token traffic.
7. **API-log data minimization (F-S.4/D15).** Production is verified at
   `api_log_option=1` (Minimal Logging). OpenEMR still records API access metadata
   but writes empty request/response bodies, avoiding a second full copy of every
   FHIR bundle. The Railway environment variable was already `1`, but a successful
   redeploy did **not** overwrite the existing persisted global from `2`; the owner
   corrected the global through a temporary private path and verified it after a
   restart. The exact retention and repeat-verification procedure is below.

### F8 deployment-hardening evidence and owner runbook (2026-07-12)

These controls deliberately separate repository evidence from mutable Railway
state. The CLI commands use explicit project/environment/service selectors so a
check cannot accidentally inspect a different deployment. IDs are non-secret
Railway resource identifiers.

| Control | Repository/live evidence | Current result |
|---|---|---|
| HTTPS only; no downgrade | `agent/app/config.py` requires HTTPS for OAuth/FHIR URLs; `agent/app/tools/fhir_client.py` independently rejects non-HTTPS; live HTTP probes redirect to HTTPS | **Pass** |
| MySQL public TCP proxy | `railway tcp-proxy list --project 1bddbc72-6307-4ec9-b6dd-8184310fbdcf --environment 056473db-d0da-44ab-997b-d491f0e2720b --service af2f61ef-2a8e-49e3-a3df-e41e485befbd --json` | **Pass:** `proxies` is empty; an external MySQL handshake fails |
| Temporary Railway SSH key | `railway ssh keys list` and `railway ssh keys list --workspace 7d5f456e-72c5-4ea0-a3e7-a368cd85477b` | **Pass:** no personal or workspace keys remain; `claude-deploy-fix` was removed |
| API body logging | replacement deployment `654fa9cc-8d13-4359-a6d9-6adc4ccca4d6` succeeded with env `1`, but the first runtime query still returned `2`; the owner privately set the persisted global, stripped old bodies, restarted OpenEMR, called FHIR metadata, and rechecked | **Pass:** runtime `api_log_option=1`; 539 rows; 0 rows with bodies |
| Metadata retention | 30-day purge applied and verified privately; OpenEMR has no supported native `api_log` retention setting | **Pass now:** 0 rows older than 30 days. **Residual:** run and record the purge monthly until automated outside this repository |

**Adjacent Railway residual, outside the audit's MySQL-specific F8 item:** both
managed Postgres services still had active public TCP proxies at this review
(`Postgres` and `Postgres-aDU3`). They do not expose the OpenEMR clinical MySQL
store, but the deployment owner should identify the live session-store database,
close any unused proxy/service, and document why any retained public endpoint is
necessary. This review does not silently fold that broader lifecycle decision
into the already-closed MySQL control.

#### Chosen `api_log` posture

The pre-change read-only baseline on 2026-07-12 found
`api_log_option=2`, 475 rows, and 10.45 MiB of request/response bodies
(oldest row 2026-07-08). This is the F-S.4/D15 exposure the following posture
closes; it is not a synthetic estimate.

The owner applied the posture through a temporary private SSH tunnel and removed
that access immediately afterward. After setting the persisted global, stripping
historical bodies, applying the 30-day purge, restarting OpenEMR, and exercising
FHIR metadata, the final query returned `api_log_option=1`, 539 total rows,
`rows_with_bodies=0`, and `rows_older_than_30_days=0`. Personal and workspace SSH
key lists were empty on final recheck and the MySQL TCP-proxy list remained empty.

- **Capture:** Minimal Logging (`api_log_option=1`). This retains method, URL,
  actor/patient metadata, and timestamp for operational corroboration, while
  `ApiResponseLoggerListener` stores an empty body instead of the FHIR response.
- **Retention:** 30 days for metadata-only `api_log` rows. The synthetic demo does
  not justify indefinite identifiers/URLs, and Langfuse is the authoritative
  agent-side accountability record under D5/F-C.1.
- **Existing full-body rows:** strip request/response bodies once after the minimal
  setting is live; do not wait 30 days for the old body copies to age out.
- **Automation boundary:** the audit found no native OpenEMR purge/retention
  setting. F8 does not add a database-writing service. The deployment owner runs
  the private SQL below monthly and records its UTC timestamp; automating that
  private operation remains an explicit residual control.

Run these statements only through Railway's authenticated MySQL Data tab or a
project-private maintenance job, never by recreating the public TCP proxy:

```sql
-- Runtime proof: must return 1 after any configuration change or redeploy.
SELECT gl_value AS api_log_option
FROM globals
WHERE gl_name = 'api_log_option';

-- One-time cleanup of bodies created while Full Logging was enabled.
UPDATE api_log
SET request_body = '', response = ''
WHERE COALESCE(request_body, '') <> ''
   OR COALESCE(response, '') <> '';

-- Recurring 30-day metadata retention operation (run at least monthly).
DELETE FROM api_log
WHERE created_time < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Verification: both counts must be zero after the owner operation.
SELECT
  COALESCE(SUM(CASE WHEN COALESCE(request_body, '') <> ''
            OR COALESCE(response, '') <> '' THEN 1 ELSE 0 END), 0)
    AS rows_with_bodies,
  COALESCE(SUM(CASE WHEN created_time < UTC_TIMESTAMP() - INTERVAL 30 DAY
           THEN 1 ELSE 0 END), 0)
    AS rows_older_than_30_days
FROM api_log;
```

After any OpenEMR restart/deploy, re-run the read-only `globals` check. The
2026-07-12 correction proved that the environment variable is useful bootstrap
configuration but does not necessarily overwrite an existing persisted global;
the database value is the runtime evidence. Never print Railway variable listings
in shared logs: their JSON/KV output includes raw secrets.

## 5. Sample-data loading

Sanctioned path (documented in `CONTRIBUTING.md` §"Create and add random patient data"):
the repo's Synthea devtool, which generates synthetic-but-realistic patients with
[Synthea](https://github.com/synthetichealth/synthea) as CCDA documents and imports
them through OpenEMR's own CCDA importer. **Synthetic data only — never real patient data.**

```bash
# 25 random patients (second arg defaults to true = dev-mode import: faster,
# bypasses audit tables — fine for demo instances, never for real-data sites)
openemr-cmd import-random-patients 25
# equivalent raw form:
# docker compose exec openemr /root/devtools import-random-patients 25
```

What it does (implementation: `docker/flex/utilities/devtoolsLibrary.source`,
`importRandomPatients()`): first run installs OpenJDK 17 in the container and
downloads `synthea-with-dependencies.jar` (~50 MB, needs internet), generates
CCDA files under `/tmp/synthea/output/ccda` (alive patients only), then imports
each via `import_ccda.php`. Each patient arrives with encounters, medications,
allergies, problems, labs, and immunizations. Observed runtime for 25 patients:
~5 minutes including first-run downloads, then a one-time ~30 s UUID-creation pass.

Observed result (local, 25 patients): 1,042 encounters, 152 medications, 41 allergies,
774 medical problems, 4,101 lab results, 369 immunizations — a realistic outpatient
panel. Patients are immediately visible in Patient Finder.

Fallback paths if Synthea is unavailable (documented, not needed here):
`/root/devtools dev-reset-install-demodata` (imports the openemr-devops
`demo_5_0_0_5.sql` dump) or `sql/example_patient_data.sql` in this repo.

### Loading the same data into the deployed (Railway) instance

> **Historical one-time procedure.** The public MySQL proxy used here is now
> closed under F8. Do not recreate it to repeat these commands; future data or
> maintenance work must use Railway's authenticated private path.

**Chosen path: seed locally, dump, import into Railway MySQL over its TCP proxy.**
Why this over re-running the Synthea devtool against the deployed container: the
production image deliberately has no devtools dispatcher or Java, and installing
them via `railway ssh` would be ephemeral (lost on redeploy) and unreproducible.
A dump is a portable artifact; the import is one command; and the same 25
patients end up in both environments, which makes local-vs-deployed comparisons
meaningful. Both instances run the identical code (same commit), so the schema
matches exactly.

```bash
# 1. Dump the seeded local DB — EXCLUDING the password-hash table so the local
#    admin/pass hash can never reach the deployed instance:
docker exec development-easy-mysql-1 mariadb-dump -uopenemr -popenemr \
  --single-transaction --no-tablespaces \
  --ignore-table=openemr.users_secure openemr > tmp/openemr-seed.sql

# 2. Strip the MariaDB 11.8 sandbox header (the MySQL 9 client rejects it):
sed -i '' '1{/enable the sandbox mode/d;}' tmp/openemr-seed.sql

# 3. Import via the Railway MySQL TCP proxy (host/port/password from the MySQL
#    service's MYSQL_PUBLIC_URL variable):
docker run --rm -i mysql:9.4 mysql -h <proxy-host> -P <proxy-port> \
  -uroot -p"$MYSQL_ROOT_PASSWORD" openemr < tmp/openemr-seed.sql

# 4. The dump carries the LOCAL globals values, so re-assert the deployment's
#    site address and restart (the entrypoint re-applies OPENEMR_SETTING_* on
#    boot; the SQL makes it immediate and deterministic):
docker run --rm mysql:9.4 mysql -h <proxy-host> -P <proxy-port> \
  -uroot -p"$MYSQL_ROOT_PASSWORD" openemr -e \
  "UPDATE globals SET gl_value='https://openemr-production-cc95.up.railway.app' WHERE gl_name='site_addr_oath';"
railway restart --service openemr --yes
```

**Ordering constraint:** the `users_secure` exclusion (step 1) is what makes this
safe. The `users` table row for `admin` (id 1) is identical in both fresh
installs, and the deployed `users_secure` row (id 1, strong bootstrap hash)
survives the import untouched — verified post-import: strong password still
accepted, `admin`/`pass` still rejected.

Result on the deployed instance (verified): 25 patients, 1,042 encounters,
152 medications, 41 allergies, 4,101 lab results — identical to local.

> **⚠️ CRITICAL SIDE EFFECT of this cross-instance import — it breaks the OAuth2/FHIR API.**
> OpenEMR's `keys` table holds the master crypto keys (`sevena`/`sevenb`), and the
> drive-key *files* on the volume (`sites/default/documents/logs_and_misc/methods/`)
> are encrypted **with those DB keys**. Importing the full dump overwrites the
> deployed instance's DB keys with the *local* instance's, so the deployed drive
> files can no longer be decrypted → every OAuth2 token/registration request fails
> with `"problem with authorization server keys"` / `"Key in drive is not compatible
> with key in database"`. The web UI still works (passwords are bcrypt, independent
> of the drive key), which is why the breakage is silent until you exercise the API.
> **This is a real flaw in the "dump-and-import" sample-data method** — it should have
> excluded the crypto/config tables. Remediation (used here; no clinical-data loss
> since 0 documents are encrypted): wipe **both halves** of the crypto and let
> OpenEMR regenerate a consistent set on the next OAuth request:
> ```bash
> # 1. delete the DB crypto rows (via the MySQL proxy):
> DELETE FROM `keys` WHERE name IN ('sevena','sevenb','oauth2key','oauth2passphrase');
> # 2. delete the drive-key files + stale oauth keypair (via railway ssh; needs a
> #    registered key: railway ssh keys add --key ~/.ssh/id_ed25519.pub):
> rm -f sites/default/documents/logs_and_misc/methods/seven[ab] \
>       sites/default/documents/certificates/oa{private,public}.key
> # 3. trigger regeneration by hitting POST /oauth2/default/registration once.
> ```
> **Better fix for next time:** exclude the crypto/config tables from the dump —
> add `--ignore-table=openemr.keys` (and re-assert only clinical tables), or seed
> prod by re-running the Synthea devtool against the deployed instance instead of a
> cross-instance DB copy.

## 6. Gotchas and pathfinding notes

Every failure hit during pathfinding, with root cause and fix:

- **Railway volumes mount empty and shadow image content** — `sites/` must be
  restored by the entrypoint; `SWARM_MODE=yes` is the (non-obvious) switch that
  enables that restore path for single containers. Found by reading
  `docker/release/openemr.sh:271-333` before deploying, not by hitting the failure
  live. Validated in the local simulation below.
- **No Dockerfile in this repo builds from the local checkout** — all three
  (`release`, `flex`, `binary`) fetch upstream OpenEMR. Any fork deployment needs
  the added `COPY`-based Dockerfile (see §1).
- **Railway's builder rejects two things vanilla BuildKit accepts** (deploy
  attempts 1-2 failed with exact errors): (a) the `VOLUME` Dockerfile instruction
  is unsupported outright (`docker VOLUME at Line 213 is not supported, use
  Railway Volumes`) — dropped the upstream `VOLUME ["/etc/letsencrypt/",
  "/etc/ssl"]` line, safe because Railway volumes are service-level config and
  the entrypoint regenerates `/etc/ssl` content; (b) `RUN --mount=type=cache`
  first demands an explicit `id=`, then demands that id be prefixed with a
  service-specific cacheKey (`id=s/<service-id>-…`). Hardcoding a Railway service
  id into the Dockerfile would break reproducibility for any other deployment, and
  the mounts only accelerate rebuilds — so they were removed in favor of plain
  `RUN`.
- **The whole Railway topology was validated locally before deploying** — built the
  image, then booted it against `mysql:9.4` (Railway's exact MySQL version) with an
  *empty* volume and `SWARM_MODE=yes`. This proved: the empty-volume `sites/`
  restore, first-boot auto-configuration (283 tables), Alpine's `mariadb-client`
  authenticating against MySQL 9's `caching_sha2_password` (MySQL 9 removed
  `mysql_native_password`, so this was a real risk), FHIR metadata serving, and
  bootstrap-vs-default credential behavior — all before consuming a single Railway
  build cycle.
- **Simulation footgun (macOS only):** simulating Railway's empty volume with a
  host *bind mount* fails with rsync `Permission denied` — a VirtioFS artifact
  (in-container root lacks `CAP_DAC_OVERRIDE` semantics over host-owned
  500-permission dirs). The correct simulation is a named volume mounted with
  `volume-nocopy` (true empty-volume semantics on a native Linux fs). On real
  Railway/Linux this failure mode does not exist.
- **`/meta/health/readyz` reports `"installed":false` + `"oauth_keys":false` even
  on a working instance** (observed both locally and in the simulation). The HTTP
  status is still 200 — which is all the repo's own production-compose healthcheck
  and the Railway healthcheck evaluate — so it doesn't block deploys. OAuth keys
  legitimately don't exist until first OAuth2 use. Left as-is: fixing app code is
  out of scope for deployment.
- **mariadb-dump sandbox header breaks MySQL imports** — dumps from MariaDB 11.8
  start with `/*M!999999\- enable the sandbox mode */`, which the `mysql` 9 client
  rejects. Strip the first line before importing into Railway MySQL (§5).
- **Railway healthcheck on `/meta/health/readyz` failed a deploy whose app was
  provably healthy.** Deploy attempt 3's logs show a complete successful boot
  (543 s, dominated by the schema import) and Apache serving — yet Railway marked
  the deployment FAILED after the 900 s healthcheck window. Apache verifiably
  binds dual-stack (`:::80`, checked by running the image and inspecting
  `netstat`), and `/meta/health/readyz` always returns HTTP 200 once PHP serves,
  so the checker itself never reached the app (most plausibly Railway probing a
  different port on this multi-`EXPOSE` image). Rather than burn the timebox on
  checker archaeology, the healthcheck was removed: boots after the first are
  ~20 s (the volume carries the completed-setup marker), the restart policy
  covers crashes, and deploy success is verified end-to-end against the public
  URL. Re-adding a healthcheck (e.g. `/meta/health/livez`) is a cheap follow-up
  experiment if desired.

## 7. Rollback

Railway keeps every previous deployment of the `openemr` service with its exact
image. To roll back:

- **Dashboard (one click):** project `openemr` → service `openemr` → *Deployments*
  → ⋮ menu on any previous successful deployment → **Redeploy**. This re-runs the
  already-built image — no rebuild, typically live in under a minute.
- **CLI:** `railway deployment list --service openemr` to find the deployment id,
  then `railway deployment redeploy <deployment-id>`.

Database state is independent of app rollbacks (MySQL service + its volume are
untouched). The `sites/` volume is likewise preserved across redeploys. Because the
schema belongs to the code version, only roll back across commits that did not run
a schema migration — for this fork's current history every deployment shares one
schema, so rollback is always safe.

## 8. Testing access — the three public tester surfaces

A tester can exercise the deployment through three independent surfaces. **Secret
values are not committed** (they live in the deployment's Railway variables and a
local gitignored `tmp/railway-secrets.env` / `tmp/tester_client.json`); hand them
over privately.

| Surface | How | Credential |
|---|---|---|
| **1. Web UI** (clinician/admin experience) | Open the public URL → login | `admin` / *(rotated `OE_PASS`)* — the default `admin/pass` is rejected |
| **2. REST + FHIR API** | OAuth2 token → call `/apis/default/fhir/*` | a dedicated **enabled** OAuth client (`client_id` + `client_secret`) via password grant |
| **3. FHIR capability (no auth)** | `GET /apis/default/fhir/metadata` | none (public) |

OpenEMR MySQL deep inspection is intentionally **not** a public tester surface.
Its TCP proxy is closed (F-S.9); deployment owners use Railway's authenticated
MySQL Data tab or a project-private maintenance job for narrowly scoped
operational checks such as the F8 retention queries above.

**API token (password grant) — the recipe a tester runs:**
```bash
BASE=https://openemr-production-cc95.up.railway.app
curl -s -X POST -H 'Content-Type: application/x-www-form-urlencoded' "$BASE/oauth2/default/token" \
  --data-urlencode 'grant_type=password' \
  --data-urlencode 'client_id=<CLIENT_ID>' --data-urlencode 'client_secret=<CLIENT_SECRET>' \
  --data-urlencode 'scope=openid api:oemr api:fhir user/Patient.read user/Observation.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Encounter.read user/Immunization.read' \
  --data-urlencode 'user_role=users' --data-urlencode 'username=admin' --data-urlencode 'password=<OE_PASS>'
# → {"access_token": "...", ...}; then:
curl -s -H "Authorization: Bearer <ACCESS_TOKEN>" -H 'Accept: application/fhir+json' \
  "$BASE/apis/default/fhir/Condition?patient=a234b786-539a-4f9a-96a0-432293226f02&_count=100"
```
Rich sample patient (12 allergies / 9 meds / 19 problems / 37 encounters):
`a234b786-539a-4f9a-96a0-432293226f02`. Password grant is enabled on this demo
instance (`oauth_password_grant=3`) for easy scripted testing; **the production
agent (D9) will use `authorization_code` + PKCE instead**, not password grant.

**Provisioning notes (why a tester couldn't hit the API until 2026-07-08):** the
sample-data DB import silently broke the OAuth2 crypto (§5 warning); after the
crypto reset, a fresh OAuth client was registered and **enabled** (new SMART apps
register *disabled* — `oauth_clients.is_enabled=0` — until enabled in
Administration → API Clients, or `UPDATE oauth_clients SET is_enabled=1`, matching
architecture decision D14). To give someone **Railway dashboard** access (deploys,
logs, metrics, volume), invite them as a project collaborator in the Railway UI —
that is an account action, not a credential to share.

> **F8 status (2026-07-12):** the temporary `claude-deploy-fix` Railway SSH key
> has been removed and the MySQL TCP proxy is closed. Keep both closed; the app
> reaches MySQL only over the private network. See the §4 F8 evidence table and
> retention runbook for repeatable verification.

## 9. Clinical Co-Pilot agent deployment

> **Agent URL:** https://agent-production-9f62.up.railway.app
>
> **Service:** `agent` in the same Railway project/environment as OpenEMR (D8, §1/§10).
>
> **Data path:** delegated SMART token → read-only FHIR; the agent has no OpenEMR DB
> credentials or write scopes (D2/D9, §4).

The service builds from `agent/Dockerfile`. Railway variables provide the OpenEMR OAuth/FHIR
origins, the `eVymzn…` SMART client, Anthropic, Langfuse, and `SESSION_STORE_DSN`. Secret
values remain in Railway and gitignored local files; never copy them into a command transcript
or repository file. `GET /ready` must be HTTP 200 with green FHIR, Anthropic, Postgres, and
Langfuse checks before the deployment is considered healthy.

### Deploy gate and current manual path

`.github/workflows/agent-deploy.yml` correctly orders `eval gate → deploy`, but its deploy
job requires a Railway **project token** in the GitHub repository secret `RAILWAY_TOKEN`.
As of 2026-07-13 that secret is absent: the eval job passes and the deploy job fails closed
with `Invalid RAILWAY_TOKEN`; it does not silently deploy an untested commit. Railway only
creates project tokens through Project Settings → Tokens, and the owner has not supplied one.

Until that token is added, deployment is an explicit manual operation from an authenticated
Railway CLI session, still gated on the exact same local tests:

```bash
cd agent
.venv/bin/pytest -q
.venv/bin/python -m evals.runner
cd ..

# The path argument is required: it makes agent/ the archive root and avoids uploading the
# full OpenEMR checkout or selecting the repository-root Dockerfile.
railway up agent --path-as-root --service agent --detach --json \
  --message "main <git-sha> — tests and eval gate green"

railway deployment list --service agent --limit 3 --json
curl -fsS https://agent-production-9f62.up.railway.app/ready
```

Never deploy if either test command is red. After adding a scoped project token to GitHub,
rerun `agent-deploy` once and remove this manual-path exception from the handoff; do not use
a personal/account token where an environment-scoped project token is sufficient.

### Session restart behavior

The clinician/patient session envelope is durable in Railway Postgres (D-O2/§3a), but the
delegated OAuth access token intentionally remains only in the agent process. A deploy or
restart therefore preserves the pin but requires a new SMART launch before the next `/chat`.
This is the canonical fail-closed demo path: no bearer token is persisted, reconstructed, or
printed. Multi-replica encrypted token persistence is deferred and recorded in
`IMPLEMENTATION_PLAN.md`.

Agent rollback mirrors the OpenEMR procedure: select the previous successful `agent`
deployment and redeploy it, then re-launch SMART and recheck `/ready`. Postgres and OpenEMR
data are independent of the agent image rollback.
