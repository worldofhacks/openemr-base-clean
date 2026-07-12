# F6 load and resource baselines

> **Status (2026-07-12):** public 10/50-VU baselines recorded. Authenticated `/chat`
> measurements and a D10 fan-out-cap decision remain pending because no authorized SMART
> session was supplied. No chart writes or billable model completions were run.
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
| 10-VU window | 2026-07-12T23:00:48Z–2026-07-12T23:01:00Z |
| 50-VU window | 2026-07-12T23:01:09Z–2026-07-12T23:01:16Z |
| Request timeout | 15 seconds |
| Requests per VU | 2: one `/health`, then one `/ready` |
| Load shape | One bounded iteration per VU; no sustained soak |

Immediately before the runs, `/ready` returned HTTP 503 with this dependency state:

| Dependency | Kind | Result |
|---|---|---|
| OpenEMR FHIR | hard | HTTP 200 |
| Anthropic model-list probe | hard | HTTP 200; no completion/tokens |
| Session store | hard | failed (`OSError`) |
| Langfuse | soft | disabled |

The public runs therefore used `ALLOW_NOT_READY=true` as a diagnostic capture mode. That
mode accepts a semantic 503 for response-shape checks, but it still records every 503 in
`ready_errors` and the built-in HTTP failure rate. A 100% successful k6 **check** rate below
means the response matched the documented contract; it does not mean the deployment was
ready.

## Public endpoint results

| VUs | Endpoint | Samples | Average | p50 | p95 | p99 | Max | Error rate |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 10 | `/health` | 10 | 107.55 ms | 112.39 ms | 120.89 ms | 121.74 ms | 121.96 ms | 0% |
| 10 | `/ready` | 10 | 4,365.36 ms | 4,259.72 ms | 4,884.94 ms | 5,253.94 ms | 5,346.19 ms | 100% (HTTP 503) |
| 50 | `/health` | 50 | 180.33 ms | 179.88 ms | 196.12 ms | 197.68 ms | 198.20 ms | 0% |
| 50 | `/ready` | 50 | 6,037.53 ms | 6,292.03 ms | 6,352.88 ms | 6,361.27 ms | 6,365.46 ms | 100% (HTTP 503) |

| VUs | Total requests | Completed iterations | Check pass rate | Combined HTTP error rate | Total request rate |
|---:|---:|---:|---:|---:|---:|
| 10 | 20 | 10/10 | 100% | 50% | 3.591 requests/s |
| 50 | 100 | 50/50 | 100% | 50% | 15.006 requests/s |

The latency thresholds passed in both runs: `/health` p95 <1 second and p99 <2 seconds;
`/ready` p95 <10 seconds and p99 <15 seconds. The readiness **availability** objective did
not pass: all readiness calls correctly returned 503 because a hard dependency was down.

This sample is deliberately small and bounded. Ten `/ready` samples make p99 directional,
not statistically stable; 50 samples are still a burst baseline rather than a capacity or
soak test.

## Railway CPU and memory during the runs

Railway raw metrics are available at 30-second resolution. The window below covers both
short runs together (2026-07-12T23:00:30Z–2026-07-12T23:02:00Z) and contains three samples
per service. Because the runs were contiguous and shorter than the sampling interval, these
figures cannot be cleanly attributed to 10 VUs versus 50 VUs.

| Service | CPU average | CPU max | Memory average | Memory max |
|---|---:|---:|---:|---:|
| MySQL | 0.27798 vCPU | 0.81088 vCPU | 858.89 MB | 877.37 MB |
| OpenEMR | 0.08502 vCPU | 0.20501 vCPU | 194.19 MB | 245.79 MB |
| Agent | 0.02334 vCPU | 0.06382 vCPU | 127.36 MB | 152.31 MB |
| Postgres | 0.00028 vCPU | 0.00056 vCPU | 35.64 MB | 35.64 MB |
| Postgres-aDU3 | 0.00027 vCPU | 0.00035 vCPU | 44.91 MB | 44.91 MB |

The presence of two low-activity Postgres services does not prove either is serving the
agent; `/ready` reported the configured session store unreachable. They are listed because
F6 requires every service in the Railway project to be visible, not because this run
establishes their ownership.

## Authenticated `/chat` status and spend boundary

Authenticated results are **pending**. No SMART session or authority to spend was supplied,
so `chat_capped.js` was not run.

The committed scenario is intentionally not a 10/50-VU chat test. It is a one-request probe
for a later authorized run:

- one VU, one immutable iteration, no retries at the k6 layer;
- one local load generator only; setup aborts if runtime options replace the scenario;
- abort-before-network if the HTTPS base URL, SMART session, spend cap, or deployment-profile
  acknowledgement is absent;
- conservative current-profile ceiling of $2.61864, rounded up to a required $2.62 cap;
- hard rejection of any cap above $3.00;
- no response-body logging or result file containing clinical data.

The cap proof and safe invocation are documented in `agent/load/k6/README.md`. Nine
guardrail cases prove the missing/invalid configuration paths abort before network I/O.

## D10 fan-out-cap recommendation

**No numeric fan-out cap is recommended from this evidence.** `/ready` probes OpenEMR's
FHIR metadata endpoint and Anthropic's model list; it does not execute the six authenticated
patient FHIR reads used by the pre-visit brief. Inferring a D10 cap from this public workload
would substitute a different query shape for the path being protected.

A cap should be chosen only after an authorized, synthetic-data run records the real
patient-read fan-out at increasing concurrency, including:

1. per-FHIR-call and whole-fan-out p50/p95/p99;
2. partial-result, timeout, 429, and 5xx rates by resource;
3. OpenEMR, MySQL, and agent CPU/memory over a sampling window long enough to separate tiers;
4. the heaviest Synthea chart identified by F-P.3, not only the median patient;
5. a concurrency step that shows the first material latency/error knee, with safety headroom.

Until those observations exist, retain the D10 design—six independent reads in parallel,
per-call timeouts, and a total turn budget—without inventing a global concurrency number.

## Reproduction and validation

Commands are in `agent/load/k6/README.md`. The public results above used:

```bash
docker run --rm \
  -e BASELINE_VUS=10 \
  -e ALLOW_NOT_READY=true \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/public_baseline.js

docker run --rm \
  -e BASELINE_VUS=50 \
  -e ALLOW_NOT_READY=true \
  -v "$PWD/agent/load/k6:/scripts:ro" \
  grafana/k6:2.1.0 run /scripts/public_baseline.js
```

Validation performed:

| Check | Result |
|---|---|
| Shell syntax for `tests/guardrails.sh` | Pass |
| Missing/invalid k6 guardrails | 9/9 expected aborts passed |
| 10-VU public run | 10/10 iterations, 20/20 bounded requests completed |
| 50-VU public run | 50/50 iterations, 100/100 bounded requests completed |
| Authenticated `/chat` | Not run—session and spend authority absent |

No raw response bodies, session identifiers, or provider secrets are stored in this report
or committed by the scripts.
