# Clinical Co-Pilot AI Cost Analysis

> **Status:** F7 cost snapshot complete for the evidence currently available.
> **Scope anchors:** `ARCHITECTURE.md` §9, D4, R1, R4, and F7.
> **Price basis:** the repository price card dated 2026-07-06: Sonnet 4.6
> input/output at $3/$15 per million tokens, cache reads at 0.10× input, and
> five-minute cache creation at 1.25× input.
> **Data posture:** synthetic Synthea data only. This document publishes aggregate
> counts, tokens, cost, and latency; no trace content, patient identifier, session ID,
> correlation ID, or credential was exported.

## Executive conclusion

The first usable post-fix Langfuse window contains only **three** request traces, from
2026-07-12T23:37:24.594Z through 2026-07-12T23:39:20.050Z. Those traces recorded
**$0.24495345** of application-estimated Sonnet cost, or **$0.08165115 per traced
request**. They are proof that cost attribution works, not a representative workload:
the window spans less than two minutes, one deployment, one model, and no observed
fallbacks or follow-up turns.

Railway reports **$1.8399776146** of accrued infrastructure usage for the current
billing period through the 2026-07-12T23:44:54Z observation. This is a provisional
current-period platform actual, not a closed invoice.

Neither number is the project's complete development spend. The trace total covers
only calls emitted after the Langfuse SDK v4 fix; it excludes every earlier Anthropic
call. No Anthropic account billing export or Langfuse invoice was available, so their
account-wide billed totals are reported as unavailable rather than inferred. In
particular, **trace-estimated model cost must not be relabeled as Anthropic billed
spend**.

At scale, cost is a step function rather than `tokens × users`. D4 and R1 make prompt
caching and model routing the immediate levers. Section §9 and R4 require topology
changes—durable state and replicas, then queues/read replicas, then multi-region or
validated self-hosted inference—to be priced separately from model tokens.

## 1. Measurement and accounting boundaries

Three different quantities remain separate throughout this analysis:

1. **Observed trace cost:** the application's D4/R1 estimator applied to model usage
   captured in a Langfuse request trace. This is the unit-economics numerator below.
2. **Provider-billed model spend:** the Anthropic account or invoice control total,
   including calls before trace coverage, retries not emitted, rounding, credits, and
   adjustments. That export was not available.
3. **Infrastructure usage:** Railway's current-period accrued service usage. It covers
   a multi-day period and cannot be divided by a two-minute, three-request trace sample.

The in-process daily cost cap is a guard, not an invoice. Langfuse trace cost is an
estimate, not a payment record. Railway usage is provisional until the billing period
closes. Gross, net, credit-adjusted, and tax-inclusive totals must stay distinct.

The reporting unit is one patient-specific pre-visit request. One request can contain
multiple model or tool calls, although the three observed traces each contain one model
call and six FHIR reads. A future production denominator should be a successfully served,
non-empty brief, deduplicated by correlation ID in the private analysis environment.

## 2. Actual Railway infrastructure usage

Command: `railway usage projects --project 1bddbc72-6307-4ec9-b6dd-8184310fbdcf
--period current --json`. The snapshot covers the billing period
2026-07-08T01:02:36Z–2026-08-08T01:02:36Z and was observed at
2026-07-12T23:44:54Z.

| Railway service | Accrued gross usage | Share of project usage |
|---|---:|---:|
| MySQL | $1.3216340219 | 71.83% |
| OpenEMR | $0.3203549536 | 17.41% |
| Agent | $0.0801033903 | 4.35% |
| Postgres-aDU3 | $0.0592027463 | 3.22% |
| Postgres | $0.0586825025 | 3.19% |
| **Project total** | **$1.8399776146** | **100.00%** |

The two Postgres services contribute $0.1178852488. They remain attributable project
usage until the deployment owner identifies which service is the live session store and
retires or reallocates the other.

The snapshot spans 4.946 days. A purely arithmetic 30-day extrapolation at the same
observed utilization is **$11.16/month**. That is not a capacity-tested production quote:
the database-dominated demo may idle differently from a clinician workload, and the
current topology has not proven it can serve the tier volumes in §6.

