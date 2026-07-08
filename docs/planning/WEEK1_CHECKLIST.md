# Week 1 — AgentForge: Clinical Co-Pilot
## Rules & Deliverables Checklist (from Week_1_AgentForge.pdf)

**Task:** Build an AI Clinical Co-Pilot embedded in OpenEMR — a multi-turn conversational agent that knows a specific patient (history, meds, labs) and surfaces what matters for today's visit. NOT a generic medical chatbot.

**Fork from:** https://github.com/Gauntlet-HQ/openemr-base-clean

---

## Non-Negotiable Rules

1. **The case study is the floor, not the ceiling.** Every feature, architectural decision, and tradeoff must be traceable back to: a customer needing reliable, fast, secure access to data.
2. **The audit is a hard gate.** AUDIT.md must be complete BEFORE building any AI layer.
3. **Deployed app URL must be included in every submission.**
4. **Every agent capability must trace to a use case in USERS.md.** No multi-turn conversation without a use case requiring it. No tool chaining without a use case requiring it.
5. **Demo data only.** Never real PHI. Act as if a signed BAA exists with all LLM providers (no training on data).
6. **Every response passes a verification layer** before reaching the user (source attribution + domain constraint enforcement).
7. **Observability wired in from the start**, not retrofitted.
8. **Happy-path-only test suites do not pass.** Every eval case must exercise a boundary, invariant, or regression risk.
9. **Project completion AND interviews are both required** for Austin admission. AI Interview required within 24 hours after each submission.
10. **All times are Central (Austin).**

---

## Schedule — Hard Gates (all times CT)

| Checkpoint | Deadline | Focus |
|---|---|---|
| Architecture Defense | 24 hours from kickoff (~Tue Jul 7) | Architecture research and planning |
| MVP | Tue Jul 7, 11:59 PM | App audit, agent plan, deployed app, demo video |
| Early Submission | Thu Jul 9, 11:59 PM | Deployed agent, eval framework, observability, demo video |
| Final | Sun Jul 12, 11:59 AM | Production-ready agent, demo video, social post |

⚠️ Each submission triggers a required AI Interview within 24 hours.
⚠️ PDF says both "Sunday @ Noon" and "Sunday 11:59 AM CT" for Final — treat **11:59 AM** as the real deadline.

---

## MVP Checklist (due Tue 11:59 PM CT)

The MVP is NOT a working agent. It is the foundation.

- [ ] **Stage 1 — Run It Locally:** OpenEMR running locally with sample patient data. Document setup process (goes in README).
- [ ] **Stage 2 — Deploy It:** Fork publicly accessible. Choose stack thoughtfully — final agent deploys to same infra. **Hard gate: URL in every submission.**
- [ ] **Stage 3 — Audit It:** `./AUDIT.md` — **hard gate.** Must contain all five audits:
  - [ ] Security audit (authn/authz risks, data exposure, PHI handling, HIPAA gaps)
  - [ ] Performance audit (bottlenecks, data structure, latency constraints for agent)
  - [ ] Architecture audit (system organization, data location, layer interactions, integration points)
  - [ ] Data Quality audit (missing fields, inconsistent formatting, duplicates, stale data)
  - [ ] Compliance & Regulatory audit (audit logging, retention, breach notification, BAA implications of sending PHI to LLM)
  - [ ] Must BEGIN with a one-page summary (~500 words) of key findings — most impactful findings, not a dump
- [ ] **Stage 4 — User Profiles:** `./USERS.md` — **hard gate.**
  - [ ] One real, narrow user (e.g., PCP with 20-patient day, ED resident on overnight intake) — "physicians need help" is not a user
  - [ ] Their concrete workflow (the moment the agent enters their day)
  - [ ] Specific use cases (e.g., "between 8:50–9:00 AM, surface what changed for each patient on today's schedule")
  - [ ] Each use case: explicit answer to **why an agent** (not a dashboard/list/chart) is the right solution
  - [ ] This is the source of truth ARCHITECTURE.md must trace back to
- [ ] **Stage 5 — AI Integration Plan:** `./ARCHITECTURE.md` — **hard gate.**
  - [ ] Where the agent lives, how it accesses patient data, authorization boundaries, risks + mitigations
  - [ ] Informed by AUDIT.md findings
  - [ ] Must BEGIN with a one-page summary (~500 words): key decisions, major considerations, tradeoffs
  - [ ] No implementation needed yet — but must be defensible (defended Tuesday)
- [ ] Demo video (3–5 min)
- [ ] Deployed app URL

📌 Naming: PDF says `USERS.md` in two places (Stage 4 hard gate, Agent Requirements) and `USER.md` once (submission table). **Decision: use `USERS.md`.**

