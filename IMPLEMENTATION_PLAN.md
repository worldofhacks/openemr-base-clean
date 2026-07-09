# IMPLEMENTATION_PLAN.md — Clinical Co-Pilot build plan

> Executable, §-anchored decomposition of the binding `ARCHITECTURE.md` (read-only here). Phases are the real submission gates, not sprints. Checkboxes are the state — update in place as work lands; never rewrite from scratch. Every task cites the architecture §/D#/F#/UC# it implements; a task with happy-path-only acceptance is incomplete. New code lives in `agent/` (the Python sidecar service) unless noted — OpenEMR app code is **not** modified (D2/D9).
>
> **Checkpoints (CT):** MVP — **done** (deploy + AUDIT + USERS + ARCHITECTURE). **EARLY — Thu 2026-07-09 11:59 PM**: a live, verified, observable agent doing UC1 end-to-end + eval framework + demo video. **FINAL — Sun 2026-07-12 11:59 AM**: production-grade agent, full evals, dashboard/alerts/runbooks, load tests, cost analysis, social post.
>
> **Posture:** production-grade — testing, failure-mode coverage, deploy/rollback are required, not optional.

---

## Phase MVP — foundation (DONE ✅)

`Deadline:` Tue 2026-07-07 · `Spec anchors:` §1, §10.1 · `Goal:` foundation + plan, no agent code.
- [x] OpenEMR local (Synthea, 25 patients) + deployed to Railway (`DEPLOYMENT.md`, D8)
- [x] `AUDIT.md` (Stage 3 hard gate — 5 sections, adversarially verified)
- [x] `USERS.md` (Stage 4 — UC1–UC4) · `ARCHITECTURE.md` (Stage 5 — §1–§11, 524-word summary)
- [x] OAuth2/FHIR crypto break found + fixed; a provisioned **enabled** OAuth client exists on prod (`DEPLOYMENT.md` §8) — so E2 auth work targets a live, working token endpoint.

---

## Phase EARLY — live verified agent, UC1 end-to-end

`Deadline:` **Thu 2026-07-09 11:59 PM CT**
`Spec anchors:` §2, §3 (UC1), §3a, §4, §5, §5a, §6, §7, §8
`Goal:` a physician can SMART-launch the co-pilot on a chart and get a **verified, cited pre-visit brief** (UC1) from the live deployed agent, with every request traced and a passing eval suite.
`Exit criteria:`
- Live agent URL serves `POST /chat` producing a verified UC1 brief for the canonical patient, streamed with citations.
- `/ready` returns 503 when any hard dependency is down (real checks, not 200).
- Langfuse shows a full trace (steps, order, latency, tokens, cost, verdict, correlation id + client_id + scopes) for every request.
- `pytest` eval suite green in CI, including the deceased-patient and empty-allergy synthetic fixtures; CI gates the Railway deploy.
- Demo video (3–5 min) recorded against the **live** agent.

**Ordering is a hard dependency chain (E1→E9); parallelizable tracks marked `∥`.**

### E1 — Agent service skeleton + observability scaffold (FIRST, per §7)
- [x] **E1.1 FastAPI skeleton + config**
  `Files:` NEW `agent/app/main.py`, `agent/app/config.py`, `agent/pyproject.toml`, `agent/Dockerfile`
  `Anchors:` §2, D3
  `Accept:` app boots; typed settings load from env (no hardcoded secrets, D-secrets); missing required env → fail-fast at startup with a clear error (not a 500 at request time).
  `Test:` `test_config_missing_env_fails_fast` (unit); app-boot smoke test.
- [x] **E1.2 `/health` (liveness) + REAL `/ready` (readiness)**
  `Files:` NEW `agent/app/routes/health.py`
  `Anchors:` §2, §7 (hard/soft deps), §5a endpoint table
  `Accept:` `/health` = process 200. `/ready` checks **hard** deps (OpenEMR FHIR metadata reachable, Anthropic API, session store) → **503** with a per-dependency body when any is down; **soft** dep (Langfuse) down → still **200** with `degraded` in body (§6, §7 — must not pull the instance from rotation). No unconditional 200.
  `Test:` `test_ready_503_when_openemr_down`, `test_ready_200_degraded_when_langfuse_down` (integration, deps mocked).
