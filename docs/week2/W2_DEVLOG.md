# W2_DEVLOG — Week 2 (Multimodal Evidence Agent)

> Append-only chronological record for Week 2. Week 1's log is frozen at
> `docs/week1/DEVLOG.md`. Convention: every entry carries What / Why / Result / Stage.

## [2026-07-13] W2 kickoff — assignment received, defense prep built · type: milestone
- What: Week 2 PRD (Multimodal Evidence Agent) received ~4h before the Architecture
  Defense. Deep-read + presearch produced `docs/week2/W2_DEFENSE_PREP.md`: hard gates
  (graded CI regression injection; required PDF bounding-box overlay; prescribed
  citation shape), the two defense vulnerabilities (W1 read-only boundary vs required
  writes; D6 no-framework vs required orchestration framework), a W2-D1..D7 decision
  slate, W1 debt list, and a grill bank.
- Why: defend from a written position, not improvisation; W1 discipline carried over.
- Result: defense package on main before the defense.
- Stage: PRE-DEFENSE.

## [2026-07-13] Week-scoped convention locked · type: decision
- What: owner ruled all W2 artifacts are NEW files (W2_ prefix, W2-D#/W2-R# numbering);
  Week 1 documents are frozen history, never edited; DEVLOG and PROJECT_STORY are also
  week-scoped (this file starts fresh; W1's ends). Repo reorganized: `docs/week1/`
  holds frozen W1 planning/defense/demo/reviews/prompts/diagrams + DEVLOG + STORY +
  COST_ANALYSIS; `docs/week2/` holds all W2 docs; graded deliverables stay at repo root.
- Why: no confusion between graded weeks; W1 submission paths preserved at root.
- Result: this layout; W1 cross-references inside frozen docs accepted as stale history.
- Stage: PRE-DEFENSE.

## [2026-07-13] Pre-W2 repo cleanup — clean slate on main · type: milestone
- What: deleted 12 stale Codex worktrees (2 skeletons left for host-side Finder delete),
  19 stale local branches, Playwright CLI logs, 2 unreferenced root PNGs. Preserved the
  two unmerged docs-only branches by merging them (FINAL_BACKLOG.md,
  CODEX_GAP_REVIEW.md). Committed the untracked W1 defense/demo docs.
- Why: owner directive: zero tech debt entering W2; nothing functional removed.
- Result: single branch (main), single worktree, clean status. Host-side residuals:
  push to origin+gitlab, delete 2 skeleton folders, prune remote branches.
- Stage: PRE-DEFENSE.

## [2026-07-13] /arch-finalize complete — binding W2_ARCHITECTURE.md at root · type: milestone
- What: cold-eyes finalize (53-agent adversarial workflow: 7 dimension auditors +
  independent PRD coverage re-derivation, refuter-verified) over the v2 draft. 3
  critical (all in the eval gate: unbuildable thresholds, stub blindness, judge
  contradiction), 31 important, 29 minor, 1 refuted. Coverage: 99 PRD requirements,
  94 covered, 5 out-of-scope with PRD-sanctioned citations, 0 uncovered.
- Why: the draft was authored in this workstream; the binding contract required an
  unattached audit before /tasks-gen.
- Result: root W2_ARCHITECTURE.md (15 §-anchors, ~500-word summary, every capability
  cited to W2-D#/R#/F# + W1 refs); W2_gap-audit.md findings register; dated revisions
  W2-D1/D4/D7, W2-O1 resolved; W2-R6 added (PyMuPDF is AGPL — pypdfium2 default).
  Owner gates decided: two-tier gate w/ live-Anthropic Tier 2 (W2-D8), Cohere behind
  a RERANKER seam w/ Monday-EOD key trigger + mxbai fallback, front-desk actor
  demoted to narrative-only, durable Postgres job rows w/ delegated-token write
  principal (pulls W1 token-persistence debt into MVP).
- Stage: PLANNING COMPLETE → /tasks-gen next. Owner actions dated in the binding
  doc tail: Cohere production key in Railway (Mon EOD trigger), ANTHROPIC_API_KEY
  into GH Actions secrets (Tier 2), push main to origin + GitLab.

## [2026-07-13] W2 presearch + owner decision conversation · type: milestone
- What: `docs/week2/W2_PRESEARCH.md` completed against the 16-section Pre-Search
  Checklist; owner decisions recorded: LangGraph (W2-D2), Cohere rerank under a
  PHI-free-query contract (W2-D4), local Tesseract OCR for grounding + bboxes (W2-D3),
  production-grade default posture, strict core-first scope, machine-authored
  provenance on derived writes (W2-D1). Research topics W2-R1..R5 spawned.
- Why: same before-code process as W1; the conversation is the checklist's required
  reference artifact.
- Result: presearch committed pre-defense; W2_RESEARCH.md queued next.
- Stage: PRE-DEFENSE.

## [2026-07-13] W2-F1 live verification + post-verification consistency pass · type: milestone
- What: independent live verification of W2-F1 (local live stack; production read-only):
  verdict **CONFIRMED** — route-level 404s on FHIR POSTs even with maximal write scopes.
  New findings W2-F7..F11; W2-F4 **resolved** with the verified minimum scope set and a
  hard constraint: clients cannot gain scopes post-registration → **replacement SMART
  client** required at MVP. Contract corrections: upload returns 200 `true` with no id
  (id via collection GET by content hash); byte-exact read-back is the FHIR
  DocumentReference→Binary projection (standard download 500s — known CSRF-key defect);
  vitals proven end-to-end through FHIR Observation reads. Binding doc gained an
  owner-approved "Verification errata" block + the Cohere-trigger date fix (Monday
  2026-07-13; 07-14 is Tuesday). Consistency pass then aligned the implementation plan
  (W2-OA3 → replacement-client task; W2-M2 marked verified-by-audit; M8/M11/M16 carry
  the id-discovery + FHIR read-back contracts), W2_RESEARCH R5 (verified-live pointer),
  W2_gap-audit (dated write-path note), and W2_DEFENSE_PREP (live-evidence addendum).
  One pre-authorized decision-level addition only: `writeback.skipped(unit_mismatch)`
  added to §6a + dated W2-D1 note (never convert units — a converted number is a
  derived value not on the page).
- Why: the tasks-gen plan (f55f046) predated part of the verification; docs must agree
  exactly with probed reality before build starts.
- Result: plan/research/gap-audit/defense/devlog aligned; **the leftover "W2-F1
  Verification Local" API client was found still ENABLED in the local dev stack and was
  disabled** (`is_enabled=0` confirmed) — the W1 E9 duplicate-launcher lesson applied;
  it was local-only, password-grant, never production.
- Stage: PLANNING COMPLETE → Wave 0 build next.
