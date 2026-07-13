# DIAGRAM_PLAN.md

Four diagrams, all **Excalidraw** (via `.claude/skills/excalidraw-diagram/` — color-zoned, hand-drawn style, fully editable). Build for defense today; reuse in ARCHITECTURE.md + demo videos.

Workflow per diagram: generate `.excalidraw` JSON (skill handles layout/colors/schema) → keep the `.excalidraw` source in `docs/diagrams/` (editable) → export SVG or PNG from excalidraw.com and embed *that* in ARCHITECTURE.md/README (GitHub doesn't render `.excalidraw` natively — the export step is required for graders to see them).

Color zones (skill palette, consistent across all four): blue = clinician/input & external, green = OpenEMR (Zone A), yellow = agent service processing (Zone B), purple = observability/infra, red = LLM provider boundary (Zone C) & failure paths, gray = deployment platform (Railway project).

1. **System context (C4-1):** PCP → OpenEMR UI → [SMART launch] → Agent Service → {OpenEMR FHIR API, Claude API, Langfuse Cloud (assumed BAA, outside the deployment boundary — D5 rev 2026-07-08), Session Store}. Trust zones A/B/C as colored boundaries. *Answers: "where does it live, where are the boundaries."*
2. **Sequence — one verified turn:** clinician → launch/token → orchestrator → parallel FHIR calls → prompt (cached prefix) → Sonnet stream → verification gate (resolve citations → constraint rules) → flush | block. Correlation ID annotated end-to-end. *The money diagram — walks the interviewer through §3+§5.*
3. **Trust boundary / authz map:** token flow (code+PKCE → scopes → delegated FHIR access), what each zone can/can't do, where enforcement lives (OpenEMR ACL). *Answers the hardest interview question directly.*
4. **Deployment:** Railway project topology (OpenEMR image+volume, managed MySQL, agent service; Langfuse Cloud drawn OUTSIDE the project boundary as an external BAA-covered processor — D5 rev 2026-07-08), CI rail: push → GH Actions evals gate → Railway deploy-on-green → /ready healthcheck, one-click rollback arrow. *Answers ops/scaling openers.*

Defense deck order: 1 → 3 → 2 → 4, with DECISIONS.md D2/D4/D5 tables as appendix slides.
