# Diagram Generation Prompt (v2 — matches D7 v2 pipeline)

Copy-paste the block below to generate the proposed-architecture diagram in the same hand-drawn style as the OpenEMR repo diagram, for side-by-side presentation.

```
Create a hand-drawn Excalidraw-style architecture diagram titled
"Clinical Co-Pilot — Proposed Architecture" with the subtitle
"SMART-on-FHIR sidecar agent for OpenEMR — verified, observable, authorization-inherited."

STYLE (match exactly — this must sit side-by-side with a sibling diagram):
- Hand-drawn/sketch aesthetic (Excalidraw, Virgil font), white background
- Horizontal numbered layer rows, stacked top to bottom; each row has a large
  colored number + name on the far left with a 2-4 word description and a small icon
- Rounded rectangles with a bold monospace-style name label on top and 1-3 short
  description lines below; dashed borders for external/context items, solid for in-scope
- Dashed arrows between boxes; solid arrows for primary request flow
- Legend box top-right: "solid arrow = request flow, dashed = dependency/context"
- One color per row (stroke + pale fill): row 1 blue, row 2 orange, row 3 purple,
  row 4 green, row 5 red, row 6 blue
- Labels short (2-5 words); annotations in small muted gray text

CONTENT — six rows:

1. CLIENTS (blue) — "Who uses it":
   [PCP Clinician — 20-patient day, 90 sec between rooms] →
   [OpenEMR patient chart — Co-Pilot launch button] →
   [Chat UI — streamed brief + citation chips] (dashed: browser)
   annotation: "read-only by construction: no diagnosis, no prescribing, no chart writes"

2. ENTRY & IDENTITY (orange) — "How access starts":
   [SMART EHR Launch — carries user + patient context] →
   [OAuth2 code + PKCE — OpenEMR is the identity authority] →
   [Scoped token — read-only SMART scopes, cached per session] →
   [Session — pinned to (clinician, patient); switch = new launch]
   annotation: "agent can never exceed the clinician's own access"

3. AGENT SERVICE (purple) — "The sidecar (FastAPI/Python)":
   [/chat SSE endpoint] → [Correlation ID minted — rides every log, tool call,
   LLM span, and outbound X-Copilot-Request-Id header] →
   [Deceased hard-stop pre-flight — refuse before any summarization] →
   [Orchestrator — direct tool-use loop, no framework] →
   [Tool registry — 6 read-only FHIR tools + deterministic composites
   ("changes since last visit" computed by code, narrated by LLM), Pydantic contracts]
   with note "independent calls fan out in PARALLEL — latency = slowest call, not sum" →
   [EvidencePacket builder — normalized records, stable IDs (Type:id:hash)]
   side boxes: [/health] [/ready — real dependency checks]

4. VERIFICATION PIPELINE (green) — "Nothing unverified ships":
   [LLM answers in TYPED CLAIMS — MedicationClaim{name, dose, status},
   LabValueClaim{...}; every claim carries evidence_ids] →
   [Field-level verify vs cited record — REJECT ON CONTRADICTION, NOT ABSENCE
   (10mg vs 5mg → reject)] →
   [Deterministic templater — display text RE-RENDERED from verified fields;
   LLM prose discarded] →
   [Constraint + phrasing rules — allergy-vs-prescription, dosage bounds;
   empty allergy result ≠ "NKDA"; treatment verbs (start/stop/prescribe) → refuse] →
   split arrows: solid green to [✓ flush verified + cited],
   dashed red to [✗ block / honest refusal (canonical messages)]
   annotation: "deterministic in serving path — LLM-as-judge only in evals"
   small box: [LLM down → templater renders EvidencePacket directly —
   grounded fallback, explicit banner]

5. DATA & EXTERNAL (red) — "Where PHI lives and exits":
   [OpenEMR FHIR R4 API + ACL — every read authorized by OpenEMR] ↔ agent tools
   [Railway MySQL — OpenEMR clinical data] (dashed)
   [Postgres — agent sessions] (dashed)
   [Claude API — Sonnet 4.6 + Haiku 4.5, assumed BAA, no training] (dashed border)
   [Langfuse Cloud — traces, dashboards, costs, correlation IDs; assumed BAA,
   HIPAA-region production path] (dashed border)
   annotation in red: "PHI exits the deployment at exactly TWO points — both
   BAA-covered (LLM provider + Langfuse Cloud); traces PHI-minimized to hashes"

6. BUILD / DEPLOY (blue) — "Ship & prove it":
   [GitHub push to main] → [GH Actions — tests + eval suite gate
   (incl. adversarial cases built to beat the verifier)] →
   [Railway deploy on green — managed TLS, per-service metrics] →
   [healthcheck /ready] ; dashed red return arrow [rollback: one-click previous deploy]
   side notes: "local dev = docker compose parity" ; "k6 load tests @ 10/50 users → baselines" ;
   "latency honesty: streamed first tokens 2-3s; re-baseline thresholds from measured data"
```
