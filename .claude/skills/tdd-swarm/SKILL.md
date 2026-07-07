---
name: tdd-swarm
description: Orchestrate parallel sub-agents through ticketed, test-driven development. Use when turning a PRD, epic, or feature list into implemented code — break work into tickets, generate frozen tests first, dispatch implementation sub-agents in dependency waves on isolated branches, and gate every ticket and wave with independent verification agents. Triggers include "run tdd-swarm", "execute this PRD", "ticket this epic", "build this with sub-agents", "implement with TDD loop".
---

# TDD Swarm

Turn a PRD into merged, tested code via a factory of sub-agents. Tests are written first by a dedicated agent and frozen. Implementers loop until green. Nothing merges without passing quality gates verified by agents that did not write the code.

**Core principles**
1. **Iron Law**: no production code without a failing test first. Code written before its test gets deleted, not adapted.
2. **Separation of powers**: the agent that implements never writes, edits, or approves its own tests or review. Test Agent owns `tests/`; Implementation Agent owns `src/`; verification agents own judgment.
3. **Trust nothing**: the orchestrator re-runs gates itself before accepting any DONE report. Sub-agent self-reports are claims, not evidence.
4. **Waves unlock waves**: downstream tickets stay locked until every gate of the current wave passes.

## Roles

| Agent | Owns | Never does |
|---|---|---|
| Planner | PRD → tickets, dependency graph, wave plan | Writes code |
| Test Agent | Failing tests per ticket (unit + integration + e2e as scoped) | Touches `src/` |
| Implementation Agent | Minimal code to green, on its ticket branch | Edits tests, approves itself |
| Reviewer Agent | Strict senior code review vs ticket DoD, file:line evidence | Rubber-stamps |
| Security Agent | Vulns, unsafe patterns, secrets, dependency risk | — |
| Integration Agent | Wave merge, repo-level gates, cross-ticket compatibility | Fixes tickets itself (files fix tickets instead) |
| Performance Agent | Wave performance smoke vs thresholds | — |

Dispatch prompts and return contracts: `references/agent-prompts.md`. Always specify the model per dispatch: cheap for mechanical tickets, standard for integration work, most capable for planning, review, and adjudication.

## Branch model

```
main (protected — no direct pushes, moves only by owner-approved PR)
  └── swarm/<epic-slug>            one per feature build / epic
        ├── ticket/T-001-<slug>    one per ticket, one per sub-agent (own worktree)
        ├── ticket/T-002-<slug>
        └── ...
```

Ticket branches merge into the swarm branch at wave review. The swarm branch reaches main only through a PR that the **owner reviews and approves — the swarm never merges to main itself**, even with every gate green. Recommended GitHub settings on the target repo: protect `main` (require PR, require passing status checks, no force pushes) and wire the repo gate suite into CI so the PR's checks re-verify what the Integration Agent ran.

## Workflow

### Phase 0 — Preconditions
- Git repo with clean status; baseline test suite green (record the count).
- PRD or epic description exists. If not, stop and produce one with the human first.
- **Build posture — always asked, never assumed.** Ask the human: `production-grade` (all gates, auth/validation/observability in scope) or `mvp` (perf/memory thresholds and non-critical gates deferred, each deferral written down). Record in `.tdd-swarm/posture.md`. Gates marked posture-gated in `references/quality-gates.md` read this file.
- Gate commands mapped for this repo (see `references/quality-gates.md`) and runnable.
- Create integration branch `swarm/<epic-slug>` off main. Create ledger `.tdd-swarm/progress.md` and lessons file `.tdd-swarm/LESSONS.md`.

### Phase 1 — Plan
Planner decomposes the PRD into small tickets (each ≤ ~half a day of agent work, one concern, explicit file scopes, acceptance criteria as Given/When/Then with stable ids `AC-1…`, dependencies). Format: `references/ticket-format.md`. Every ticket carries `traces_to:` — the PRD requirement / architecture section / use case it serves. **A ticket that traces to nothing gets deleted; a requirement no ticket traces to is a planning gap.** Write one file per ticket in `tickets/`, build `TICKETS.md` index, mirror to GitHub Issues.