---

## Agent Requirements (own the design of each)

- [ ] **Agentic chatbot:** multi-turn, maintains context, invokes tools to retrieve/reason over patient data. Not a search bar/dashboard/report generator.
- [ ] **Verification system:** every response passes through it before reaching user:
  - [ ] Source attribution — every claim traceable to specific records; unattributable claims not stated as fact
  - [ ] Domain constraint enforcement — clinical rules, dosage thresholds, interaction flags; flag/reject violations
  - [ ] Document approach AND known limitations
- [ ] **Observability:** answerable from logs at any time:
  - [ ] What did the agent do on a request, in what order?
  - [ ] How long did each step take?
  - [ ] Did tools fail, and why?
  - [ ] Tokens consumed and cost?
- [ ] **Evaluation:** test suite measuring whether the agent works — surfaces failure modes, regressions, clinical edge cases (missing data, ambiguous queries, unauthorized-access attempts)

---

## Engineering Requirements (graded, not optional)

- [ ] Every eval case exercises a **boundary** (missing data, malformed input, empty record), **invariant** (claims always cite a source), or **regression risk**. Document the failure mode each test guards against.
- [ ] **Correlation ID** on every agent invocation — appears in every log entry, tool call, and LLM interaction; full trace reconstructable from logs alone.
- [ ] **Strict schemas** (Pydantic/Zod or equivalent) for every tool input and output. Contracts are the source of truth.
- [ ] **Dashboard** (LangSmith/Langfuse/Braintrust or equivalent), real-time: total requests, error rate, p50/p95 latency, tool call counts, retry counts, verification pass/fail rate — minimum; add agent-specific metrics.
- [ ] **Runnable API collection** (Postman/Bruno or equivalent) covering core agent endpoints — graders must be able to run any workflow without reading source.
- [ ] **Separate `/health` and `/ready` endpoints.** `/ready` must actually check OpenEMR, LLM provider, and observability backend are reachable — not unconditional 200.
- [ ] **≥3 alerts** on the dashboard: p95 latency threshold, error rate threshold, tool failure rate. Document what each means + on-call response.
- [ ] **Baseline profiles:** CPU, memory, request latency, throughput under load test scenarios — included in submission.
- [ ] **Load tests** at 10 and 50 concurrent users against the deployed agent. Record p50/p95/p99 latency + error rate at each level.

---

## Final Submission Checklist (due Sun Jul 12, 11:59 AM CT)

- [ ] **GitHub repo** — forked from OpenEMR; setup guide, architecture overview, deployed link
- [ ] **`./AUDIT.md`** — all findings + 1-page (~500 word) summary first
- [ ] **`./USERS.md`** — target user + use cases (see naming decision above)
- [ ] **`./ARCHITECTURE.md`** — AI plan: framework choices, verification strategy, tradeoffs; 1-page (~500 word) summary first
- [ ] **Demo video (3–5 min)** — one per submission; key decisions + product showcase
- [ ] **Eval dataset** — test suite with results
- [ ] **AI cost analysis** — actual dev spend + projected production costs at 100 / 1K / 10K / 100K users, including architectural changes at each level. NOT just cost-per-token × n users.
- [ ] **Deployed application** — publicly accessible; agent must work live (early + final)
- [ ] **Social post** (final only) — X or LinkedIn: describe project, show agent, tag @GauntletAI

---

## Interview Prep (required after each major deliverable)

**Audit:** most important finding; what you'd have missed skipping the audit; how it changed your AI plan.
**Architecture:** why the verification layer is designed that way; behavior on tool failure/missing record; trust boundaries and enforcement.
**Evaluation:** what evals reveal that a happy-path demo wouldn't; what you found running it; what you'd add next.
**Production:** scaling to a 500-bed hospital / 300 concurrent clinical users; what must change before a real physician relies on it; scariest failure mode and why.

---

## The Standard

> "The deliverable that matters is not the one that looks most impressive in a demo. It's the one you could defend in front of a hospital CTO who is deciding whether to put it in front of their physicians."

Also: Week 1 architecture compounds into weeks 2–3. Good architecture compounds; tech debt costs double later.

---

## Ambiguities to Verify with Staff

1. Final deadline: "Sunday @ Noon" vs "Sunday 11:59 AM CT" — assume 11:59 AM.
2. Architecture Defense "24 hours" — confirm exact start time of the clock.
3. ~~USERS.md vs USER.md~~ — resolved: using `USERS.md` (2 of 3 PDF mentions, incl. the hard gate).

*See Appendix (pp. 11–13 of PDF) for the full pre-search planning checklist: constraints → architecture discovery → post-stack refinement.*
