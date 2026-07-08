---
name: eval-triage
version: 1.0.0
description: >-
  Participatory walkthrough for diagnosing failing evals in an LLM/agent app:
  reproduce → compare a passing case → bisect the pipeline to the first divergence →
  categorize the failure → propose a minimal fix + verification plan. A coach, not
  an autopilot: smallest diagnostic first, ranked hypotheses, a pause at every
  phase; proposes fixes, never auto-applies them, and never edits an eval to green
  without evidence and an explicit user decision. For deterministic code bugs use
  /bug-hunt. Invoke on "eval-triage", "triage this failing eval", "this eval is
  failing", "the eval suite went red".
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion
---

# eval-triage

Eval failures in an agent system have many possible parents: the eval itself, the
judge/assertion, the prompt, retrieval/tools, verification rules, state carry-over,
model nondeterminism, or output parsing. The job is to find *which one*,
systematically, with the smallest possible diagnostics — not to make red turn green.

**Cadence at every phase:** STATE the goal in plain terms → run/suggest the smallest
diagnostic (prefer commands the user runs) → report FINDINGS + an updated hypothesis
list ranked by likelihood × cost-to-test → PAUSE for the user's call before advancing.
Two gates always pause even if told to move faster: the app-vs-eval verdict, and the fix.

---

## 1 · Frame

- Which eval(s), since when, every time or flaky? One failure or a cluster?
- What changed last (prompt, model version, tool code, verifier rule, fixture, seed
  data)? `git log` around the eval and the code it exercises.
- Classify the eval: deterministic assertion, golden answer, judge-scored, or
  invariant check. The grading mechanism is a suspect too.

## 2 · Reproduce

- Run the single failing case in isolation with full output captured. If it needs
  N runs to fail, record the rate — flakiness is itself a finding (nondeterminism
  category), not noise to push through.
- Capture the full trace for the failing run (correlation ID → the observability
  trace: tool calls, inputs/outputs, model spans, verification verdicts).

## 3 · Compare a passing sibling

Find the nearest passing case (same use case, different fixture — or same fixture,
earlier commit). Diff everything cheap first: fixture data, prompt rendered, tool
results, retrieved records, verifier verdicts. The first divergence localizes the
failure to a pipeline stage.

## 4 · Bisect the pipeline

Walk the stages in order and check each output against expectation:

```
fixture/seed data → retrieval/tool calls → normalized evidence
  → prompt assembly → model output (structured claims) → verification rules
  → rendered response → eval assertion/judge
```

At each stage ask: is this output already wrong? First wrong stage owns the bug.
Common verdicts by stage: fixture drift (seed data changed) · tool regression
(wrong filter/field) · evidence normalization gap · prompt/template change · model
nondeterminism or drift · verifier rule too strict/too loose · parsing/schema
mismatch · eval assertion wrong or judge rubric ambiguous.

## 5 · The app-vs-eval gate (always pauses)

Present the evidence and put the verdict to the user: **is the app wrong, or is the
eval wrong?** An eval may only be changed when the evidence shows the *expectation*
is wrong — and that decision is the user's, recorded in the eval file as a dated
comment. Weakening an eval to pass is never a fix.

## 6 · Propose the minimal fix (always pauses)

- Smallest change that addresses the root cause at the stage that owns it.
- Verification plan: the failing case green · its passing siblings still green ·
  the full suite run · if the fix touched a verifier rule or prompt, run the
  adversarial subset too.
- If the failure exposed a missing eval case (a gap, not a bug), propose the new
  case with its boundary/invariant/regression tag and the failure mode it guards.

## 7 · Record

Append a dated entry to `docs/planning/EVAL_LOG.md`: symptom → stage → root cause →
fix → new/changed evals. Two lines is fine. This log is interview material — it
proves the eval suite is used, not just installed.

---

## Hard rules

- Never edit an eval to make it pass without the §5 gate.
- Never fabricate a root cause to keep moving — an unproven cause is a hypothesis.
- Never fix at a downstream stage what broke upstream (no prompt patches for tool bugs).
- Flaky ≠ ignorable: nondeterminism gets a rate measurement and a decision, not a retry loop.
