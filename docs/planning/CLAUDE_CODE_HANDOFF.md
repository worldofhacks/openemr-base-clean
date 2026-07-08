# CLAUDE_CODE_HANDOFF.md — instructions for the finalize pass (Brain 2)

**Build posture: production-grade** (confirmed with user 2026-07-06). Planning mode: Default.

> **STATUS 2026-07-07 — finalize pass COMPLETE.** The two remaining MVP planning hard gates are closed: root **`USERS.md`** (Stage 4) and root **`ARCHITECTURE.md`** (Stage 5, opens with a 524-word one-page summary). The Stage-3 audit (`AUDIT.md`) landed first and its flagged revisions were applied to the ADRs (D2/D5/D7/D10/D12 revised, D14/D15 added — see DECISIONS.md revision blocks dated 2026-07-07). The arch-finalize gap audit ran cold-eyes across all 12 dimensions (`docs/planning/gap-audit.md`): coverage table has zero blank cells, all critical + important findings resolved in ARCHITECTURE.md or at source, zero required a user decision. **Next step: `/tasks-gen`** to produce `IMPLEMENTATION_PLAN.md` from ARCHITECTURE.md §10 (the three-checkpoint build order is the spine). **Do not start agent code before then — MVP is the foundation + plan.**

## Artifacts written (docs/planning/)
PRESEARCH.md · RESEARCH.md · DECISIONS.md · ARCHITECTURE_DRAFT.md · DIAGRAM_PLAN.md · this file.
Original PRD: `Week_1_AgentForge.pdf` (repo/project root). Deliverables checklist: `WEEK1_CHECKLIST.md`.

## Instructions
1. Read ALL of docs/planning/* + the PRD + WEEK1_CHECKLIST.md. **Do not start implementation.**
2. Run the second-pass gap audit across: missing flows, lifecycle states, failure modes, interfaces/schemas, unclear source-of-truth, unresearched deps, inconsistent decisions, overbuilt scope, missing tests / deploy path / trust boundaries / diagrams / task-planning anchors.
3. Propose precise edits; confirm load-bearing changes with Alex; then produce the finalized repo-root `ARCHITECTURE.md`. **PRD format constraint: it must BEGIN with a ~500-word one-page summary (key decisions, considerations, tradeoffs) — this is a graded hard gate.** Keep §-anchors from the draft.
4. Only then generate `IMPLEMENTATION_PLAN.md` (three-checkpoint build order in draft §10 is the spine).

## Still open (carried forward to the build phase / `/tasks-gen`)
- **R12 — the one number awaiting measurement:** the p50≈28s / LLM≈85% latency anchor is an unverified planning assumption; **replace with real Langfuse data at Early** (RESEARCH.md R12; ARCHITECTURE.md §7/§9/§10.2).
- **Deploy actions before Final (from the audit):** pin `https://` + reject downgrade (F-S.9); close the Railway MySQL TCP proxy (F-S.9, DEPLOYMENT.md §4.5); set `api_log_option`/retention posture (F-S.4 / D15). ARCHITECTURE.md §4/§11.
- O1 UI embedding detail (launch target: tab vs iframe panel) — resolve during Early build.
- O2 session store (Postgres default vs Redis) — default Postgres; revisit if latency demands.
- O3 submission portal + GitLab mirror requirement (staff answer pending — logistics).
- Demo-data richness for labs/trends (Stage 1 confirmed the Synthea set is rich enough for UC1–UC4; heavy-patient pid=7 is the load worst case, F-P.3).
- Verify-then-flush streaming granularity (per-block verification UX) — stream-interruption handling now specified in §6.
- Langfuse self-host alert delivery channel — resolved to checker→webhook/Slack in §7; still a known op tension (self-host alerting thinner than SaaS).
- Parallel fan-out cap (D10): pick concurrency limit after observing OpenEMR under k6 (§7 load tests).
- Note: voice I/O (D11) was cut from wk1 scope on 2026-07-06 — do not resurrect without the user's say-so; prior analysis in DECISIONS.md D11 + git history.

## Known tensions to scrutinize adversarially
- Latency budget vs verification-before-flush (measure; budget per stage in §3)
- Prompt-cache assumption vs session shapes (cache prefix stability discipline in prompt assembly)
- Audit-before-AI gate vs 5-hour defense: the draft deliberately contains NO agent implementation, only architecture — consistent with the gate
- D6 (no framework) vs wk2–3 unknown requirements — keep the seam clean
