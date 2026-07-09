---
name: devlog
version: 1.0.0
description: >-
  Maintain a chronological development log of every decision, action, finding, and
  pivot in the project, and synthesize it into a coherent sequential narrative you can
  read start-to-finish to understand and explain the whole process and its why. Grounds
  itself in git history so nothing committed goes unexplained. Two artifacts:
  docs/DEVLOG.md (raw append-only record) and docs/PROJECT_STORY.md (the synthesized
  narrative). Invoke on "devlog", "log this", "update the devlog", "capture what we
  did", "rebuild the story", or at any phase boundary.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion
---

# devlog

A running, evidence-grounded journal of the project's whole life — so at any moment you
can read a coherent, sequential story of what was done, in what order, and *why*, and
defend any part of it. Built for a workflow where you'll be interviewed on your process:
the log is the raw record; the narrative is the thing you study to explain it.

**Two artifacts:**
- `docs/DEVLOG.md` — the append-only chronological record. Newest entries at the bottom
  so it reads top-to-bottom as time's arrow. Never rewrite history here; only append.
- `docs/PROJECT_STORY.md` — the synthesized, sequential narrative, regenerated on demand
  from the log + git + the ADRs. This is the "start-to-finish story" you read and explain.

**The grounding guarantee:** git is the floor. Every entry should be reconcilable to a
commit, a finding ID, an artifact, or a dated decision. If it's committed, it belongs in
the story — the reconcile mode (§ mode 3) is what makes "track everything, no matter what"
true even when you forget to log manually.

---

## Entry format (DEVLOG.md)

Each entry is dated and typed. Keep them short; link, don't duplicate.

```
## [YYYY-MM-DD] <short title>   ·   type: decision | action | finding | pivot | milestone
- What: <what happened, one or two lines>
- Why: <the problem being solved / the motivation>
- Alternatives: <what else was considered — REQUIRED for decision/pivot>
- Result: <outcome + evidence: commit hash, file path, finding ID (F-*), ADR (D#)>
- Stage: <project stage / checkpoint this belongs to>
```

Type taxonomy (use the right one — it's what makes the narrative legible):
- **decision** — a choice made (link the ADR D# if one exists; alternatives required).
- **action** — something built, deployed, configured, fixed.
- **finding** — something discovered (audit result, bug, measurement) that shaped later work.
- **pivot** — a reversal of a prior decision, with what evidence forced it (the richest
  entries — e.g. the deployment platform change, a scope cut, an architecture revision).
- **milestone** — a checkpoint reached (a deliverable submitted, a stage completed).

---

## Mode 1 — Bootstrap (first run: backfill the story to date)

If `docs/DEVLOG.md` doesn't exist yet, reconstruct the log from what's already true:
1. `git log --reverse --pretty=format:'%h %ad %s' --date=short` — the full commit spine,
   oldest first.
2. Read the artifacts that record decisions and findings: `docs/planning/DECISIONS.md`
   (D#), `AUDIT.md` (F#), `docs/planning/RESEARCH.md` (R#), `docs/prompts/*`, README /
   DEPLOYMENT.md, and any defense docs.
3. Weave them into dated entries in chronological order — every ADR becomes a decision
   entry, every major finding a finding entry, every reversal a pivot entry, each
   checkpoint a milestone. Ground each to its commit/artifact.
4. Where the "why" isn't recoverable from the artifacts, mark it `[why: confirm]` and ask
   the user rather than inventing it.

## Mode 2 — Capture (the everyday append)

After any meaningful unit of work — a decision, a build step, a finding, a pivot, a
submission — append entries. Make it cheap: ask only for the "why" and "alternatives" the
artifacts can't tell you; derive the rest (files, commits) from git. Prefer to run this at
every phase boundary and after anything you'd want to explain later.

## Mode 3 — Reconcile (the safety net — run periodically)

Diff the log against ground truth so nothing slips:
1. `git log` since the last logged commit → list commits with no DEVLOG entry.
2. For each unexplained commit, draft an entry (ask the user for the "why" if the message
   doesn't carry it).
3. Report anything in the log that no longer matches the repo (a decision later reversed
   should have a pivot entry pointing forward, not a silent edit).

## Mode 4 — Synthesize (rebuild PROJECT_STORY.md)

Regenerate the narrative from the full log + git + ADRs. Structure:
- **One-paragraph arc** — the whole project in ~6 sentences (problem → what was built →
  the turns → where it stands).
- **Phased chronological narrative** — grouped by stage/checkpoint, prose not bullets,
  each phase telling: what we set out to do, what we decided and *why*, what we found,
  what changed course and what forced it, what shipped. Cite D#/F#/R#/commits inline so
  every claim is traceable.
- **The pivots, called out** — a short list of every reversal (decision → evidence that
  changed it → new decision), because those are what an interviewer probes and what proves
  the process was reasoned, not lucky.
- **Altitude on request** — default is a readable walkthrough; offer a tight "exec
  summary" version and a "deep" version with every entry expanded.

---

## When to run
- **Bootstrap** once, now, to capture the story so far.
- **Capture** at every phase boundary and after any decision/finding/pivot.
- **Reconcile** before each submission and each interview (catch anything unlogged).
- **Synthesize** before each interview and whenever you want to re-read the whole arc.

## Hard rules
- Ground every entry in evidence — commit, file, finding ID, or ADR. Never fabricate a
  "why"; if it's unknown, mark it and ask.
- DEVLOG.md is append-only — never rewrite past entries; a changed decision gets a new
  pivot entry pointing forward.
- Link, don't duplicate — reference DECISIONS.md/AUDIT.md, don't restate them.
- The narrative must stay sequential and honest, including the missteps and reversals —
  the pivots are the most valuable part of the story, not something to smooth over.
- Reconcile against git so "track everything" is real: if it's committed, it's in the story.