Compute waves: Wave N = all tickets whose dependencies are complete. Two tickets in the same wave must not share file scopes — if they do, add a dependency or merge them.

**Adversarial plan review**: before the human sees it, dispatch a Plan Reviewer (different model than the Planner if available) to attack the plan — missing requirements, hidden ticket couplings, wrong wave assignments, untestable criteria. Planner fixes findings.

**CHECKPOINT: present the ticket list, traceability map, dependency graph, and wave plan to the human for approval before any code.**

### Phase 2 — Tests first (per wave, RED)
At the start of each wave, for each ticket: create branch `ticket/<id>-<slug>` off the integration branch (own worktree: `git worktree add ../wt-<id> ticket/<id>-<slug>`), then dispatch a Test Agent to write failing tests from the acceptance criteria.

- Every test is tagged to its criterion (`spec(T-014:AC-2)` in name or comment). Spec-lint (see gates) fails if any criterion has no test or any new test cites no criterion.
- Verify each test **fails for the right reason** (missing feature — not a typo, import error, or setup crash). A test that errors instead of failing gets fixed before proceeding.
- **Test-design review before freezing.** Dispatch a Reviewer against the tests only: do they encode every acceptance criterion, would a lazy implementation pass them, do they assert behavior or implementation detail? Findings go back to the Test Agent. Tests freeze wrong if nobody reviews them — this is the last cheap moment to fix a bad test.
- Commit reviewed tests to the ticket branch. Tests are now **frozen**: implementers are dispatched without permission to edit test files (enforce via agent tool/prompt restrictions; optionally a PreToolUse hook — see `references/agent-prompts.md`).
- Tests are written at wave start, not all upfront — interfaces evolve between waves and stale tests are worse than late tests.
- **Non-deterministic surfaces (LLM behavior, external services, concurrency) don't get fake deterministic tests.** The ticket's test plan splits: deterministic contracts (tool schemas, parsing, authz, routing) → normal frozen tests; LLM-behavior claims (answer quality, grounding, refusals) → **eval cases** in the eval harness with a graded threshold, marked `eval` in the test plan. Never mock an LLM response and call the assertion on your own mock a behavior test.

### Phase 3 — Implement (GREEN loop)
Dispatch one Implementation Agent per ticket in the wave, in parallel, each confined to its worktree, branch, and file scopes. Each agent loops:

```
implement → run local gates → all pass? commit & report DONE
                    ↓ no
              fix immediately (cannot continue to anything else)
```

Local gates (full list in `references/quality-gates.md`): format, lint, typecheck, unit tests, new tests present, coverage not reduced, no TODOs, no debug logging, docs updated.

- **Max 3 iterations of the full loop.** Attempts 1–2 on the assigned model; attempt 3 re-dispatched on a more capable model with the failure history. Still red → ticket status `blocked`, escalate to the human with the failure table. Never loop silently past the cap.
- Implementer believes a test is wrong? It does not touch the test. It returns `BLOCKED(TEST_DISPUTE)` with reasoning; the orchestrator sends the dispute + reasoning to a fresh Test Agent to adjudicate, and to the human if they disagree.

Status protocol (every implementer report): `DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED(reason)`. Handle: DONE → verify; DONE_WITH_CONCERNS → read concerns before verifying; NEEDS_CONTEXT → supply and re-dispatch; BLOCKED → change something (context, model, ticket split) — never re-dispatch unchanged.

