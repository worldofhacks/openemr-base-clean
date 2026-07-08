---
name: tasks-gen
version: 1.0.0
description: >-
  Decompose the binding repo-root ARCHITECTURE.md into a prescriptive, §-anchored
  IMPLEMENTATION_PLAN.md ordered against real checkpoint deadlines. Every phase cites
  the architecture sections it implements; every task carries files touched,
  acceptance criteria (including edge/error behavior), and its test/eval implication.
  Flags — never invents — work that lacks architecture backing. Never writes
  application code; never modifies ARCHITECTURE.md. Invoke on "generate the tasks",
  "make the implementation plan", "decompose the architecture", or after
  /arch-finalize has produced ARCHITECTURE.md.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion
---

# tasks-gen

The architecture is the contract; this skill turns it into an executable plan
precise enough that an implementer never guesses.

```
ARCHITECTURE.md (binding) + docs/planning/* + deadline checklist
  → IMPLEMENTATION_PLAN.md (phases → tasks, §-anchored, acceptance-pinned)
```

---

## 1 · Read the inputs

1. **Repo-root `ARCHITECTURE.md`** — primary. Read fully, including the posture
   line, the non-goals, and every `§N` anchor. This document is read-only here.
2. **`docs/planning/DECISIONS.md`** — locked choices constrain task design; a task
   may not quietly contradict an ADR.
3. **`docs/planning/PRESEARCH.md` + `USERS.md`** — use cases and flows give tasks
   their acceptance scenarios.
4. **The deliverables checklist / checkpoint deadlines** (ask for the path) — the
   plan's phase boundaries are the real submission gates, not arbitrary sprints.

## 2 · Decompose

Write `IMPLEMENTATION_PLAN.md` at repo root:

**Phases** — each phase block carries:
- `Deadline:` the checkpoint it must land before (e.g., MVP / Early / Final)
- `Spec anchors:` the ARCHITECTURE.md § sections it implements
- `Goal:` one line
- `Exit criteria:` verifiable statements (deploy green, evals passing, doc committed)

**Tasks** — dense checkbox bullets. Every task carries:
- `Files:` which files are NEW vs extended (concrete paths)
- `Anchors:` the § / ADR it implements
- `Accept:` 2–5 bullets pinning behavior **including the edge and error cases the
  architecture names** (a task whose acceptance is only the happy path is incomplete)
- `Test:` the unit/integration/eval case that proves it (evals tagged
  boundary / invariant / regression)

**Ordering rules:**
- Hard gates first — anything the checklist marks as a gate blocks everything behind it.
- Trust-boundary and contract work (auth, schemas, verification) before features
  that depend on them.
- Observability wiring lands with the first feature that emits a trace, not after.
- Deploy pipeline + health checks before the first feature that needs a live URL.
- Mark parallelizable tracks explicitly (independent files, no shared contract).

## 3 · Flag, never invent

If a needed task has no architecture backing (no §, no ADR), do NOT write the task.
Add it to a `## Needs architecture` section at the bottom with one line on what's
missing, and tell the user to route it through /arch-finalize (or a dated ADR
addendum) first. The plan may not silently extend the contract.

## 4 · Living-plan conventions

- Checkboxes are the state — the plan is updated as work lands, never rewritten
  from scratch.
- A `## Cut / deferred` section records scope removed mid-week, each entry dated
  with a one-line reason (cuts are decisions; they deserve a paper trail).
- A `## Deliverables map` section lists every graded deliverable → the phase/task
  that produces it, so nothing is discovered missing at submission time.

## 5 · Done criteria

- Every ARCHITECTURE.md § is implemented by at least one task or explicitly listed
  as not-this-week (with reason).
- Every graded deliverable appears in the deliverables map.
- Every task has files, anchors, acceptance (with edge/error), and a test hook.
- No task exists without architecture backing.

---

## Hard rules

- Never write application code.
- Never modify ARCHITECTURE.md — route changes through /arch-finalize.
- Never emit a task whose acceptance criteria are happy-path-only.
- Never bury scope cuts — they go in the dated Cut section.
