# C02 phase 1 — owner runbook: enable required gates on GitHub + GitLab

**Owner-only (plan §4c #1).** Agents prepared this; only the repository owner holds admin.
Everything below is ready to paste. After applying, run the *Evidence export* block and
archive its output in `docs/week2/evidence/W2_CI_EVIDENCE.md` (closes the Track A
"C02 phase-1 protection enabled" box; the red/green merge drills are phase 2).

Check names verified live on PR #24's head SHA (`a48987f`), 2026-07-19. `agent-eval-gate`
runs on every `pull_request` with no path filter, so all five required contexts exist on
every PR — including docs-only PRs (fail-closed).

## 1. GitHub — ruleset on `main` (~2 minutes)

From the repo root (as `worldofhacks`, admin):

```bash
gh api repos/worldofhacks/openemr-base-clean/rulesets -X POST \
  --input docs/week2/evidence/c02/github-ruleset-main.json
```

What it enforces: no branch deletion; no force-push; changes land only via PR; five
required checks — `eval-tier1` plus the four `quality-security-contracts / *` jobs —
with the up-to-date-branch (strict) policy so the evaluated SHA is the merged SHA;
`bypass_actors` is empty (nobody, including admins, can bypass — the plan's
"restrict bypass"). If you want an emergency escape hatch, add a bypass actor for the
org-admin role afterwards and document it in W2_CI_EVIDENCE.md as the documented
emergency role.

Notes:
- `required_approving_review_count` is 0: the eval gates are the blocking mechanism and
  you are a solo maintainer. Raise to 1 if you want review-required too (you would then
  need a second account or to allow self-approval via bypass).
- `eval-tier2-live` is deliberately NOT in phase 1 — it does not run in `pull_request`
  context (protected-environment secrets). Its exact-SHA requirement lands in phase 2
  via the fail-closed bridge (`.github/scripts/verify_github_gate.py`).

## 2. GitLab — protected branch + required pipeline (~3 minutes)

Project: `https://labs.gauntletai.com/alexander.miller/openemr-base-clean`
(URL-encoded id: `alexander%2Fmiller...` — use the numeric project ID from the project
home page if the path form is rejected; placeholder `<PROJECT_ID>` below).

```bash
GL=https://labs.gauntletai.com/api/v4
PROJ=alexander.miller%2Fopenemr-base-clean   # or numeric <PROJECT_ID>
# a) Protect main: nobody pushes directly; maintainers merge; no force-push
curl -sf -X POST "$GL/projects/$PROJ/protected_branches" \
  -H "PRIVATE-TOKEN: $GITLAB_ADMIN_TOKEN" \
  --data "name=main&push_access_level=0&merge_access_level=40&allow_force_push=false"
# (if main is already protected with weaker settings, delete and re-create:
#  curl -sf -X DELETE "$GL/projects/$PROJ/protected_branches/main" -H "PRIVATE-TOKEN: ..." )
# b) Merges require a green pipeline (the .gitlab-ci.yml eval stage)
curl -sf -X PUT "$GL/projects/$PROJ" \
  -H "PRIVATE-TOKEN: $GITLAB_ADMIN_TOKEN" \
  --data "only_allow_merge_if_pipeline_succeeds=true&allow_merge_on_skipped_pipeline=false"
```

UI equivalents: Settings → Repository → Protected branches; Settings → Merge requests →
Merge checks → "Pipelines must succeed".

Phase-2 dependency (do NOT do now, tracked in W2-O4): the `github-exact-sha-bridge` job
needs the masked read-only GitHub status token and `GITHUB_TIER2_ARTIFACT_DIGEST` CI
variables provisioned before it can verify the live Tier-2 result; until then it fails
closed on main, which is the intended posture.

## 3. Evidence export (run immediately after applying)

```bash
{
  echo "## C02 phase-1 config exports ($(date -u +%Y-%m-%dT%H:%MZ))"
  echo '### GitHub rulesets'; gh api repos/worldofhacks/openemr-base-clean/rulesets
  echo '### GitHub ruleset detail'
  gh api repos/worldofhacks/openemr-base-clean/rulesets --jq '.[].id' | while read -r id; do
    gh api "repos/worldofhacks/openemr-base-clean/rulesets/$id"
  done
  echo '### GitLab protected branches'
  curl -sf "$GL/projects/$PROJ/protected_branches" -H "PRIVATE-TOKEN: $GITLAB_ADMIN_TOKEN"
  echo '### GitLab merge-check settings'
  curl -sf "$GL/projects/$PROJ" -H "PRIVATE-TOKEN: $GITLAB_ADMIN_TOKEN" | \
    python3 -c 'import json,sys;d=json.load(sys.stdin);print({k:d[k] for k in ("only_allow_merge_if_pipeline_succeeds","allow_merge_on_skipped_pipeline")})'
} > /tmp/c02-phase1-exports.txt
```

Paste `/tmp/c02-phase1-exports.txt` into `docs/week2/evidence/W2_CI_EVIDENCE.md` under a
"C02 phase 1" heading (sanitize nothing — these are configuration exports, no secrets —
but do NOT paste tokens).
