# Week 2 CI evidence ledger

## Local pre-release evidence

This evidence was produced from the integrated working tree on 2026-07-15. It is not
canonical exact-SHA release evidence until the tree is committed and the protected gates
rerun that commit.

| Evidence | Result |
|---|---|
| Base Python suite before closeout | 663 passed, 5 skipped |
| Focused numeric/PHI/pin/exactly-once/readback set | all selected tests passed |
| Integrated Python/eval/API/ops/Bruno/corpus matrix | 948 passed, 6 skipped; 85% coverage against the locked 83% floor |
| Explicit grounding/eval/write/trend/critic/correlation regression slice | 106 passed |
| Recorded Tier 1 CLI | PASS; 50 manifest cases loaded and 50 executor calls; schema 50/50, citations 50/50, factual 23/23, safety 10/10, no-PHI 50/50 |
| Tier 1 artifact | Aggregate-only (`cases: []`); recording and result artifact scan passed |
| Live Tier 2 without owner inputs | Nonzero `INCONCLUSIVE`; protected CI/main also refuses to run without the reviewed canonical baseline |
| Static/dependency security | Ruff, strict targeted mypy, Bandit, Semgrep, pip-audit, and npm audit passed with zero findings/vulnerabilities |
| Deployable image | Docker image built and `/health` returned `alive` with synthetic configuration |
| Tier-1/Tier-2 workflow policy | plain `pull_request`; no `pull_request_target`; protected same-repository live environment |
| Deployment binding | workflow-run exact SHA; same SHA injected into web and worker |
| GitLab bridge | exact repository, SHA, workflow, check conclusion, workflow run, artifact name, and SHA-256 digest required |

## Reviewed live baseline

The reviewed baseline is generated only from the aggregate-only, green, exact-SHA live
result produced by the canonical `main` gate. No provider transcript, prompt, fixture value,
or case observation is retained in the baseline.

| Evidence | Value |
|---|---|
| Source SHA | `60597031142d269a79ab3a8aa0e3537cc6c6f90b` |
| GitHub run | [agent-eval-gate 29471973725](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29471973725) |
| Live execution | PASS; 50 manifest cases and 50 executor calls; zero retries |
| Category arithmetic | schema 50/50; citations 50/50; factual 23/23; safety 10/10; no-PHI 50/50 |
| Live result SHA-256 | `12d223abc2c6a6ac964e820203e1009ae22de834cdebe2e0dc9d9b7e0917dab2` |
| Baseline artifact SHA-256 | `3f2344382814b2ecabd1675da1d62e73ab5f944100ff33d1a906482ddcbaf117` |
| Artifact scanning | PASS; aggregate-only result and generated baseline |

## Red-gate regression evidence

All five drills start from the reviewed Railway-fix base
`598e0e75cc76e4c4fe3a7c810cefb42a127d1df4`. Every drill is an unmerged,
ordinary-pushed `tier2/<exact-sha>` ref. The mutations affect independently produced runtime
observations only; golden expectations, fixtures, prompts, and source inputs remain unchanged.
The schema and citation drills intentionally mutate one observed output member. Failed Tier-1
aggregates retain `cases: []`.

### Malformed extraction schema

- Exact SHA: `9fc192830f727b9bf9f93c9752276af2496783a9`
- Run/job: [agent-eval-gate 29474943771](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29474943771), `eval-tier1` job `87545826944`
- UTC result time: `2026-07-16T05:50:54.7057845Z`
- Result: expected Tier-1 failure; 50 cases and 50 executor calls
- Arithmetic: schema 49/50, current `0.98`, baseline/delta not applicable to recorded Tier 1, threshold `1.0`
- Trigger: `failed 100% invariant`
- Other categories: citations 50/50; factual 23/23; safety 10/10; no-PHI 50/50
- Artifact scan: PASS; local aggregate SHA-256 `5f988c10ca02ae4af8493874f98c7eb64cf8caec3b3580d56245bd474baccd42`

### Incomplete CitationV2

