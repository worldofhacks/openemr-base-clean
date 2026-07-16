# Week 2 cost and latency evidence

## What is already measured

The prior synthetic Week 1 production trace window records three requests and distinguishes
Langfuse native generation cost from full provider economics:

| Measure | Result |
|---|---:|
| Native generation cost/request | $0.03895990 |
| Reconstructed provider cost/request | $0.08165115 |
| Successful bounded chat latency | 33.77 seconds (`n=1`) |

These are shared-path anchors only. They are not relabeled as Week 2 VLM, critic, or live
50-case measurements.

## Week 2 evidence contract

The Tier-2 runner exports aggregate call counts, input/output tokens, cost, retries, and
p50/p95 for extraction, answer, and judge legs. `ops.spike_tier2` independently projects the
required 50-case call shape, including each VLM page call. The final cost table is accepted
only when all of the following are present:

1. exact source SHA and deployment ID;
2. provider/model and pricing version;
3. complete green 50-case status;
4. measured tokens, retries, p50/p95, and cost by leg;
5. rate-limit and account-spend headroom supplied without credentials or headers;
6. deployed CPU and peak memory for web, worker, OpenEMR, MySQL, and Agent Postgres.

Protected Anthropic execution, sanitized billing totals, and Railway resource totals are
owner actions. Until those are supplied, the live Week 2 rows remain intentionally absent;
CI treats provider exhaustion or budget ceilings as `INCONCLUSIVE` with a nonzero exit.

## Bottleneck interpretation

The existing one-request trace is dominated by answer-model time, while the previous 50-VU
public burst saturated uncached readiness probes. The final profile must separately measure
OCR/VLM, grounding, sparse/dense retrieval, reranking, writes/readback, critic verification,
and composition before assigning a Week 2 bottleneck.
