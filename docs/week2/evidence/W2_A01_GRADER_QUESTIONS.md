# A01 — six grader questions (draft; owner sends)

**Owner-only send (plan §4c #3).** Direct to the course/grader channel — these are
grading-scope questions, not repo questions. Record answers (verbatim or approved
paraphrase, with author + timestamp) as an appended block in
`docs/week2/W2_DECISIONS.md`; map any resulting work back to its AF-P2 finding.
Until answered, the conservative current behavior stays in place (plan §1).

---

Subject: AgentForge Week 2 — six scope clarifications before final grading

Hi — while closing out the Week 2 Clinical Co-Pilot submission I found six places where
the requirements document (Week_2_AgentForge.pdf) supports two readings. I have kept the
conservative behavior in each case; I would like written confirmation (or correction) so
the submission is graded against the intended scope.

1. **Critic agent (pp.4–5).** Page 4 says "A critic agent is extension work, not core";
   page 5 lists "Critic agent that rejects uncited claims or unsafe action suggestions"
   as a Core Deliverable. Is the critic graded as core? (One exists post-composition and
   is flag-gated — the question is scope, not implementation order.)

2. **Third document type (pp.3–5).** The MVP table requires two document types; page 5's
   Core Deliverables include "A third document type such as referral fax or medication
   list." Is `medication_list` implemented as a source document + grounded extraction
   artifact — with NO clinical write path, by safety design — sufficient for that bullet?

3. **Click-to-source UI + contextual retrieval (pp.3–5).** Are the page-5 bullets
   "Click-to-source UI for citation snippets, with a simple document preview" and
   "Contextual retrieval improvements such as better chunking, query rewriting, or
   domain-specific filters" graded core? Do section-aware chunking plus deterministic
   query building qualify as "contextual retrieval improvements"?

4. **Lab trend chart (pp.3–5).** Page 5 asks for a "Lab trend chart widget that uses
   extracted Observation data." Our trends are backed by write/readback-verified
   extraction artifacts; no discrete FHIR Observation resources are created (this fork
   exposes no supported client Observation write). Does an artifact-backed trend chart
   qualify, or is a discrete FHIR Observation write required?

5. **Eval-data wording (pp.3–7).** Page 7 forbids "extracted clinical values" and raw
   documents in analytics/eval artifacts, while Stage 4 requires fixture documents with
   expected extraction values. Do fully synthetic fixtures (with obvious non-clinical
   markers) satisfy the page-7 intent for the eval dataset itself, with the no-PHI rule
   applying to generated logs/traces/results?

6. **Checkpoint calendar (p.3).** Please confirm the calendar dates/timezone mapping for
   MVP (Tue 11:59 PM), Early Submission (Thu 11:59 PM), and Final (Sun noon, Central),
   and how schedule conformance is graded — the submission includes a late-final note.

Thank you — happy to provide evidence links for any of these.

---

*Prepared 2026-07-19 by the W2 remediation session (plan task A01). Findings:
AF-P2-01..06; RTM rows W2-REQ-42..46, 80, 100..103.*
