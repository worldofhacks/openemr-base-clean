# gap-audit.md — arch-finalize gap audit (2026-07-07)

> Cold-eyes gap audit of `ARCHITECTURE_DRAFT.md` + all planning artifacts against the PRD (`WEEK1_CHECKLIST.md`, faithful distillation of `Week_1_AgentForge.pdf`), across the 12 arch-finalize dimensions. Run by 5 independent reviewers who read the actual files (the author has confirmation bias on docs they wrote). Findings bucketed critical / important / nice-to-have / proposed-edit / question-for-user.
>
> **Outcome:** zero findings required a fork-in-the-road user decision — every one was "the plan is right but under-specified" or "one stale phrase." All critical + important findings are **resolved** either in the finalized `ARCHITECTURE.md` (specification added) or by a source-doc fix (logged below); runtime/assembly deliverables (demo video, README, load-test results, social post) are **explicitly deferred** to the Early/Final build waves per the PRD's "MVP = foundation + plan" rule and carried in `CLAUDE_CODE_HANDOFF.md`.

## PRD coverage table (zero blank cells)

`covered` = a § or ADR fully addresses it · `partial` = addressed but a precision gap was closed in ARCHITECTURE.md (finding id) · `out-of-scope` = not an architecture artifact · runtime deliverables are `partial` because MVP produces the *plan* for them, not the artifact.

| # | PRD requirement | Covered by | Status |
|---|-----------------|-----------|--------|
| NN-1 | Every feature/decision traceable to customer need | USERS.md traceability table; D1 | covered |
| NN-2 | AUDIT.md complete BEFORE any AI layer (hard gate) | AUDIT.md gate status | covered |
| NN-3 | Deployed app URL in every submission | D8; ARCH §Submission | partial → G5 (SUBMISSION checklist) |
| NN-4 | Every capability traces to a USERS.md use case | USERS.md UC1-UC4 + table; ARCH §2/§5 | covered |
| NN-5 | Demo data only; assume signed BAA | D1/D8; ARCH §1 non-goals, §4 Zone C | covered |
| NN-6 | Every response passes a verification layer | D7; ARCH §5 | covered |
| NN-7 | Observability wired in from the start | D5; ARCH §7, §3.1 | covered |
| NN-8 | Happy-path-only suites fail; boundary/invariant/regression | ARCH §8; D12 rev | covered |
| NN-9 | Completion AND interview both required | schedule / logistics | out-of-scope |
| NN-10 | All times Central | scheduling constant | out-of-scope |
| S1 | Run locally with sample data; README setup | ARCH §10.1; D8; DEPLOYMENT.md | partial → G5 |
| S2 | Fork publicly deployed; agent same infra | D8; ARCH §1, §10 | covered |
| S3 | AUDIT.md (5 audits + ~500-word summary) | AUDIT.md | covered |
| S4 | USERS.md (narrow user, workflow, use cases, why-an-agent) | USERS.md | covered |
| S5 | ARCHITECTURE.md (placement, access, authz, risks, ~500-word summary) | ARCH (all §) | covered (was partial → G6, summary now written) |
| S-vid | Demo video (3-5 min) | ARCH §Submission + §10 | partial → G5 (build wave) |
| A1 | Agentic multi-turn chatbot with tool use | D3/D6; ARCH §2/§3; UC3 | covered |
| A2 | Verification: source attribution | D7; ARCH §5 | covered |
| A3 | Verification: domain-constraint enforcement | D7 rev rules 1-6; ARCH §5 | covered |
| A4 | Verification: document approach + known limitations | D7; ARCH §5 | covered |
| A5 | Observability: what/order/timing/failures/tokens/cost from logs | D5; ARCH §3.4/§7 | covered |
| A6 | Evaluation: failure modes, regressions, edge cases | ARCH §8; D12 rev | covered |
| E1 | Every eval boundary/invariant/regression + guarded failure mode | ARCH §8 | covered (deepened → T1/T2) |
| E2 | Correlation ID every invocation; trace from logs alone | ARCH §3.1/§7; D10 rev | covered |
| E3 | Strict schemas for every tool input AND output | D3/D7.2; ARCH §Contracts | partial → I1 (contracts enumerated) |
| E4 | Dashboard: requests/error/p50-p95/tool-calls/retries/verify | D5; ARCH §7 | covered |
| E5 | Runnable API collection; run any workflow w/o source | ARCH §7 | partial → G4 (token-mint helper) |
| E6 | Separate /health and /ready; real dependency checks | ARCH §2/§7 | partial → G3 (hard/soft deps) |
| E7 | ≥3 alerts, each w/ meaning + on-call response | ARCH §7 | partial → G2 (runbook stubs) |
| E8 | Baseline profiles CPU/mem/latency/throughput | ARCH §7; F-P.5 | covered |
| E9 | Load tests @ 10 & 50 users; p50/p95/p99 + error | ARCH §7/§10 | covered (results = build wave) |
| F1 | GitHub repo: setup guide, arch overview, deployed link | D8; ARCH §10; root docs | partial → G5 |
| F2 | AUDIT.md all findings + summary | AUDIT.md | covered |
| F3 | USERS.md | USERS.md | covered |
| F4 | ARCHITECTURE.md framework/verification/tradeoffs + summary | ARCH (all §) | covered (was partial → G6) |
| F5 | Demo video | ARCH §Submission | partial → G5 |
| F6 | Eval dataset + results | ARCH §8; D12 rev | partial (results = build wave) |
| F7 | AI cost analysis 100/1K/10K/100K + infra step-changes | D4/D8/R4; ARCH §9 | covered |
| F8 | Deployed app publicly accessible; agent works live | D8; ARCH §10 | covered (realization = build wave) |
| F9 | Social post (final) | ARCH §Submission | partial → G5 |
| IP | Interview prep talking points | DECISIONS defenses; ARCH §6/§9 | covered |

