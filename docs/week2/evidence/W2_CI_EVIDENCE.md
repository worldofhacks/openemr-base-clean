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

## Required red evidence

Before submission, record throwaway-branch SHA, run URL, category arithmetic, and trigger for
each of these cases. Do not retain provider transcripts or fixture-derived content.

| Drill | Expected trigger | SHA / run URL |
|---|---|---|
| malformed extraction schema | deterministic schema below 100% | owner CI run required |
| incomplete CitationV2 | deterministic citation below 100% | owner CI run required |
| unsafe cross-patient action | deterministic safety below 100% | owner CI run required |
| short-PHI leak | no-PHI category below 100% | owner CI run required |
| factual failures crossing threshold/delta | below 90% or more than five points below baseline | owner CI run required |

The reviewed baseline PR, protected secret-bearing run, branch-protection settings, and run
URLs are external owner actions. This ledger must not claim them until GitHub/GitLab supply
the evidence; canonical green evidence is appended only after all red drills are retained.
