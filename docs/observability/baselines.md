# F6 load and resource baselines

> **Status (2026-07-12):** fresh 10/50-VU public baselines and bounded authenticated
> `/chat` controls are recorded. The 50-VU run exposed readiness saturation. A conservative
> D10 operational cap is recommended pending an authenticated multi-user knee test.
>
> **Anchors:** `ARCHITECTURE.md` §7, D10, audit F-P.5, `IMPLEMENTATION_PLAN.md` F6.

## What was measured

The public profile uses k6 2.1.0 and the
[`per-vu-iterations`](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/per-vu-iterations/)
executor. Each VU performs exactly one `GET /health` and one `GET /ready`; therefore the
10-VU and 50-VU runs issue exactly 20 and 100 requests. The scripts expose p50, p95, p99,
maximum latency, request rate, and endpoint-specific error rates using k6
[custom metrics](https://grafana.com/docs/k6/latest/using-k6/metrics/create-custom-metrics/).

| Item | Value |
|---|---|
| Target | `https://agent-production-9f62.up.railway.app` |
| Runner | Local Docker, `grafana/k6:2.1.0`, one load generator |
| 10-VU run started | Approximately 2026-07-12T23:36:28Z |
| 50-VU run started | Approximately 2026-07-12T23:36:56Z |
| Request timeout | 15 seconds |
| Requests per VU | 2: one `/health`, then one `/ready` |
| Load shape | One bounded iteration per VU; no sustained soak |

Production `/ready` was HTTP 200 with all configured checks green immediately before these
fresh runs. The runs used the default strict mode: a non-200 readiness response failed its
status check and contributed to `ready_errors`.

## Public endpoint results

| VUs | Endpoint | Samples | Average | p50 | p95 | p99 | Max | Error rate |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 10 | `/health` | 10 | 236.52 ms | 233.41 ms | 244.15 ms | 244.17 ms | 244.18 ms | 0% |
| 10 | `/ready` | 10 | 4.07 s | 4.06 s | 4.10 s | 4.11 s | 4.11 s | 0% |
| 50 | `/health` | 50 | 121.19 ms | 124.33 ms | 130.63 ms | 131.12 ms | 131.19 ms | 0% |
| 50 | `/ready` | 50 | 6.45 s | 6.65 s | 6.81 s | 6.82 s | 6.82 s | 78% (39/50 HTTP 503) |

| VUs | Total requests | Completed iterations | Check pass rate | Combined HTTP error rate | Total request rate |
|---:|---:|---:|---:|---:|---:|
| 10 | 20 | 10/10 | 100% | 0% | 4.358 requests/s |
| 50 | 100 | 50/50 | 84.4% | 39% | 14.142 requests/s |

All latency thresholds passed in both runs. The 10-VU run also passed its readiness
availability objective. The 50-VU run did not: only 11 of 50 readiness calls returned 200,
while 39 returned 503.

For one representative failed-readiness correlation, application logs showed successful
Langfuse and Anthropic probes but no matching successful OpenEMR metadata probe before the
503. That pattern is consistent with an OpenEMR metadata timeout under the burst, but this
is an **inference**, not a dependency detail proven by the captured response. The 50-VU
result does prove that this deployment could not keep `/ready` available under that burst.

The public samples are deliberately small and bounded. Ten readiness samples make p99
directional, not statistically stable; 50 samples are still a burst baseline rather than a
capacity or soak test. The 50-VU window also overlapped two unrelated application chat calls
at approximately 23:37:24Z and 23:37:44Z, so resource use cannot be attributed solely to the
public burst.

### Earlier diagnostic capture

An earlier pair of public runs at 23:00Z began while `/ready` was already returning 503
because the configured session store reported an `OSError`. Those runs used
`ALLOW_NOT_READY=true` to preserve response-shape and latency diagnostics, but they were not
healthy-load baselines and are superseded by the fresh runs above. This history is retained
so the precondition-failed attempt is not mistaken for, or silently replaced by, a healthy
run.

## Authenticated `/chat` results and spend boundary

The authenticated scenario is intentionally not a 10/50-VU chat test. It is one VU, one
immutable iteration, and no k6-layer retry. The setup aborts before network I/O if the HTTPS
base URL, SMART session, spend cap, or deployment-profile acknowledgement is absent. The
current conservative profile ceiling is $2.61864, rounded up to a required $2.62 cap, and
the script rejects any cap above $3.00. The proof is in `agent/load/k6/README.md`.

Every authenticated attempt is retained below:

| Attempt | Client | Result | Duration | Checks/errors | Evidence and interpretation |
|---|---|---|---:|---|---|
| Control | Bruno | HTTP 200 | 42.373 s | Success | Confirmed the authenticated serving flow immediately before k6. |
| First k6 probe | k6, 1 VU × 1 iteration | HTTP 502 | 35.67 s | 0/4 checks; 100% `chat_errors` | Railway edge logs show deployment `11a6` was removed while replacement `ee3` became active. This is a recorded failed attempt, not a latency baseline. |
| Fresh-session retry | k6, 1 VU × 1 iteration | HTTP 200 | 33.77 s | 4/4 checks; 0% `chat_errors` | Ran against stable deployment `ee3`; Railway edge recorded the 200 at 2026-07-12T23:39:20.071Z with 33,673 ms total duration. |

The successful retry has one sample, so its p50, p95, and p99 are all 33.77 seconds. Those
percentiles are purely directional; `n=1` supports an end-to-end smoke baseline, not a
latency distribution or capacity claim. The first 502 is not combined with the successful
sample because it occurred during a deployment replacement, but it remains part of the
test record.

Nine guardrail cases prove the missing/invalid configuration paths abort before network
I/O. No response bodies, session identifiers, or provider secrets were printed or stored.
No further authenticated calls were made after the one explicitly bounded retry.

## Railway CPU and memory

Railway raw metrics are available at 30-second resolution. The following two samples per
service cover 2026-07-12T23:38:30Z–2026-07-12T23:39:30Z, including the successful one-request
chat retry. With `n=2`, they are directional and cannot isolate the request from background
service activity.

| Service | CPU average | CPU max | Memory average | Memory max |
|---|---:|---:|---:|---:|
| Agent | 0.00587248 vCPU | 0.00827243 vCPU | 150.06 MB | 203.59 MB |
| OpenEMR | 0.0184802 vCPU | 0.0313056 vCPU | 132.91 MB | 133.27 MB |
| MySQL | 0.014042 vCPU | 0.028084 vCPU | 932.77 MB | 932.77 MB |
| Postgres | 0.0004681 vCPU | 0.0004949 vCPU | 36.49 MB | 36.49 MB |
| Postgres-aDU3 | 0.0002643 vCPU | 0.0002669 vCPU | 45.99 MB | 45.99 MB |

The presence of two low-activity Postgres services does not establish which instance owns
the agent's live session-store traffic. They are both listed because F6 requires every
service in the Railway project to remain visible. Because the fresh 50-VU public window was
confounded by unrelated chat traffic, no CPU or memory number is attributed to that burst.

## D10 operational fan-out cap

Use the following conservative production hand-off until an authenticated multi-user knee
test supplies stronger evidence:

1. **Cap intra-brief FHIR fan-out at six concurrent reads**—at most one in-flight read for
   each of the six designed patient resources.
2. **Admit only one chat brief per agent instance at a time.** Queue or reject additional
   work according to the serving owner's policy rather than multiplying concurrent patient
   fan-outs on the same instance.

This is an operational cap, not a claim that the current documentation branch implements or
enforces admission control; the serving path and application code are outside F6's scope.
It is deliberately conservative because the 50-VU run saturated readiness after a green
preflight, while the only successful authenticated chat measurement is `n=1`.

Public `/ready` probes are **not equivalent** to the six authenticated patient-resource
reads used by a brief, so the public burst does not numerically derive a safe patient fan-out
or multi-user limit. Raising the one-brief-per-instance cap requires an authorized synthetic
data test that measures the real six-read shape at increasing brief concurrency, including:

1. per-FHIR-call and whole-fan-out p50/p95/p99;
2. partial-result, timeout, 429, and 5xx rates by resource;
3. OpenEMR, MySQL, and agent CPU/memory over a sampling window long enough to separate tiers;
4. the heaviest Synthea chart identified by F-P.3, not only the median patient;
5. the first material latency/error knee, followed by explicit safety headroom.

## Reproduction and validation

Commands are in `agent/load/k6/README.md`. The fresh strict public results used:

```bash
docker run --rm \
  -e BASELINE_VUS=10 \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/public_baseline.js

docker run --rm \
  -e BASELINE_VUS=50 \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/public_baseline.js
```

Validation performed:

| Check | Result |
|---|---|
| Shell syntax for `tests/guardrails.sh` | Pass |
| Missing/invalid k6 guardrails | 9/9 expected aborts passed |
| Fresh 10-VU public run | 10/10 iterations and 20/20 requests completed; 100% checks; 0% HTTP errors |
| Fresh 50-VU public run | 50/50 iterations and 100/100 requests completed; 84.4% checks; 39/50 readiness responses were 503 |
| Bruno authenticated control | HTTP 200 in 42.373 seconds |
| First capped k6 `/chat` probe | HTTP 502 in 35.67 seconds during deployment replacement; retained as a failure |
| One capped fresh-session retry | HTTP 200 in 33.77 seconds; 4/4 checks; 0% errors; `n=1` |

No raw response bodies, session identifiers, provider secrets, or clinical content are
stored in this report or committed by the scripts.
