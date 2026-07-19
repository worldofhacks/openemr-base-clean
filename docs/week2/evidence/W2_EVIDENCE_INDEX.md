# W2 Evidence Index

Closure evidence for `docs/week2/W2_IMPLEMENTATION_PLAN.md` (§8 checklist). Every entry
records the task, the exact SHA(s), and the verification performed. Created by W00; all
subsequent tasks append here. A §8 box may be checked only with a link recorded in this
file or directly in the checklist.

## W00 — Working-tree hygiene and push readiness (2026-07-19)

- **Doc set committed on `main`:** `6301e2f` `docs(w2): final-submission audit +
  remediation plan` (W2_ARCHITECTURE.md, W2_gap-audit.md, W2_IMPLEMENTATION_PLAN.md,
  new W2_BACKLOG_CHANGE_REQUEST_G.md, new prompts/W2_EXECUTION_PROMPT.md) +
  `a9e5b75` `chore: ignore personal Claude Code settings overrides`.
- **Both remote HEADs verified equal after push** (`git ls-remote`, 2026-07-19):
  - `origin` (github.com/worldofhacks/openemr-base-clean) `main` =
    `a9e5b756209ab2e0fcd919d5ca061f4c63153681`
  - `gitlab` (labs.gauntletai.com/alexander.miller/openemr-base-clean) `main` =
    `a9e5b756209ab2e0fcd919d5ca061f4c63153681`
- **G-D2 + R08 code isolated** on review branch `feat/g-d2-reader` (based on `6583079`),
  two commits per plan §6 PR 0/0b:
  - `f9b8fc4` `chore(w2): record owner decisions G-D1..G-D3; remove license-family ban`
  - `2dab3e0` `fix(agent): stop vetoing valid extractions on unreadable OCR evidence (R08)`
  - Branch pushed only after the R08 full-suite + recorded-gate verification (plan §4d.3).
- **Local tool state left uncommitted** (`.claude/settings.local.json` modification,
  `.agents/`, `AGENTS.md`); `.gitignore` now ignores the settings override (the file
  itself remains tracked — untracking is an owner call).
- Mid-run drift note: `W2_DECISIONS.md` gained owner entries G-D1/G-D3 (and G-D2's
  stale §9.2 pointer fix) at 13:52 local, confirmed deliberate by the owner; content
  rides `f9b8fc4`.

## R08 / PR 0b — extraction-robustness fix verified (2026-07-19)

- **Branch:** `feat/g-d2-reader` @ `a48987f` (rebased onto main @ `93ab760`); commits
  `7c99b75` (G-D1..G-D3 decision records + license-gate removal), `8881ed2` (R08 fix,
  16 new frozen tests), `a48987f` (picklable intake OCR fake — assertions unchanged).
- **Full suite:** `cd agent && .venv/bin/pytest -q` → **952 passed, 5 skipped**
  (≥ 936+5 baseline; +16 new frozen tests). First run caught one test-local OCR fake
  that could not pickle into the G-D3 spawned OCR child — hoisted to module level,
  assertions byte-identical (see W2_DEVLOG.md R08 verification entry).
- **Recorded 50-case gate:** `make eval-tier1` → gate=PASS, **zero category delta**
  (schema 50/50, citation 50/50, factual 23/23, safe_refusal 10/10, no_phi 50/50);
  artifact-scan PASS. Generated timing jitter in `evals/results-tier1.json` reverted.
- **PR 0b:** https://github.com/worldofhacks/openemr-base-clean/pull/24 — merge waits on
  C02 phase-1 protection (owner admin) + review; §8 R08 box stays unchecked until merged
  through the protected flow.