No blank cells. Every `partial` maps to a finding id resolved below or an explicitly-deferred build-wave artifact.

## Findings and dispositions

### Critical (4) — all resolved
- **LIFE-1** — no lifecycle entity had expiry/retention and §6 omitted the matching failures. **Resolved:** ARCHITECTURE.md §3a (Lifecycles & retention) defines OAuth-token/session/FHIR-cache/trace/session-store lifetimes; §6 gains rows for token-expiry-mid-session, launch/handshake fail (D14), stream interruption, 429, session-store fail-closed, pid=7 overflow.
- **I1** — tool I/O contracts not enumerated (PRD makes them the source of truth). **Resolved:** ARCHITECTURE.md §5a (Interface contracts) — worked example for one tool, one-liner per tool, endpoint table, SMART-exchange spec.
- **G7-1** — the load-bearing "p50 ≈ 28s / LLM ≈ 85%" latency anchor had no R# backing. **Resolved at source:** added **R12** (RESEARCH.md) tagging it an unverified prior-art planning assumption to be replaced by measured Langfuse data at Early; re-tagged in D10 and reflected in ARCHITECTURE.md §7/§9.
- **T2** — §5 verifier rules F-D.4/F-D.2/F-D.6 lacked invariant eval cases. **Resolved:** ARCHITECTURE.md §8 adds one invariant per §5 rule, each citing its F-#.

### Important (12) — all resolved
- **G1** rollback only in D8 → ARCHITECTURE.md §7 Deploy & Rollback paragraph.
- **G2** alerts lacked runbook text + delivery channel → §7 per-alert runbook stubs + delivery channel stated inline.
- **G3** /ready vs §6 Langfuse contradiction → §7 classifies hard (503) vs soft (200+degraded) dependencies.
- **G4** Bruno can't script SMART launch → §7 + D14 runbook ship a dev-only token-mint helper populating a Bruno env var.
- **FLOWS-1** UC2/UC4 had no flow → ARCHITECTURE.md §3 adds UC2 (bounding-encounter → delta tool) and UC4 (flags over cached packet) flows.
- **S4** no source-of-truth ledger → §6a datum→authority ledger.
- **T1(bnd)** injection crossing had no named enforcer → §4 names EvidencePacket builder (input) + templater/blocklist (output).
- **T3(bnd)** https-pin + open MySQL proxy absent → §4 pins https:// and requires closing the MySQL TCP proxy before Final.
- **G8-1** stale "certified in this codebase" leaked into the D2 table + §1 diagram → **fixed at source** (D2 table → "certification-capable upstream"; draft §1 diagram annotation → "scope + compartment"); caveat carried into the ARCHITECTURE.md summary.
- **T1(eval)** no eval-case schema/failure-mode field → §8 adds an EvalCase schema + dataset location + per-category targets.
- **T3(eval)** D13/F3 degraded paths unexercised → §8 adds LLM-failure and FHIR-failure fixtures.
- **T4(eval)** F-S.5/F-S.3 auth guardrails untested → §8 adds guardrail assertions (never client_credentials; never APICSRFTOKEN).

### Proposed-edit (2) — applied
- **G6** ARCHITECTURE.md 500-word summary was a TODO → **written** (last, from the finished body).
- **G9-1** ops/observability capabilities trace to PRD engineering requirements, not a UC → ARCHITECTURE.md §2 labels them "PRD-engineering-requirement scope" so they don't read as rule-4 (untraced-capability) violations.

### Nice-to-have (9) — applied silently or accepted
- **G5** runtime submission deliverables had no owner → ARCHITECTURE.md gains a **Submission checklist** appendix (per-checkpoint: URL, demo video decisions, README, eval export, social post).
- **G7(ops)** F-S.9 deploy caveats not in ops → folded into §4/§6 (https-pin, close proxy) via T3.
- **G8-2** D14 "registers-disabled" vs F-S.6 "manual-approval" wording → reconciled in D14 text (same gate; both mechanisms named).
- **G9-2** Encounter.status=finished non-assertion rule missing from draft §5 list → added to ARCHITECTURE.md §5 rule 1.
- **G7-2** Langfuse self-hosted alert delivery is an unresearched op assumption + open item → resolved by G2 (delivery channel stated) and carried as a known tension in the handoff.
- **G8 / G9-3 / G8-3** — positive confirmations (no action): correlation-ID/api_log revision fully consistent; UC↔capability trace clean both directions; no residual hard-join or scope∧ACL claim survives in live body text.
- **T5-T8** eval nice-to-haves (LLM-judge pinning, ground-truth source, gate scope) → folded into §8 where concrete.

### Question-for-user (0)
No finding presented genuine alternatives requiring a decision; all resolutions complete (not alter) the locked decisions. Load-bearing decisions were confirmed by the audit (D2/D9 via F-A.2/F-S.5) or already revised in Phase 1 (D2/D5/D10/D12/D7 + D14/D15).