- Exact SHA: `8b59440706ebe17b82636cfee5d95952255eed89`
- Run/job: [agent-eval-gate 29474943695](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29474943695), `eval-tier1` job `87545826629`
- UTC result time: `2026-07-16T05:50:54.3762974Z`
- Result: expected Tier-1 failure; 50 cases and 50 executor calls
- Arithmetic: citations 49/50, current `0.98`, baseline/delta not applicable to recorded Tier 1, threshold `1.0`
- Trigger: `failed 100% invariant`
- Other categories: schema 50/50; factual 23/23; safety 10/10; no-PHI 50/50
- Artifact scan: PASS; local aggregate SHA-256 `7d1e80a1e5efed7b4228abe630cb676332466e087bbc92c43bd04c1d3646ae40`

### Simulated prohibited-side-effect evidence

- Exact SHA: `df521bdf5476cebc6be11b85db027e0cf25e82a5`
- Run/job: [agent-eval-gate 29474987667](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29474987667), `eval-tier1` job `87545959439`
- UTC result time: `2026-07-16T05:52:00.3563229Z`
- Result: expected Tier-1 failure; 50 cases and 50 executor calls
- Arithmetic: safety 9/10, current `0.9`, baseline/delta not applicable to recorded Tier 1, threshold `1.0`
- Trigger: `failed 100% invariant`
- Other categories: schema 50/50; citations 50/50; factual 23/23; no-PHI 50/50
- Artifact scan: PASS; local aggregate SHA-256 `2c8246d023d7fac7232cc7298c07baf209e5c31d880918e50a09bd8920d37401`

### Short-PHI generated-surface leak

- Exact SHA: `980f5e20d4124e7542d09d39a77e0b34bab358f5`
- Run/job: [agent-eval-gate 29474987307](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29474987307), `eval-tier1` job `87545958501`
- UTC result time: `2026-07-16T05:51:44.9723466Z`
- Result: expected Tier-1 failure; 50 cases and 50 executor calls
- Arithmetic: no-PHI 49/50, current `0.98`, baseline/delta not applicable to recorded Tier 1, threshold `1.0`
- Trigger: `failed 100% invariant`
- Other categories: schema 50/50; citations 50/50; factual 23/23; safety 10/10
- Artifact scan: PASS; local aggregate SHA-256 `09e3a442ace73434bed4f8f5abc85cad7884813daba892c5059c37771c2e8b42`
- Safety note: the source-derived short value existed only in the in-memory scanner input and was never emitted to an external logging sink, printed, persisted, or uploaded.

### Factual baseline regression

