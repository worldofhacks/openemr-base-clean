---
name: interview-prep
version: 1.0.0
description: >-
  Generate submission-grounded mock interview questions in the style of an AI video
  interview that probes PROCESS, FINDINGS, STRATEGY, SELF-CRITIQUE, and PRODUCTION
  THINKING — not repo trivia. Reads the project's own artifacts (AUDIT.md,
  ARCHITECTURE.md, DECISIONS.md, USERS.md, planning + defense docs) and produces 15
  specific, answerable questions with a "what a strong answer hits" scaffold for each,
  then optionally runs a timed mock and critiques answers. Invoke on "interview prep",
  "mock interview", "generate interview questions", or before a submission interview.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion
---

# interview-prep

The video interview after each submission tests whether you can *defend and reflect on
your own work* — how you did it, what you found, why you chose what you chose, what you're
unsure of, and what production would demand. It is NOT a code quiz. This skill generates
questions in exactly that style, grounded in your actual submission, so prep is realistic.

**The observed style (calibrate every question to this):** the questions probe judgment,
not recall. Good exemplars from a real MVP interview:
- "What was the most important audit finding?"
- "What were you least confident in for the architecture?"
- "What would you not have found if you'd started building without the audit?"
- "What is the observability, and what is it actually observing?"
- "What would you change before there's production data on it?"
Every question this skill emits should feel like those: open, reflective, defensible from
evidence, with a clear strong-vs-weak answer.

---

## 1. Read the submission

1. Ask which submission stage this is for — **MVP / Early / Final** — and read that
   stage's requirements if a checklist exists. The stage shifts the question mix (§3).
2. Read the grounding artifacts (whatever exists): `AUDIT.md`, `ARCHITECTURE.md`,
   `USERS.md`, `docs/planning/DECISIONS.md`, `docs/planning/RESEARCH.md`, and the
   `docs/defense/*` prep docs. Questions must be answerable from these — no generic
   "tell me about microservices" filler.
3. Note the load-bearing decisions, the sharpest findings, the owned tradeoffs, the
   open questions, and anything the submitter revised about their own prior plan —
   these are the richest question seams.

## 2. Generate 15 questions

Emit exactly 15, distributed across the five categories below (roughly 3 each). Every
question must be **specific to this submission** — name the actual finding, decision, or
component — and must have a non-obvious strong answer. Avoid yes/no; avoid anything
answerable by reading one file aloud.

Categories:
- **Findings** — the most important finding and why; what a finding forces; what would
  have been missed; the finding you almost got wrong.
- **Process / methodology** — how the audit/analysis was actually done; how a claim was
  verified; how you separated a real defect from a demo artifact; how you avoided a false
  positive.
- **Strategy / tradeoffs** — why this choice over the alternative; what the choice costs;
  what would reverse it; where you deliberately cut scope and why.
- **Self-critique** — least confident in; what worries you most; what your verification
  does NOT catch; a decision you revised and what forced it.
- **Production thinking** — scaling to real load; what changes before real data / a real
  user relies on it; the failure mode that would hurt someone.

For **each** question also emit a compact **"strong answer hits:"** scaffold — 2–4 bullets
naming the specific evidence, decision, and honest limitation a good answer would include.
This makes the output a study tool, not a blind quiz. Mark 3–4 questions as **"most
likely"** based on what the submission most invites.

Write to `docs/defense/INTERVIEW_BANK_<stage>.md`.

## 3. Stage-shift the mix

- **MVP** — weight toward audit findings, methodology, the user/architecture *plan*, and
  "what's designed vs. built" honesty (observability/agent are plans, not running).
- **Early** — weight toward the now-LIVE agent, the verification layer in practice, live
  observability (be ready to show the dashboard), eval design, and first measured numbers.
- **Final** — weight toward eval results, load/scale behavior, the cost model, production
  readiness, and "what would you do next."

## 4. Optional — run a mock

If the user asks to practice: ask one question at a time, wait for their spoken/typed
answer, then critique against the scaffold — what landed, what evidence was missing, and
the one follow-up an interviewer would ask next. One question per turn; never dump the
next before they've answered. Keep critique specific and kind: the goal is reps, not a grade.

---

## Hard rules

- Questions must be answerable from THIS submission's artifacts — no generic filler.
- Every question carries a strong-answer scaffold; never quiz blind.
- Calibrate to the reflective, judgment-probing style — not code recall.
- In a mock, one question per turn; critique against evidence, and always name the next
  follow-up so the user learns where answers get pressure-tested.
- Never invent findings or decisions the artifacts don't support — if a category is thin,
  say so and ask what to ground it in.
