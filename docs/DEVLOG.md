# DEVLOG.md — Clinical Co-Pilot for OpenEMR

> Append-only chronological record of every decision, action, finding, and pivot. Newest at the bottom — reads top-to-bottom as time's arrow. Never rewrite past entries; a changed decision gets a new **pivot** entry pointing forward. Every entry is grounded to a commit, a decision (D#), a finding (F#), a research note (R#), or a dated artifact. Link, don't duplicate — the ADRs live in `docs/planning/DECISIONS.md`, findings in `AUDIT.md`, research in `docs/planning/RESEARCH.md`.
>
> Synthesized narrative: `docs/PROJECT_STORY.md`. Bootstrapped 2026-07-09 from git history + the planning/audit/deploy artifacts.

---

## [2026-06-27] Project genesis — fork of OpenEMR master · type: milestone
- What: Repo created as a pruned import of OpenEMR **master (v8.2.0-dev)** — the full application, git history stripped to one commit.
- Why: The Gauntlet AgentForge week-1 brief is to build an AI Clinical Co-Pilot *on top of* a real EHR fork; OpenEMR is the sanctioned base.
- Result: `ef3d490` (8,690 files). This is stock upstream code — every later audit finding is an *inherited* constraint, not a fork bug.
- Stage: Pre-work / base import.

---

## [2026-07-06] Architecture planning day (arch-draft) — persona, research, ADRs · type: milestone
- What: Ran the interview-gated architecture planning playbook: wrote `PRESEARCH.md`, `RESEARCH.md` (R1–R11), the ADR log `DECISIONS.md` (D1–D13), the `ARCHITECTURE_DRAFT.md`, and the defense script. No code.
- Why: The PRD's own rule — every capability must trace to a use case; the audit is a hard gate before any AI. Plan first, defensibly.
- Result: The planning artifact set (committed 2026-07-07 in `bf26da4`); decisions below are dated to this planning day.
- Stage: Planning (pre-MVP).

## [2026-07-06] D1 target user = PCP, 20-patient day · type: decision
- What: Locked the persona as a primary-care physician with a ~20-patient day and a 90-second between-rooms moment.
- Why: OpenEMR's core is ambulatory (no inpatient ADT/census); Synthea models primary-care encounters and exports FHIR R4; PCP is the PRD's own example. Narrow user → measurable latency/capability targets.
- Alternatives: ED resident (only scattered history events, no triage/acuity → would force fabricated data); hospitalist (fights the ambulatory platform).
- Result: `DECISIONS.md` D1; backed by R10. Invalidation clause: verify Synthea lab-trend depth in Stage 1.
- Stage: Planning.

## [2026-07-06] D2 placement = external SMART-on-FHIR sidecar · type: decision
- What: The agent is an external OAuth2/SMART client of OpenEMR, not code inside it; authorization is *inherited* from OpenEMR's OAuth2/SMART surface.
- Why: The only option where the trust-boundary answer is "enforced in OpenEMR's own certified authz layer, not a parallel one we built." Crisp boundary; agent death ≠ EHR death; free Python tooling; independent scaling. ONC §170.315(g)(10) makes SMART-on-FHIR the *federally mandated* integration pattern (R5).
- Alternatives: Embedded PHP module (fastest data path, but self-argue authz, EHR-wide blast radius, PHP/Laminas lock-in); hybrid (two boundaries to prove).
- Result: `DECISIONS.md` D2. Load-bearing — later confirmed by audit F-A.2, reworded by F-S.1 (see 2026-07-07).
- Stage: Planning.

