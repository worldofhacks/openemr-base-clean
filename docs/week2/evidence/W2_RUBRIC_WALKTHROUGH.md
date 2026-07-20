# W2 rubric walkthrough — every expected row, where to see it, and its proof

**Purpose.** Direct response to early-submission feedback: *"please walk through the rubric
expected rows like the bounding-box click-to-source overlay, a green eval-gate run, etc."*
Each row below maps a rubric expectation (AgentForge Week 2 PDF, page cited) to (a) how to
see it live in ≤60 seconds, (b) its durable evidence, (c) honest status. Live steps assume
the deployed app (`README` grader quickstart; SMART sign-in; synthetic patient
**Daron260 Windler79**; all documents are synthetic fixtures from
`agent/evals/fixtures/`). Demo-beat timestamps reference the published S01 video:
https://x.com/imdecentralized/status/2079069184420700532?s=20

**Status legend:** ✅ live now · 🔀 lands when the named PR merges · 👤 gated on an owner
action (§4c) then live. Nothing here is claimed without a link. **D01 final pass
2026-07-19 at release SHA `b31207ce33ebe0706b2dc9fa13816b73fb08d4fc`** — deployed and
attested (`/health` returns the release SHA; `/ready` all-green ×3 cache-busted; worker
identity attested by `scripts/verify_deployed_sha.py` → PASS).