The Railway API did not expose a Git commit SHA for the active CLI deployment. The live
trace window follows successful deployment `ee3c97f8-a02f-4313-b53a-996a9f3d3ba8`,
created at 2026-07-12T23:34:26.740Z. The deployment identifier is provenance, not a
patient or session identifier.

## 3. Live Langfuse unit economics

The current Langfuse project was queried with Railway-injected credentials held only in
process memory. The aggregation read trace and generation metadata, discarded raw API
objects, and emitted only the following totals.

### Coverage and execution

| Metric | Observed aggregate |
|---|---:|
| Coverage start | 2026-07-12T23:37:24.594Z |
| Coverage end | 2026-07-12T23:39:20.050Z |
| Request traces | 3 |
| Model generations | 3 |
| Model mix | 3 Sonnet 4.6; 0 Haiku |
| LLM calls | 3 total; 1/request |
| FHIR reads | 18 total; 6/request |
| `source=llm` traces | 3 |
| Degraded traces | 0 |
| Deterministic fallbacks/refusals | 0 observed |
| Trace latency mean | 51,561 ms |
| Trace latency p50 | 45,462 ms |
| Trace latency p95 | 72,817 ms |
| Generation latency mean | 37,158 ms |

With only three observations, nearest-rank p95 is the maximum observation. These
latencies describe this tiny development burst, not an SLO baseline.

### Token and model-cost totals

| Metric | Total | Mean per traced request | Provenance |
|---|---:|---:|---|
| Ordinary input tokens | 102 | 34.0 | Langfuse native generation usage |
| Cache-read input tokens | 45,029 | 15,009.7 | Langfuse native generation usage |
| Output tokens | 6,871 | 2,290.3 | Langfuse native generation usage |
| Cache-creation tokens | 34,153 implied | 11,384.3 implied | Derived reconciliation; direct API field absent |
| Complete application-estimated cost | $0.24495345 | **$0.08165115** | Trace metadata `cost_usd` |
| Langfuse native generation cost | $0.11687970 | $0.03895990 | Native generation cost field |

The native exporter sends ordinary input, output, and cache-read usage to Langfuse, but
does not send cache-creation usage. The application-level trace cost is higher by
$0.12807375. Under the implemented Sonnet input price and 1.25× cache-write multiplier,
that exact difference reconciles to **34,153 cache-creation tokens**:

```text
$0.12807375 ÷ ($3 / 1M × 1.25) = 34,153 tokens
```

This is a transparent derivation from two API cost fields plus the checked-in estimator,
not a directly exported token count. The complete trace-level estimate is the better
observed unit-cost numerator; the native Langfuse widget currently understates cost by
omitting the cache-write class.

### Invoice boundary

| Cost control | Available result |
|---|---|
| Trace-estimated Sonnet cost for the post-fix window | $0.24495345 |
| Anthropic account-wide billed spend | Not available from the trace API or repository |
| Trace-to-Anthropic invoice variance | Not calculable without the provider billing export |
| Langfuse Cloud billed spend | Not available from the trace API or repository |
| Credits, taxes, and adjustments | Not available from the queried sources |
| Complete gross/net development spend | Cannot be stated from current evidence |

The $0.24495345 trace total is already a subset of future Anthropic account billing; it
must not be added to an Anthropic invoice when that invoice becomes available. Railway's
$1.8399776146 is a separate infrastructure category, but the combined value still would
not be complete project spend because earlier untraced model usage remains unknown.

## 4. Per-request model economics

D4 and R1 supply the pricing model implemented in `agent/app/llm/cost.py`:

| Model | Ordinary input / 1M | Output / 1M | Intended role |
|---|---:|---:|---|
| Claude Sonnet 4.6 | $3.00 | $15.00 | Clinical synthesis and typed claims |
| Claude Haiku 4.5 | $1.00 | $5.00 | Future bounded utility work after eval validation |

For model `m`:

```text
C_model(m) = P_input(m) / 1,000,000
             × [I_uncached + 1.25 × I_cache_create + 0.10 × I_cache_read]
             + P_output(m) / 1,000,000 × O
```

Applied to the aggregate window:

```text
Sonnet input = $3/1M × [102 + 1.25×34,153 + 0.10×45,029]
Sonnet output = $15/1M × 6,871
Total = $0.24495345 = $0.08165115/request
```

