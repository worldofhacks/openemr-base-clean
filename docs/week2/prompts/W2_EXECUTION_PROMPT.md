# Claude Code execution prompt — AgentForge W2 remediation plan

Versioned handoff prompt (owner-approved 2026-07-19). Paste the block below into Claude Code
from the `openemr-base-clean/` repo root. Companion spec: `docs/week2/W2_IMPLEMENTATION_PLAN.md`.

---

# Mission: Execute the AgentForge W2 remediation plan to submission-ready

You are executing `docs/week2/W2_IMPLEMENTATION_PLAN.md` in this repository
(`openemr-base-clean/`) to completion. That plan is the SPEC — this prompt tells you how to
execute it, not what the tasks are. The submission is already late: correctness beats speed.
A working, honest, evidence-backed submission is the only acceptable outcome.

## Read these, in order, before any code

1. `docs/week2/W2_IMPLEMENTATION_PLAN.md` — the entire plan. §4d is your binding execution
   protocol. §4c lists owner-only actions you must NEVER attempt yourself.
2. `docs/week2/Week_2_AgentForge.pdf` — the requirements document. On ANY conflict between
   plan text and PDF, the PDF wins: stop, record the discrepancy in the plan, then proceed.
3. `docs/week2/W2_gap-audit.md` — findings + the 103-row RTM your tasks close.
4. `.tdd-swarm/gates.md` — the TDD gate discipline (Tier 1/Tier 2).
5. `docs/week2/W2_DECISIONS.md` (G-D2 at the end) and `docs/week2/W2_DEVLOG.md` (G-fix entry)
   — the two owner decisions that changed reader/VLM behavior on 2026-07-19; they explain
   test-assertion changes you will encounter. `TICKETS.md` hard rules and
   `docs/week2/W2_TIER2_CI_POLICY.md` still bind.

## Binding execution loop (plan §4d — every task, no exceptions)

- BEFORE code: re-read the task's cited PDF sections + RTM rows; restate acceptance criteria
  in the PDF's own words in the PR description.
- RED FIRST: write the failing test pinning the acceptance criterion before implementing.
  Frozen tests are contracts — never weaken, never delete, never go green by editing an
  assertion. A genuine behavior change requires an owner-recorded decision in
  W2_DECISIONS.md/W2_DEVLOG.md FIRST (G-D2/G-fix entries are the template).
- BEFORE any PR: full suite `cd agent && .venv/bin/pytest -q` must be ≥ the 936-passed /
  5-skipped baseline, AND the recorded 50-case gate must be green. Any rubric-category delta
  is stop-and-diagnose, never a judgment call.
- EVIDENCE: a §8 checkbox may be checked only with a link to the commit / CI run / production
  probe at the exact SHA. Unverifiable = unchecked. Update the checklist and W2_DEVLOG.md as
  you complete each task.
- SINGLE-WRITER: own only your task's files (§4b conflict table). PRs contain only their own
  diff. Re-read any shared doc immediately before writing — parallel-session drift has
  corrupted this tree twice already.

## Current tree state (verified 2026-07-19 — handle in W00 before anything else)

The working tree holds THREE uncommitted concerns that W00 separates:
(a) the audit/plan doc set; (b) G-D2 code edits (reader/test/gates/pyproject — see W00's file
list); (c) the R08 extraction-robustness fix, already implemented and 16-tests-verified but
NOT full-suite-verified: `agent/app/llm/vlm.py`, `agent/app/ingestion/image_reader.py`,
`agent/app/ingestion/reader.py` (also carries G-D2 docstrings — one review branch, two
commits is acceptable), `agent/tests/test_vlm_evidence_gate.py`,
`agent/tests/test_image_intake_robustness.py`, plus DEVLOG/DECISIONS entries.
Local tool state (`.claude/`, `.agents/`, `AGENTS.md`) is NEVER committed.

## Execution order

1. **W00** (tree hygiene) → **PR 0b / R08** (run full suite + recorded gate over the
   extraction fix FIRST — if anything is red, diagnose there before the train moves).
2. **Track A in parallel** (§4 Track A + §8 Track A boxes): C02 phase-1 prep, R07, S01 first
   pass, E01-lite, O02-lite, D01-lite known-gaps banner.
3. **PR train** (§6): R01 → R02 (single-owner, the long pole — staff it first) → 2b/R09 →
   R03/R04/R05/R06/C01 in parallel lanes per §4b conflicts → C02 phase 2 → E01 → REL1.
4. **Evidence operations** against the accepted SHA: O01, O02, O03, S01 final (the citations
   beat MUST show click-to-source with the visible bbox overlay), D01 full sync.
5. **STOP before V01.** V01 requires cold eyes — a fresh session that did not implement.
   When everything through D01 is checked, report completion to the owner and instruct them
   to launch V01 as a separate session. Never self-grade the verdict flip.

Sub-agents are permitted per §4a (dispatch protocol); keep R02 single-owner and
`evals/recordings/` exclusively R02's.

## Owner-action pauses (§4c — prepare, then stop and ask; never do)

Branch-protection host configuration (prepare configs, owner applies with admin), Tier-2
credential provisioning, grader/P2 communication (A01 drafts, owner sends), demo-link
publishing approval, REL1 go/no-go, the late-submission note. When a task blocks on one of
these, park it, state exactly what the owner must do, and continue on unblocked lanes.

## Hard boundaries

- No OpenEMR PHP/routes/schema edits; no write-path enablement beyond what a task specifies.
- Synthetic, non-clinical data only — everywhere, including fixtures, traces, and the video.
  No PHI, no secrets, in any log, commit, artifact, or CI output.
- Do not start `docs/week2/W2_BACKLOG_CHANGE_REQUEST_G.md` work — it is deferred behind
  submission and must not enter the PR train before the P0s merge.
- Do not add dependencies without task backing (the PyMuPDF ban was removed by G-D2, but no
  submission task requires adding it — don't).
- Deployed probes must cache-bust: `/health?cb=<unique>`, `/ready?cb=<unique>`.
- Commits: Conventional Commits, with trailer `Assisted-by: Claude Code`.

## Stop conditions (halt, record, ask the owner)

An unexpected frozen-test failure · any golden-category delta · any PDF-conformance question
· any file outside your task's scope · anything that would require weakening a test or gate
· any owner-action item. The grader will deliberately introduce a regression to test the CI
gate (PDF p.5 HARD GATE) — the discipline above is what makes it bounce.

## Definition of done (for this session)

Every §8 box through D01 is checked with evidence links, except boxes blocked solely on §4c
owner actions (enumerate those in your final report). Final report format: per-task status +
evidence link + SHA, the owner-action punch list, and the instruction to launch V01 fresh.
