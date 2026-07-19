# Ops: Week 2 observability wiring (R05 / AF-P1-04)

This directory owns the operational side of the Week 2 observability contract
(PDF p.5 Core Req 7, pp.6–7): the alert definitions, the scheduled evaluator, the
dashboard definition and its documented mapping, and the Week-1 Langfuse checker.

## The structured event lane (production sink)

Production composition (`app/service.py`) wires `EventEmitter` to
`StructuredLogEventSink` (`app/observability/events.py`): one JSON line per
registry-validated `LogEventEnvelope` on stdout, beside the structured application
logs. Every line carries `log_type: "w2.event"`, the event type, and the
`correlation_id` / `job_id` / `case_id` identifiers, so the platform log export is
searchable by any of the three. Attributes passed the closed event registry before
reaching the sink — clinical values, free text, identifiers, and unknown keys are
rejected upstream, so this lane is PHI-free by construction. Tests inject
`InMemoryEventSink` through the same `AgentServices(event_sink=...)` seam.

To export an events file for the alert checker from a Railway deployment:

```bash
railway logs --service <agent-service> --json \
  | jq -c 'select(.message | test("\"log_type\":\"w2.event\"")) | .message | fromjson? // empty' \
  > w2-events.jsonl
# Lines that are already raw JSON events (stdout lane) can be filtered more simply:
railway logs --service <agent-service> --json \
  | jq -c 'fromjson? // empty | select(.log_type == "w2.event")' > w2-events.jsonl
```

## W2 alerts: definitions, evaluator, schedule

- Definitions: `w2_alerts.json` (nine rules; thresholds are rubric-exact and pinned by
  `ops/tests/test_w2_definitions.py`).
- Evaluator: `w2_alert_checker.py` — stdlib-only, independent of the serving app.
  It loads `w2_alerts.json`, derives metrics from two PHI-free sources, evaluates
  every rule, notifies newly firing alerts to `COPILOT_ALERT_WEBHOOK_URL` (aggregate
  values + a runbook link only), and reports resolutions. Rules whose source has no
  data are reported `no_data` — never fabricated, never firing.
- Schedule: `.github/workflows/agent-w2-alerts.yml` (GitHub Actions cron,
  every 6 hours, plus `workflow_dispatch`). Chosen over a Railway cron because the
  repo already runs its agent gates in GitHub Actions with the required secrets, and
  the eval artifacts the regression alert reads are committed to the repo the workflow
  checks out. Firing state persists between runs via a cache-backed `--state-file`,
  so a sustained breach notifies once and a recovery reports `resolved`.

Metric sources per rule:

| Rule | Metric | Source |
|---|---|---|
| `extraction-failure-rate` | worker `encounter.summary` events, `severity=error` ÷ total (1 h window) | event lane |
| `retrieval-p95` | `retrieval.completed` `latency_ms` p95 (15 min window) | event lane |
| `ingestion-p95` | worker `encounter.summary` summed `step_latencies_ms` p95 (1 h window) | event lane |
| `queue-oldest` | `queue.state` max `queue_age_ms` (5 min window) | event lane |
| `worker-heartbeat` | not derivable from the event lane — `no_data` (readiness probe owns it; see runbook) | — |
| `breaker-open` | not derivable from the event lane — `no_data` (breaker state events lack duration; see runbook) | — |
| `deterministic-eval` | min deterministic category `current_score` | `evals/results-tier1.json` |
| `factual-eval-threshold` | `factually_consistent` `current_score` | `evals/results-tier1.json` |
| `factual-eval-delta` | (baseline − current) × 100 for `factually_consistent` | `evals/results-tier1.json` vs `evals/w2_baseline.json` |

Every rule links its response actions in
`docs/week2/evidence/W2_RUNBOOKS.md` ("Alert-to-runbook mapping" section);
`ops/tests/test_w2_alert_checker.py` pins that every rule id has a mapping line and a
real section anchor.

Local dry-run:

```bash
cd agent
python -m ops.w2_alert_checker --once --dry-run \
  --events-file /path/to/w2-events.jsonl \
  --results evals/results-tier1.json --baseline evals/w2_baseline.json
```

Alert drills (fire → clear on synthetic conditions) are tests, not scripts:
`ops/tests/test_w2_alert_checker.py::test_drill_extraction_failure_rate_fires_and_clears`,
`::test_drill_retrieval_p95_fires_and_clears`,
`::test_drill_eval_regression_over_five_points_fires_and_clears`.

## Dashboard: `w2_dashboard.json` panel mapping (documented queries)

`w2_dashboard.json` is the panel definition of record. Langfuse has no import format
for it, so the chosen approach is the documented mapping below: each panel maps onto
either the Langfuse UI (for trace-lane data) or a documented query over the structured
event lane (`jq` over the exported JSON lines). `data_policy: aggregate_refs_only`
holds for every view — all queried fields are counts, latencies, closed codes, or
hashes.

| Panel | Source | Query / UI mapping |
|---|---|---|
| `ingestion` | event lane | `jq 'select(.event_type=="ingestion.stage")'` → rate by `.attributes.state`, p50/p95 of `.attributes.latency_ms`, failures where `.attributes.state=="failed"` |
| `grounding` | event lane | `jq 'select(.event_type=="grounding.completed")'` → grounded/unsupported rates from `.attributes.fields_*`, `.attributes.grounding_rate` |
| `retrieval` | event lane | `jq 'select(.event_type=="retrieval.completed")'` → hits (`.attributes.hit_count>0`), misses, p50/p95 of `.attributes.latency_ms`, degraded share |
| `worker-routing` | event lane + Langfuse | `jq 'select(.event_type=="handoff.completed")'` → counts by `.attributes.worker`/`.attributes.reason_code`; Langfuse UI: trace tree `graph.worker.*` spans per trace |
| `queue` | event lane | `jq 'select(.event_type=="queue.state")'` → depth by `.attributes.state`, max `.attributes.queue_age_ms`; heartbeat age via `/ready` probe detail |
| `write-intents` | event lane | `jq 'select(.event_type=="write_intent.transition")'` → counts by `.attributes.leg`/`.attributes.state`, reconciliations = `state=="unknown"` |
| `tokens-cost` | event lane + Langfuse | `jq 'select(.event_type=="encounter.summary")'` → sums of `.attributes.input_tokens`/`.attributes.output_tokens`/`.attributes.cost_usd`; Langfuse UI: native generation usage/cost widgets on `previsit-brief` traces |
| `evals` | committed artifacts | `evals/results-tier1.json` categories (`current_score`, `baseline_score`, `percentage_point_delta`, `passed`) vs `evals/w2_baseline.json` |
| `readiness` | deployment | `GET /ready` — hard/soft dependency results with per-check detail and latency |
| `breakers` | event lane + logs | `jq 'select(.event_type=="breaker.state")'` → state by `.attributes.dependency`; open duration from consecutive state-change timestamps |

Langfuse UI side (trace lane): traces are named `previsit-brief`, tagged
`client:*` / `source:*` / `fallback:*`, with hashed user/patient ids, correlation id as
session id, and per-step spans (`fhir.*`, `llm.complete`, `verify`, `graph.*`) carrying
`latency_ms` metadata — the W1 checker (`alert_checker.py`) already aggregates these
via the Observations API.

## Week-1 checker

`alert_checker.py` remains the Langfuse-backed W1 lane (p95 latency, request error
rate, tool failure rate, LLM fallback rate). It is scheduled operationally alongside
the W2 checker and is unchanged by R05 apart from being imported for shared transport
helpers.