- Exact SHA: `1692dd1c181104022612cbc8483268b9e1c1f574`
- Run/job: [agent-eval-gate 29475115387](https://github.com/worldofhacks/openemr-base-clean/actions/runs/29475115387), `eval-tier2-live` job `87547319216` (run attempt 2)
- UTC result time: `2026-07-16T06:17:15Z`
- Result: expected protected Tier-2 failure; 50 cases and 50 executor calls
- Arithmetic: factual 21/23, current `0.913043`, baseline `1.0`, delta `-8.695652` percentage points, threshold `0.9`
- Trigger: `failed >5 percentage-point baseline regression`
- Other categories: schema 50/50; citations 50/50; safety 10/10; no-PHI 50/50
- Artifact scan: PASS; one aggregate-only live result scanned
- Retry note: attempt 1 did not reach the live job because a reusable quality shard failed transiently while the standalone quality and recorded gates on the same SHA were green. The exact-SHA failed-job rerun made no source change and did not repeat a false judge result.

### Post-drill green control

After all five negative runs reached their expected terminal failures, the unmodified
Railway-fix base was rerun locally through the network-disabled recorded executor. This
control proves that removing each isolated drill mutation restores the governed gate before
the evidence-bearing release commit enters the protected GitHub gates.

- Exact SHA: `598e0e75cc76e4c4fe3a7c810cefb42a127d1df4`
- UTC result time: `2026-07-16T06:24:00Z`
- Result: PASS; 50 manifest cases and 50 executor calls
- Arithmetic: schema 50/50; citations 50/50; factual 23/23; safety 10/10; no-PHI 50/50
- Aggregate SHA-256: `dac850f4188ae97f95f7a4bf651fd3d8866632969dfb1eb39ed305bb0caba344`
- Artifact scan: PASS; aggregate, recordings, and reviewed baseline scanned

The evidence-bearing pull-request head and the merge SHA must each pass the canonical
GitHub Tier-1 and protected Tier-2 gates before deployment. Their immutable run URLs and
the exact deployed SHA are release records in GitHub Actions and Railway rather than
precomputed values in this commit.

## E01-lite — durable committed copies of the current green exact-SHA results (2026-07-19)

GitHub artifact retention is ~14 days; these repository copies are the durable record
(AF-P1-06 interim; full E01 re-binds to the accepted release SHA).

- Source run (green `agent-eval-gate` on `main`, 2026-07-17):
  https://github.com/worldofhacks/openemr-base-clean/actions/runs/29553727457
- Both artifacts carry `source_sha = 658307936f0396d292c94fff3f9ef8089f1697e7`
  (= the audit baseline and the deployed `/health` SHA), `status = PASS`,
  50 manifest cases / 50 executor calls, all five rubric categories met.
- Committed copies + SHA-256 digests (verified at download, 2026-07-19):
  - `docs/week2/evidence/eval-results/results-tier1-6583079.json`
    `8d43bb568a689d08ea6c54b95e6588a28d9369fbcd3b0b40b68ce274041c5b3b`
  - `docs/week2/evidence/eval-results/results-tier2-live-6583079.json`
    `b66bae841231aa4ea8683cd64225752ef217dca0a9148ca4389efc3f421b7a3f`
    (live Tier-2 aggregates: cost $3.0658, p50 5611 ms, p95 12266 ms, 621k in /
    58k out tokens, retrieval_hit_count 202, grounding 0.9596)
- Sanitization: `python -m evals.artifact_scan --eval-result <file>` → PASS on both
  (aggregate-only; no case text, no PHI).
- The committed `agent/evals/results-tier2.json` INCONCLUSIVE placeholder is replaced by
  full E01 against the accepted SHA (with the submission check binding evidence SHA =
  release SHA); it is intentionally untouched here.

## E01 final — exact-SHA Tier-2 live mint at the release SHA (2026-07-19)

Supersedes E01-lite above for the accepted release; the 6583079 copies and the
89a2b86 chain evidence remain archived, not deleted.

- **Release SHA:** `b31207ce33ebe0706b2dc9fa13816b73fb08d4fc` (merge of PR #41 on top
  of PR #40; the Tier-2 remediation + Langfuse real-timestamp cycles).
- **Green mint run** (workflow_dispatch `agent-eval-gate`, exact_sha input, protected
  `eval-tier2-live` environment): https://github.com/worldofhacks/openemr-base-clean/actions/runs/29713267431
  — all seven jobs green including `eval-tier2-live` (fresh full live 50-case gate).
  Artifacts: `eval-results-tier2-live` id 8449762736, `eval-results-tier1` id 8449513666.
- **Chain-proof run at the prior candidate** `89a2b861951da18ec144bc1ac58029dbc4d73134`
  (superseded by the Langfuse cycle, retained as evidence the remediation worked
  end-to-end): https://github.com/worldofhacks/openemr-base-clean/actions/runs/29711288074 — green.
- Both committed copies carry `source_sha = b31207ce33ebe0706b2dc9fa13816b73fb08d4fc`,
  `status = PASS`, 50 manifest cases / 50 executor calls, all five rubric categories met
  (`factually_consistent` 22/22 ≥ 0.90 threshold; four 100% invariants at 50/50).
- Committed copies + SHA-256 digests (verified at download, 2026-07-19):
  - `docs/week2/evidence/eval-results/results-tier1-b31207c.json`
    `c1255e0ee2554f36066b343b9c71e56bf1697905a42329e4965f2d41fec188e9`
  - `docs/week2/evidence/eval-results/results-tier2-live-b31207c.json`
    `132b36357a0505591bc6728f2f55218fbcf24e814500b906ab6ad14119899fb6`
    (live Tier-2 aggregates: cost $2.6824, p50 5305 ms, p95 12330 ms, 566k in /
    52k out tokens, retrieval_hit_count 90, grounding 0.9631)
- Sanitization: `python -m evals.artifact_scan --eval-result <file>` → PASS on both,
  re-executed locally on the committed copies (aggregate-only; no case text, no PHI).
- The reviewed live baseline `agent/evals/w2_baseline.json` was re-minted from a green
  full live 50-case run at `5698d89c…` (branch head of PR #40) after the G-D6 fixes;
  the CI mint above validates against it at the release SHA.

## C02 phase 1 — protection applied and exported (2026-07-19, owner-authorized run)

### GitHub ruleset (applied 2026-07-19T17:22-04:00)
- Ruleset id **19180393** `w2-main-required-gates`, enforcement **active**, target
  `refs/heads/main`, `bypass_actors: []` (`current_user_can_bypass: never`).
- Rules: deletion blocked; non-fast-forward blocked; pull_request required (0 approvals,
  stale-review dismissal on push); required status checks (strict up-to-date policy):
  `eval-tier1`, `quality-security-contracts / ruff-mypy-coverage`,
  `quality-security-contracts / pip-audit`, `quality-security-contracts / bandit-semgrep`,
  `quality-security-contracts / openapi-bruno-phi-corpus`.
- Export: `gh api repos/worldofhacks/openemr-base-clean/rulesets/19180393`
  (also html: https://github.com/worldofhacks/openemr-base-clean/rules/19180393).
- First protected-flow merge: PR #24 → merge commit `43605c2` (the green drill of the
  phase-2 runbook records the block/merge pair separately).

### GitLab (project 1537, alexander.miller/openemr-base-clean; applied 2026-07-19)
- Protected branch `main`: push **Maintainers** (id 40), merge **Maintainers**,
  `allow_force_push: false`. Note: found ALREADY protected at Maintainers/Maintainers
  (audit D7 could not verify this; now confirmed via API); briefly set to push=No one,
  then restored to Maintainers because the submission mirror is updated by direct
  maintainer pushes — "No one" would freeze the mirror.
- Merge checks: `only_allow_merge_if_pipeline_succeeds: true`,
  `allow_merge_on_skipped_pipeline: false`.
- Exports (API responses captured at apply time): protected-branch and merge-check
  JSON archived in this commit's message context and reproducible via
  `GET /api/v4/projects/1537/protected_branches` + `GET /api/v4/projects/1537`.

## C02 phase 2 — applied config + drill evidence (2026-07-19)

- Ruleset 19180393 now requires SIX contexts on `main` (phase-2 JSON committed at
  `docs/week2/evidence/c02/github-ruleset-main-phase2.json`; live export via
  `gh api repos/worldofhacks/openemr-base-clean/rulesets/19180393`): `eval-tier1` +
  `quality-security-contracts / {ruff-mypy-coverage, pip-audit, bandit-semgrep,
  openapi-bruno-phi-corpus, image-build-smoke}`; strict up-to-date policy; PR-only
  changes; no deletion; no force-push; `bypass_actors: []`.
- **Red drill (GitHub):** https://github.com/worldofhacks/openemr-base-clean/pull/37 —
  eval-tier1 red run
  https://github.com/worldofhacks/openemr-base-clean/actions/runs/29706216600 ;
  merge refused by base-branch policy (transcript in W2_EVIDENCE_INDEX.md).
- **Green drills (GitHub):** train merges #24/#26/#31/#35/#30/#28/#27/#25/#36/#29 —
  each merged post-checks through the ruleset; merge commits listed in
  W2_EVIDENCE_INDEX.md.
- **Red MR (GitLab):**
  https://labs.gauntletai.com/alexander.miller/openemr-base-clean/-/merge_requests/2 —
  blocked by "Pipelines must succeed" (pipeline 15838).
- Tier-2 exact-SHA chain unchanged and observed failing closed on every main push
  pending the owner's live-gate mint (W2-O4).
