# W2 Tier-2 CI Secret Policy (frozen)

**Status: FROZEN (locked-decision).** Produced by ticket **W2-M24** for
consumption by **W2-M19/W2-M20** (the Tier-1/Tier-2 CI jobs and branch
protection). Sources: `W2_ARCHITECTURE.md` §6a (fork-PR secret policy,
enforcement surface) and **W2-D8** (two-tier gate: Tier 2 is the full 50-case
live-Anthropic run, PR-blocking). This is a new W2 doc — an allowed exception —
never an edit of a binding doc. The machine lint
`agent/ops/spike_tier2.py::lint_policy_doc` asserts all six clauses below;
`lint_workflows` (same module) enforces clause 2's three-way conjunction
read-only over the existing `.github/workflows/*.yml`.

Context: the fork repo `worldofhacks/openemr-base-clean` GitHub Actions secrets
are EMPTY — owner action **W2-OA2** (put `ANTHROPIC_API_KEY` into repo secrets)
is pending, noted and not blocking. Until and after W2-OA2 lands, every clause
here holds unchanged.

## Clause 1 — No repository secrets to forks

Fork pull requests receive **no repository secrets**, ever. No workflow may
expose a repository or environment secret (directly, via `env`, via inputs, or
via reusable-workflow `secrets:` passing) to a job that runs on behalf of a
fork PR. There is no "trusted fork" carve-out.

## Clause 2 — Never check out fork code under `pull_request_target`

Fork PR code is **never** checked out under a `pull_request_target` trigger.
Concretely: no `actions/checkout` step in a `pull_request_target`-triggered
workflow may set `ref` to PR-head code
(`github.event.pull_request.head.sha`, `github.event.pull_request.head.ref`,
`github.head_ref`, or any equivalent spelling). A checkout **without** a `ref`
override under `pull_request_target` checks out the trusted base repository
and is compliant. The violation is the **three-way conjunction**:
`pull_request_target` trigger **and** PR-head-code checkout **and** secrets
access. Known compliant near-miss: `.github/workflows/dependabot-auto-merge.yml`
uses `pull_request_target` **and** `secrets.AUTO_MERGE_APP_PRIVATE_KEY` but
performs **no checkout of PR code**, so it satisfies the conjunction and passes.

## Clause 3 — Forks run Tier 1 only

Fork PRs run **Tier 1 only**: the offline, secret-free checks (deterministic
tests, lints, offline eval assertions). The Tier-2 live 50-case gate never
executes with repository secrets in a fork PR context.

## Clause 4 — Maintainer reproduction for the required Tier-2 result

Before merge, a **maintainer** reproduces the **exact fork commit** on a
**trusted same-repository branch** to produce the required **Tier-2** result
(the full 50-case live run). The Tier-2 status attaches to that exact commit
SHA; **any new commit invalidates the approval/status** and requires a fresh
maintainer reproduction. No fork-context run may ever substitute for this
result.

## Clause 5 — Same-repo PRs: least-privilege environments

Same-repository PRs run Tier 2 in **least-privilege** GitHub environments with
required reviewer **approval** before secret-bearing jobs execute. No secret
**echo** into logs (no printing, no debug dumps of env), and no
**artifact retention** of secret material or raw provider transcripts — only
aggregate results are retained.

## Clause 6 — STOP escalation, never case reduction

If the Tier-2 gate fails to fit quota, runtime, or cost budgets, that is a
**STOP escalation** — a dependency problem raised to the owner — and is
**never** solved by reducing the **50** cases, sampling them down, or
bypassing/weakening the gate (locked-decision, W2-D5/W2-D8).

## Enforcement surface

- `agent/ops/spike_tier2.py::lint_workflows` — read-only lint over the
  existing workflows; fires only on the full three-way conjunction of
  clause 2 (proven against a synthetic violating fixture in
  `agent/tests/test_tier2_spike.py`; passes the dependabot-auto-merge
  near-miss).
- `agent/ops/spike_tier2.py::lint_policy_doc` — asserts this document states
  all six clauses.
- W2-M20 wires both into the CI jobs it builds and adds the trusted dry run +
  fork simulation (explicitly reassigned there by W2-M24's ticket).
