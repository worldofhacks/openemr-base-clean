# Clinical Co-Pilot AI Cost Analysis

> **Status:** Draft methodology complete; actual development spend is pending live Langfuse and Railway exports.
> **Scope anchors:** F7, `ARCHITECTURE.md` §9, D4, R1, R4.
> **Price basis:** repository price card dated 2026-07-06. Re-verify provider prices and discount rules before replacing placeholders.
> **Data posture:** synthetic Synthea data only. Publish aggregate usage and cost; do not export prompts, responses, patient identifiers, or other PHI-bearing trace content.

## Executive conclusion

Clinical Co-Pilot cost is a step function, not `cost per token × users`.

Near the demo tier, the main AI lever is the stable patient-context prompt prefix: a warm Sonnet cache read costs 10% of ordinary input, while the first five-minute cache write costs 125% of ordinary input. Repeated turns for the same patient therefore become materially cheaper after the second use. The second near-term lever is the D4 model split: Sonnet 4.6 for clinical reasoning and structured claim generation, Haiku 4.5 only for bounded utility work that passes the same eval gate. The deterministic verifier is code and incurs no model charge.

At larger tiers, those token optimizations remain useful but stop defining the system's total cost. Agent replicas and a durable Redis session/cost-control layer appear around 1K users; queues, read replicas, and a likely managed-PaaS exit appear around 10K; multi-region capacity, off-peak pre-computation, and potentially self-hosted open-weight inference dominate at 100K. Each tier must be sized as a new topology with fixed and step-change costs, as required by §9, D4, and R4.

No defensible **recorded** actual-development-spend total exists in the repository yet. Project-to-date Anthropic and Railway invoices can supply the actual dollars, including spend incurred before tracing. E7.0 Langfuse provisioning is still pending and the deployed agent uses `NullTraceSink`, so per-brief attribution must come from a later representative window and cannot retroactively explain earlier spend. The placeholders below are deliberate completion gates, not zero-dollar assumptions.

## 1. Scope, unit economics, and definitions

### Included costs

- Anthropic model usage: ordinary input, prompt-cache creation, prompt-cache reads, and output.
- Railway platform usage for OpenEMR, the agent, managed data services, volumes, backups, and network egress.
- Session/cache/queue services introduced at scale.
- Langfuse Cloud, including the production HIPAA/BAA plan when modeling real PHI rather than this synthetic demo.
- Storage and egress for precomputed briefs, aggregate traces, and operational retention.
- At the 100K tier, GPU inference capacity, redundancy, model-serving infrastructure, and the operational burden required for a clinically validated open-weight model.

### Excluded or separately reported

- Engineering labor and the opportunity cost of the one-week build.
- OpenEMR licensing, because the application is open source; hosting remains included.
- Promotional credits, taxes, and one-time grants. Show these as reconciliation adjustments rather than reducing the underlying run rate.
- Costs of real clinical rollout that are not yet designed, such as enterprise support, security assessments, and legal/BAA negotiation. Record them separately when known.

### Reporting units

Report all of the following; none is sufficient alone:

1. Total spend during a timestamped measurement window.
2. Cost per successfully served brief.
3. Cost per all attempted briefs, including deterministic fallbacks and refusals.
4. Cost per active clinician-month.
5. Monthly run rate by infrastructure tier.

In this document, a **user** means one active clinician, consistent with D1's PCP persona. A **brief** means one patient-specific pre-visit brief. A **turn** is one LLM-backed interaction; a brief can have a cold generation plus zero or more follow-up turns.

## 2. Actual development spend — completion worksheet

### Why the totals are placeholders

- `docs/DEVLOG.md` records E7.0 as an owner console step that is still pending.
- The deployed service currently selects `NullTraceSink`, so no live Langfuse cost series exists.
- No Railway billing export or per-service allocation is committed.
- The in-process daily cost cap is a guard, not a durable meter; it resets by process/day and must not be presented as an invoice.
- A future traced window measures unit economics; it does not replace project-to-date provider invoices or recreate pre-observability usage.

### A. Project-to-date actual spend

Use provider invoices from the project's first paid usage through a named cutoff. This is the F7 actual-development-spend total.

