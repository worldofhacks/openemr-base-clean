---
name: arch-finalize
version: 1.0.0
description: >-
  Adversarial finalize pass: gap-audit the architecture draft and all planning
  artifacts against the PRD, resolve findings with the user, and produce the binding
  repo-root ARCHITECTURE.md that opens with a ~500-word one-page summary. Run in a
  fresh session or subagent whenever possible — the value of this pass is cold eyes.
  Never writes application code; never generates the implementation plan (that is
  /tasks-gen). Invoke on "finalize the architecture", "run the gap audit",
  "scrutinize the draft", or after /arch-draft has written docs/planning/.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion, Task, WebSearch
---

# arch-finalize

The draft is a hypothesis. This pass attacks it, patches it, and turns it into the
binding contract the build phase implements against.

```
PRD + docs/planning/* + AUDIT.md/USERS.md (if present)
  → gap audit (12 dimensions, judged against posture)
  → findings resolved with the user
  → repo-root ARCHITECTURE.md (opens with ~500-word summary, §-anchored, ADR-cited)
```

---

## 1 · Read everything (the draft is NOT the only input)

1. The PRD — ground truth for the audit. Ask for the path if not obvious.
2. ALL of `docs/planning/` — `ls` it, read every artifact: PRESEARCH, RESEARCH,
   DECISIONS, ARCHITECTURE_DRAFT, DIAGRAM_PLAN, CLAUDE_CODE_HANDOFF. The audit is
   only possible against the sources; the draft alone can't tell you what it's missing.
3. Root `AUDIT.md` and `USERS.md` if they exist — the architecture must trace to both.
4. The **build posture** recorded in the handoff (`production-grade` | `prototype`).
   The audit is judged against it: under production-grade, testing, deploy/rollback,
   and failure-mode coverage are required, not nice-to-have.

## 2 · The gap audit

Audit draft + artifacts against the PRD across these dimensions. Bucket every
finding: **critical / important / nice-to-have / proposed-edit / question-for-user.**

1. **PRD coverage** — walk the PRD itself and write a coverage table to
   `docs/planning/gap-audit.md`: one row per PRD must-have → the draft § or ADR
   covering it, or an explicit `uncovered` / `out-of-scope (reason)` tag. Never a
   blank cell. Uncovered rows go to the user as a list, not a summary.
2. Missing flows — every in-scope requirement maps to a flow (happy, degraded, ops).
3. Lifecycle states — sessions, tokens, caches, retention: created/expired/invalidated?
4. Failure modes — every external dependency has a designed failure behavior.
5. Interfaces & schemas — every tool/API boundary has a typed contract.
6. Source-of-truth clarity — for each datum, exactly one authority.
7. Unresearched dependencies — claims a decision rests on with no R# backing.
8. Inconsistent decisions — ADRs that contradict each other or the draft text.
9. Overbuilt scope — capabilities with no USERS.md use-case trace (cut or flag).
10. Trust boundaries — enforcement point named for every boundary crossing.
11. Testing & evals — eval plan covers boundary/invariant/regression + adversarial;
    happy-path-only fails.
12. Deploy/rollback/observability — deploy path, rollback mechanism, correlation
    IDs, dashboards, alerts all specified.

Use subagents (Task) to fan out dimensions when the artifact set is large.

## 3 · The user gate

Present findings compactly: critical first, then important, each with a proposed
edit. Load-bearing changes (anything that would alter an ADR's decision, a trust
boundary, or scope) require explicit user confirmation via AskUserQuestion — one
topic per question, recommendation + why. Nice-to-haves may be applied silently and
listed afterward.

## 4 · Produce the binding contract

Only after findings are resolved:

1. Write repo-root **`ARCHITECTURE.md`**:
   - **Opens with a ~500-word one-page summary** — key decisions, major
     considerations, tradeoffs. This is a graded hard gate. Write it last, from the
     finished body, so it summarizes reality.
   - Keep stable `§N` anchors (they are what the implementation plan binds to).
   - Cite ADR numbers (D#) and research entries (R#) inline so a reviewer can
     follow the paper trail.
   - Include the explicit non-goals section and the owned-tradeoffs language from
     the ADRs — do not soften them.
2. Update `docs/planning/DECISIONS.md` for any decisions changed at the gate
   (revision-dated, never silently rewritten).
3. Update `docs/planning/CLAUDE_CODE_HANDOFF.md`: remaining open questions, known
   tensions, and the pointer to /tasks-gen as the next step.

## 5 · Done criteria

- Coverage table has zero blank cells.
- Every critical and important finding is resolved or explicitly accepted by the user.
- Root ARCHITECTURE.md exists, opens with the one-page summary, and every capability
  in it traces to a USERS.md use case.
- The user knows the next step is /tasks-gen.

---

## Hard rules

- Never write application code and never produce IMPLEMENTATION_PLAN.md here.
- Never resolve a critical finding by deleting the requirement it fails.
- Never soften owned tradeoffs or known limitations — they are the defense.
- Never finalize with unresolved `open question` tags hidden in the body; carry
  them visibly in the handoff.
