# Clinical Co-Pilot social posts

## X

> Built a Clinical Co-Pilot for OpenEMR: a read-only SMART-on-FHIR sidecar that turns Synthea demo charts into cited pre-visit briefs. Deterministic verify-then-flush blocks unsupported claims before display. Live: https://agent-production-9f62.up.railway.app @GauntletAI

Suggested media: `chat-ui-verified-brief.png`.

## LinkedIn

> I built and deployed a Clinical Co-Pilot for OpenEMR around one narrow moment: helping a primary-care physician get oriented in the 90 seconds between exam rooms.
>
> The architecture is an external SMART-on-FHIR sidecar. OpenEMR remains the system of record and authorization authority; the agent reads FHIR R4 with the clinician's delegated token, with no database credentials, write scopes, or chart mutations.
>
> The part I care about most is the deterministic **verify-then-flush** path:
>
> 1. Read-only tools retrieve the relevant chart records into a typed evidence packet.
> 2. The model drafts typed claims carrying evidence IDs.
> 3. A deterministic verifier checks each claim against the cited fields and clinical constraints.
> 4. A deterministic templater renders only verified fields for display.
>
> Raw model prose never goes directly to the physician. Unsupported or contradictory claims are blocked instead of being polished into something convincing, and displayed clinical claims trace back to source evidence.
>
> The current deployment is a synthetic-data demo using Synthea patients only—no real PHI. It is read-only by construction and does not diagnose, recommend treatment, prescribe, order, message patients, or write to the chart.
>
> Live agent: https://agent-production-9f62.up.railway.app (the patient-specific flow requires a SMART launch from OpenEMR).
>
> Built for @GauntletAI.
>
> #HealthIT #FHIR #ClinicalAI #OpenEMR

Suggested media: `chat-ui-verified-brief.png` followed by
`chat-ui-multiturn.png`.
