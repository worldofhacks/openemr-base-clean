# Clinical Co-Pilot Alert Checker and Runbooks

This is the F3 operational runbook for the independent checker in
`agent/ops/alert_checker.py`. It implements the four alerts in
`ARCHITECTURE.md` §7 without importing the serving application or placing a
notification secret in the repository.

The checker is read-only. It polls aggregate-safe observation fields from
Langfuse and sends aggregate counts/rates to a configured webhook. It never
sends prompts, completions, trace IDs, correlation IDs, patient or clinician
identifiers, API response bodies, Langfuse keys, or the webhook URL.

## API contract and current assumptions

The checker follows Langfuse's current recommended Cloud data-extraction path:

- `GET /api/public/v2/observations` with project keys via HTTP Basic Auth
  (public key as username, secret key as password).
- Every query supplies `fromStartTime` and `toStartTime`, requests only
  `core,basic,metadata,trace_context`, and follows `meta.cursor` until it is
  null.
- Observations are grouped locally by `traceId`, as Langfuse recommends when a
  consumer needs request-level counts or durations from the observations-first
  API.

Primary references:

- [Langfuse Public API and Basic Auth](https://langfuse.com/docs/api-and-data-platform/features/public-api)
- [Langfuse Observations API v2 fields and cursor pagination](https://langfuse.com/docs/api-and-data-platform/features/observations-api)
- [Langfuse Metrics API guidance for trace-level grouping](https://langfuse.com/docs/metrics/features/metrics-api)

Observations API v2 is Cloud-only. That matches D5's Langfuse Cloud deployment
decision. A future self-host migration must either provide the v2 endpoint or
add a separately tested legacy adapter; the checker does not silently fall back
to a differently shaped API.

Langfuse documents up to ten minutes of v2 data delay for observations written
by Python SDK versions below 4.0 or ingestion that lacks version 4 markers. The
default `COPILOT_ALERT_SETTLE_DELAY_SECONDS=600` therefore evaluates a closed
window ending ten minutes in the past. Set it lower only after the live project
proves its ingestion path is current enough. This delay applies to detection,
not to webhook delivery.

The metrics depend on the following trace conventions:

| Signal | Trace convention |
|---|---|
| Request population | `traceName=previsit-brief` (configurable) |
| Request latency | Root `metadata.request_latency_ms` when available; otherwise sequential child `metadata.latency_ms` values plus the longest parallel `fhir.*` span, falling back to completed-root `endTime-startTime`; p95 uses nearest rank |
| Request error | Root observation has `level=ERROR` or error metadata, or the trace carries `request:error` |
| Tool failure | Observation name starts `fhir.` or `tool.` (configurable) and has `level=ERROR` or error metadata |
| LLM fallback | Trace context contains `fallback:<kind>` where kind is not `none`; the existing sink emits this tag |

Known dependency: baseline FHIR reads and failures are not complete in Langfuse
until CXR-05 moves the trace boundary ahead of the fan-out and emits the real
tool spans. Until then, the checker is runnable but tool-failure and request-error
coverage is limited to what the current trace exporter records. Do not interpret
a zero rate as proof that untraced failures did not occur.

The current sink creates Langfuse observations during export, after the clinical
work has completed, and places measured step durations in `metadata.latency_ms`.
For that reason, export-span wall time is only the final latency fallback. The
checker treats `fhir.*` spans as one parallel fan-out and uses its longest span
as that stage's critical path; other recorded stages are summed. CXR-05 should
eventually emit one explicit root `request_latency_ms`; the checker already
gives that field precedence.

Live API and webhook validation are pending the owner-provisioned Langfuse
project keys and webhook endpoint. Unit tests freeze the documented request and
response shapes without network access.

## Configuration and operation

Run one window during setup:

```bash
cd agent
python -m ops.alert_checker --once
```

Run continuously (the default interval is 60 seconds):

```bash
cd agent
python -m ops.alert_checker
```

Required variables:

| Variable | Meaning |
|---|---|
| `LANGFUSE_BASE_URL` | Langfuse origin, such as the project region's Cloud origin; `LANGFUSE_HOST` is accepted as a compatibility alias |
| `LANGFUSE_PUBLIC_KEY` | Basic Auth username; secret, never logged |
| `LANGFUSE_SECRET_KEY` | Basic Auth password; secret, never logged |
| `COPILOT_ALERT_WEBHOOK_URL` | HTTPS webhook/Slack incoming-webhook URL; secret, never logged or included in payloads |

For a local query-only check, explicitly set
`COPILOT_ALERT_DRY_RUN=true`; otherwise a webhook is required and missing
delivery configuration exits nonzero.

Tunable variables and defaults:

| Variable | Default | Constraint |
|---|---:|---|
| `COPILOT_ALERT_P95_LATENCY_MS` | `15000` | `> 0` |
| `COPILOT_ALERT_REQUEST_ERROR_RATE` | `0.05` | ratio from `0` to `1` |
| `COPILOT_ALERT_TOOL_FAILURE_RATE` | `0.10` | ratio from `0` to `1` |
| `COPILOT_ALERT_LLM_FALLBACK_RATE` | `0.10` | ratio from `0` to `1`; operational starting point for §7's open `X%`, re-baseline from live traffic |
| `COPILOT_ALERT_WINDOW_MINUTES` | `15` | positive integer |
| `COPILOT_ALERT_SETTLE_DELAY_SECONDS` | `600` | nonnegative integer |
| `COPILOT_ALERT_INTERVAL_SECONDS` | `60` | positive number |
| `COPILOT_ALERT_HTTP_TIMEOUT_SECONDS` | `10` | positive number |
| `COPILOT_ALERT_PAGE_LIMIT` | `1000` | `1..1000`, the documented v2 maximum |
| `COPILOT_ALERT_MAX_PAGES` | `20` | positive integer; exceeding it fails the query instead of sampling silently |
| `COPILOT_ALERT_TRACE_NAME` | `previsit-brief` | nonempty string |
| `COPILOT_ALERT_TOOL_PREFIXES` | `fhir.,tool.` | comma-separated nonempty prefixes; legacy singular `COPILOT_ALERT_TOOL_PREFIX` is accepted |
| `COPILOT_ALERT_PARALLEL_TOOL_PREFIX` | `fhir.` | nonempty prefix for the parallel fan-out whose longest recorded duration is used |

Threshold comparison is strictly greater-than. An empty request window does not
fire rate or latency alerts. A metric with no denominator is reported as zero;
request and completed-request counts remain visible in the sanitized cycle log.

The webhook payload includes a Slack-compatible `text` field plus structured
`window`, `metrics`, and `alerts` objects. If the receiver accepts only Slack's
minimal schema, place a small webhook adapter in front of it or configure a
Slack incoming webhook that ignores extra fields. No authentication header is
sent to the webhook; possession of its uncommitted URL is the delivery
credential.

Continuous breaches notify once per checker process. The alert rearms after a
clean window, so a later recurrence notifies again. This prevents one-minute
notification storms while preserving a new incident signal. Process restarts
reset this in-memory deduplication; downstream receivers should also deduplicate
if they require restart-stable incident identity.

Exit codes:

| Code | Meaning |
|---:|---|
| `0` | Window evaluated successfully, including no-data/no-alert windows |
| `2` | Invalid or incomplete configuration |
| `3` | Langfuse query/network/response-shape failure |
| `4` | A firing alert could not be delivered |
| `5` | Unexpected checker failure |

Errors are emitted as sanitized JSON lines. They intentionally omit exception
messages from network/query failures because upstream messages can echo URLs,
credentials, or data.

## Alert: p95 request latency

Default threshold: `> 15,000 ms` over the completed requests in the window.

Meaning: at least five percent of completed pre-visit requests are taking longer
than the clinical reading budget. Requests without explicit, step-derived, or
completed-root timing do not enter the percentile; compare `completed_requests`
with `requests` before concluding the latency distribution is healthy.

Likely causes:

- Anthropic latency or rate limiting.
- The OpenEMR laboratory N+1 path on a large chart (F-P.3).
- A slow FHIR dependency, retry, or total-turn-budget pressure.
- Synchronous Langfuse export latency (CXR-13) until isolated.

First on-call action:

1. Open the Langfuse trace breakdown and compare FHIR/tool spans with
   `llm.complete` spans.
2. Check Anthropic status and the agent `/ready` dependency body.
3. Compare the fallback-rate alert; elevated fallback plus latency usually
   indicates provider retries/timeouts.

Escalate:

- Provider-side: follow provider incident status and verify D13 remains grounded.
- FHIR-side: inspect OpenEMR health and the large-chart tool span; do not raise
  timeouts blindly past the total turn budget.
- Deploy-correlated and sustained for 15 minutes: roll back through Railway or
  revert-and-redeploy per §7.

Resolved when p95 remains below the re-baselined threshold for two closed
windows and completed/request counts are consistent.

## Alert: request error rate

Default threshold: `> 5%` of traced requests.

Meaning: root request spans are ending in an explicit error state. This is not
the same as a safe D13 fallback; a fallback is counted separately unless the
request root is also marked erroneous.

Likely causes:

- Session-store or other hard dependency failure.
- Unhandled serving exception or bad deploy.
- LLM/FHIR failure escaping the documented degradation boundary.
- Expired/invalid authentication represented incorrectly at the request root.

First on-call action:

1. Group failing root observations by stage/name in Langfuse; never copy raw
   inputs into an incident channel.
2. Check `/ready`, then the service's sanitized Railway logs by correlation ID.
3. Compare the deployment timestamp and error-rate onset.

Escalate:

- Deploy-correlated: roll back immediately if the prior deployment is healthy.
- Dependency-correlated: page the owning service/provider and confirm the agent
  is failing closed rather than serving unpinned or unsupported content.
- Security/auth correlation: treat repeated cross-patient or authorization
  failures as a security review, not a reliability tweak.

Resolved when the rate is below 5% for two closed windows and the triggering
error class has an owner or rollback record.

## Alert: tool failure rate

Default threshold: `> 10%` of traced `fhir.*` and `tool.*` observations.

Meaning: individual read tools are failing often enough that briefs may be
partial. The partial-answer contract requires the physician-facing response to
name the unavailable category; the alert does not prove that presentation rule
was met, so verify it separately.

Likely causes:

- OpenEMR FHIR instability, timeout, or large lab query.
- Missing granted scope or token expiry.
- Mapper/interop error for a specific resource type.
- Fan-out concurrency above the deployed OpenEMR capacity.

First on-call action:

1. Group failed observations by `fhir.*`/`tool.*` name and compare status metadata.
2. Check OpenEMR `/fhir/metadata`, agent `/ready`, and token age/scope coverage.
3. Confirm the served brief explicitly names missing data and does not translate
   absence into a negative claim (especially allergies).

Escalate:

- One tool only: assign the FHIR resource/mapping owner; preserve the other
  five results.
- All tools: investigate OpenEMR/auth before Anthropic.
- Load-correlated: use F6 results to reduce fan-out/concurrency rather than
  masking failure with unbounded retries.

Resolved when the rate is below 10% for two closed windows and a synthetic
failure still produces the named partial-answer behavior.

## Alert: LLM fallback rate

Default operational threshold: `> 10%` of traced requests. §7 intentionally left
this as `X%`; keep the environment override and re-baseline it from live traffic.

Meaning: requests are being served by D13 deterministic fallback rather than the
verified LLM narration. The response remains grounded, but clinical synthesis is
absent and the fallback banner must be visible.

Likely causes:

- Anthropic outage, 429s, timeout, or client error.
- Cost cap or tool-loop convergence limit.
- Prompt/output-size pressure.
- Verification rejects every model claim and supersedes it with grounded data.

First on-call action:

1. Group traces by `fallback:<kind>` and inspect the ordered, sanitized spans.
2. Verify the UI is showing the deterministic fallback banner and real citation
   chips; raw model prose must never be exposed as a workaround.
3. Check Anthropic status, cost-cap state, and recent model/config changes.

Escalate:

- Provider incident: accept D13 temporarily, notify the provider owner, and
  monitor request latency/error rate.
- Verification spike: treat as a model/verifier compatibility incident; keep
  fail-closed behavior and use frozen invariants before changing matching rules.
- Deploy-correlated: roll back if the fallback rate changed materially after a
  release.

Resolved when the rate is below the live re-baselined threshold for two closed
windows and sample responses confirm verified narration or an intentional D13
outcome.

## Verification and synthetic delivery

The isolated suite validates API pagination/auth shape, all four thresholds,
continuous-breach deduplication, webhook sanitization, and nonzero failure exits:

```bash
cd agent
pytest -q ops/tests
```

`test_checker_notifies_once_per_continuous_breach_and_rearms_after_resolution`
is the synthetic breach proof: one window fires all four alerts, the identical
next window does not notify again, a clean window rearms them, and the next
breach sends a second notification.

Before declaring live delivery complete:

1. Provision the Langfuse Cloud project/keys (E7.0) and a test webhook outside
   version control.
2. Run `--once` with a closed window known to contain a synthetic breach.
3. Confirm exactly one webhook message, aggregate fields only, and a non-secret
   checker log.
4. Remove/rotate the test webhook after validation and record the live evidence
   in the DEVLOG or Final submission artifact.