| Field / cost bucket | Actual | Source / owner | Status |
|---|---:|---|---|
| Project spend start (UTC) | **[TODO F7-P2D-01]** | Owner/provider consoles | Establish first paid usage |
| Project-to-date cutoff (UTC) | **[TODO F7-P2D-02]** | Owner | Common cutoff for all vendors |
| Anthropic gross usage | **[TODO F7-P2D-03]** | Anthropic billing export | Includes pre-Langfuse calls |
| Railway gross usage | **[TODO F7-P2D-04]** | Railway invoices/usage export | Include all project services |
| Langfuse gross usage | **[TODO F7-P2D-05]** | Langfuse invoice | Likely zero/free-tier during demo; verify |
| Other attributable vendor spend | **[TODO F7-P2D-06]** | Invoices | Name every included vendor |
| Credits and adjustments | **[TODO F7-P2D-07]** | Provider invoices | Show separately from gross |
| **Gross actual development spend** | **[TODO F7-P2D-08]** | Sum P2D-03:06 | Before credits |
| **Net actual development spend** | **[TODO F7-P2D-09]** | Gross less P2D-07 | Not the steady-state baseline |

### B. Representative unit-economics window

After E7.0 is live, use a separate timestamped window to attribute cost per brief, cache behavior, retries, and fallbacks. Reconcile its provider/Railway slice to the overlapping invoice dates, but do not add the window again to the project-to-date total.

| Field | Required value | Source / owner | Status |
|---|---:|---|---|
| Window start (UTC) | **[TODO F7-LIVE-01]** | Owner | Awaiting live run |
| Window end (UTC) | **[TODO F7-LIVE-02]** | Owner | Awaiting live run |
| Agent deployment SHA | **[TODO F7-LIVE-03]** | Git/Railway | Awaiting live run |
| Pricing snapshot date | **[TODO F7-LIVE-04]** | Provider price card | Re-verify R1 |
| Synthetic patients only confirmed | **[TODO F7-LIVE-05: yes/no]** | Owner | Must be `yes` |

### Current exporter limitation

The in-memory `RequestTrace` schema contains `model` and `cache_creation_tokens`, but the current `LangfuseSink.emit()` metadata omits both. It exports aggregate input, output, cache-read tokens, estimated cost, source, degraded state, and fallback kind. The current single-model deployment can be associated with its immutable `LLM_MODEL` configuration, but a future Sonnet/Haiku per-call mix and cache-creation repricing cannot be reconstructed from today's Langfuse export alone.

Before completing the affected placeholders, either:

1. add `model` and `cache_creation_tokens` (and per-call model attribution for mixed-model flows) to the exporter in a separate implementation task; or
2. obtain equivalent call-level data from the provider console/export and reconcile it to Langfuse correlation IDs.

This document does not silently treat missing cache-creation/model fields as zero.

### Langfuse/provider unit-economics export

| Metric | Actual | Provenance | Notes |
|---|---:|---|---|
| Attempted briefs | **[TODO F7-LF-01]** | Langfuse request traces | Deduplicate by correlation ID |
| Successfully served, non-empty briefs | **[TODO F7-LF-02]** | Trace `source` + route result | Primary denominator |
| Deterministic fallbacks | **[TODO F7-LF-03]** | `fallback_kind` | Report cost separately |
| Deterministic refusals | **[TODO F7-LF-04]** | refusal verdict/source | Usually zero LLM cost for pre-flight hard stops |
| Sonnet calls | **[TODO F7-LF-05]** | Deployment config + provider export | Current runtime is expected to be Sonnet-only; Langfuse does not export model today |
| Sonnet ordinary input tokens | **[TODO F7-LF-06]** | Langfuse usage export | Exclude cache token classes |
| Sonnet cache-creation tokens | **[TODO F7-LF-07]** | Provider export or instrumented Langfuse sink | Current sink omits this field |
| Sonnet cache-read tokens | **[TODO F7-LF-08]** | Langfuse usage export | Warm eligible prefix |
| Sonnet output tokens | **[TODO F7-LF-09]** | Langfuse usage export | Use actual, not `max_tokens` |
| Haiku calls and token classes | **[TODO F7-LF-10]** | Provider export or future per-call instrumentation | Do not assume D4 target routing is implemented |
| Trace-estimated Anthropic spend | **[TODO F7-LF-11]** | Sum `cost_usd` | Recompute using the dated price card |
| Provider-console billed spend | **[TODO F7-LF-12]** | Anthropic billing export | Invoice/control total |
| Trace-to-provider variance | **[TODO F7-LF-13]** | Calculated | Explain retries, rounding, credits, or missing traces |