## [2026-07-06] D3/D4/D6/D9/D10/D13 core stack decisions · type: decision
- What: D3 Python+FastAPI+Pydantic (R9); D4 Claude Sonnet 4.6 primary + Haiku utility behind one `llm.complete()` seam (R1); D6 direct Anthropic tool-use loop, no framework (R6); D9 FHIR-API-only reads with the delegated token (no SQL from the agent); D10 parallel fan-out of the 6 independent FHIR reads (`asyncio.gather`, per-call timeout + turn budget); D13 deterministic degradation — render the EvidencePacket via the templater if the LLM hard-fails.
- Why: Each traces to sourced research or a first principle (see the R# in DECISIONS.md). D9 protects D2's whole defense — direct SQL would bypass ACL + audit.
- Alternatives: TypeScript (D3); GPT-5.5 / open-source now (D4); LangGraph (D6); sequential reads (D10) — all rejected with reasons in the ADRs.
- Result: `DECISIONS.md` D3, D4, D6, D9, D10, D13.
- Stage: Planning.

## [2026-07-06] D7 verification layer = evidence-packet + structured-claims (v2) · type: decision
- What: The load-bearing trust mechanism: normalize every tool result into an **EvidencePacket** (stable IDs `ResourceType:id:hash8`), the model answers in **typed claims carrying evidence_ids**, a verifier does field-level match (**reject on contradiction, not absence**), and a **deterministic templater re-renders the physician's text from verified fields** so the model can't phrase past a check. v2 supersedes a v1 "citation-tags-in-prose" design.
- Why: Ungrounded medical LLMs hallucinate >60%, with 45%+ fabricated references (R7); claim-level verification cuts that 30–50% and doubles as the PRD schema-contract requirement.
- Alternatives: v1 citation-tags-in-prose (superseded — the LLM could phrase its way past verification).
- Result: `DECISIONS.md` D7. Made concrete by the audit on 2026-07-07 (D7-rev).
- Stage: Planning.

## [2026-07-06] PIVOT: D8 deployment = Railway, superseding a VPS plan · type: pivot
- What: Deploy everything as one Railway project (OpenEMR image + volume, managed MySQL, agent native build), replacing an earlier VPS + Compose + Caddy plan. Local dev stays Docker Compose.
- Why: In a one-week solo sprint, engineering hours are the scarcest resource — Railway zeroes out ops (TLS, domains, deploy pipeline, metrics, DB) and deploys on push. Auto-TLS matters doubly (SMART/OAuth needs HTTPS everywhere).
- Reverses: The VPS + Compose + Caddy plan (strongest raw-control/cost story, ~$13–25/mo fixed) — rejected because it spends the week on undifferentiated ops.
- Result: `DECISIONS.md` D8; backed by R11. Owned tradeoffs: no OpenEMR-on-Railway prior art, Railway's 2025–26 outage history, usage-based cost variance.
- Stage: Planning.

## [2026-07-06] PIVOT: D11 voice I/O cut from scope · type: pivot
- What: Considered STT+TTS, then cut it from week-1 scope.
- Why: Research R8 *falsified the premise* — browser `SpeechRecognition` is **not** on-device (Chrome→Google, Edge→Azure, Safari→Apple speech clouds), so the "no new PHI trust zone" argument collapsed. Doing voice defensibly (self-hosted Whisper + confirm-before-send UX) is a week of polish the core gates can't spare.
- Reverses: The initial intent to include voice; forced by R8.
- Result: `DECISIONS.md` D11. Revisit path (wk2–3) documented: client-side skin over `/chat`, self-hosted Whisper at the dedicated tier.
- Stage: Planning.

## [2026-07-06] D12 clinical safety posture — non-goals, hard-stops, refusal-as-feature · type: decision
- What: Read-only *by construction* (no diagnose/treat/prescribe/order/message/write/cross-patient/write-scopes); a deceased-indicator pre-flight hard-stop → deterministic refusal; canonical refusals (ambiguous data, wrong-patient, treatment-advice, expired session); session pinned to (clinician, patient) with a turn cap.
- Why: In a clinical setting a confident wrong answer is the trust-killing failure mode; a clear refusal is the defensible behavior. Blanket hard-stops beat per-case cleverness.
- Result: `DECISIONS.md` D12. Test-gap closed later (D12-rev, 2026-07-07).
- Stage: Planning.

---

## [2026-07-07] tdd-swarm skill added · type: action
- What: Added the tdd-swarm skill for AI-assisted development.
- Why: Tooling for the build phase.
- Result: `d092ff6`.
- Stage: MVP prep.

## [2026-07-07] Stage 1 — OpenEMR local + 25 Synthea patients · type: action
- What: Brought the dev-easy Docker stack up locally; loaded sample data with `openemr-cmd import-random-patients 25` (Synthea → CCDA → `import_ccda.php`).
- Why: Stage 1 needs ~20–30 realistic synthetic patients so an outpatient schedule looks real; Synthea is the repo's sanctioned path (CONTRIBUTING.md). Synthetic data only.
- Result: 25 patients / 1,042 encounters / 152 meds / 41 allergies / 774 problems / 4,101 labs / 369 immunizations. `DEPLOYMENT.md` §5.
- Stage: MVP / Stage 1.

## [2026-07-07] Image-path decision — build from fork source · type: decision
- What: Deploy Railway from the fork's own checkout via `docker/railway/Dockerfile` (derived from `docker/release/Dockerfile`), replacing the upstream `git clone` source stage with `COPY . /openemr`.
- Why: The fork is pruned OpenEMR master 8.2.0-dev with no matching release tag — no official image is code-identical. Official/flex/binary images would ship upstream's code, not the fork's.
- Alternatives: `openemr/openemr:latest` (ships tagged upstream); `:flex` (runtime clone + 10–20 min rebuild per deploy); `binary` (upstream PHAR pinned to 7_0_4); modify release/Dockerfile in place (mutates an upstream file). All rejected — see `DEPLOYMENT.md` §1.
- Result: `722a8ba` (docker/railway/Dockerfile + railway.json).
- Stage: Stage 2.

## [2026-07-07] FINDING: Railway builder rejects VOLUME + unqualified cache mounts (deploy attempts 1–2) · type: finding
- What: The fork-source Dockerfile failed twice on Railway-builder strictness: `VOLUME` is unsupported ("use Railway Volumes"), and `RUN --mount=type=cache` requires a service-specific cacheKey id.
- Why: Blocked the build; both are instructions vanilla BuildKit accepts.
- Result: Dropped the upstream `VOLUME` line; removed the cache mounts (hardcoding a service id would break reproducibility). `DEPLOYMENT.md` §6.
- Stage: Stage 2 (pathfinding).

## [2026-07-07] FINDING: Railway healthcheck false-failed a healthy app (attempt 3) · type: finding
- What: Attempt 3 built and booted cleanly (543 s, dominated by schema import; Apache verifiably binding `:::80`; `/meta/health/readyz` returns 200), yet Railway marked it FAILED after the 900 s healthcheck window — the checker never reached the app (likely probing the wrong port on this multi-`EXPOSE` image).
- Why: A broken healthcheck was gating an otherwise-successful deploy.
- Result: `DEPLOYMENT.md` §6.
- Stage: Stage 2 (pathfinding).

## [2026-07-07] PIVOT: remove the Railway healthcheck; verify against the public URL (attempt 4) · type: pivot
- What: Removed the `/meta/health/readyz` healthcheck; attempt 4 succeeded.
- Why: The healthcheck was false-failing a provably-healthy app. Post-first boots are ~20 s (volume carries the completed-setup marker); the `ON_FAILURE` restart policy covers crashes; deploy success is verified end-to-end against the public URL instead.
- Reverses: The initial choice to gate Railway deploys on `/meta/health/readyz`; forced by attempt 3's false FAIL.
- Result: `DEPLOYMENT.md` §3/§6. (Re-adding `/meta/health/livez` noted as a cheap follow-up.)
- Stage: Stage 2.

## [2026-07-07] MILESTONE: OpenEMR fork live on Railway over HTTPS · type: milestone
- What: Public deployment live at **https://openemr-production-cc95.up.railway.app** — login 200 over HTTPS, a generated `OE_PASS` accepted while `admin/pass` is rejected (no default-cred window), FHIR metadata 200 (34 resource types), no phpMyAdmin/Xdebug exposed. Topology: `openemr` (docker/railway/Dockerfile) + managed MySQL, `SWARM_MODE=yes` to restore `sites/` into the empty Railway volume, target port pinned to 80.
- Why: Stage 2 hard gate — this fork's code (not upstream) publicly deployed on Railway with a defensible security posture, no compose in production.
- Result: `745df75`; `DEPLOYMENT.md` §3–§4.
- Stage: Stage 2 (gate met).

## [2026-07-07] Seed prod: local DB dump → Railway MySQL (users_secure excluded) · type: action
- What: Dumped the seeded local DB (excluding `users_secure` so the local `admin/pass` hash can't ride in), stripped the MariaDB sandbox header, imported over the Railway MySQL TCP proxy, re-asserted `site_addr_oath`, restarted. Verified identical: 25 patients / 1,042 encounters / 4,101 labs.
- Why: Chosen over re-running Synthea on prod — the prod image has no devtools/Java; a dump is a portable, one-command, reproducible artifact putting the *same* patients in both environments.
- Result: `DEPLOYMENT.md` §5. **This import later proved to have a hidden side effect — see the OAuth crypto break below.**
- Stage: Stage 2.

## [2026-07-07] MILESTONE: Stage 3 AUDIT.md — read-only forensic audit (hard gate) · type: milestone
- What: A 5-section read-only audit (Security, Performance, Architecture, Data Quality, Compliance/HIPAA) of the fork **before any agent code**, run against the live local + deployed instances, opening with a ~500-word summary. Distinguishing method: every critical/high finding was independently re-checked by an **adversarial refuter**. Net verdict: the integration architecture (D2/D9) is sound; the real payload is enumerable FHIR data-field defects + an un-joinable audit trail.
- Why: The PRD's hard gate — establish the platform's ground-truth data/audit/authz behavior before designing the agent's verification (§5) and observability (§7) layers.
- Result: `c51d6db` (`AUDIT.md`).
- Stage: Stage 3 (gate met).

## [2026-07-07] FINDING F-D.1: immunization status inversion — the §5 justification · type: finding
- What: `FhirImmunizationService.php:100-105` compares `completion_status == "Completed"` (capital C), but the DB stores `completed` lowercase, so **all 67/67 completed vaccines render `status: not-done` + "patient objection"** (verified live).
- Why: A naïve agent would tell a physician the patient declined every vaccine. This is the concrete proof that the D7/§5 verification layer is load-bearing, not theater — the agent must never surface FHIR `status` verbatim.
- Result: `AUDIT.md` F-D.1; forces §5 rule 1.
- Stage: Stage 3.

## [2026-07-07] FINDING F-C.1 + F-C.2: api_log can't attribute the agent, and can't be joined · type: finding
- What: OpenEMR's `api_log` omits OAuth `client_id` and granted scopes (no column; write path never captures them) **and** has no correlation-id column or header-capture path; the accessors exist but feed only the error logger, and there's no join path to `api_token` (different identity spaces).
- Why: A §164.312(b) accountability gap. Forces the D10/§7 revision (no shared-id join) and elevates D5 (Langfuse becomes the system of record for client_id + scopes + correlation id).
- Result: `AUDIT.md` F-C.1, F-C.2. Drives the D10-rev and D5-rev1 below.
- Stage: Stage 3.

## [2026-07-07] FINDING F-S.4 / F-D.4 / F-A.2 / F-S.5 / F-P.1 · type: finding
- What: **F-S.4** — full PHI FHIR bodies stored plaintext at rest in `api_log` by default (`api_log_option=2`); a second in-boundary PHI store. **F-D.4** — AllergyIntolerance `criticality` is null across the whole dataset (label-vs-numeric key bug); `type` never set. **F-A.2** — D2 CONFIRMED: a real SMART/OAuth2 EHR-launch surface with S256-*enforced* PKCE (but it's certification-*capable* stock upstream, not fork-certified). **F-S.5** — `authorization_code` attributes to the clinician; `client_credentials` collapses to the synthetic `oe-system` user. **F-P.1** — `BaseService` runs uncached schema introspection per construction (~26 metadata round-trips per `GET /Patient`); ~0.39 s live per-read floor.
- Why: F-S.4→D15; F-D.4→§5 rule 2; F-A.2 confirms D2/D9; F-S.5 confirms D9 (never client_credentials); F-P.1 sets the §9 latency floor.
- Result: `AUDIT.md`.
- Stage: Stage 3.

## [2026-07-07] PIVOT (in-audit): adversarial refuter overturned 3 first-pass verdicts · type: pivot
- What: The refutation pass corrected the audit itself: **F-S.1** — the ACL "smoking gun" was **factually wrong** (`git grep addAclRestrictions` returns six registered calls; user-scoped reads *do* run `aclCheckCore`); downgraded high→low, and D2's wording refined to "scopes + single-patient compartment binding." **F-C.4** — refuted: the LLM-PHI-egress "breach" described *unbuilt* design as shipped (no agent code exists; `GET /chat`→404); downgraded to a forward-looking note. **F-D.6/F-D.2** — the "active-only hides 16/19 conditions" claim was false (all 19 return on a default read; the filter is simply broken); downgraded to demo-data/interop nits.
- Why: The gate exists to catch exactly these — a false authz claim, a category error (unbuilt-as-shipped), demo-data noise mistaken for defects.
- Reverses: The first-pass high ratings of F-S.1, F-C.4, F-D.6, F-D.2.
- Result: `AUDIT.md` (post-verification severities). The corrections are *why* the audit is trustworthy.
- Stage: Stage 3.

## [2026-07-07] Apply audit decision revisions — D2/D7/D10/D12 + D14/D15 · type: decision
- What: Folded the audit back into the ADRs: **D2-rev** (scopes+compartment wording, certification-capable, checkUserHasAccessToPatient stub → D12 pin is the real guarantee); **D7-rev** (six concrete verifier rules — F-D.1 status, F-D.4 criticality, F-D.5 NKDA phrasing, F-D.6 consume-all-conditions, F-D.2 dose); **D12-rev** (require synthetic deceased + no-allergy eval fixtures — the demo data can't exercise those safety paths, F-S.7/F-D.5); **D14** (user-scoped OAuth apps register DISABLED — runbook must enable, F-S.6); **D15** (`api_log` is a second PHI store — set retention, F-S.4/F-C.3).
- Why: Keep the ADRs honest and the build spec accurate — the audit's findings must move work onto the §5/§7 layers, not sit in a report.
- Result: `de4e5bc`.
- Stage: Stage 3→4 bridge.

## [2026-07-07] PIVOT: D10 — withdraw the api_log shared-id join claim · type: pivot
- What: Withdrew D10's prior claim that agent traces could be *hard-joined* against OpenEMR's `api_log` via a shared correlation id. Restated: **Langfuse (D5) is the authoritative agent-side trace**; api_log correlation is best-effort/fuzzy on `(user_id, patient_id, request_url, utc_timestamp)` and weak (same delegated `user_id` every call). Agent still sends `X-Copilot-Request-Id` (forward-compatible). Also re-tagged the 28 s p50 latency figure as **R12** — an unverified planning assumption to be measured at Early.
- Why: Audit F-C.1/F-C.2/F-A.5/F-P.6 proved the join point doesn't exist and D9's read-only rule forbids adding a column.
- Reverses: D10's original "full trace reconstructable via a cross-system join into api_log."
- Result: `DECISIONS.md` D10 revision; `de4e5bc`.
- Stage: Stage 3→4.

## [2026-07-07] D5-rev1: Langfuse elevated to a HIPAA accountability control · type: decision
- What: Langfuse is no longer *merely* observability — it becomes the §164.312(b) system of record for `{client_id, exercised_scopes, correlation_id, user, patient, request_url, utc_timestamp}` per FHIR call.
- Why: Because api_log omits client_id + scopes (F-C.1), the agent's trace is the *only* complete record of which app under which grant touched PHI.
- Result: `DECISIONS.md` D5 revision 2026-07-07. (The *hosting* still says self-hosted here — that flips on 2026-07-08.)
- Stage: Stage 3→4.

## [2026-07-07] MILESTONE: USERS.md (Stage 4 gate) · type: milestone
- What: The one narrow user (PCP) with the evidence, the launch-to-brief moment, four use cases (UC1 pre-visit brief, UC2 what-changed, UC3 cited Q&A, UC4 attention flags) each with a "why a conversational agent" trace, non-goals, and a traceability table.
- Why: PRD Stage 4 hard gate and the source of truth ARCHITECTURE.md traces to.
- Result: `7f4aaeb` (`USERS.md`).
- Stage: Stage 4 (gate met).

## [2026-07-07] MILESTONE: ARCHITECTURE.md finalized via arch-finalize gap audit (Stage 5 gate) · type: milestone
- What: A cold-eyes gap audit across 12 dimensions (`docs/planning/gap-audit.md`, zero blank coverage cells); 4 critical + 12 important findings all resolved *without* a fork-in-the-road user decision (they completed, not altered, locked decisions). The binding `ARCHITECTURE.md` opens with a 524-word summary, is §-anchored, cites D#/R#/F#/UC# inline, folds in the audit's confirmations (D2/D9) *and* challenges (revised D10/§7); F-D.1 is the concrete §5 justification. Added lifecycle/retention, tool contracts, source-of-truth ledger, expanded failure modes, alert runbooks, eval fixtures, submission checklist. Added **R12** to formally re-tag the latency anchor.
- Why: PRD Stage 5 hard gate — the binding contract the build implements against.
- Result: `33cc5bb` (`ARCHITECTURE.md` + 9 planning files).
- Stage: Stage 5 (gate met).

## [2026-07-07] FINDING + FIX: cross-instance DB import silently broke the OAuth2/FHIR API · type: finding
- What: The 2026-07-07 dump-and-import (seeding prod) overwrote prod's `keys`-table master crypto (`sevena`/`sevenb`) with the *local* instance's, while prod's drive-key files on the volume were encrypted with prod's *original* keys — so they couldn't be decrypted. Every OAuth2 token/registration 500'd ("Key in drive is not compatible with key in database"). **The web UI kept working** (bcrypt passwords are independent), hiding a production-down API behind a working login. Fix: wipe *both* halves (DB crypto rows + drive-key files via `railway ssh`) so OpenEMR regenerates a consistent set (0 encrypted docs → no data loss); then register + enable a fresh OAuth client (D14). Documented tester access (§8).
- Why: A tester (and later the E2 SMART client) couldn't hit the live REST/FHIR API; the failure was invisible from the web UI. Real flaw in the dump-and-import method — it should exclude the crypto/config tables.
- Result: `06fee47`; `DEPLOYMENT.md` §5 (CRITICAL callout) + §8. Live fix + fuller docs continued into 2026-07-08.
- Stage: Stage 2/3 (deploy hardening).

## [2026-07-07] Defense docs + diagrams committed · type: action
- What: Added `docs/defense/` (DEFENSE.md, script, diagram prompt), `docs/diagrams/` (4 excalidraw), and the operational prompts + skills.
- Why: Architecture-defense material and the reproducible staged-prompt lifecycle.
- Result: `bf26da4`.
- Stage: Stage 5.

---

## [2026-07-08] MILESTONE: IMPLEMENTATION_PLAN.md (spec → build plan) · type: milestone
- What: Decomposed ARCHITECTURE.md into 31 §-anchored tasks phased against real deadlines — EARLY (Thu) = a live, verified, observable agent doing UC1 end-to-end + eval framework + demo; FINAL (Sun) = full verification/dashboard/alerts/load/cost + UC2–UC4 + deploy hardening. Each task carries Files/Anchors/Accept (incl. edge+error)/Test; observability + trust-boundary work ordered before features. Coverage table zero blanks; dated Cut/deferred + Needs-architecture sections.
- Why: The PRD needs a defensible build plan; ordering encodes the hard dependencies (E1→E9).
- Result: `04dd59a` (`IMPLEMENTATION_PLAN.md`).
- Stage: Bridge to build.

## [2026-07-08] MILESTONE: E1 agent build — skeleton + observability scaffold (test-first) · type: milestone
- What: Built E1 with observability **first** (§7). E1.1: FastAPI skeleton + typed **fail-fast** config (missing env fails at boot, not request time; https-downgrade rejected, F-S.9; secrets as SecretStr). E1.2: `/health` liveness + a **real** `/ready` (hard deps OpenEMR FHIR metadata / Anthropic / session store → 503 with per-dependency body; soft dep Langfuse → 200 `degraded`; no unconditional 200). E1.3: JSON logging + correlation-ID middleware (honors inbound `X-Copilot-Request-Id` or mints one; propagated to logs + outbound header; no PHI in the message). 19 tests green; verified against the LIVE OpenEMR (real `/ready` returned 503 with genuine per-probe results).
- Why: E1 is first on the Early critical path because §7 requires the scaffold before features; a real `/ready` is a graded deliverable.
- Result: `447bb19` (E1.1), `e2e04e2` (E1.2), `4c6f846` (E1.3). Python 3.12 venv (host 3.14 was broken).
- Stage: EARLY build (E1 done).

## [2026-07-08] build/tasks-gen prompts + planning skills committed · type: action
- What: Committed the build/tasks-gen operational prompts and the arch/tasks/bug/eval planning skills.
- Why: Complete the reproducible staged-prompt + skill toolchain in-repo.
- Result: `076426d`.
- Stage: EARLY.

## [2026-07-08] PIVOT: D5 — Langfuse Cloud under an assumed BAA supersedes self-hosted · type: pivot
- What: Flipped observability hosting from **self-hosted Langfuse** to **Langfuse Cloud** under an assumed BAA (HIPAA data region `hipaa.cloud.langfuse.com`, AWS us-west-2, Pro+, signed BAA before PHI). Cut the Railway Langfuse service group (web/worker + Postgres + ClickHouse + Redis), the ClickHouse memory-cost risk (D8-update), and the self-host-alerting tension. Langfuse moves out of trust Zone B; PHI egress becomes **two BAA-covered points** (LLM + Langfuse Cloud). The elevated §164.312(b) accountability role (D5-rev1) is unchanged.
- Why: The original self-hosted premise — "self-hosting is the only way to avoid an un-BAA'd third party" — no longer holds: Langfuse Cloud now offers a BAA + dedicated HIPAA region (verified 2026-07-08, langfuse.com/security/hipaa; R2 addendum). Under the PRD's assumed-BAA rule the observability vendor sits in the *same* posture as the LLM provider (D4), so self-host ops cost buys nothing.
- Reverses: The original 2026-07-06 D5 decision to self-host for PHI-in-boundary; forced by verification that a Langfuse Cloud BAA + HIPAA region exists.
- Alternatives: Keep self-hosting (rejected — buys nothing once a BAA exists; retained only as the documented MIT cloud→self-host exit if vendor terms change). Still rejected: LangSmith, Braintrust.
- Result: `DECISIONS.md` D5 revision 2026-07-08 + D8 update + R2 addendum + ARCHITECTURE.md §1/§4/§6a. Uncommitted working-tree changes at bootstrap time (ripples through ARCHITECTURE/AUDIT/defense/diagrams).
- Stage: EARLY (planning revision).

---

## [2026-07-09] Submission remote wired to Gauntlet labs GitLab · type: action
- What: Pointed the `gitlab` remote at the Gauntlet labs GitLab (`labs.gauntletai.com/alexander.miller/openemr-base-clean`). SSH auth failed (port 22 refused — Elestio host SSH + fail2ban; git-SSH on a non-standard port); switched to **HTTPS + PAT**, which pushed and auto-created the project.
- Why: The graded submission repo (O3). SSH was a dead end; HTTPS over the working 443 sidestepped it.
- Result: local/github/gitlab all in sync; token stripped from `.git/config` after push.
- Stage: Submission plumbing.

## [2026-07-09] DEVLOG + PROJECT_STORY bootstrapped · type: milestone
- What: Reconstructed this DEVLOG from the full git history + DECISIONS/AUDIT/RESEARCH + deploy/prompt docs, and synthesized `docs/PROJECT_STORY.md`.
- Why: A grounded, sequential record to study and defend the process in interviews.
- Result: `docs/DEVLOG.md`, `docs/PROJECT_STORY.md`.
- Stage: EARLY (documentation).

## [2026-07-09] Build resume — Phase 0 green baseline + plan reconcile · type: milestone
- What: Resumed the Early build. Installed the agent package (`pip install -e ".[dev]"`, Python 3.12) and ran the suite — **19 passed, 0 failed**. Confirmed the app boots (uvicorn) with `/health`→200 and a real `/ready`→503 against the LIVE OpenEMR (openemr_fhir probe HTTP 200; anthropic/session correctly down). Ticked E1.1/E1.2/E1.3 in `IMPLEMENTATION_PLAN.md` — the plan now truthfully reflects state.
- Why: "Building properly" means a clean green baseline before stacking features; the plan must be a truthful state tracker before continuing E2→E9.
- Result: green baseline confirmed; true next-unbuilt task = **E2.1** (authorization_code + PKCE client). No code changed (E1 already committed at `447bb19`/`e2e04e2`/`4c6f846`).
- Stage: EARLY (E1 done, resuming at E2).

## [2026-07-09] E2 — SMART/OAuth client + session pin (trust boundary before features) · type: milestone
- What: **E2.1** — `app/auth/smart_client.py`: authorization_code + PKCE(S256) SMART client (SMART-conformant authorize URL with `aud`=FHIR base + EHR-launch scope; auth-code token exchange; `TokenResponse` with SecretStr + launch/patient binding). Guardrails encoded + tested: never `client_credentials` (F-S.5), never `APICSRFTOKEN` (F-S.3); disabled client → explicit `CoPilotNotEnabledError` (§6/D14). **Proved live** (Selenium-in-harness-only, opt-in `RUN_LIVE=1`): full flow against the deployed OpenEMR → token → **real FHIR data** (Patient bundle total=3, Condition 200). **E2.2** — `app/session/store.py` + `migrations/001_sessions.sql`: session pinned to (clinician, patient); cross-patient request refused (`CrossPatientError` — the real enforcer since OpenEMR's check is a stub, F-S.2); lifetime = MIN(token exp, idle, turn cap); store-down → fail-closed (`SessionStoreUnavailable`, §6).
- Why: §4 says trust-boundary + auth land before features. The pin is the true clinician↔patient guarantee (F-S.2). Proving real FHIR before "E2 done" de-risks the whole D9 data path.
- Result: registered + enabled an auth-code client on prod (D14). Suite **34 passed, 1 skipped** (live opt-in). Commits: E2.1, E2.2. Next unbuilt = **E3.1** (freeze Pydantic tool contracts).
- Stage: EARLY (E2 done).

## [2026-07-09] E3 — scope fix, tool contracts, six read tools + fan-out · type: milestone
- What: **Scope gate (F-C.5)** — encoded a minimum-necessary scope policy (`app/auth/scopes.py`): openid + the six `user/*.read` scopes; **DROPPED `offline_access`** (no refresh token — session is bounded MIN(token/idle/turn-cap) + re-launch on expiry; documented as a D9 addendum, §6 refresh-branch flagged for arch-finalize). Re-consented LIVE — all six scopes come back granted; a runtime guard fails at token exchange if any is missing. **E3.1** — froze the Pydantic tool contracts (`app/tools/contracts.py`): tri-state `ToolResult` (ok/no_records/failed; FAILED must name what's missing) + six evidence-record shapes carrying the fields §5 rules touch. **E3.2** — six read tools + `run_previsit_fanout` (`app/tools/{fhir_client,fhir_tools}.py`): concurrent `asyncio.gather`-style fan-out (D10, wall-clock ≈ slowest), per-call timeout + total turn budget, explicit `category=laboratory` (F-P.2), https-pin (F-S.9), correlation header (D10-rev); partial-failure → FAILED naming what's missing (§6/F3); allergy tri-state (empty = NO_RECORDS, never NKDA). Fixed a mapper bug on real data — MedicationRequest `dosageInstruction: [[]]` (F-D.2) — with a regression test.
- Why: §4 puts trust-boundary/contract work before features; the scope gap would have 401'd meds/labs/encounter at runtime. Contracts are the source of truth before tools.
- Result: **proved live** — the six-tool fan-out against the deployed OpenEMR returns real data for ALL six (patient 1, conditions 19 incl. inactive, meds 18, labs 11, encounters 37, allergies 12); zero 401s. Suite **62 passed, 1 skipped**. Next unbuilt = **E4** (EvidencePacket builder).
- Stage: EARLY (E3 done).

## [2026-07-09] E4 — EvidencePacket builder (stable, unique evidence IDs) · type: milestone
- What: `app/evidence/packet.py` — normalizes the six tools' `ToolResult`s into typed `EvidenceRecord`s (the only thing the LLM + E6 verifier see, §5/§6a), each with a §5a `ResourceType:id:hash8` evidence id. **Null/empty FHIR id → deterministic synthetic id** (hash of type+date+display+patient, so citations survive the audit's null-id records); **within-request uniqueness guaranteed** (duplicate/collision ids disambiguated with `#n`) since the verifier resolves every claim against these ids. Packet carries `notices`: tool_failed (missing data — named), no_records (allergy → "confirm with patient", never NKDA), and trimmed (large-chart cap → what was dropped, §6/F-P.3). Made the FHIR mappers tolerate a null id (`res.get("id") or ""`) so the pipeline never crashes on the audit's case.
- Why: evidence-id stability + uniqueness are load-bearing for E6 — an ambiguous/absent id breaks a citation. The audit flagged that Med/Condition/Allergy records can lack ids.
- Result: **proved live** — packet built from the real fan-out = **98 evidence records, all ids unique, 0 notices**; plus an end-to-end null-id test (missing FHIR id → synthetic id, no crash). Suite **73 passed, 1 skipped**. Next unbuilt = **E5** (orchestrator: direct Anthropic tool-use loop).
- Stage: EARLY (E4 done).

## [2026-07-09] E5 — orchestrator: direct Anthropic tool-use loop, prompt cache, D13 fallback · type: milestone
- What: **E5.1** — `app/llm/provider.py` (thin `llm.complete()` seam, D4 — the only module that imports the Anthropic SDK; normalizes responses to SDK-agnostic block/usage types; wraps any SDK failure into a single `LLMUnavailable`), `app/llm/cost.py` (day-bucketed USD cost cap — first real LLM spend; cache reads billed at ~0.1×, R1), `app/orchestrator/loop.py` (manual tool-use loop, D6 — call → dispatch tool_use to bound FHIR tools → feed results back → repeat to end_turn). **Prompt cache proven, not assumed (R1):** the prompt is a frozen system block (cache breakpoint → cross-request) + a **byte-stable patient-evidence prefix** (cache breakpoint → cross-turn) + the volatile question (no breakpoint); the evidence prefix is delimited DATA, not instructions (§4 injection containment). Model = `claude-sonnet-4-6` per **D4** (the claude-api skill defaults to Opus; D4 is the project owner's explicit cost/quality choice — swap = config). **E5.2** — D13: LLM hard-fail (retries exhausted/timeout) **or** cost-cap trip **or** tool-loop non-convergence → render the EvidencePacket via the deterministic templater (`app/verify/templater.py`: grouped, values+dates, state-aware, "generated WITHOUT LLM assistance" banner; empty allergy → "confirm with patient" not NKDA F-D.5, missing dose → "confirm before dosing" F-D.2, criticality never surfaced as risk F-D.4). The physician always gets something grounded — never a raw error (§6).
- Refinement (LLM error handling, classified for E7 alerting): the provider seam now maps SDK failures by HTTP status into a taxonomy instead of one blanket `LLMUnavailable` — `LLMUnavailable` (TRANSIENT: 429/5xx/timeout/connection, after the SDK's own backoff → graceful D13 degradation), `LLMClientError` (PERSISTENT 4xx: 400/401/403/422 — a bug/misconfig that recurs, carries the HTTP status), and `LLMRequestTooLarge` (413). The orchestrator flags each fallback with a machine-readable `fallback_kind` (`transient` | `client_error` | `request_too_large` | `cost_cap` | `no_convergence`) so E7 can alert on a persistent defect distinctly from normal degradation. **413 routes to the trim policy, not a blanket fallback:** on a 413 the loop shrinks the evidence packet down a trim schedule (60→25→10 records/type via new `trim_packet`, each drop named in a notice — never silent) and retries; only if even the smallest packet is too large does it fall back, flagged `request_too_large`.
- Why: test discipline was the point — the three load-bearing behaviors (cache wiring, tool-loop control, deterministic fallback) are proven by **mocked-LLM** unit tests that assert request structure and loop behavior, never model output quality (that's deferred to E8 evals; asserting a mock's own text is the anti-pattern the user called out). One real call is left as an opt-in live smoke. Treating a persistent client error as graceful degradation would hide a bug behind a grounded-looking answer — hence the distinct `fallback_kind`.
- Result: **suite 119 passed / 3 live deselected** — cache breakpoints present + prefix byte-identical across turns; tool dispatched then end_turn; D13 fires (flagged) on transient/client-error/cost-cap/iteration-cap; error classification by status (429/500/529→transient, 400/401/403/422→client_error, 413→too-large); 413 trim-retry succeeds and, when exhausted, falls back flagged too-large. **Live smoke PROVEN** (key supplied in `agent/.env`, sourced into env; `RUN_LIVE=1`): one real Sonnet call ran the loop end-to-end and invoked the `get_conditions` tool (`source=llm, iterations=2, tool_calls=['get_conditions']`); and a repeat call on the identical stable prefix earned a full **cache read of 16,483 tokens** (r1 write=16483/read=0 → r2 write=0/read=16483) — R1 prompt caching confirmed by trace, not assumed. Added `anthropic` dep; `.env.example` scaffolded (secrets stay in gitignored `.env`, sourced at runtime — config stays `env_file=None` for test isolation); `LLM_MODEL`/`DAILY_COST_CAP_USD` settings. Next unbuilt = **E6** (verification v1 — the §5 trust layer; extends the same templater with the verified-claims path).
- Stage: EARLY (E5 done).

## [2026-07-09] E6 — §5 verification layer, built via TDD-swarm (separation of powers) · type: milestone
- What: The load-bearing trust layer (§5, D7 v2), built on branch `swarm/e6-verification` with **real separation of powers** (owner-requested): a **Test Agent** wrote and FROZE the deterministic verifier tests (`agent/tests/test_{claims,verifier,templater_verified}.py`, 47 tests) before any implementation existed; an independent **test-design review** approved the freeze; a separate **Implementation Agent — forbidden from editing the tests** — built `app/verify/{claims,verifier,rules}.py` + extended `app/verify/templater.py` to green; an independent **Reviewer** (wrote neither) verified against the ticket DoD. The pipeline: LLM answers in **typed claims** (`MedicationClaim/LabValueClaim/ConditionClaim/AllergyClaim/ImmunizationClaim/TextClaim`, each carrying `evidence_ids`) → verifier does **field-level match vs the cited record, rejecting on CONTRADICTION not absence** (dose 10mg vs 5mg → blocked, reason names both; both silent → pass) → the deterministic templater **re-renders display text ONLY from the verified fields copied off the cited record**, so the model's prose is discarded and it cannot phrase past verification → forbidden-phrasing + treatment-verb screens → verdict `pass|flagged|blocked|refused(kind)`. Audit rules encoded + tested: **F-D.1** (FHIR status never rendered verbatim — a "declined/refused/patient objection" assertion is blocked, never emitted); **F-D.4** (any criticality-derived risk claim rejected; criticality never rendered as risk); **F-D.5** (empty allergy → "confirm with patient", never NKDA); **F-D.6** (all conditions consumed incl. inactive; "no history of X" rejected when an inactive/resolved match exists); **F-D.2** (empty dose never invented); **D12** deceased pre-flight hard-stop (keys on `deceasedDateTime`/`deceasedBoolean`); treatment-verb → `REFUSED(TREATMENT_ADVICE)`.
- Why: the owner wanted the trust layer built with genuine separation — tests frozen by an agent that never sees the implementation, an implementer that cannot edit the tests, and reviewers who wrote neither — so the verifier's correctness isn't graded by the code that has to pass. All checks are deterministic verifier-logic tests (claim + evidence → accept/reject/rewrite); LLM output-quality is deferred to E8 evals (no mock-your-own-mock).
- Result: **suite 167 passed / 3 live deselected** (119 prior + 47 E6 + 1 finding-#2 fail-closed test). Orchestrator re-ran every gate itself (trust nothing): frozen tests byte-identical since the freeze `40ad57e` (impl commit `6187b66` touched only `app/verify/`), independent reviewer **APPROVE**, all 11 DoD items PASS, 0 critical/important. **Finding #2 closed fail-closed** via a second swarm micro-cycle (Test Agent froze `2b58dea` — unresolvable-cited TextClaim → BLOCKED; Impl Agent fixed `e5197da` — no test edited; reviewer APPROVE). Findings #1/#3/#4 recorded **deferred with reasons** in `.tdd-swarm/progress.md` (F-D.1 immunization trap forward-looking — no Immunization tool in the Early six-tool scope; cosmetic notice surfacing; §5-sanctioned demo-depth screens). **Boundary closed: owner approved; PR #1 `swarm/e6-verification` → main MERGED** (merge commit preserves the Test-Agent/Impl-Agent separation-of-powers history). Next = E7 (Langfuse traces).
- Stage: EARLY (E6 done, merged to main).

## [2026-07-09] E7 — Langfuse tracing: the accountability system-of-record · type: milestone
- What: E7.1 (§7, §3.1, D5-rev, F-C.1/F-C.2) test-first. NEW `app/observability/{trace,langfuse}.py`: a `RequestTrace` value object + a `RequestTracer`/`TraceBuilder` that the orchestrator feeds a span per model call and per tool dispatch, emitting **one trace per request** carrying the accountability fields **{correlation_id, client_id, exercised_scopes, user, patient, request_url, utc_timestamp}** + ordered steps with per-step latency + tokens + cost + verdicts + the **E5 `fallback_kind` taxonomy** (so fallback-rate is a dashboard tag/alert). Langfuse is the **HIPAA system-of-record** because OpenEMR's `api_log` can't attribute a request to the Co-Pilot OAuth client or its scopes (F-C.1) and isn't reliably joinable (F-C.2). **D5 PHI-minimization:** patient + user are stored as one-way hashes (never raw); `client_id`/scopes are accountability metadata kept in the clear. **Soft dependency (§6):** the `TraceSink` seam (`InMemoryTraceSink` for tests, `LangfuseSink` lazy-imports the SDK) is wrapped so a Langfuse outage/misconfig **drops the export with a counter and never touches serving** — proven by `test_langfuse_down_serving_continues` and `test_tracing_failure_never_breaks_the_brief`. Wired into `orchestrator.run_previsit_brief(..., tracer=, accountability=)` (optional — backward-compatible with E5). Extracted `estimate_cost` in `cost.py` so pricing lives in one place. Model = `claude-sonnet-4-6` (D4).
- Why: F-C.1 is the audit finding that makes D5 the system-of-record, not a nicety — without client_id + scopes on the trace, "which client, acting as whom, touched this record" is unanswerable. Observability that can take down serving is worse than none, hence the soft-dependency seam. Unit tests use an in-memory sink (deterministic, no network); no LLM-output-quality assertions.
- Result: **suite 182 passed / 4 live deselected** (167 prior + 15 E7). Added `langfuse` dep (lazy). **E7.0 (provision the Langfuse Cloud project + keys) is the owner's console step** — pending; the live smoke `test_langfuse_live` (emit one real trace) is opt-in via `RUN_LIVE=1` + `LANGFUSE_*` once keys land in `agent/.env` (the `/ready` langfuse probe already reports the configured host). Note: verification verdicts ride on the trace (`verdicts` field) and populate once E6 verification is wired into the serving loop (a later integration step). Next = E8 (eval framework) / the verify-in-loop wiring.
- Stage: EARLY (E7.1 done; E7.0 provisioning pending owner).

## [2026-07-09] E6a — verify-then-flush wired into the serving loop (via TDD-swarm) · type: milestone
- What: The orchestrator's final step changed from returning the LLM's RAW text to the §5 verified path (D7, §3 UC1 step 3), built on `swarm/e6a-verify-in-loop` with the same separation of powers as E6. **Mechanism:** the LLM answers by calling a **`submit_claims`** tool (schema-validated typed claims, each citing `evidence_ids`); the orchestrator parses them (`parse_claims`), runs the E6 `Verifier` field-level check against the EvidencePacket, and serves ONLY `render_from_verified(...)` — BLOCKED/REFUSED content is dropped, so **the model cannot phrase past verification**. A **D12 deceased pre-flight** refuses (deterministic "review the chart manually") BEFORE the LLM is ever consulted. Per-claim verdicts are recorded to the E7 trace (`verdicts` now populated). An end_turn without `submit_claims` (prose) is wrapped as an uncited `TextClaim` → BLOCKED → not served — the backstop that keeps raw prose out of the brief while leaving the frozen E5/E7 tests green (`source` stays `"llm"`).
- Why (process): the Test Agent froze the end-to-end invariant FIRST (an LLM claim unsupported by evidence is never served + the trace's verdicts populate); the Impl Agent (barred from editing tests) wired it and **returned BLOCKED(TEST_DISPUTE)** rather than weaken §5 when it hit an E7 test that pinned raw served prose — separation of powers catching a real conflict. Orchestrator adjudicated the dispute VALID (that assertion encoded the pre-§5 contract); a Test-role agent updated it to the soft-dependency intent. An independent Reviewer then found an **important robustness defect** (`parse_claims` crashed the turn on a malformed `submit_claims` payload — §6 violation); a second micro-cycle (Test froze fail-closed tests → Impl fixed → Reviewer APPROVE) closed it.
- Result: **suite 190 passed / 4 live deselected**. Orchestrator re-ran every gate itself (trust nothing): frozen invariant + finding tests byte-identical since freeze; impl commits touched `app/` only; **Reviewer APPROVE, no safety/behavioral findings** (rubric-1 clean — no path serves unverified/BLOCKED/fabricated-citation content). Commits: verify-then-flush `9c10488`, dispute-fix (test) + fail-closed `200b39b`. One item DEFERRED with rationale in `.tdd-swarm/progress.md`: the E6-verifier renders a claim's own *label* (name/display) when a cited record's label is empty (absence, §5-passes) — documented §5 limitation, sensitive fields (dose/value) always record-sourced; deferred to E6-verifier hardening. **Boundary: PR opened `swarm/e6a-verify-in-loop` → main, NOT merged** — pausing for owner review before merge; E8 (lean-sequential, against this verified path) queued after.
- Stage: EARLY (verify-then-flush built; awaiting owner PR review/merge).
