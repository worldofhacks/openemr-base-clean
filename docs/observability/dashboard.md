# Clinical Co-Pilot Langfuse dashboard

> **Status (2026-07-13):** F2 metric definitions, live production telemetry, filters, API
> totals, and reusable widget IDs are finalized. Six widgets are visible on the live grid;
> four API-created widgets require the owner's one-click placement because Langfuse does not
> expose dashboard-grid mutation through its public API. That bounded UI handoff is recorded
> below; no computer-control workaround was used.
>
> **Dashboard:** [Clinical Co-Pilot / Production](https://us.cloud.langfuse.com/project/cmrie27hr0kw7ad0dpgmwsb2u/dashboards/cmrifybt80kpxad0dh10bhmln)
>
> **Anchors:** F2, ARCHITECTURE.md §7, D5, D10-rev, F-C.1/F-C.2.

## Purpose and data boundary

The `Clinical Co-Pilot / Production` dashboard is the operating view over the authoritative
agent-side accountability trace. It must answer four questions without opening individual
prompt or response bodies:

1. Is the service receiving and completing requests?
2. Where is latency or failure occurring—FHIR, verification, or the model?
3. Is verify-then-flush passing, flagging, blocking, or refusing claims as expected?
4. What does each served request cost, and how often does deterministic fallback take over?

Only synthetic Synthea traffic is permitted in the demo project. Widgets use trace names,
observation names, tags, scores, timings, token usage, and PHI-minimized metadata. They do
not display prompt/response bodies, raw patient or clinician identifiers, OAuth tokens, or
FHIR payloads. Patient and clinician dimensions remain one-way hashes under D5.

Langfuse's [custom-dashboard documentation](https://langfuse.com/docs/metrics/features/custom-dashboards)
defines dashboard/widget behavior. The
[Metrics API v2 documentation](https://langfuse.com/docs/metrics/features/metrics-api) is the
machine-readable contract used by F3 and F7; dashboard totals and API totals must agree over
the same UTC window and filters.

## Global dashboard settings

| Setting | Value |
|---|---|
| Dashboard name | `Clinical Co-Pilot / Production` |
| Environment filter | `production` |
| Trace name | `previsit-brief` |
| Default window | Last 24 hours; UTC |
| Refresh | Automatic/live |
| Identifier display | Hashed IDs only; no raw input/output columns |
| Project region | Langfuse US Cloud for synthetic demo data |

## Dashboard inventory

Request-level rates use distinct `previsit-brief` traces as their denominator. Verification
rates instead use `verify` observations because one request can contain many independently
verified claims. This prevents a claim-heavy brief from distorting request error/fallback
rates and prevents a multi-claim brief from collapsing verification safety into one bit.

### Visible on the live grid

| Widget | Live source and calculation | Operational use |
|---|---|---|
| Traces | Distinct `previsit-brief` traces | Traffic and denominator for request-level rates |
| Model Costs | Native Langfuse generation cost | Provider-model spend visible to Langfuse |
| Trace Latency p50/p95 | Native root trace duration | Serving latency and F4 threshold evidence |
| FHIR tool-call count | Counts split across `fhir.get_patient_summary`, `fhir.get_conditions`, `fhir.get_active_medications`, `fhir.get_recent_labs`, `fhir.get_encounters`, and `fhir.get_allergies` | Confirms the six-read D10 fan-out is traced |
| Request error rate | Error-level root traces ÷ distinct traces | Route/dependency failures, separate from safe fallback |
| LLM retry/rework count | Trace `llm_calls` / generation count, interpreted as repeated model work | Provider retry/rework signal without treating normal verification as an error |

### Created through the public API; owner grid placement remains

| Reusable widget | Widget ID | Calculation after placement |
|---|---|---|
| Verification passes | `cmrigkkpe0kllad0ds1szao7j` | `pass` verdict observations |
| Verification non-pass numerator | `cmrigkuzp0l5jad0dicysa6v0` | `flagged + blocked + refused` verdict observations |
| LLM fallback numerator | `cmrigkv3f0l5mad0ddlmk25pz` | Distinct traces where `fallback_kind != none`; divide by Traces |
| Native LLM cost/request | `cmrigkv6l0l5pad0dzbta02l8` | Native generation cost ÷ distinct traces |

The public create endpoint creates a reusable widget definition but does **not** place that
widget on a dashboard grid. Grid placement is available only through the dashboard UI under
the official [custom-dashboard](https://langfuse.com/docs/metrics/features/custom-dashboards)
and [API](https://langfuse.com/docs/metrics/features/metrics-api) contract. The four widget
IDs above therefore prove creation, not rendering. The remaining F2 owner hand-off is to add
those existing widgets to the linked dashboard and visually confirm their shared filters.

## Emission contract: live

App fix commit `4dd1826` updated the sink for Langfuse SDK v4 and emits a native root span
plus native `generation` observations with usage, cost, and duration. Railway deployment
`ee3c97f8-a02f-4313-b53a-996a9f3d3ba8` carries that fix. Its `/ready` Langfuse probe returned
HTTP 200 and the full readiness response was green before validation.

| Dashboard requirement | Live emitted form |
|---|---|
| Request duration and error | Root `previsit-brief` span covering the serving interval, with native start/end and level/outcome |
| Provider cost/tokens | Native `llm` generation with model, usage details, and cost details |
| Retry/rework count | Stable `llm_calls` metadata plus generation count |
| Tool/FHIR outcome | One named `fhir.*` observation per call with status and duration |
| Verification result | One `verify` observation per claim verdict |
| Fallback/refusal | Stable `fallback:*` tag and `fallback_kind` metadata |

### Where to look in Langfuse

Use the dashboard above for aggregate F2 health. For one request, open **Tracing**, filter to
`environment=production` and trace name `previsit-brief`, then expand the trace in this order:

- the root `previsit-brief` observation for request outcome, duration, source/fallback metadata,
  and the PHI-minimized D5/F-C.1 accountability context;
- the six `fhir.*` observations for read name, outcome, duration, and — only with the approved
  content switch — the normalized typed tool result;
- the `llm` generation for model, usage, native cost, and, with that switch, the exact model
  prompt/completion;
- the `verify` observations for each `pass`, `flagged`, `blocked`, or `refused` verdict and, with
  that switch, the submitted claim; the trace/root output carries the verified served answer.

On that live trace, open the **Scores** panel for the stable request-level fields
`claims_submitted`, `claims_verified`, `claims_dropped`, `verification_drop_rate`, `source`, and
`degraded`. For the offline gate, open **Datasets** → `clinical-copilot-offline-evals` → the
`eval-gate-<commit-or-timestamp>` run. Its item traces carry `offline_gate_passed` and, when the
case yields verifier accounting, the applicable live score names above. The checked-in JSON and
offline process exit remain authoritative; the Langfuse run is the review/drill-down surface.

If trace content is blank while the spans and metrics are present, first check the owner-approved
Synthea-demo `LANGFUSE_LOG_CONTENT` switch; that is an intentional privacy state, not a broken trace. Never paste keys,
tokens, raw authorization headers, or trace content into dashboard documentation or screenshots.
The D16 content policy remains **pending owner finalize sign-off**; D5/F-C.1 accountability and
§7 aggregate telemetry do not depend on that approval.

CLI/API validation on 2026-07-12 (synthetic data only) produced two concrete drill-down points:

- **Dataset:** `clinical-copilot-offline-evals` → run ID
  `4b7d5fdf-dfd4-4981-bc32-2e84cceeca21` (`eval-gate-d16-premerge`), 10/10 linked item traces
  with `offline_gate_passed` and verifier-accounting scores.
- **Live request:** trace `e81c974b3aa5aac45c631c5fb0c5c866`, a fresh José SMART launch
  with content enabled: exact provider prompt and served brief, raw structured answer metadata,
  6/6 FHIR content spans, 14/14 verifier claim spans, and the six request-level scores above.

### Metric semantics

- **Error is not fallback.** A successfully served deterministic fallback remains a completed
  request and contributes only to fallback rate unless a separate serving error is recorded.
- **A blocked claim is not a failed request.** `flagged`, `blocked`, and `refused` feed the
  verification view. Request error changes only when the serving operation itself fails.
- **Repeated model work needs context.** Multiple generation observations can represent
  retries or rework; the widget is an operational signal, not proof of a transport retry
  without the associated trace metadata.
- **Native cost and full provider economics are different views.** Native Langfuse generation
  cost powers the live dashboard. F7 reconstructs the full provider economics when token
  classes omitted from native cost—specifically cache-creation tokens in this window—must be
  priced.
- **Tool failures are observation outcomes.** F3 computes its alerting ratio from failed/error
  `fhir.*` observations over all `fhir.*` observations using the same UTC filter.

## Live validation

The bounded validation window contains only synthetic demo activity. Counts below were
queried without copying session IDs, prompts, patient identifiers, response bodies, or API
keys into this repository.

| Evidence | Live result |
|---|---|
| Agent fix / Railway deployment | `4dd1826` / `ee3c97f8-a02f-4313-b53a-996a9f3d3ba8` |
| UTC validation window | 2026-07-12T23:37:24.594Z–2026-07-12T23:39:20.050Z |
| `/ready` Langfuse check | HTTP 200; full deployment readiness green |
| Trace count | 3 `previsit-brief` traces |
| Observation coverage | 130 total: 3 root + 3 `llm` generations + 18 FHIR + 106 `verify` |
| FHIR coverage | Six named reads per trace; 18/18 observations present |
| Native Langfuse model cost | $0.11687970 total; $0.03895990 per generation/request |
| F7 reconstructed full provider economics | $0.24495345 total; $0.08165115 per request |
| Cost reconciliation | F7 is $0.12807375 higher because cache-creation tokens were absent from native Langfuse cost |
| Verification verdicts | 33 `pass`, 20 `flagged`, 52 `blocked`, 1 `refused` (106 total) |
| Fallback tags | `fallback:none` on 3/3 traces; 0 fallback traces |
| Widgets visibly rendered | Traces, Model Costs, Trace Latency p50/p95, FHIR tool-call count, Request error rate, LLM retry/rework count |
| Reusable widgets awaiting grid placement | Four: verification passes, verification non-pass, LLM fallback numerator, native LLM cost/request |

There are three generations and three requests in this window, so the native average is
simultaneously per generation and per request. That equivalence is specific to this sample;
future windows with more than one generation per trace must keep the two denominators
separate.

## Rendered-grid evidence boundary

The owner explicitly stopped computer-control and screenshot work and directed this Final
pass not to drive the dashboard UI. A screenshot of the current partial grid would be stale
the moment the four widgets are placed and would overstate completion if presented as the
full required dashboard. The live dashboard link, exact widget IDs, calculations, filters,
and API totals above are the durable evidence. The owner's remaining one-click action is to
place the four existing widgets; a sanitized full-grid screenshot can then be captured by
the owner for the submission video without exposing prompt/response bodies.

## Reproduction and ownership

The live checks can be reproduced without reading application source:

1. Confirm deployment `ee3c97f8-a02f-4313-b53a-996a9f3d3ba8` reports a green `/ready`,
   including HTTP 200 for Langfuse.
2. Use the Langfuse SDK or Metrics API v2 with the exact UTC window, `production`
   environment, and `previsit-brief` trace filter to query trace, observation, latency,
   verdict, fallback, and native-cost aggregates.
3. Reconcile native generation cost with F7's token-class reconstruction; do not force the
   two values to match when cache-creation tokens are absent from Langfuse native cost.
4. Confirm the six `fhir.*` names produce 18 observations across the three traces and that
   the verification counts sum to 106.
5. In the Langfuse UI, open the linked dashboard and place the four existing reusable widget
   IDs on the grid. The API cannot perform this layout step.
6. Verify that every placed widget shares the environment, trace-name, and UTC filters before
   using it for F3/F7 operations.

The deployment owner owns project membership, keys, retention, and dashboard grid changes.
The on-call owner uses this dashboard with `docs/observability/runbooks.md`; changes to metric
semantics require a documentation update in the same PR.