`cost_usd` can be summed from current traces, but dated-price recomputation by model/token class remains blocked until F7-LF-05, F7-LF-07, and F7-LF-10 have an instrumented or provider source.

### Representative-window Railway and observability allocation

| Cost bucket | Actual | Provenance | Allocation rule |
|---|---:|---|---|
| OpenEMR service | **[TODO F7-RWY-01]** | Railway usage export | Direct service cost |
| Managed MySQL | **[TODO F7-RWY-02]** | Railway usage export | Direct service cost |
| Agent service | **[TODO F7-RWY-03]** | Railway usage export | Direct service cost |
| Volumes, backups, and egress | **[TODO F7-RWY-04]** | Railway usage export | Direct where possible |
| Shared project/plan fee | **[TODO F7-RWY-05]** | Railway invoice | Allocate by measured compute or show separately |
| Langfuse Cloud | **[TODO F7-OBS-01]** | Langfuse invoice | Demo free tier is not a production-cost assumption |
| Credits/adjustments | **[TODO F7-ADJ-01]** | Provider invoices | Report below gross spend |
| **Gross representative-window spend** | **[TODO F7-TOTAL-01]** | Sum overlapping window costs | Before credits |
| **Net representative-window spend** | **[TODO F7-TOTAL-02]** | Gross less adjustments | Label promotional effects |
| **Cost/successfully served brief** | **[TODO F7-TOTAL-03]** | Net ÷ F7-LF-02 | Unit economics; not project-to-date total |

### Collection and reconciliation procedure

1. Export project-to-date Anthropic, Railway, and Langfuse invoices through one cutoff; calculate F7-P2D gross and net actual spend.
2. Provision E7.0 and emit live traces from one immutable deployment SHA.
3. Instrument the missing model/cache-creation fields or secure equivalent provider call-level data.
4. Choose a separate UTC window that contains representative cold briefs, warm follow-ups, fallbacks, and retries.
5. Export available Langfuse usage fields plus the supplemental model/cache-creation source.
6. Deduplicate retries and trace fragments into one attempted brief by correlation ID. Preserve every model call within the attempt.
7. Recompute model cost with the price card dated for the window, then reconcile it against the provider console. Do not silently accept a variance.
8. Export Railway usage for the same representative window and allocate only costs attributable to the project. Show shared plan fees explicitly.
9. Reconcile gross and net totals; confirm the representative window is a subset of, not an addition to, project-to-date spend.
10. Publish only aggregates. Retain raw exports in the approved private operational location, not in git.

## 3. Per-brief model economics

### Price card and implemented formula

D4 and R1 provide the planning rates:

| Model | Ordinary input / 1M | Output / 1M | Intended role |
|---|---:|---:|---|
| Claude Sonnet 4.6 | $3.00 | $15.00 | Clinical reasoning and structured claims |
| Claude Haiku 4.5 | $1.00 | $5.00 | Bounded utility calls after eval validation |

The implemented estimator applies a 0.10 multiplier to cache reads and a 1.25 multiplier to five-minute cache creation. For model `m`:

```text
C_model(m) = P_input(m) / 1,000,000
             × [I_uncached + 1.25 × I_cache_create + 0.10 × I_cache_read]
             + P_output(m) / 1,000,000 × O
```

where each token class comes from actual usage records. Sum the formula across every Sonnet and Haiku call in a brief. Never price the configured `max_tokens` ceiling as though it were consumed output.

### Stable-prefix cache model

Let:

- `S` = stable patient-context prefix tokens;
- `V` = volatile, uncached input tokens per turn;
- `O` = output tokens per turn;
- `h` = fraction of eligible stable-prefix tokens served as cache reads;
- `1-h` = fraction written on a cold cache miss.

Assuming every miss creates the five-minute cache:

```text
Expected input cost(m)
  = P_input(m) / 1,000,000
    × [V + S × (1.25 × (1-h) + 0.10 × h)]
```

Then add output cost. This makes the cache-hit rate and the stable/volatile split first-class inputs instead of burying them inside an average token count.

### Measured cache example

The E5 live smoke test is the one measured cache datum currently in the repository: a first Sonnet request wrote a 16,483-token stable prefix, and an identical repeat request read all 16,483 tokens from cache.

Stable-prefix-only economics at Sonnet rates:

| Treatment of 16,483 tokens | Cost | What it means |
|---|---:|---|
| Ordinary uncached input | $0.049449 | Baseline without prompt caching |
| Cold five-minute cache creation | $0.061811 | 1.25× write premium |
| Warm cache read | $0.004945 | 0.10× input rate; saves $0.044504 versus ordinary resend |

These figures exclude volatile input, output, retries, and additional calls. For two sends, one write plus one read costs $0.066756 for the stable prefix versus $0.098898 uncached—a 32.5% saving despite the cold-write premium. At five sends within the cache window the stable-prefix saving is 67%; at ten it is 78.5%; the long-run ceiling approaches the advertised 90% cache-read discount.

This example proves the mechanism, not the average brief. The 16,483-token packet came from one live smoke flow; F7-LF-06 through F7-LF-10 must supply the production distribution.

### Cache sensitivity

The effective multiplier below is `1.25 × (1-h) + 0.10 × h`, relative to ordinary input at 1.0. It assumes misses create a cache entry.

| Cache-hit rate `h` | Effective stable-prefix multiplier | Interpretation |
|---:|---:|---|
| 0% | 1.250 | All writes; caching costs more if no repeat arrives |
| 50% | 0.675 | 32.5% below ordinary resend |
| 80% | 0.330 | 67.0% below ordinary resend |
| 90% | 0.215 | 78.5% below ordinary resend after write churn |
| 95% | 0.158 | 84.3% below ordinary resend after write churn |
| 100% | 0.100 | Full 90% cache-read discount |

The operational goal is not “enable caching”; it is “create enough same-patient reuse inside the cache TTL to recover the write premium.” Measure hit rate by eligible tokens, not merely by calls.

## 4. Two-model routing

| Workload | Model/cost path | Current status | Gate before expansion |
|---|---|---|---|
| Clinical synthesis and typed claim generation | Sonnet 4.6 | Implemented default | Existing verification/eval gate |
| Bounded utility work | Haiku 4.5 | **[SCENARIO — routing not yet evidenced in live traces]** | Task-specific accuracy, injection, and schema evals |
| Claim verification and deterministic rendering | No model | Implemented in code | Keep deterministic; do not assign Haiku spend |
| Hard-stop/refusal pre-flight | No model where detected before LLM | Implemented for defined hard stops | Trace avoided calls |
| 100K open-weight inference | Self-hosted/hybrid | **[FUTURE SCENARIO]** | Clinical quality parity, tool-use reliability, safety evals, and failover |

For projections, record both:

- **Observed mix:** `[TODO F7-MIX-01]` from provider data or an instrumented per-call exporter; current Langfuse metadata cannot reconstruct a mixed-model flow.
- **Scenario mix:** `[TODO F7-MIX-02]` with an explicit percentage of turns routed to Haiku after validation.

Do not retroactively label current Sonnet calls as Haiku-eligible savings. The model split is a controlled future lever until routing and eval evidence exist.

## 5. Workload model

D1 anchors the upper workflow at 20 scheduled patients per clinician-day. Keep adoption and working days explicit:

```text
Monthly briefs = U × 20 × D × A
Monthly turns  = Monthly briefs × (1 + F)
```

where:

- `U` = active clinicians;
- `D` = clinician days per month;
- `A` = fraction of scheduled visits for which a brief is generated;
- `F` = mean follow-up LLM turns per brief.

Planning volume at `D=20` and `A=100%` is an upper-bound scenario, not observed demand:

| Active clinicians | Briefs/month before adoption adjustment | Formula carried into projection |
|---:|---:|---|
| 100 | 40,000 | `40,000 × A` |
| 1,000 | 400,000 | `400,000 × A` |
| 10,000 | 4,000,000 | `4,000,000 × A` |
| 100,000 | 40,000,000 | `40,000,000 × A` |

Required live/scenario inputs:

| Input | Value |
|---|---:|
| Clinician days/month `D` | **[SCENARIO INPUT F7-WL-01; default sensitivity point 20]** |
| Adoption `A` | **[SCENARIO INPUT F7-WL-02]** |
| Follow-up turns/brief `F` | **[TODO F7-WL-03 from traces]** |
| Stable-prefix tokens `S` p50/p95 | **[TODO F7-WL-04 from traces]** |
| Volatile input tokens `V` p50/p95 | **[TODO F7-WL-05 from traces]** |
| Output tokens `O` p50/p95 | **[TODO F7-WL-06 from traces]** |
| Cache-hit rate `h` by eligible tokens | **[TODO F7-WL-07 from traces]** |
| Sonnet/Haiku call mix | **[TODO F7-WL-08 from provider/instrumented traces + scenario]** |
| Precomputed first-brief share | **[SCENARIO INPUT F7-WL-09]** |
| Fallback/refusal/retry rates | **[TODO F7-WL-10 from traces]** |

