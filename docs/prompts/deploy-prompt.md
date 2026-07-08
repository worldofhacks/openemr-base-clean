# Claude Code Prompt — Stage 1 + 2: Local Run & Railway Deployment

Run from the root of the forked repo (openemr-base-clean). Prereqs: Docker Desktop running, Railway account created, GitHub fork connected to Railway (or `railway` CLI logged in).

---

```
You are working in my fork of Gauntlet-HQ/openemr-base-clean (OpenEMR EHR). This is
Stage 1 and Stage 2 of a graded one-week project: get OpenEMR running locally with
sample patient data, then deploy this fork publicly on Railway, and document both
processes to a submission-grade standard. Deployment method is Railway ONLY — do not
propose or configure a VPS, and do not run docker compose in production; Railway
services are the production topology. Local dev uses the repo's compose setup.

STRICT NON-GOALS (do not do these):
- No AI/agent code, no LLM integration, no new features — a security/architecture
  audit is a hard gate before any AI work and comes after this.
- No modifications to OpenEMR application code beyond what deployment strictly needs.
- No real patient data — demo/synthetic data only.
- Never commit secrets. .env.example only.

PHASE 0 — RECON (do this before acting):
1. Inspect the repo's deployment options: docker/development-easy/ (local dev),
   docker/production/, docker/dockerhub/, any Dockerfiles, and DOCKER_README.md,
   CONTRIBUTING.md (it documents a Synthea-based random-patient devtool).
2. Railway constraints to design around: it does NOT run compose files — each
   container is a separate service; it builds from a Dockerfile in the repo or runs
   a registry image; persistent state needs a Railway volume; it provides managed
   MySQL; it injects a public domain with TLS and routes to the port the container
   exposes.
3. Decide and write down: which image path deploys THIS FORK's code (not upstream
   OpenEMR) — likely building from the repo's production Dockerfile, or the official
   openemr image pinned to the same version if the fork is code-identical (verify
   before assuming). Record the decision and reasoning in DEPLOYMENT.md — I must be
   able to defend it in an interview.

PHASE 1 — LOCAL (Stage 1):
1. cd docker/development-easy && docker compose up -d --wait  (first build is slow;
   that's expected). App at http://localhost:8300, login admin/pass.
2. Load realistic sample patient data using the repo's sanctioned path (the Synthea
   devtool documented in CONTRIBUTING.md). Target ~20-30 patients so an outpatient
   schedule looks real. If the devtool is broken, document the failure and use the
   next-best documented OpenEMR sample-data path — tell me before spending more than
   30 minutes on alternatives.
3. Verify and record: login works; patients visible with encounters, meds, allergies,
   labs; enable the REST + FHIR APIs in Administration → Config → Connectors and
   confirm GET /apis/default/fhir/metadata returns the capability statement.
4. Document every command and click as you go — this becomes the README setup guide.

PHASE 2 — RAILWAY (Stage 2):
1. Create one Railway project (it will later also host an agent service and a
   Langfuse stack — name services accordingly: "openemr", "mysql").
2. Provision Railway managed MySQL. Wire OpenEMR to it via env vars (the OpenEMR
   image supports auto-configuration via MYSQL_HOST / MYSQL_USER / MYSQL_PASS /
   MYSQL_DATABASE and admin bootstrap vars — verify exact names against the image
   docs/entrypoint in the repo rather than guessing).
3. Attach a Railway volume for OpenEMR's persistent site state (sites/ directory) —
   verify the exact mount path from the image's docs/entrypoint.
4. Deploy the OpenEMR service from this fork (GitHub-connected build or image),
   confirm the public Railway domain serves the login page over HTTPS.
5. SECURITY BASELINE (do immediately after first boot, and document):
   - Rotate the default admin password (admin/pass is a known default).
   - Set the Site Address / base URL in Administration → Config → Connectors to the
     Railway HTTPS domain (required for OAuth/FHIR later).
   - Enable REST + FHIR APIs; re-verify /apis/default/fhir/metadata on the public URL.
   - Confirm no phpMyAdmin or debug service is publicly exposed.
6. Load the same sample data into the deployed instance. Prefer the most reproducible
   path (e.g., seed locally and import the dump into Railway MySQL, or re-run the
   devtool against the deployed instance) — document which you chose and why.
7. Timebox: if OpenEMR-on-Railway pathfinding exceeds ~3 hours of blockers, stop and
   report the specific blocker with what you tried — do not silently burn the day.

PHASE 3 — DOCUMENTATION (submission-grade, graded):
1. Write DEPLOYMENT.md at repo root:
   - Architecture of the deployment (services, volume, env vars table, ports, domain)
   - Exact reproducible steps for local setup AND Railway deployment
   - The image-path decision and reasoning from Phase 0
   - Every gotcha/failure hit and its fix (pathfinding notes are graded thinking)
   - Security baseline performed (credential rotation, site address, API enablement)
   - Sample-data loading procedure
   - Rollback: Railway one-click previous-deployment redeploy
2. Update README.md with a concise Setup section linking to DEPLOYMENT.md (the PRD
   requires the repo to include a setup guide and the deployed link).
3. Create .env.example with every env var name used (values blanked).
4. Commit in small, well-messaged commits as you go. Push to origin main.

SUCCESS CRITERIA (verify each, then print this checklist filled in):
[ ] Local: compose up → login works → ~20+ Synthea patients with clinical data
[ ] Local + deployed: FHIR metadata endpoint returns capability statement
[ ] Railway: public HTTPS URL serves OpenEMR; login works with ROTATED credentials
[ ] Railway: sample patients visible in the deployed instance
[ ] Default credentials no longer work on the deployed instance
[ ] DEPLOYMENT.md + README setup section + .env.example committed and pushed
[ ] The public URL is recorded at the top of DEPLOYMENT.md (needed in every submission)

Ask me before: paying for anything beyond Railway's base plan, deleting any data,
or force-pushing. Otherwise proceed autonomously and report at each phase boundary.
```
