# Week 2 performance baselines and SLO locking

## Reproducible profile

`agent/load/k6/w2_profiles.js` defines bounded 1/10/50-VU profiles for retrieval,
ingestion/extraction, the full graph, and the shared Week 1 chat path. Cost-bearing profiles
refuse to start without an explicit spend acknowledgement. They do not print response
bodies, sessions, patient identifiers, prompts, or tokens.

For every run, retain only aggregate CPU, peak memory, p50/p95 latency, throughput,
error/degradation rate, tokens, cost, backend/model versions, deployment ID, and exact SHA.
The final report must identify whether a value came from local synthetic execution or the
deployed synthetic-only environment.

## Existing shared-path evidence

The last committed Week 1 production profile in `docs/observability/baselines.md` measured:

| Load | Surface | p95 | Error result |
|---:|---|---:|---:|
| 10 VU | `/health` | 244.15 ms | 0% |
| 10 VU | `/ready` | 4.10 s | 0% |
| 50 VU | `/health` | 130.63 ms | 0% |
| 50 VU | `/ready` | 6.81 s | 78% readiness errors |
| 1 VU | authenticated Week 1 chat | 33.77 s (`n=1`) | 0% on the stable retry |

The 50-VU readiness result is a known saturation baseline, not an acceptable SLO. Closeout
adds short-lived readiness caching and bounded probes; its post-deploy profile must replace,
not silently overwrite, these numbers.

## Deterministic lock rule

Targets are locked only after a green, exact-SHA, synthetic-only profile:

- retrieval must first meet p95 <= 2 seconds;
- ingestion must first meet p95 <= 30 seconds;
- locked latency target = `min(working ceiling, ceil(1.25 * measured p95))`;
- throughput floor = `floor(0.80 * sustained throughput)`;
- resource budget = `ceil(1.25 * measured peak)` and must be below 80% of deployed
  capacity.

No qualifying post-closeout deployed run has been supplied in this workspace. Therefore the
2-second retrieval and 30-second ingestion values remain acceptance ceilings, not fabricated
measured baselines. Running and recording the protected deployed profile is an owner action.
