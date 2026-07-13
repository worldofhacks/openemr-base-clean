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