- [x] **E1.3 Structured logging + correlation-ID middleware** (∥ with E1.2)
  `Files:` NEW `agent/app/middleware/correlation.py`, `agent/app/logging.py`
  `Anchors:` §3.1, §7, D10-rev, E2 (correlation minted at launch)
  `Accept:` every request has a correlation id (minted at session launch, propagated to every log line + span, §3.1); id appears as `X-Copilot-Request-Id` on outbound FHIR calls; logs are JSON with no PHI in the message field.
  `Test:` `test_correlation_id_propagates_to_logs_and_outbound_headers` (integration).

### E2 — SMART/OAuth client (trust boundary before features, §4)
- [x] **E2.1 authorization_code + PKCE(S256) client**
  `Files:` NEW `agent/app/auth/smart_client.py`
  `Anchors:` §4 (Zone B), §5a SMART exchange, D2, D9, F-A.2 (S256 enforced), F-S.5
  `Accept:` completes the SMART EHR-launch code+PKCE(S256) exchange against the provisioned enabled client; **never negotiates `client_credentials`** (F-S.5) and never sends `APICSRFTOKEN` (F-S.3); token cached per session; on `launch/patient` context, binds that patient. Edge: launch against a disabled client (D14) → explicit "co-pilot not enabled" error, not a hang (§6).
  `Test:` `test_auth_code_pkce_s256_happy`, `test_refuses_client_credentials` (guardrail, F-S.5), `test_disabled_client_explicit_error` (§6).
