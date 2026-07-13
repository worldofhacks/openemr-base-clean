# Final Demo Video Script — Clinical Co-Pilot (~4 min)

> Recorded against the live Railway deployment. Patient: **José Oquendo**. One take
> preferred; if a take dies, do a **fresh SMART launch** (never reuse a stale session,
> never redeploy mid-demo — in-process token/PKCE is lost on restart, §3a).

## 60-second PRE-FLIGHT (before recording)

1. Open `https://agent-production-9f62.up.railway.app/ready` → confirm all 4 checks green.
2. Open `/app` directly and look at the suggestion chips:
   - **If chips read "What are the patient's active problems?" etc. (3 lookup chips)** → latest build is live. Record.
   - **If chips read "What changed since the last visit?" (old 4)** → the deployed build predates tonight's scroll-fix + chip merges. Redeploy latest main from the Railway dashboard NOW, wait for /ready green, THEN start recording with a fresh launch. Do not redeploy again after this point.
3. Log into OpenEMR, open José Oquendo's chart, confirm the launch button is visible.
   If two launch buttons show, use **"Clinical Co-Pilot Agent (EHR launch)"** — never the E9 one.
4. Optional B-roll tab, pre-loaded: the Langfuse trace view (a `previsit-brief` root trace
   with six `fhir.*` spans + one `llm` GENERATION showing tokens/cost).

## Hard rules while recording
- Fresh SMART launch per take. Patient = José Oquendo only.
- Never claim the agent "catches the immunization bug" (immunizations are out of read
  scope). The honest claim: **the audit's medication-dose finding crashed the live
  integration exactly as predicted — and the verifier is why that never reaches a physician.**
- If a response refuses or says "confirm with patient" — that's a FEATURE. Narrate it.

---

## THE SCRIPT

### [0:00–0:25] Cold open — the problem (talking head or chart on screen)

> "A primary-care physician gets about 90 seconds between exam rooms to answer:
> who is this patient, and what changed? This is a Clinical Co-Pilot for OpenEMR —
> a read-only AI agent that produces a verified, cited pre-visit brief.
> The design thesis: the model never speaks to the physician directly.
> Prompting is not the safety boundary — verification is."

### [0:25–0:55] Launch from the chart (screen: José's chart → click launcher)

> "I'm in OpenEMR on José Oquendo's chart. One click launches the co-pilot via
> SMART-on-FHIR EHR launch — a standard OAuth2 authorization-code flow with PKCE.
> The agent gets a delegated, read-only token scoped to me and this patient.
> It's an external sidecar: it inherits OpenEMR's authorization — I didn't build a
> parallel one — and it can never exceed my own access. No write scopes exist."

*(Click launch; the brief starts rendering.)*

### [0:55–2:10] The brief (screen: scroll slowly through it)

> "In a few seconds I get the pre-visit brief: demographics, active problems,
> medications, recent labs, allergies, encounters — six FHIR reads fanned out in
> parallel. Every claim you see carries a citation chip —"

*(Click one citation chip → popover.)*

> "— which resolves to the actual FHIR resource it came from. Under the hood the
> model must answer in typed, structured claims. A deterministic verifier does
> field-level comparison against the cited evidence and rejects on contradiction.
> Then the text on screen is re-rendered from the verified fields — the model's own
> prose is discarded. It cannot phrase its way past verification.
> This isn't theater: our pre-build audit found the demo dataset's medication rows
> ship with no usable dose text — and that exact finding crashed our first live
> integration, exactly as the audit predicted. The verifier is why a physician sees
> 'dose not specified — confirm before dosing' instead of an invented dose."

*(Scroll to the very bottom of the brief so it's visibly complete.)*

### [2:10–3:10] Multi-turn Q&A (click chips / type)

*(Click: "What are the patient's active problems?")*

> "Follow-ups run in the same session, same token, same verification gate.
> Active problems — grounded, cited."

*(Click: "What medications is the patient currently taking?")*

> "Medications — note the honest handling of missing data. The agent never fills
> a gap with a guess."

*(Click or type: "What are the most recent lab results?")*

> "Labs, with dates — stale results get flagged as stale rather than presented
> as current. And if I ask something the data can't support, it refuses with a
> canonical message instead of improvising. In a clinical setting, a confident
> wrong answer is the trust-killing failure — refusal is a feature."

### [3:10–3:50] Under the hood (screen: Langfuse trace tab, or stay on UI)

> "Every request mints a correlation ID that rides every log line, tool call, and
> LLM span into Langfuse: the full trace — six FHIR spans, the model generation with
> token counts and cost, and the verification verdict. That trace is also our HIPAA
> accountability record: it logs the OAuth client, the exact scopes exercised, per
> call — which OpenEMR's own audit log omits. Evals run in CI and gate every deploy;
> the eval suite is ten-for-ten green tonight."

### [3:50–4:20] Honesty + close (talking head)

> "What it deliberately doesn't do: no diagnosis, no treatment advice, no writes —
> read-only by construction, so worst-case prompt injection produces wrong words,
> never wrong writes. The 'what changed since last visit' delta tool is deferred and
> documented as a cut, not hidden. Synthetic Synthea data only.
> One patient, ninety seconds, a brief the physician can trust because every claim
> is verified against the chart — that's the product. Thanks."

---

## Fallback lines (if something misbehaves on camera)

- Brief renders but a section is empty → "No records returned for that category —
  and it says so explicitly rather than inventing content. Absence is rendered as
  absence."
- A chip refuses → "That's the verification gate doing its job — it refuses rather
  than guess. In this domain that's the correct behavior."
- Langfuse tab slow → skip it; say the observability line over the chat UI instead.
