# C02 phase 2 — required-gate completion + red/green merge drills (owner-executed)

**Prerequisite:** phase 1 applied (`W2_C02_PHASE1_RUNBOOK.md`) and C01 (PR #29) merged so
the `quality-security-contracts / image-build-smoke` check exists on PRs.

## 1. GitHub — extend the ruleset (phase 2)

Update the phase-1 ruleset to also require C01's image smoke:

```bash
RID=$(gh api repos/worldofhacks/openemr-base-clean/rulesets --jq '.[] | select(.name=="w2-main-required-gates") | .id')
gh api "repos/worldofhacks/openemr-base-clean/rulesets/$RID" -X PUT \
  --input docs/week2/evidence/c02/github-ruleset-main-phase2.json
```

**Tier-2 exact-SHA chain (already wired in code; document, don't re-invent):** PRs are
blocked by the five/six required checks (Tier 1 + quality + image smoke).
`eval-tier2-live` cannot run in PR context (protected-environment secrets; fork policy
clauses 1–3). The exact-SHA live requirement binds at the NEXT stage: on every `main`
push, `eval-tier2-live` must reuse a green exact-SHA live result
(`reuse_live_eval.py` — fail-closed, observed failing closed on every docs push
2026-07-19), and `agent-deploy` deploys only on that workflow's success. To mint the
live result for an accepted SHA: `gh workflow run agent-eval-gate -f exact_sha=<sha>`
(protected `eval-tier2-live` environment approval + `ANTHROPIC_API_KEY` — owner action
W2-O4), or push `tier2/<sha>`.

## 2. GitLab — bridge credentials (owner action W2-O4)

The required pipeline already contains `eval-tier1` + `github-exact-sha-bridge` (main
only). The bridge fails closed until these CI variables exist (Settings → CI/CD →
Variables, masked):

- `GITHUB_STATUS_TOKEN` (read-only fine-grained token able to read checks/artifacts on
  `worldofhacks/openemr-base-clean` — name per `.github/scripts/verify_github_gate.py`;
  verify the exact env var name the script reads before creating it)
- `GITHUB_TIER2_ARTIFACT_DIGEST` — the SHA-256 of the accepted `results-tier2.json`
  artifact (recorded in `W2_CI_EVIDENCE.md` at E01)

Fork-secret and stale-result handling are already documented in
`docs/week2/W2_TIER2_CI_POLICY.md` (frozen) — cite it in the evidence, do not restate.

## 3. Red / green merge drills (one per host; archive URLs in W2_CI_EVIDENCE.md)

**GitHub red:** open a draft PR from an existing red drill branch (choose one that is
mergeable against current main — `drill/w2-red-schema` … or the new
`drill/w2-red-retrieval-{ranking,availability}` once R02 merges; rebase the drill
branch first if needed):

```bash
gh pr create --head drill/w2-red-schema --base main --draft \
  --title "drill(w2): red candidate — must be unmergeable" \
  --body "C02 phase-2 red merge drill. DO NOT MERGE. Expect required checks red + merge blocked."
# after checks: capture the block
gh pr view <N> --json mergeable,mergeStateStatus,statusCheckRollup > /tmp/red-drill.json
# expect mergeStateStatus: BLOCKED; attempt merge to capture the refusal:
gh pr merge <N> --squash 2>&1 | tee /tmp/red-drill-merge-refusal.txt   # must fail
gh pr close <N>
```

**GitHub green:** the next real green PR in the train (e.g. PR #24) merging through the
ruleset IS the green drill — capture `gh pr view <N> --json mergeStateStatus` (CLEAN)
plus the merge commit URL.

**GitLab red/green:** push a temporary branch cherry-picking the red drill commit, open
an MR, capture the failed pipeline + blocked merge (API: `merge_status`), close;
then capture any green MR merging with "Pipelines must succeed" on.

## 4. Evidence capture

Append to `docs/week2/evidence/W2_CI_EVIDENCE.md`: ruleset export (phase-2 JSON id +
`gh api .../rulesets/$RID`), the red PR/MR URLs + refusal outputs, the green merge URLs,
and the GitLab variable names (NEVER values). That closes AF-P0-01 + AF-P1-11 evidence.
