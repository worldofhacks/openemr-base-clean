# Clinical Co-Pilot Langfuse dashboard

> **Status:** Console configuration and live screenshot are pending the Langfuse Cloud
> project/key handoff. This file is a working specification until the live-validation table
> is populated; do not mark F2 complete from this draft alone.
>
> **Anchors:** F2, ARCHITECTURE.md §7, D5, D10-rev, F-C.1/F-C.2.

## Purpose and data boundary

The `Clinical Co-Pilot / Production` dashboard is the operating view over the authoritative
agent-side accountability trace. It must answer four questions without opening individual
prompt or response bodies:

1. Is the service receiving and completing requests?
2. Where is latency or failure occurring—FHIR, verification, or the model?
3. Is verify-then-flush passing, blocking, or refusing claims as expected?
4. What does each served request cost, and how often does deterministic fallback take over?

Only synthetic Synthea traffic is permitted in the demo project. Widgets use trace names,
observation names, tags, scores, timings, token usage, and PHI-minimized metadata. They do
not display prompt/response bodies, raw patient or clinician identifiers, OAuth tokens, or
FHIR payloads. Patient and clinician dimensions remain one-way hashes under D5.

Langfuse's [custom-dashboard documentation](https://langfuse.com/docs/metrics/features/custom-dashboards)
defines the UI workflow. The [Metrics API v2 documentation](https://langfuse.com/docs/metrics/features/metrics-api)
is the machine-readable definition used by F3 and F7; dashboard totals and API totals must
agree over the same UTC window and filters.

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

## Required widgets

The dashboard is incomplete unless every row below is visible with live data. Rates use a
single denominator—distinct `previsit-brief` traces in the selected window—so a tool-heavy
request cannot inflate a request-level percentage.

| Widget | Source and calculation | Visualization | Operational use |
|---|---|---|---|
| Request count | Distinct `previsit-brief` traces | Big number + hourly time series | Traffic and the denominator for all request rates |
| Error rate | Distinct traces carrying an error outcome ÷ request count | Percentage + hourly time series | Route/dependency failures; separate from safe fallback |
| Request latency p50 / p95 | Trace latency, p50 and p95 | Two-value chart + time series | F4 baseline and the p95 alert threshold |
| Tool-call count | Observation count where name begins `tool.`; split by observation name | Stacked bar | Volume and tool mix, including the six baseline FHIR reads once traced |
| Retry count | Observations explicitly tagged as retry attempts; split by stage/reason | Big number + bar | Provider/network instability without conflating normal tool-loop iterations |
| Verification pass/fail rate | `verification_verdict` categorical scores or equivalent verdict observations, grouped `pass`, `flagged`, `blocked`, `refused` | 100% stacked bar | Verify-then-flush safety behavior; pass rate and fail/drop rate |
| Cost per request | Sum native generation cost ÷ distinct request traces; show average and p95 | Currency number + time series | F7 unit economics and outlier detection |
| LLM-fallback rate | Traces with `fallback_kind != none` ÷ request count | Percentage + breakdown | D13 degradation frequency |
| Refusal-kind breakdown | Refused traces grouped by deterministic refusal kind | Bar | Distinguish deceased-patient hard stops from failures |

### Emission contract gate

The dashboard can only aggregate fields that Langfuse ingests as native metrics or stable
categorical filters. Replaying a short export span after the request and copying a number
into metadata does not create a native latency or cost measure. Before console configuration,
the deployed emission must prove this contract:

| Dashboard requirement | Required emitted form |
|---|---|
| Request duration and error | One root request observation covering the real serving interval, with native start/end and error level/outcome |
| Provider cost/tokens | `llm.complete` as a Langfuse `generation` carrying model, `usage_details`, and `cost_details` |
| Retry count | Explicit retry attempt/reason metadata; normal tool-loop iterations remain untagged |
| Tool/FHIR outcome | One `fhir.*`/`tool.*` observation per call with stable status |
| Verification result | One `verify` observation or score per verdict with categorical verdict |
| Fallback/refusal | Stable trace tag/field naming the fallback or refusal kind |

As of the local `final/langfuse-emission` commit `620200e`, FHIR and per-verdict spans plus
fallback tags satisfy the last three rows. Native root duration/error, explicit retry, and
native generation usage/cost remain app-owned blockers. Do not configure zero-valued or
export-duration widgets and present them as request latency, error, retry, or cost evidence.

### Metric semantics

- **Error is not fallback.** A successfully served deterministic fallback remains a completed
  request and contributes only to fallback rate unless a separate route/dependency error is
  recorded. This prevents safe D13 behavior from making availability look worse than it is.
- **A blocked claim is not a failed request.** `blocked` and `refused` feed the verification
  chart. The request error numerator changes only when the serving operation itself fails.
- **Retries are explicit.** Multiple `llm.complete` observations can be normal tool-loop
  iterations. Count a retry only when its trace metadata/observation marks it as a retry.
- **Cost is native usage cost.** Do not average a `cost_usd` string copied into metadata when
  Langfuse has native generation usage/cost; the F7 export must reconcile to provider billing.
- **Tool failures are observation outcomes.** F3 computes the alerting ratio from `tool.*`
  observations with failed/error outcome over all `tool.*` observations, using the same
  definition displayed in the tool widget.

## Live validation

Populate this table from one bounded authenticated synthetic `/chat` run after Railway has
all three `LANGFUSE_*` variables and the deployed agent emits the complete trace. Record
counts only—never paste a session ID, prompt, patient identifier, response body, or API key.

| Evidence | Result |
|---|---|
| Railway deployment ID / agent commit | **PENDING** |
| UTC validation window | **PENDING** |
| `/ready` Langfuse check | **PENDING** |
| Trace present with correlation/accountability metadata | **PENDING** |
| FHIR/tool, LLM, and verify observations present | **PENDING** |
| Token usage and native cost present | **PENDING** |
| Verification verdicts and fallback/refusal fields queryable | **PENDING** |
| All required widgets return live data | **PENDING** |

## Screenshot

Add the sanitized live dashboard capture at `docs/observability/langfuse-dashboard.png` and
embed it here only after the validation table is complete. The capture must show every
required widget and must not expose API keys, raw prompts/responses, or unhashed identifiers.

## Reproduction and ownership

1. Sign in to `https://us.cloud.langfuse.com` through GitHub or Google SSO.
2. Select the Clinical Co-Pilot project and create the dashboard/global filters above.
3. Create each widget from the Required widgets table; keep all time windows identical.
4. Run one bounded synthetic authenticated request and confirm the trace before accepting
   any zero-valued widget as valid.
5. Cross-check request count, latency, cost, tool failures, and fallback totals with the F3
   Metrics API checker for the same UTC window.
6. Save the sanitized screenshot and record the immutable agent deployment/commit here.

The deployment owner owns project membership, keys, retention, and dashboard changes. The
on-call owner uses this dashboard with `docs/observability/runbooks.md`; changes to metric
semantics require a documentation update in the same PR.
