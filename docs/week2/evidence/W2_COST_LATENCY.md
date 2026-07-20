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

## O02-lite — partial datapoints (2026-07-19) — **partial, NOT AF-P1-08 closure**

Everything in this section is explicitly labeled partial: the four-flow k6 profile
(`agent/load/k6/w2_profiles.js`), Railway CPU/memory, W1 comparison, and billing
reconciliation still require owner-provisioned access (61 non-reused synthetic session
contexts, `ALLOW_PROVIDER_SPEND`, Railway metrics/billing exports — plan A-8 / W2-O4)
and land with full O02 against the accepted release SHA.

### Committed exact-SHA live Tier-2 aggregates (durable copies, see W2_CI_EVIDENCE.md E01-lite)

Source: green `agent-eval-gate` run 29553727457 on `main`, `source_sha = 6583079…`
(= deployed `/health` SHA), 50 live cases (VLM + answer + judge legs included):

| Measure | Value |
|---|---:|
| Cost (50 cases) | $3.0658 |
| p50 / p95 (full case) | 5 611 ms / 12 266 ms |
| Tokens in / out | 621 426 / 58 404 |
| Retrieval hits | 202 |
| Extraction grounding rate | 0.9596 |

### Public deployed retrieval probe (2026-07-19, ~18:10 UTC)

`POST /evidence/search` (public, anonymous; query "lipid statin therapy primary
prevention", k=5, 5 items returned every hit), n=30 sequential with 0.5 s gaps, fresh
HTTPS connection per request, against `/health` = `6583079…` with `/ready` all-green at
probe time:

| Measure | Value |
|---|---:|
| errors | 0 / 30 |
| p50 | 4 937 ms |
| p95 | 6 488 ms |
| min / max | 4 001 ms / 6 562 ms |

**Signal:** the working retrieval SLO target (p95 ≤ 2 s) FAILS on this measurement.
Corroborating R07's finding that a fresh `HybridRetriever` + ONNX session load costs
~4.5 s even warm on dev hardware, the deployed per-request latency points at
reranker/model-session cost dominating the search path on Railway's shared vCPU.
Full O02 must separate sparse/dense/rerank legs before locking SLOs; R07's follow-up
note (probe/session reuse) is the candidate fix. This measurement is a bottleneck-
analysis input, not an SLO lock.


### O02 re-measure at the release SHA (2026-07-19, post-REL1)

Same probe (n=30 sequential, 0.5 s gap, fresh HTTPS connection each): **p50 7 038 ms /
p95 8 553 ms / min 5 578 / max 10 649, 0 errors** at `293f18b`. Conditions differ from
the earlier 4 937/6 488 ms measurement: this run executed DURING the O01 production
journey (concurrent OCR/VLM extraction jobs + readiness refreshes on the same shared
vCPU). Server-side probe logs at the release SHA independently measured the rerank leg
alone at 8.5–10.7 s cold / ~4.5 s warm-dev. **Honest verdict: the working retrieval SLO
(p95 ≤ 2 s) FAILS on this instance class in both measurements.** Dominant bottleneck:
local mxbai ONNX cross-encoder rerank on Railway's shared vCPU. Candidate remediations
(for the owner / full O02): probe + request reuse of the single warmed retriever
(removes redundant session loads — also the memory-spike mechanism), smaller rerank
candidate pool (currently 30), a paid vCPU tier, or Cohere rerank (R06's bounded-retry
path) with its network budget. Full four-path k6 profile remains owner-gated (61
synthetic contexts, ALLOW_PROVIDER_SPEND, Railway metrics/billing — W2-O4).