### Phase 4 — Ticket verification
On each DONE ticket, before it counts:
1. **Orchestrator re-runs local gates itself** in the ticket worktree. Any failure → back to the implementer (counts toward the cap).
2. Dispatch **Reviewer Agent** with the ticket file, diff package, and DoD — verdict on spec compliance AND code quality, findings with file:line evidence.
3. Dispatch **Security Agent** on the diff.
4. Critical/Important findings → fix dispatch to the implementer → re-review. Minor findings → recorded in the ledger for wave review.

Ticket passes all three → status `review-passed`, ledger line appended, GitHub issue updated.

### Phase 5 — Wave review
When every ticket in the wave is `review-passed`, dispatch the Integration Agent:
- Merge ticket branches into the integration branch (ticket order = dependency order).
- Run repo gates: full build, affected integration tests, API compatibility, dependency graph validation, migration validation, security + secret scan, regression suite, performance smoke vs thresholds (Performance Agent).
- **Architecture-drift check**: compare what the wave actually built against the architecture doc — undeclared dependencies, crossed subsystem boundaries, contracts changed without a migration ticket. Drift = a finding, either fixed or the architecture doc amended with the human's sign-off, never silently absorbed.
- Any failure → Integration Agent files a fix ticket (normal ticket, same rules, assigned into a repair wave). It does not patch code itself.
- All pass → tear down wave worktrees, mark wave complete in ledger, **unlock next wave**.

### Phase 6 — Complete: PR to main, owner approves
After the final wave: dispatch a whole-branch Reviewer (most capable model) on swarm-branch-vs-main. Resolve findings, run the full repo gate suite once more, then open a PR from `swarm/<epic-slug>` to main. PR body: ticket list with issue links, gate evidence (commands + results), blocked-ticket history, and known limitations.

**The owner performs the final review and approves/merges the PR. The orchestrator's job ends at "PR open, checks green" — it never merges to main.** After the owner merges: close GitHub issues, delete ticket branches, final ledger entry.

## What reaches the human (escalation taxonomy)

Only four things interrupt the owner — everything else the orchestrator settles agent-to-agent:

1. **Safety/correctness design questions** — anything touching auth, data integrity, PHI/PII, money
2. **Blocked tickets** — cap exhausted or unresolvable TEST_DISPUTE, with failure history attached
3. **Deferral approvals** — a gate or DoD item an agent proposes to skip (posture change, threshold change)
4. **Load-bearing architecture decisions** — drift findings, contract changes, new dependencies

Progress updates, passing gates, and routine review findings do NOT ping the human; they go to the ledger. Ping fatigue trains owners to ignore escalations — protect the channel.

## Lessons loop

`.tdd-swarm/LESSONS.md` accretes what the swarm learns: every blocked ticket's root cause, every wave-review failure, every adjudicated test dispute gets one entry (pattern → why → what to do instead). The Planner reads it before decomposing; every implementer dispatch names it as required reading. Repos keep it across epics — the second swarm run should be smarter than the first.

## Durable progress

Append to `.tdd-swarm/progress.md` on every state change: `Ticket <id>: <status> (commits <base7>..<head7>, gates <pass/fail>, wave <n>)`. On session start or after compaction, read the ledger and `git log` before doing anything — tickets marked complete are complete; resume at the first incomplete ticket. Never re-dispatch finished work.

## Red flags — stop and fix the process

- Implementation before its failing test exists, or a test that never failed
- Freezing tests that no independent reviewer looked at
- Accepting a DONE without re-running gates yourself
- Implementer editing anything under the test paths
- Two same-wave agents sharing a file scope
- A 4th quiet retry after the cap
- Reviewer prompt that pre-judges findings ("don't flag X")
- Dispatching from memory after compaction instead of the ledger
- Merging a wave with an open Critical/Important finding
- Pushing to or merging main yourself — main moves only by owner-approved PR

## References

- `references/agent-prompts.md` — dispatch templates + return contracts per role
- `references/ticket-format.md` — ticket file schema, TICKETS.md index, GitHub Issues sync
- `references/quality-gates.md` — local gates, repo gates, per-repo command mapping