| # | Rubric row (PDF cite) | See it live | Evidence | Status |
|---|---|---|---|---|
| 1 | Lab PDF ingestion → strict-schema extraction (p.3 MVP; p.4 Req 1–2) | Upload `golden/lab-clean-glucose.pdf` → status polls to complete → grounded artifact + two byte-verified sha256 digests re-read from OpenEMR | `W2_EVIDENCE_INDEX.md` s01-beat2/3; schema tests in `agent/tests/` | ✅ |
| 2 | Intake form ingestion + no-duplicate round-trip (p.3; p.2 FHIR integrity) | Upload `golden/intake-full-valid.pdf` **twice** → same document id, digests re-verify, vitals exactly-once | s01-beat4; idempotency tests | ✅ |
| 3 | **Third document type** — medication list (p.5 Core Deliverables) | Doc type **Medication list** → upload R09's golden fixture → stored + grounded artifact; UI states "source + grounded artifact only" — NO MedicationRequest/clinical write path exists, by safety design | R09 golden cases in the gate; `tests/test_medication_list.py`; migration 007 | ✅ merged ([#35](https://github.com/worldofhacks/openemr-base-clean/pull/35), R09 replay of #33) |
| 4 | **Bounding-box click-to-source overlay** (p.5 Req 5 — named in feedback) | From any grounded field or citation chip click **Open page 1** → dialog renders the exact page with the value visibly boxed | s01-beat3; `W2_ARCHITECTURE.md` §citation; W2-REQ-29 RTM row | ✅ |
| 5 | Machine-readable per-claim citations (p.5 Req 5) | **Cited answer** → ask `type 2 diabetes; glucose` → per-claim CitationV2 across three source classes (chart / uploaded document / guideline), each chip click-through | [PR #26](https://github.com/worldofhacks/openemr-base-clean/pull/26) (R01) contract + fail-closed 503 tests | ✅ merged |
| 6 | **Critic rejects uncited claims** (p.5 Core Deliverables) | Always-on deterministic critic approves every composition server-side before bytes flush; visible critic decision marker renders with the per-claim contract | Critic suite (`orchestrator/critic.py` tests); adversarial `test_graph_bypass_verifier` | ✅ engine + visible marker (#26 merged) |
| 7 | Hybrid RAG + rerank over guideline corpus (p.3–4 Req 3) | Same cited answer shows **guideline** chips (VA/DoD) beside patient-record chips. **Query phrasing note:** use the canonical condition-term phrasing (e.g. `type 2 diabetes; glucose`) — guideline hits key off clinical condition terms; free-form paraphrases may miss (known limitation, backlog G07) | Corpus manifest + retrieval suites; R02 evaluator traverses production `HybridRetriever` ([#31](https://github.com/worldofhacks/openemr-base-clean/pull/31) + golden gate through production retrieval, `279d663`) | ✅ |
| 8 | Supervisor + 2 workers, explainable handoffs (p.4 Req 4) | Langfuse: filter by the `w2.<hex>` correlation id printed in the workbench → supervisor → worker spans with routing reasons | R03 conditional-routing + nested-trace tests ([PR #30](https://github.com/worldofhacks/openemr-base-clean/pull/30)); O01 packaged trace ([evidence/o01/](o01/W2_O01_JOURNEY_BUNDLE.md)); real span timestamps at the release SHA ([PR #41](https://github.com/worldofhacks/openemr-base-clean/pull/41)) | ✅ |
| 9 | **Lab trend chart using extracted observation data** (p.5 Core Deliverables) | **Lab trends**: exact-unit charts (mg/dL vs % — 6.5 can never read as 65); every point clicks through to its verified page/bbox. Honest note: trends are backed by write/readback-verified extraction artifacts; this fork exposes no supported client FHIR Observation write, and creating one was rejected as a safety decision (`W2_DECISIONS.md` W2-D10/W2-F3) | s01-beat "Lab trends"; artifact authority ledger (R04, PR #28) | ✅ |
| 10 | **Green eval-gate run** (p.5 Req 6 — named in feedback) | Recorded 50-case gate: PASS, zero category delta, five boolean rubric categories (schema_valid, citation_present, factually_consistent, safe_refusal, no_phi_in_logs) | [E01 final](W2_CI_EVIDENCE.md): `evidence/eval-results/results-tier{1,2-live}-b31207c.json` + sha256 digests; green mint [run 29713267431](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29713267431) | ✅ recorded + live at the release SHA |
| 11 | **HARD GATE: introduced regression turns CI red** (p.5) | C02 phase-2 drill: red candidate blocked by required checks, green merges — both archived with run URLs | [`evidence/c02/`](c02/) phase-1/2 runbooks; ruleset 19180393 (bypass: never); red drill [PR #37](https://github.com/worldofhacks/openemr-base-clean/pull/37) blocked ([run 29706216600](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29706216600)); ten green train merges | ✅ |
| 12 | Observability + cost per encounter (p.5 Req 7) | Correlation-ID walk (beat 4:05): one id reconstructs queue → OCR/VLM → grounding → retrieval → writes → critic; per-step latency/token/cost in trace | R05 root-cause wiring + alert drills (merged [#36](https://github.com/worldofhacks/openemr-base-clean/pull/36), replay of #32); [`W2_R05_PRODUCTION_VERIFICATION.md`](W2_R05_PRODUCTION_VERIFICATION.md); [`W2_COST_LATENCY.md`](W2_COST_LATENCY.md) incl. release-SHA re-measure | ✅ wiring + partial report · 👤 O02 full four-path profile (owner-gated W2-O4) |
| 13 | Useful on imperfect scans (p.2 scenario; W2-REQ-91/92) | Upload a degraded/handwritten-style image: extraction completes with UNSUPPORTED fields redacted and visibly unverified — never invented, never a 500 | R08 ([PR #24](https://github.com/worldofhacks/openemr-base-clean/pull/24)): evidence-quality gate + image hardening, 16 pinned tests; G-D3 decision record | ✅ merged |
| 14 | Safe refusals + honesty guards (p.4 hard problems) | Ask beyond evidence → refusal; allergy-honesty note ("never treated as NKDA"); adversarial suite: prompt-injection, deceased hard-stop, cross-patient | Adversarial eval category in the gate; refusal beats in demo | ✅ |
| 15 | No PHI in logs/artifacts (p.2, p.6) | `no_phi_in_logs` rubric category green; artifact scanner over all generated eval surfaces; synthetic-only data everywhere | Gate results; `artifact_scan` in CI | ✅ |
| 16 | Deployed app + demo video + cost/latency report + backup-restore (p.5 deliverables table) | `/health` + `/ready` (cache-busted) at the release SHA; 3–5 min video per the recording kit; four-path O02 report; timed O03 restore | REL1 verified at `b31207c` (health/ready + exact-SHA worker attestation); [O01 bundle](o01/W2_O01_JOURNEY_BUNDLE.md); [published S01 video](https://x.com/imdecentralized/status/2079069184420700532?s=20) | ✅ deployed + verified + video published · 👤 O02 full / O03 restore (blockers recorded in the evidence-index closeout) |

## Engineering requirements (PDF pp.6–7) — each bullet's proof

These are graded "alongside the core submission and are not optional" (p.6). The 103-row RTM
in `W2_gap-audit.md` is the authoritative per-row ledger; this table is the shortcut.

| Engineering requirement (p.6–7) | Proof |
|---|---|
| Typed contracts, schema evolution, data authority | R04 authority ledger + divergence tests (PR #28); frozen Pydantic schemas; migration notes |
| Timeouts, retries, circuit breakers on outbound calls | R06 Cohere bounded retry + breaker (PR #27); Anthropic retry posture; per-page OCR kill budget |
| Schema is source of truth; no raw VLM bypass | Strict double validation (`vlm.py` + pipeline `_strict_extraction`); G-D3 preserves it |
| Correlation ID across every boundary | `w2.<hex>` id printed per job; full trace reconstructable from it alone (demo beat 7; O01 bundle) |
| Structured, searchable, PHI-free logs | R05 event wiring + redaction tests (PR #32); `no_phi_in_logs` rubric category |
| Dashboards + alert definitions | R05 scheduled `w2_alerts` evaluation + drill evidence (PR #32) |
| CI: build/lint/type/tests/coverage/dep-audit/security | Existing quality workflows + C01 image-smoke & mypy ratchet (PR #29); dependency audit per PR |
| Testing strategy documented; every test names its failure mode | `W2_ARCHITECTURE.md` testing section; D01 keeps it release-accurate |
| Failure modes + incident response documented | `docs/week2/evidence/W2_RUNBOOKS.md`; architecture failure-mode section |
| Runnable API collection | Bruno collection + `tests/test_w2_openapi_and_bruno.py` keeps it honest |
| Baseline CPU/memory/latency/throughput profiles | `W2_BASELINES.md` (W1 vs W2) + O02 four-path report at the release SHA |
| OpenAPI 3.0 committed + contract-tested | `agent/ops/openapi.yaml` + contract tests |
| Integration tests with fixtures/stubs, CI without live keys | Recorded tier under `network_disabled()`; stubbed VLM/LLM; committed fixtures |
| Data model: owner, lineage, access, validation | R04 ledger (PostgreSQL = durable artifact authority; OpenEMR = source-document authority) |
| Analytics/eval PHI audit | `artifact_scan` over all generated surfaces, in CI |
| Backup + recovery, RPO/RTO, golden set reproducible from repo | O03 drill + `W2_BACKUP_RESTORE.md`; deterministic fixture generator (AC-7) |

## Known open findings (stated, not hidden)

- Retrieval p95 measured 6.5 s vs the ≤2 s SLO target pre-R07-deploy; root cause identified
  (per-request ONNX session cost), fix merged in #25, re-measured in O02 at the release SHA.
- The gap-audit verdict remains **Not Ready** until the independent V01 pass signs off at the
  release SHA — by design, never self-graded.

*Prepared 2026-07-19 (owner-directed, responding to early-submission feedback). D01 final
pass completed 2026-07-19 at release SHA `b31207ce33ebe0706b2dc9fa13816b73fb08d4fc`:
statuses above carry live links; the remaining 👤 items are the S01 video, the full O02
profile, and the O03 restore drill. S01 records the video following exactly the rows
above.*