- [x] **E2.2 Session store pinned to (clinician, patient)**
  `Files:` NEW `agent/app/session/store.py` (Postgres, D-O2), `agent/migrations/001_sessions.sql`
  `Anchors:` §3a lifecycles, §4, §6a ledger, D12, F-S.2
  `Accept:` session row created at launch pinned to (clinician, patient); patient switch **requires a fresh launch** (cross-patient reuse refused — the pin is the real enforcer since OpenEMR's check is a stub, F-S.2); lifetime = `MIN(token exp, idle timeout, turn cap)` (§3a); session-store unreachable → **fail-closed**, refuse to serve (§6), never serve unpinned.
  `Test:` `test_cross_patient_request_refused` (invariant), `test_session_expiry_min_of_three`, `test_session_store_down_fails_closed` (§6).

### E3 — FHIR tool layer (∥ internal tracks once contracts frozen)
- [x] **E3.1 Pydantic tool contracts (freeze first — source of truth)**
  `Files:` NEW `agent/app/tools/contracts.py`
  `Anchors:` §5a, PRD strict-schemas, D3
  `Accept:` typed input+output Pydantic models for all 6 tools + `get_changes_since_last_visit`; outputs are EvidencePacket record shapes (§5a worked example); invalid tool output → validation error surfaced as a tool failure, not a silent pass.
  `Test:` `test_tool_output_schema_rejects_malformed` (contract).
- [ ] **E3.2 The 6 read tools + parallel fan-out**
  `Files:` NEW `agent/app/tools/fhir_tools.py`, `agent/app/tools/fhir_client.py`
  `Anchors:` §2, §3 (UC1 step 2), D9, D10, F-P.2, F-P.5
  `Accept:` `get_patient_summary / get_active_medications / get_recent_labs / get_encounters / get_allergies / get_conditions` each call FHIR with the delegated token over pinned `https://` (reject downgrade, F-S.9); the 6 independent reads run concurrently via `asyncio.gather` (D10) with **per-call timeout + total turn budget**; `get_recent_labs` passes explicit `category=laboratory` to prune the 10-way Observation fan-out (F-P.2). Edge: one call fails/times out → partial result that **names** what's missing, never silent omission (§6/F3); huge chart (pid=7-class) → bounded selection + note (§6, F-P.3).
  `Test:` `test_parallel_fanout_wallclock_approx_slowest` (integration), `test_partial_failure_names_missing` (boundary), `test_labs_pass_category` (regression, F-P.2).
- [ ] **E3.3 `get_changes_since_last_visit` deterministic composite** (∥ after E3.2)
  `Files:` extend `agent/app/tools/fhir_tools.py`
  `Anchors:` §3 (UC2 flow), §5 design rule (LLM never computes deltas), D10 (dependent chain)
  `Accept:` first reads Encounter to bound "since last visit" (dependent, sequential); computes the delta **in code** (LLM only narrates); no reliable prior encounter → returns a resolvable "could not identify prior visit" signal for the D12 refusal, never a guessed delta.
  `Test:` `test_delta_computed_deterministically`, `test_no_prior_encounter_signals_refusal` (boundary). *(UC2 is Early-optional; see Cut section — ship if E1–E6 land with time.)*

### E4 — EvidencePacket builder (the only thing LLM+verifier see)
- [ ] **E4.1 Normalize tool results → typed evidence records + stable IDs**
  `Files:` NEW `agent/app/evidence/packet.py`
  `Anchors:` §5 (pipeline top), §4 (input-side injection enforcer), §6a ledger, F-C.6
  `Accept:` every tool result becomes an `EvidenceRecord` with stable id `ResourceType:{uuid}:{hash8}`; chart free-text is stored as **typed, delimited data — never instructions** (input-side injection containment, §4); Observation records carry their `category` so vitals/labs/social-history aren't conflated (F-S.8/F-C.6 caveat); the packet — not raw FHIR — is what the LLM and verifier consume (§6a).
  `Test:` `test_stable_evidence_ids`, `test_injection_text_is_delimited_data` (adversarial), `test_observation_category_preserved`.

### E5 — Orchestrator (direct Anthropic tool-use loop)
- [ ] **E5.1 Direct tool-use loop + prompt-cached patient prefix**
  `Files:` NEW `agent/app/orchestrator/loop.py`, `agent/app/llm/provider.py` (thin `llm.complete()` seam, D4)
  `Anchors:` §3 (UC1), §5, D4, D6, R1 (prompt cache)
  `Accept:` runs the UC1 summary plan (fan-out → prompt assembly → stream); the stable patient-context prefix is structured for prompt-cache hits on later turns (D4/R1); the LLM is instructed to answer only in typed claims (E6); provider access is behind `llm.complete()` (swap = config, D4). Edge: 429 → backoff within turn budget before falling to D13 (§6).
  `Test:` `test_loop_emits_typed_claims`, `test_429_backoff_then_fallback` (boundary).
- [ ] **E5.2 Deterministic degradation on LLM failure (D13)**
  `Files:` extend `agent/app/orchestrator/loop.py`
  `Anchors:` §6, D13
  `Accept:` LLM hard-fail (retries exhausted/timeout) → render the EvidencePacket via the templater (grouped, values+dates, state-aware) with an explicit "generated without LLM assistance" banner; the verifier still runs; fallback rate is traced + alertable. Never "LLM failed, no answer."
  `Test:` `test_llm_failure_renders_grounded_fallback_with_banner` (boundary, required fixture — §8/E8).

### E6 — Verification v1 (§5 — the load-bearing trust layer)
- [ ] **E6.1 Typed claims → field-level verify → deterministic templater**
  `Files:` NEW `agent/app/verify/verifier.py`, `agent/app/verify/templater.py`, `agent/app/verify/claims.py`
  `Anchors:` §5, D7, F-D.1
  `Accept:` each typed claim carries `evidence_ids`; verifier does **field-level match, reject on contradiction not absence** (10mg vs 5mg → reject; both silent → pass); the templater **re-renders display text from verified fields** — the LLM's prose is discarded if it diverges (it cannot phrase past verification); verdict ∈ `pass|flagged|blocked|refused(kind)` logged per response.
  `Test:` `test_reject_on_contradiction_not_absence` (invariant), `test_templater_discards_divergent_prose` (invariant).
- [ ] **E6.2 Encode the audit's concrete §5/D7 rules**
  `Files:` extend `agent/app/verify/verifier.py`, NEW `agent/app/verify/rules.py`
  `Anchors:` §5 rules 1–6, D7-rev, F-D.1/F-D.4/F-D.5/F-D.6/F-D.2, D12
  `Accept:` (1) FHIR `status` never rendered verbatim — an immunization never renders "patient refused" (F-D.1), Encounter.status not asserted (F-D.6); (2) reject any criticality-based claim, never rank/deprioritize allergy risk (F-D.4); (3) empty allergy result → **"no allergy records returned; confirm with patient,"** never "NKDA" (F-D.5); (4) consume all conditions, never send `clinical-status=active`, reject "no history of X" if an inactive match exists (F-D.6); (5) empty dose → "dose not specified — confirm before dosing," de-dup order+plan (F-D.2); (6) **deceased-indicator pre-flight → deterministic refusal** before any summarization (D12, keys on `deceasedDateTime`/`deceasedBoolean`); treatment-verb blocklist → refusal.
  `Test:` one invariant per rule (§8/E8) — `test_immunization_never_rendered_refused`, `test_reject_criticality_claim`, `test_empty_allergy_phrasing`, `test_no_history_rejected_when_inactive_match`, `test_empty_dose_phrasing`, `test_deceased_hardstop_refusal`, `test_treatment_verb_refused`.

### E7 — Langfuse wired (traces = HIPAA system-of-record, D5-rev; **cloud-hosted per D5 rev 2026-07-08 — no Langfuse services to deploy**)
- [ ] **E7.0 Provision the Langfuse Cloud project**
  `Files:` — (console step; keys land in Railway/agent env as `LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`)
  `Anchors:` D5 rev 2026-07-08, D8
  `Accept:` Langfuse Cloud project created (demo posture: free tier, US region `https://us.cloud.langfuse.com`, assumed BAA under the demo-data-only rule; production path documented: HIPAA region `https://hipaa.cloud.langfuse.com`, Pro plan+, signed BAA before real PHI); project **retention policy set**; keys wired into the agent env; `/ready` reports the langfuse probe green.
  `Test:` — (provisioning; covered by the E1 `/ready` probe against the configured host).
- [ ] **E7.1 Trace every request with the accountability fields**
  `Files:` NEW `agent/app/observability/langfuse.py`; extend orchestrator/verifier/tools to emit spans
  `Anchors:` §7, §3.1, D5-rev, F-C.1, F-C.2
  `Accept:` each request → one Langfuse trace with steps + order + per-step latency + tokens + cost + verification verdict; the trace **carries `{correlation_id, client_id, exercised_scopes, user, patient, request_url, utc_timestamp}`** (D5 is the system-of-record because api_log omits client_id/scopes, F-C.1); no hard api_log join is attempted (D10-rev/F-C.2). Edge: Langfuse down → serving unaffected, export buffered/dropped with a counter (§6, soft dep).
  `Test:` `test_trace_has_client_id_and_scopes` (invariant), `test_langfuse_down_serving_continues` (boundary).

### E8 — Eval framework v1 (production-grade — happy-path-only fails)
- [ ] **E8.1 pytest harness + EvalCase schema + dataset**
  `Files:` NEW `agent/evals/conftest.py`, `agent/evals/schema.py`, `agent/evals/dataset/` (golden set from demo patients)
  `Anchors:` §8, PRD eval requirements
  `Accept:` `EvalCase{id, category: boundary|invariant|regression|adversarial, input, fixture?, expected, guards, pass_criteria}` — every case names the failure mode it guards; correctness measured vs FHIR ground truth; runner produces a results export (§11 deliverable).
  `Test:` the harness runs; a trivial invariant case passes/fails as expected (meta-test).
- [ ] **E8.2 Required synthetic fixtures (the demo data can't provide these)**
  `Files:` NEW `agent/evals/fixtures/deceased_patient.py`, `agent/evals/fixtures/no_allergy.py`, `agent/evals/fixtures/llm_failure.py`, `agent/evals/fixtures/fhir_failure.py`
  `Anchors:` §8, D12-rev, F-S.7, F-D.5, D13, F3
  `Accept:` **deceased-patient** fixture (mocked `Patient.deceasedDateTime`) drives the D12 hard-stop refusal; **no-allergy** fixture drives the F-D.5 phrasing rule; **llm-failure** fixture drives the D13 banner; **fhir-failure** fixture drives the F3 partial-answer. Each is a failing-then-passing invariant, not a happy-path demo.
  `Test:` these fixtures back the E5.2/E6.2 invariant tests above.
- [ ] **E8.3 Adversarial + guardrail cases** (∥ with E8.2)
  `Files:` NEW `agent/evals/adversarial/`
  `Anchors:` §8, §4, F-S.5, F-S.3, D12
  `Accept:` prompt-injection attempts (chart text trying to issue instructions → treated as data, §4); unauthorized-data extraction (cross-patient ask → refused); guardrail assertions — agent never negotiates `client_credentials` (F-S.5), never sends `APICSRFTOKEN` (F-S.3).
  `Test:` these ARE the eval cases.
- [ ] **E8.4 CI eval-gate-before-deploy**
  `Files:` NEW `.github/workflows/agent-evals.yml`
  `Anchors:` §8, §10.2, D8
  `Accept:` evals run per push; Railway deploys the agent **only on green** (eval-gate); red evals block deploy.
  `Test:` a deliberately-failing eval blocks the workflow (verified once).

### E9 — Deploy the live agent (needs a URL before the demo)
- [ ] **E9.1 Agent as a new Railway service in the existing project**
  `Files:` NEW `agent/railway.json` (or service config); extend `DEPLOYMENT.md`
  `Anchors:` §1, §2, §10.2, D8 (same Railway project — PRD same-infra rule)
  `Accept:` agent builds from `agent/Dockerfile` and deploys as a service in the existing `openemr` Railway project; env wired (OpenEMR base URL pinned `https://`, Anthropic key, Langfuse, session-store DB); public HTTPS URL live; `/ready` green against the live OpenEMR.
  `Test:` `curl` the live `/ready` → 200; live `/health` → 200.
- [ ] **E9.2 UC1 end-to-end on the live agent**
  `Files:` — (verification task)
  `Anchors:` §3 (UC1), §5, §7
  `Accept:` a real SMART launch → `POST /chat` streams a **verified, cited** pre-visit brief for the canonical patient from the live agent; the brief obeys the §5 rules (no "patient refused" vaccines, empty-allergy phrasing if applicable); the request appears as a complete Langfuse trace. Edge: mid-stream interruption → response marked incomplete, not presented as complete (§6).
  `Test:` live end-to-end smoke (recorded for the demo); `test_stream_interruption_marks_incomplete`.
- [ ] **E9.3 Demo video (3–5 min) against the live agent**
  `Files:` NEW `docs/demo/early-demo.md` (script/link)
  `Anchors:` §11
  `Accept:` shows the 2–3 key decisions (D2 sidecar, §5 verification justified by F-D.1, D5 accountability) + a live UC1 brief; recorded against the deployed URL, not local.
  `Test:` — (artifact).

---

## Phase FINAL — production-grade hardening

`Deadline:` **Sun 2026-07-12 11:59 AM CT**
`Spec anchors:` §5 (full rules), §6, §7 (dashboard/alerts/rollback/Bruno/baselines/load), §8 (full suite), §9, §11, §4 (deploy hardening)
`Goal:` defensible in front of a hospital CTO — full verification, observability, load evidence, cost model, and the audit's deploy actions closed.
`Exit criteria:` dashboard live with all required metrics + ≥3 alerts with runbooks; Bruno collection runs end-to-end; k6 @ 10/50 VUs recorded; cost analysis from real traces; https-pin + MySQL-proxy-closed + api_log retention set; full eval suite green; social post published.

- [ ] **F1 Verification v2 — full constraint rules** `Files:` extend `agent/app/verify/rules.py` `Anchors:` §5, D7 `Accept:` dosage bounds, interaction-flag lookup (read-only, never advice), stale-lab flagging with dates (F-D.6), full forbidden-phrasing screen; each rule has an invariant eval. `Test:` expanded §8 invariants.
- [ ] **F2 Dashboard** `Files:` NEW `docs/observability/dashboard.md` + Langfuse config `Anchors:` §7, PRD `Accept:` real-time request count, error rate, p50/p95, tool-call counts, retry counts, verification pass/fail rate, token cost/request, LLM-fallback rate, refusal-kind breakdown. `Test:` each metric visible with live data.
- [ ] **F3 ≥3 alerts + runbooks + delivery** `Files:` NEW `agent/ops/alert_checker.py`, `docs/observability/runbooks.md` `Anchors:` §7 alert table `Accept:` p95>15s (R12 re-baselined), error>5%, tool-failure>10%, LLM-fallback — each with threshold + likely cause + first on-call action + escalation; delivered via checker→webhook/Slack. `Test:` a synthetic breach fires the channel once.
- [ ] **F4 Re-baseline latency (R12)** `Files:` update ARCHITECTURE §7/§9 via /arch-finalize (NOT here) `Anchors:` R12 `Accept:` replace the 28s assumption with measured Langfuse p50/p95 from Early traffic; feed the alert threshold + cost model. `Test:` numbers sourced from real traces. *(Route the doc change through /arch-finalize — this plan does not edit ARCHITECTURE.md.)*
- [ ] **F5 Runnable API collection (Bruno) + token-mint helper** `Files:` NEW `agent/bruno/` collection + `agent/bruno/mint-token.md` `Anchors:` §7, G4, D14 `Accept:` covers `/chat`, `/health`, `/ready`, sample tool flows; ships a dev-only token-mint helper populating a Bruno env var so a grader runs the authed flows end-to-end without reading source. `Test:` a fresh clone runs the collection green.
- [ ] **F6 Baselines + k6 load @ 10/50 VUs** `Files:` NEW `agent/load/k6/*.js`, `docs/observability/baselines.md` `Anchors:` §7, F-P.5 `Accept:` CPU/mem per service from Railway metrics; p50/p95/p99 + error rate at 10 and 50 concurrent users recorded; fan-out cap chosen from observed OpenEMR behavior under load (D10). `Test:` — (recorded artifact).
- [ ] **F7 AI cost analysis** `Files:` NEW `docs/COST_ANALYSIS.md` `Anchors:` §9, D4, R1, R4 `Accept:` actual dev spend from Langfuse cost traces + Railway billing; projections at 100/1K/10K/100K with the infra step-changes (not per-token×n); prompt-cache economics dominate. `Test:` — (artifact traces to real numbers).
- [ ] **F8 Close the audit's deploy actions** `Files:` extend `DEPLOYMENT.md` `Anchors:` §4, §11, F-S.9, F-S.4, D15 `Accept:` agent pins `https://` + rejects downgrade (F-S.9); Railway MySQL TCP proxy **closed** (F-S.9); `api_log_option`/retention set for the deployment (F-S.4/D15); temporary `claude-deploy-fix` SSH key removed. `Test:` proxy unreachable externally; `api_log` posture documented.
- [ ] **F9 Full eval suite + UC2/UC3/UC4** `Files:` extend `agent/evals/`, `agent/app/` `Anchors:` §3 (UC2–UC4), §8 `Accept:` UC2 (what-changed), UC3 (cited Q&A with treatment-verb refusal), UC4 (attention flags, never rank on null criticality) shipped + eval-covered; regression pins for canonical queries. `Test:` full `pytest` green in CI.
- [ ] **F10 Social post** `Files:` NEW `docs/demo/social-post.md` `Anchors:` §11 `Accept:` X/LinkedIn post describing the project, showing the agent, tagging @GauntletAI. `Test:` — (artifact).

---

## Deliverables map (graded item → producing task)

| Graded deliverable | Phase/Task |
|---|---|
| Deployed live agent (Early + Final) | E9.1, E9.2 |
| Agentic multi-turn chatbot w/ tool use | E3, E5 (UC1); F9 (UC2–UC4) |
| Verification layer (attribution + constraints + limitations) | E6.1, E6.2; F1 |
| Observability from the start (trace: steps/order/timing/failures/tokens/cost) | E1.3, E7.1 |
| Correlation ID everywhere | E1.3, E7.1 |
| Strict schemas (tool I/O) | E3.1 |
| Dashboard (all required metrics) | F2 |
| ≥3 alerts + on-call response | F3 |
| Runnable API collection | F5 |
| Separate /health + real /ready | E1.2 |
| Baseline profiles | F6 |
| Load tests @ 10/50 | F6 |
| Eval suite (boundary/invariant/regression + adversarial) | E8, F9 |
| Synthetic deceased + empty-allergy fixtures | E8.2 |
| AI cost analysis (100/1K/10K/100K) | F7 |
| Demo video (Early + Final) | E9.3; F10-adjacent |
| Social post (Final) | F10 |
| Deployed URL in every submission | E9.1 (recorded in submission) |
| Rollback mechanism | §7 (deploy) — E9.1 wires deploy-on-green; rollback = D8 Railway redeploy |

---

## Cut / deferred (dated — cuts are decisions)

- **2026-07-08 — UC2/UC3/UC4 deferred to FINAL (F9).** Early scopes to UC1 end-to-end only (the prompt's tight-Early rule); UC2's deterministic delta (E3.3) is built if E1–E6 land with time, else moves to F9. Reason: Thursday requires one verified use case live, not four.
- **2026-07-08 — Voice I/O stays cut (D11).** Not resurrected; prior analysis in DECISIONS.md D11.
- **2026-07-08 — SMART UI embed = new tab, not iframe (O1).** Iframe polish deferred; default launch-in-new-tab for Early.
- **2026-07-08 — Redis session store deferred (O2).** Postgres for Early/Final; revisit only if latency demands.

---

## Needs architecture (flagged, not invented — route through /arch-finalize)

- **None blocking Early.** Every Early/Final task above traces to a § / D# / F#.
- **Watch item (not a task yet):** the concrete **fan-out concurrency cap** for D10 (§7 says "chosen from observed OpenEMR behavior under load") has no numeric backing until F6 runs — it is data-derived, not a missing decision. If load reveals OpenEMR instability requiring a *design* change (e.g. request queueing at Early scale), route that through /arch-finalize rather than adding it here.
- **R12 latency anchor** is an explicit open assumption (ARCHITECTURE §7/§9); F4 replaces it with measured data via /arch-finalize — this plan must not edit ARCHITECTURE.md.

---

## Parallelization summary (for a solo dev with AI assist)

- **Serial spine (must be in order):** E1 → E2 → E3.1 → E3.2 → E4 → E5 → E6 → E7 → E9.
- **∥ tracks:** E1.2 ∥ E1.3; E8.1/E8.2/E8.3 can be authored alongside E5/E6 (they consume the same contracts); E3.3 (UC2) ∥ after E3.2; F2/F5/F6/F7/F10 are largely independent in FINAL.
- **Critical path to Early:** E1 → E2 → E3.1→E3.2 → E4 → E5 → E6 → E7 → E9.1 → E9.2 → E9.3. Eval framework (E8) must be green before E9.1 deploys (E8.4 gate).