## 6. Tier projections — topology first

For each tier, calculate:

```text
Monthly total(tier)
  = LLM workload(tier)
  + platform baseline(tier)
  + tier-specific step costs(tier)
  + observability/security(tier)
  + storage/egress(tier)
```

Per-user and per-brief figures are derived outputs. They are not the sizing method.

| Tier | Workload envelope (`D=20`, before `A`) | Required topology | Step costs and dominant drivers | Monthly projection |
|---:|---:|---|---|---:|
| 100 | 40K briefs | One Railway project: OpenEMR, managed MySQL, single agent; Langfuse Cloud; on-demand Sonnet | Fixed managed-service floor is material; cache reuse and output tokens dominate variable AI cost | **[TODO F7-TIER-100: LLM scenario + measured Railway baseline]** |
| 1K | 400K briefs | Horizontal agent replicas; durable Redis session/token/cache and shared cost controls; scale managed DB | Replica floor and Redis are new steps; concurrency, session durability, and cache locality matter more than multiplying the 100-user bill by ten | **[TODO F7-TIER-1K: load-tested replica count + Redis/DB quote + LLM scenario]** |
| 10K | 4M briefs | Queue-based brief/tool execution; worker pools; read replicas; dedicated/multi-region plan; likely managed-PaaS exit | Queue/worker and replica envelopes replace simple request scaling; PaaS usage curve, HA, and data replication dominate the architecture decision | **[TODO F7-TIER-10K: dedicated-platform range + queue/replica capacity + LLM scenario]** |
| 100K | 40M briefs | Multi-region serving; queue and off-peak precompute; dedicated inference fleet or hybrid API fallback; replicated clinical-data access | GPU fleet/throughput, redundancy, model operations, and quality parity dominate; per-token API pricing is no longer the only basis | **[TODO F7-TIER-100K: GPU/hybrid TCO range + multi-region platform + residual API]** |

### Tier transition tests

| Transition | Trigger evidence | Decision |
|---|---|---|
| 100 → 1K | 10/50-VU load baseline, agent CPU/memory, session loss on replica/restart, cache-hit locality | Add replicas and durable shared state; re-price cache behavior |
| 1K → 10K | Queueable morning burst, DB read pressure, PaaS bill, regional availability requirement | Introduce queue/workers/read replicas; compare Railway with dedicated/multi-region TCO |
| 10K → 100K | Annualized provider API spend, model throughput, GPU utilization, clinical eval parity | Move only when avoided API spend exceeds full GPU/serving/SRE/redundancy TCO |

The 100K switch is conditional, not ideological. R4 says self-hosting can become cost-competitive and remove per-token exposure; it does not establish clinical parity. Keep Sonnet as fallback or primary until an open-weight candidate passes the same structured-tool-use, grounding, and adversarial evals.

## 7. Morning pre-computation and batch economics

Pre-computation addresses the morning burst and perceived latency; it does not automatically improve prompt-cache hits.

Each patient's prefix is unique. A five-minute prompt cache helps repeated same-patient turns inside that TTL, while a brief generated hours before a visit is likely cold again unless the provider TTL and scheduling window are deliberately aligned. Batch pricing instead reduces the cost of the first cold generation.

R1 records a 50% batch discount. Model the split as:

```text
C_briefs = (1 - p_pre) × C_on_demand
           + p_pre × C_batch
           + C_follow_up_turns
           + C_precomputed_storage
```

where `p_pre` is the fraction of first briefs prepared off-peak. Do not assume batch and prompt-cache discounts stack; confirm the provider's dated billing rules and invoice behavior first.

The queue must also suppress waste:

- cancel or avoid briefs for changed/cancelled schedules;
- bound refresh frequency when chart data changes;
- store only the minimum PHI-bearing artifact for the minimum retention window;
- trace batch failures and fall back to on-demand generation;
- compare dollars per **opened/served** brief, not per generated brief.

## 8. Sensitivity and break-even analysis

At minimum, rerun the model across:

- cache-hit rates: 0%, 50%, 80%, 90%, 95%;
- p50 and p95 stable-prefix and output token sizes;
- observed model mix plus at least two validated Haiku-routing scenarios;
- adoption and follow-up-turn distributions;
- retry, fallback, and refusal rates;
- precomputed share and unused-precompute rate;
- provider batch eligibility and discount;
- Railway measured baseline versus dedicated-platform quotes;
- 100K API-only, self-hosted-only, and hybrid inference TCO.

### Self-hosted inference break-even

Use full TCO:

```text
API cost avoided
  > GPU amortization/lease
    + model-serving compute
    + idle and peak headroom
    + multi-region redundancy
    + storage/network
    + inference observability
    + model/SRE operations
    + residual provider fallback
```

Then apply a non-financial hard gate: the candidate must meet or exceed the current clinical/eval quality and safety boundary. A cheaper model that weakens structured claims or verification compatibility does not break even.

## 9. Risks and known unknowns

- “100K users” is interpreted as active clinicians. If it instead means covered patients or monthly end users, recompute the workload before using the tier table.
- The current five-minute cache window may be too short for schedule-level reuse; only live hit-rate data can settle this.
- The current runtime exposes one configured model and is expected to be Sonnet-only. Haiku savings are a scenario until routing is implemented and traced.
- The current Langfuse sink omits model and cache-creation metadata; mixed-model attribution and cache-write repricing require instrumentation or provider data.
- Actual output length may dominate a rich brief because Sonnet output is $15/M. Use actual usage, not configured ceilings.
- FHIR query count and database pressure affect worker/replica sizing even though they are not model-token charges.
- Langfuse's demo free tier must not stand in for the D5 production HIPAA plan (currently documented as $199/month plus a signed BAA before real PHI).
- Provider pricing, batch eligibility, cache TTLs, and discount stacking can change. Timestamp every completed projection.
- Precomputed briefs create another PHI-bearing stored artifact in a real deployment; retention and access controls must be priced and designed.

## 10. Completion checklist

- [ ] Export project-to-date provider invoices and fill F7-P2D actual-spend placeholders.
- [ ] Provision E7.0 and confirm the fields actually exported by the live sink.
- [ ] Instrument model/cache-creation/per-call model attribution or obtain equivalent provider data.
- [ ] Export a representative Langfuse window and fill F7-LF placeholders.
- [ ] Reconcile trace-estimated cost to the Anthropic billing console.
- [ ] Export Railway usage for the identical UTC window and fill F7-RWY placeholders.
- [ ] Record gross/net actual development spend and both cost-per-brief denominators.
- [ ] Measure cache-hit rate by eligible tokens and model mix by workload.
- [ ] Fill workload inputs and run p50/p95 sensitivity cases.
- [ ] Obtain Redis, queue/read-replica, dedicated-platform, and GPU/hybrid scenario inputs for their relevant tiers.
- [ ] Re-verify R1 prices, batch/cache rules, and the D5 production Langfuse plan.
- [ ] Have the owner sign off on assumptions and remove every `TODO F7-*` placeholder before marking F7 complete.

## Source ledger

| Anchor | What this analysis takes from it |
|---|---|
| `ARCHITECTURE.md` §9 | Real traces + Railway billing; topology changes at 100/1K/10K/100K; caching/model split; managed-PaaS and self-hosted-inference transitions |
| `docs/planning/DECISIONS.md` D4 | Sonnet 4.6 reasoning, Haiku 4.5 utility, thin provider seam, $3/$15 and $1/$5 planning rates, cache leverage, 100K revisit |
| `docs/planning/RESEARCH.md` R1 | Model price card, 90%-off cache reads, 50% batch discount, prompt-caching rationale |
| `docs/planning/RESEARCH.md` R4 | No GPU at demo tier; dedicated self-hosted inference becomes a 100K economic option |
| `IMPLEMENTATION_PLAN.md` F7 | Actual spend plus nonlinear tier projections is the acceptance criterion |
| `docs/DEVLOG.md` E5 | Measured 16,483-token cache creation/read smoke pair |
| `agent/app/llm/cost.py` | Implemented cache/read multipliers and per-model estimator; cost cap is a guard, not a meter |
| `agent/app/observability/trace.py` + `langfuse.py` | The in-memory schema has model/cache-creation fields, but the current Langfuse sink omits them; completion requires exporter instrumentation or provider data |
| `docs/DEVLOG.md` E7/E9 | Langfuse provisioning and live traces are still pending, justifying the explicit actual-spend placeholders |
