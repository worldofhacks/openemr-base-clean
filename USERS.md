# USERS.md — Who this is for, and the moments it serves

> Stage 4 hard gate. This is the **source of truth** the finalized `ARCHITECTURE.md` traces back to: every capability the architecture builds must map to a use case here (UC#), and any capability without a trace does not get built (PRD rule). Persona and use cases are sourced from `docs/planning/PRESEARCH.md` and decision **D1** (`docs/planning/DECISIONS.md`, research **R10**). Safety constraints reference the pre-build audit (`AUDIT.md`, F-# findings).

---

## The one user

**A primary care physician (PCP) running a ~20-patient outpatient day**, working in the 30–90 second gaps *between* exam rooms, interrupted constantly, with EHR fatigue. Not an ED physician, not a hospitalist, not a nurse or admin — **one** narrow user, chosen deliberately so every latency, capability, and safety decision has a measurable target.

### Why this user and no other (the choice is evidence-backed, D1 / R10)

Three independent grounds, each verified against *this* codebase and *this* demo data — not asserted:

1. **The platform is ambulatory.** OpenEMR's core surface is outpatient practice management — appointment calendar, office encounters, insurance/billing controllers (`src/RestControllers/`: Appointment, Encounter, Insurance, Employer). There is no inpatient census / ADT / bed-management workflow. A hospitalist or ED persona fights the platform's own data model.
2. **The sanctioned demo data is primary-care-shaped.** The repo's only blessed sample-data path is Synthea (`CONTRIBUTING.md`), which by design models "the 10 most frequent reasons for **primary care** encounters and the 10 highest-morbidity chronic conditions" as birth-to-death longitudinal records — conditions, medications, allergies, immunizations, observations/labs, encounters — exported native FHIR R4. The **25-patient local set the agent is built against confirms this shape** (per patient: dozens of encounters, active problem lists, medication history, allergy records, labs). ED encounters appear only as scattered history events with no triage/acuity context; inpatient structure is effectively absent. A non-PCP persona would force *fabricated* data — violating the grounding principle from day one.
3. **PRD fit.** "A primary care physician with a 20-patient day" is the PRD's own first-listed example of a well-chosen narrow user. Narrow user + a crisp 90-second moment gives every decision a target to hit.

**Secondary users (documented, out of week-1 scope — `scope simplification`):** nurse (different ACL profile), resident (supervised access), practice admin (no clinical data). The architecture must not *preclude* them — authorization is role-based via OpenEMR SMART scopes, so adding them is configuration, not redesign — but they are not built this week.

---

## The moment: when the agent enters the physician's day

The physician has finished with the patient in Room 2 and has ninety seconds before Room 3. Today Room 3 is a 58-year-old with diabetes, hypertension, and a medication change from three months ago — a chart with years of history across a dozen screens. Reconstructing "who am I about to see, why, what changed, what's on file, what matters today" from dense EHR notes under that time pressure is the daily pain.

From the patient's chart in OpenEMR the physician clicks **Co-Pilot** (a SMART EHR-launch affordance that already exists as a near-zero-diff attach point — `SMARTLaunchController` on the demographics render event, F-A.3). A SMART-on-FHIR launch hands the agent the clinician's delegated OAuth token and the patient context; a session is created, pinned to *this* clinician and *this* patient (D12), with a correlation ID minted for the whole turn. The physician reads a **pre-visit brief** in the seconds they have, asks a follow-up or two in plain language, walks into Room 3 already oriented — and every clinical statement in front of them carries a citation back to a record in the chart.

Success = the physician walks in oriented in the time they actually have. Failure = the brief is **wrong, slow, or unverifiable** — any one of those loses their trust *permanently*, which is why the verification layer (§5) and the honest-refusal posture (D12) are load-bearing, not polish.

---

## Use cases (each traces to a capability; each earns "why a conversational agent")

The PRD rule: a capability with no use-case trace does not get built, and a use case must justify *why a multi-turn conversational agent* is the right shape rather than a static report or a search box. Each use case notes the FHIR data it touches and the audit-derived safety constraint that governs how the agent may speak about it.

### UC1 — The pre-visit brief (the core object of value)
**What:** on launch, a grounded one-screen summary of the patient about to be seen — active problems, active medications, recent/abnormal labs with dates, known allergies, last encounter and today's reason.
**Why a conversational agent (not a dashboard):** the brief is the *opening* of a dialogue, not the end of one. A static widget forces the physician to re-scan for whatever it didn't surface; the agent lets them immediately drill in ("why is she on metformin *and* glipizide?") in the same context, with the patient already loaded and the evidence already retrieved and cached. The value is the seconds saved *plus* the ability to interrogate without switching screens.
**Data touched (FHIR):** Patient, Condition, MedicationRequest, Observation, Encounter, AllergyIntolerance — the six independent reads fanned out in parallel (D10).
**Safety constraints (audit):** an empty allergy result renders **"no allergy records returned; confirm with patient,"** never "NKDA" (F-D.5). FHIR `status` fields are never surfaced verbatim — the stock Immunization mapper reports every completed vaccine as "patient refused" (F-D.1), so the brief must not repeat it. Every line is re-rendered from verified evidence fields (§5), never the model's free prose.

### UC2 — What changed since the last visit
**What:** a focused delta — new or changed problems, medication starts/stops/dose changes, new labs since the prior encounter.
**Why a conversational agent:** "what changed" is a *computed* question over longitudinal data, and the answer shape varies per patient (sometimes one med change, sometimes a new diagnosis). The delta itself is computed by a **deterministic tool** (the LLM never does the arithmetic — §5); the conversational layer lets the physician then ask "changed by whom?" or "show me the trend" without re-deriving context. A report can't anticipate the follow-up; the agent holds it.
**Data touched:** Encounter (to bound "since last visit"), Condition, MedicationRequest, Observation.
**Safety constraints:** resolved/inactive conditions are consumed, never dropped — the agent must reject a "no history of X" claim if an inactive match exists, and must never filter `clinical-status=active` (a broken upstream filter returns nothing, F-D.6). Medication changes with no usable dose render "dose not specified — confirm before dosing," never an invented dose (F-D.2).

### UC3 — Source-cited chart Q&A
**What:** free-text follow-ups about *this* patient — "when was her last A1c and what was it?", "is she on anything that interacts with a new ACE inhibitor?" (read-only lookup, not advice) — each answer carrying a citation chip to the FHIR record it came from.
**Why a conversational agent:** this is the definitional case for a multi-turn tool-using agent — open-ended natural-language questions over structured records, where the set of possible questions is unbounded and a fixed UI cannot enumerate them. Citation-per-claim is what makes it *trustable* rather than a chatbot.
**Data touched:** any of the six resources on demand; new FHIR calls only when the cached session data can't answer (F2, TTL cache).
**Safety constraints:** every claim carries an `evidence_id` resolved against the EvidencePacket; a claim that cannot be resolved is not stated as fact (§5). Stale labs are flagged with their date rather than implied current (F-D.6). Treatment-verb requests (start/stop/prescribe/order/diagnose) are refused, not answered (D12) — Q&A is *lookup*, never *advice*.

### UC4 — Evidence-backed attention flags
**What:** surfaced, cited flags the physician might otherwise miss in the time available — an abnormal recent lab, an allergy that matters for a likely prescription, a decade-stale value presented *as* stale.
**Why a conversational agent:** flags are only useful if the physician can immediately ask "why is this flagged?" and get the underlying evidence — a one-way alert without an interrogable rationale is the alert-fatigue failure mode. The agent presents the flag *and* holds the evidence for the follow-up.
**Data touched:** Observation (labs/vitals), AllergyIntolerance, Condition.
**Safety constraints:** allergy `criticality` is null across the entire dataset (a real mapper bug, F-D.4) — the agent must **never infer, rank, or deprioritize** allergy risk from criticality, and never treat absence of a flag as reassurance. A flag is a prompt to look, never a clinical judgment.

---

## Non-goals (explicit, not implied — D12)

The agent is **read-only by construction**, not by policy. It does **not**:

- **Diagnose** or offer differential diagnoses.
- **Recommend treatment**, or say what to start/stop/change.
- **Prescribe, order labs/imaging, or place any order.**
- **Message patients** or write/edit anything in the chart (no write scopes — the agent holds only read scopes).
- **Search across patients** — a session is pinned to one (clinician, patient) pair; a different patient requires a fresh launch (the agent-side pin is the real enforcement, since OpenEMR's own patient-access check is a stub, F-S.2).
- **Act as anyone but the launching clinician** — no service super-user, never `client_credentials` (which would attribute access to a synthetic system user, F-S.5).
- **State an unverifiable clinical fact as fact** — uncertainty and absence are communicated honestly (a confident wrong answer is the trust-killing failure mode; a clear refusal preserves trust).

**Refusal is a feature.** Canonical deterministic refusals cover: a record with a deceased indicator (hard-stop before any summarization, D12), ambiguous data the agent can't resolve reliably, wrong-patient / out-of-scope requests, treatment-advice requests, and expired sessions (re-launch from the chart).

---

## Traceability (capabilities must map here)

| Use case | Core capability ARCHITECTURE.md must provide | Primary decisions |
|----------|----------------------------------------------|-------------------|
| UC1 pre-visit brief | Parallel 6-read FHIR fan-out → EvidencePacket → verified, cited brief | D2, D9, D10, D7/§5 |
| UC2 what-changed | Deterministic delta tool + conversational drill-in | D7/§5 (deterministic composites), D9 |
| UC3 cited chart Q&A | Multi-turn tool-use loop, citation-per-claim, refusal on advice | D6, D7/§5, D12 |
| UC4 attention flags | Constraint/phrasing rules over evidence, interrogable rationale | D7/§5 (rules from F-D.1/4/5/6) |
| All | SMART launch, session pin, correlation ID, Langfuse trace, degradation | D2, D12, D5, D10, D13 |

*Anything ARCHITECTURE.md proposes that does not map to a row above must be cut or explicitly flagged as out-of-scope.*