The deterministic verifier and renderer incur no model charge. All three observed calls
used Sonnet; Haiku routing is not implemented or evidenced, so no current savings are
assigned to it.

### Independent cache example

The earlier E5 smoke test measured a 16,483-token stable prefix written once and read
once. At D4/R1 Sonnet rates:

| Treatment of 16,483 tokens | Cost |
|---|---:|
| Ordinary uncached input | $0.049449 |
| Cold five-minute cache creation | $0.061811 |
| Warm cache read | $0.004945 |

One write plus one read costs $0.066756 versus $0.098898 for two ordinary sends, a
32.5% saving. At five sends within the cache window, the stable-prefix saving is 67%; at
ten it is 78.5%. The long-run ceiling approaches the R1 90%-off cache-read discount.
Caching loses money when a prefix is written and never reused, so the production metric
must be eligible tokens served from cache, not merely “cache enabled.”

## 5. Workload assumptions

D1 supplies the upper workflow of 20 scheduled patients per clinician-day:

```text
Monthly briefs = active clinicians × 20 patients/day × 20 days/month × adoption
Monthly turns  = monthly briefs × (1 + mean follow-up turns)
```

For the §6 comparison, adoption is set to 100% and follow-up turns to zero. Those are
scenario choices, not observed demand. The live trace sample contains three initial
requests but is too small to estimate clinician adoption or follow-up behavior.

| Input | Value used or evidence state |
|---|---|
| Clinician days/month | 20-day scenario |
| Adoption | 100% upper-bound scenario |
| Follow-up turns/brief | 0 in projection; not estimable from three initial requests |
| Model mix | Observed 100% Sonnet; no Haiku routing |
| Model cost/request | $0.08165115 observed mean |
| Ordinary input/output/cache-read tokens | Aggregate and means in §3 |
| Cache-creation tokens | 34,153 aggregate, derived from cost reconciliation |
| Cache-hit rate by eligible tokens | Not exposed by current aggregate API fields |
| Fallback/refusal rate | 0/3 observed; sample too small for forecasting |
| Precomputed share | 0% in the observed on-demand window |

## 6. Tier projections—topology first

The dollar column deliberately separates the mechanically extrapolated observed model
mean from infrastructure step costs. The three-request mean is not a forecast, and an
unpriced topology is labeled unpriced rather than assigned an invented number.

| Active clinicians | Briefs/month | Observed-mean model scenario | Required topology and unpriced step costs |
|---:|---:|---:|---|
| 100 | 40,000 | $3,266.05 | Current OpenEMR/MySQL/agent shape only as a starting point; $11.16 demo run-rate is not capacity proof; production Langfuse plan and durable session service must be priced |
| 1,000 | 400,000 | $32,660.46 | Horizontal agent replicas, durable Redis/session and cost controls, scaled database; replica count and managed-service quotes unavailable |
| 10,000 | 4,000,000 | $326,604.60 | Queues, worker pools, read replicas, HA/dedicated platform comparison; load-derived capacity and vendor quotes unavailable |
| 100,000 | 40,000,000 | $3,266,046.00 | Multi-region service, off-peak queueing, replicated data access, and API-vs-GPU hybrid TCO; GPU and operations quotes unavailable |

These are **model-only sensitivity figures**, not total monthly projections. Treating the
100-user Railway run-rate as though it scaled linearly would violate §9. The following
evidence gates each topology change:

| Transition | Evidence required | Decision rule |
|---|---|---|
| 100 → 1K | 10/50-VU CPU, memory, latency, session-loss and cache-locality results | Add replicas and durable shared state; re-price cache locality |
| 1K → 10K | Morning-burst queue depth, database read pressure, PaaS bill, HA requirement | Introduce queue/workers/read replicas; compare managed and dedicated TCO |
| 10K → 100K | Annual provider spend, model throughput, GPU utilization, clinical eval parity | Move only when avoided API spend exceeds full GPU, SRE, redundancy, and fallback TCO |

R4 makes self-hosted inference an economic option at 100K, not an automatic choice. A
cheaper model must still pass the same structured-claim, grounding, tool-use, and
adversarial evals; Sonnet remains the fallback until parity is demonstrated.

## 7. Batch and two-model levers

R1 records a 50% batch discount. Off-peak pre-computation can reduce the morning burst,
but each patient prefix is unique and may be cold again by visit time. A future model is:

