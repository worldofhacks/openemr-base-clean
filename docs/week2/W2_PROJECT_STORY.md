# W2_PROJECT_STORY — Week 2 (Multimodal Evidence Agent)

> Synthesized narrative for Week 2, rebuilt from `W2_DEVLOG.md` + git history at phase
> boundaries. Week 1's story is frozen at `docs/week1/PROJECT_STORY.md`.

## Where Week 2 starts

Week 1 shipped a read-only, verify-then-flush clinical co-pilot: typed claims, a
deterministic verifier, citations to the chart, Langfuse as the accountability record,
and an eval gate wired into deploy. Week 2's assignment makes the agent SEE: scanned lab
PDFs and intake forms in, grounded and cited answers out, work routed through a
supervisor and two workers, and quality proven by a 50-case boolean-rubric CI gate that
graders will deliberately try to break.

Two Week 1 positions bend under the new requirements, and the story of Week 2 is how
they bend without breaking. Read-only-by-construction becomes append-only-by-construction:
the agent gains exactly two create capabilities, idempotent and source-linked, and no
ability to edit or delete anything a clinician wrote. And the no-framework loop gives way
to LangGraph exactly through the escape hatch Week 1's decision log carved for it: D6
documented multi-agent requirements as its own invalidation, and that clause fired.

The thesis is unchanged: the model drafts, deterministic checks decide. Extraction adds a
new pixel-level version of the same idea: a vision model proposes structured fields, a
local OCR layer with word coordinates either grounds each field (earning it a citation
and a bounding box) or exposes it as unsupported.

*(Narrative continues at the next phase boundary.)*
