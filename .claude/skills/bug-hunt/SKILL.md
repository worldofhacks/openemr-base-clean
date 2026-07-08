---
name: bug-hunt
version: 1.0.0
description: >-
  Root-cause debugging with a reproduce-first discipline: capture the bug as a
  failing test before fixing, localize to the true cause, fix through the test
  loop, verify, and pin a permanent regression test. Two modes — build (a
  deterministic bug you can run locally) and incident (a production/observability
  symptom, driven from traces and correlation IDs). Never goes green by weakening
  or deleting a test. For LLM/eval behavior failures use /eval-triage. Invoke on
  "bug-hunt", "debug this", "find the root cause", "this test is failing", or a
  reported production incident.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion
---

# bug-hunt

The discipline that separates this from poking at it: root cause over symptom ·
reproduce with a failing test before touching the fix · leave a permanent
regression pin · never weaken a test to pass.

---

## 1 · Frame + pick a mode

- Restate the symptom: expected vs actual, trigger, repro steps known so far.
- Classify: logic · wiring/config · data · performance · flaky · regression · security.
- Ask the user (one question, with a recommendation):
  - **Build mode** (default) — deterministic bug in code runnable locally.
  - **Incident mode** — a live-system symptom (error rate, alert, bad response in
    prod) that may not reproduce locally.

## 2 · Reproduce

**Build mode:** write the smallest failing test that captures the bug, using the
repo's own test runner. Watch it fail before any fix — this test is the permanent
regression pin. No reproduction, no fix.

**Incident mode:** start from the trace, not the code. Pull the correlation ID for
a failing request and walk its trace end-to-end (tool calls, latencies, verdicts,
errors). Capture the bug as the nearest deterministic artifact available: a failing
test against the same inputs if possible; otherwise an eval case or a documented
trace-level assertion — and say explicitly which you used. Never fake a
deterministic test around a genuinely non-deterministic surface.

## 3 · Localize

Shrink the search space with the cheapest tools first: the failing test's stack ·
`git log`/`git bisect` when "it used to work" · logs filtered by correlation ID ·
binary-search instrumentation. State the suspected component before opening it —
prediction sharpens reading.

## 4 · Root cause

Distinguish the *site* of the failure from the *cause* of the failure. Ask "why is
that value wrong?" until the answer is a decision, not another symptom. Verify the
cause: make a prediction the hypothesis implies, test it. An unproven cause is a
hypothesis — say so rather than fixing on faith.

## 5 · Fix through the loop

- Fix at the causal site, smallest change that resolves it.
- Failing test → green. Full suite → green. If the bug touched a contract (schema,
  tool I/O, verifier rule), run the eval suite too.
- If other call sites share the broken pattern, list them — fixing one of N
  instances is a finding, not a completion.

## 6 · Verify + pin

- The reproduction test stays in the suite permanently, named for the behavior it
  guards, with a one-line comment on the failure mode.
- Incident mode: confirm against the live system (the alert clears, the trace shape
  is correct) and note the confirming correlation ID.

## 7 · Compound (optional, offer it)

One dated line in `docs/planning/LESSONS.md`: bug class → root cause → the rule
that prevents recurrence. If the bug class is mechanically detectable, propose the
lint/test/eval that would have caught it.

---

## Hard rules

- Never fix before reproducing (or explicitly invoking the incident-mode escape hatch).
- Never go green by deleting, skipping, or weakening a test.
- Never stop at the symptom site — the fix lands at the cause.
- Never leave the fix unpinned — every bug becomes a regression test or a documented
  trace assertion.