```text
C_briefs = (1 - precomputed_share) × on_demand_cost
           + precomputed_share × batch_cost
           + follow_up_cost
           + storage_and_unused_precompute_cost
```

No batch or precomputed call appeared in the three-trace window, so no batch saving is
included. Confirm that batch and cache discounts stack before modeling them together.
Count dollars per opened/served brief, not per generated brief, and price the PHI-bearing
storage/retention boundary before real-data rollout.

D4's Sonnet/Haiku split is also a future lever. The observed mix is 3 Sonnet calls and
zero Haiku calls. Assigning a Haiku percentage before routing and task-specific evals
would invent savings, so the current projection remains Sonnet-only.

## 8. Risks, sensitivity, and next evidence

- Three traces across 115 seconds cannot establish a representative mean, p95,
  cache-hit distribution, retry rate, fallback rate, or adoption curve for production.
- The observed output average is 2,290 tokens; output at $15/M is material, but the
  sample is too small to set a durable response-length target.
- Cache-creation usage is absent from Langfuse native usage and cost widgets. The exact
  reconciliation is possible today only because the trace-level estimator preserved the
  complete cost and the deployment used one known model.
- The Anthropic billing control total is unavailable, so pre-coverage calls, provider
  rounding, credits, and any dropped traces cannot be reconciled.
- Railway exposes current-period service usage, not a two-minute infrastructure slice;
  dividing $1.84 by three requests would be invalid.
- Langfuse invoice/plan cost is unavailable. A demo cloud tier must not stand in for the
  production HIPAA/BAA posture documented elsewhere.
- The five-minute cache may be too short for schedule-level reuse. Measure eligible-token
  hit rate by same-patient turn spacing.
- “100K users” is interpreted as active clinicians. Recompute the workload if it instead
  means covered patients or monthly end users.
- Provider pricing, cache TTLs, batch rules, and discount stacking can change; refresh
  D4/R1 inputs before a procurement decision.

The next representative window should span cold and warm same-patient turns, retries,
fallbacks, and normal clinician timing. Export an Anthropic billing control total for the
same period, add direct cache-creation usage to the Langfuse generation, and measure
Railway under the 10/50-VU workload before replacing the sensitivity figures with a
forecast.

## 9. F7 evidence status

- [x] Current-period Railway usage and service allocation recorded from `railway usage`.
- [x] Live post-SDK-fix Langfuse coverage window recorded.
- [x] Aggregate trace counts, tokens, model mix, latency, source, and fallback state recorded.
- [x] Trace-estimated total and per-request model cost recorded.
- [x] Native-generation cost gap reconciled to the missing cache-creation class.
- [x] Trace-estimated cost distinguished from Anthropic account-wide billing.
- [x] Tiny sample size and coverage start stated prominently.
- [x] 100/1K/10K/100K model sensitivity paired with nonlinear topology changes.
- [x] Missing invoices, quotes, capacity evidence, and direct cache-write usage labeled
      unavailable rather than zero.
- [x] Every evidence cell contains a value or an explicit unavailable state.

## Source ledger

| Anchor/evidence | Use in this analysis |
|---|---|
| `ARCHITECTURE.md` §9 | Real traces plus Railway billing; nonlinear 100/1K/10K/100K topology; cache/model split; managed-vs-self-hosted transitions |
| `docs/planning/DECISIONS.md` D4 | Sonnet/Haiku roles, provider seam, price assumptions, prompt-cache leverage, 100K revisit |
| `docs/planning/RESEARCH.md` R1 | Cache-read and batch discounts; dated model price basis |
| `docs/planning/RESEARCH.md` R4 | No GPU at demo tier; self-hosted inference becomes a conditional high-scale option |
| `agent/app/llm/cost.py` | Checked-in cost formula and cache multipliers |
| `agent/app/observability/langfuse.py` | Trace `cost_usd`, native generation usage/cost, and the cache-creation export gap |
| Langfuse aggregate query, 2026-07-12T23:44Z | Three post-v4-fix traces; aggregate tokens, $0.24495345 trace cost, $0.11687970 native cost, and latency/source counts |
| Railway usage snapshot, 2026-07-12T23:44:54Z | $1.8399776146 provisional current-period infrastructure usage and service allocation |
