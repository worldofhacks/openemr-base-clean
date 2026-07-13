# Codex Onboarding Prompt — familiarize only, no task

Paste as the first message to Codex in the repo root. Goal: Codex deeply understands the
project, conventions, and current state — and makes NO changes.

---

```
You are joining an in-progress project. Your ONLY job in this session is to familiarize
yourself with the repository — read, understand, and report back. Make NO code changes,
create NO files, run NO build tasks, and do NOT start any implementation. This is
read-only orientation. Another AI agent (Claude Code) is ACTIVELY building in this repo
right now, so touching anything risks a conflict — read only.

WHAT THIS PROJECT IS
A "Clinical Co-Pilot": a SMART-on-FHIR sidecar AI agent that integrates into OpenEMR (an
open-source EHR) to give a primary-care physician a verified, source-grounded pre-visit
brief. The repo is a fork of OpenEMR (the PHP EHR — the foundation) with a new Python
agent service under agent/. The agent is READ-ONLY (never writes to OpenEMR), and every
response passes a deterministic verification layer before it can reach a physician. Built
for a one-week program (Gauntlet AI); currently mid-build on the "Early" milestone.

READ THESE, IN THIS ORDER (understand each before moving on):
1. README.md and DOCKER_README.md — the OpenEMR base (what the foundation is).
2. ARCHITECTURE.md — the BINDING contract for the agent. Read the ~500-word summary first,
   then the § sections. This is the source of truth for how the agent is designed.
3. AUDIT.md — the pre-build security/architecture/data-quality/compliance audit. Note the
   finding IDs (F-*); several drove the design. The immunization-status and medication-dose
   findings are load-bearing.
4. USERS.md — the target user (one primary-care physician) and use cases (UC1–UC4).
5. docs/planning/DECISIONS.md — the ADR log (D1–D15). Every major choice, its alternatives,
   and its tradeoffs. This is how decisions are recorded here.
6. docs/planning/RESEARCH.md — the sourced facts (R#) behind the decisions.
7. IMPLEMENTATION_PLAN.md — the §-anchored build plan. The E-task checkboxes are the live
   state tracker; the F-tasks are the Final phase. Read which E-tasks are ticked to see how
   far the build has progressed.
8. docs/DEVLOG.md and docs/PROJECT_STORY.md — the chronological narrative of what's been
   done and why, in order.
9. DEPLOYMENT.md — how OpenEMR is deployed (Railway), including the crypto-key incident and
   the tester-access section.
10. agent/ — the Python sidecar. Read agent/pyproject.toml, then the app/ tree
    (config, health, middleware, auth, tools, evidence, llm, verify, observability) and the
    tests/ tree. This is where all agent code lives.
11. Any CLAUDE.md files (root and agent/) — project conventions. Treat them as the
    coding-standards/rules-of-the-repo document; they are agent-neutral and apply to you too.

NON-NEGOTIABLE RULES (internalize these — they constrain any future work):
- The agent is READ-ONLY: no write tools, no chart mutations, no OpenEMR DB credentials.
- Do NOT modify OpenEMR application code (the PHP). All new work lives in agent/ (D2/D9).
- ARCHITECTURE.md is the binding contract. It is not edited in-code — changes route through
  a planning pass (the arch-finalize workflow) with owner sign-off.
- Demo/synthetic data only (Synthea). Never real PHI.
- Test-first discipline: no production code without a failing test; the verification layer
  is the highest-stakes code and is guarded by frozen tests.
- Secrets live in a gitignored agent/.env, never committed, never printed. .env.example
  documents the variable names.
- Traceability vocabulary: decisions = D#, audit findings = F#, use cases = UC#, research =
  R#. Anything you later propose should cite these.

HOW TO ORIENT ON THE CODE (read-only):
- Test suite: the canonical commands are `cd agent && pip install -e ".[dev]" && pytest -q`.
  You may READ the tests to understand behavior; do not run mutating commands or add code.
- Git: origin is GitHub (worldofhacks/openemr-base-clean); there is also a GitLab mirror.
  Read `git log --oneline -30` to see the build sequence. Do not push or branch.

CURRENT STATE TO CONFIRM FOR YOURSELF:
From IMPLEMENTATION_PLAN.md checkboxes + DEVLOG + git log, determine which E-tasks are
complete and which is in progress. (As of writing, E1–E8 are done — skeleton, SMART auth,
tools, evidence packet, orchestrator, verification-in-loop, observability, eval-gate — and
E9, the deploy + /chat serving route, is in progress.)

REPORT BACK (this is your only deliverable this session):
1. A concise summary of what the project is and how the agent is architected, in your own
   words — enough to prove you understand it.
2. The verification layer specifically: explain how a response is prevented from containing
   an unsupported claim (the verify-then-flush path). This is the heart of the system.
3. The current build state: what's done, what's in progress.
4. The repo's conventions and the non-negotiable rules, restated.
5. Any ambiguities, gaps, or questions you have.
6. WITHOUT executing anything: propose 2–3 areas where you could contribute without
   colliding with the active E9 build (e.g., Final-phase F-tasks that are independent, an
   independent read-only review of existing agent code, or documentation) — as suggestions
   for the owner to assign later, not actions to take now.

Do not write or modify anything. When you've read enough to report the above, stop and
report.
```
