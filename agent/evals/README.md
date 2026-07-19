# Week 2 graded gate

Run commands from `agent/`.

The recorded Tier 1 gate executes every manifest case with network access disabled and writes
category arithmetic, hashes, call counts, latency, and usage totals only. Its artifact contains
no case IDs or per-case rows:

```bash
python -m evals.w2_runner run --tier recorded
```

Tier 1 replays an exact uploaded-document claim selector through the production answer
resolver and separately checks extraction-citation coverage and every rendered claim's
CitationV2/page/bbox contract. Recording index v2 binds fixture hashes, the extraction
prompt/schema, the answer system prompt, the `submit_claims` tool schema, and the
exact shared answer question, verified-evidence context schema, and deterministic selector
replay version.

Refresh those metadata-only bindings after a reviewed contract change, then run the full
recorded gate and artifact scan:

```bash
make record-evals
git diff -- evals/recordings/index.json
```

This command is offline and clears live-provider credentials. It records no prompts, model
output, transcripts, document text, extracted values, or clinical claim text.

## Production retrieval in the gate (R02 / AF-P0-02)

Every graded case traverses the production `corpus.retrieval.HybridRetriever` over the
committed corpus/index: real BM25, the committed dense matrix, reciprocal-rank fusion, the
real reranker seam with its score floor, and the real `RetrievalUnavailableError` contract.
Offline determinism comes from recorded model adapters (`evals/retrieval_adapters.py`): the
pinned bge query vectors and mxbai rerank scores are replayed byte-identically from
`evals/recordings/retrieval.json`, which binds the corpus manifest hash and both model
revisions and fails closed when stale. The retired term-overlap pseudo-retrieval
(`evals.execution._local_retrieve`) is never an accepted evaluator route.

Golden retrieval behaviors are pinned as `expected_retrieval` blocks in
`evals/golden/cases.json`: a relevant guideline hit with exact ordered chunk ids and a
rendered-guideline association (`lab-clean-hba1c-high`), rank stability over fused lipid
candidates (`lab-multi-lipid-panel`), a healthy zero-hit miss (`lab-missing-collection-date`),
a no-query boundary (`intake-followup-no-retrieval-query`), and an explicit
retrieval-unavailable degradation (`lab-retrieval-unavailable-degraded`).

Regenerate the retrieval recording only after a reviewed corpus/model/case change. This is
an EXPLICIT ONLINE owner step (downloads the pinned model revisions; never run in CI):

```bash
python -m evals.record_retrieval --write   # online, owner-only
python -m evals.record_retrieval --check   # offline coverage check
```

The aggregate result pins the retrieval configuration (`retrieval` block: corpus version,
corpus manifest hash, embedder/reranker revisions, retrieval-recording hash); the artifact
scanner fails any result whose pins drift from the committed corpus.

### Recorded-tier baseline (PR-time delta rule)

The recorded tier loads the committed `evals/w2_recorded_baseline.json` on every run, so the
PDF's "fail if any category regresses by more than 5% or drops below the pass threshold"
rule binds at PR time, not only live-tier. A missing baseline fails closed in CI. Regenerate
only from a complete green recorded run bound to an exact SHA (refused in CI):

```bash
SOURCE_SHA=<40-hex-commit-sha> \
  python -m evals.w2_runner run --tier recorded --bootstrap-recorded-baseline \
  --output /tmp/results-tier1-candidate.json
python -m evals.w2_runner recorded-baseline \
  --results /tmp/results-tier1-candidate.json \
  --output evals/w2_recorded_baseline.json
```

### Retrieval mutation drills (must turn the gate red)

Two documented drills prove the gate detects production-retrieval regressions, mirroring the
`drill/w2-red-*` pattern (temporary branch, one mutating commit, red gate evidence, then
restore). Permanent pytest equivalents live in `evals/test_retrieval_gate.py`.

1. **Break ranking** — invert the reranker ordering in `corpus/retrieval.py`
   (`RerankerSeam.rerank`: return `1.0 - score` per score). Run
   `make eval-tier1`: `citation_present` drops below its 100% invariant (ordered-chunk and
   rendered-guideline cases fail) and the gate exits red.
2. **Break availability** — make retrieval construction unavailable (raise
   `RetrievalUnavailableError` from `evals.retrieval_adapters.default_eval_retriever`).
   Run `make eval-tier1`: hit-expectation cases observe `unavailable` instead of their
   pinned hits, `citation_present` fails, and the gate exits red.

Restore the mutation and rerun `make eval-tier1` to green before merging anything.

For a reviewed, exact-SHA diagnosis, use `diagnose-live` locally with one or more explicit
`--case-id` arguments (20 maximum). It emits `tier=live_subset`, cannot generate a baseline,
and cannot satisfy the required `eval-tier2-live` status. This diagnostic path is not a
substitute for the single final full Tier-2 run.

```bash
SOURCE_SHA=<40-hex-commit-sha> RUN_LIVE=1 \
  python -m evals.w2_runner diagnose-live \
  --case-id <case-id> --output /tmp/results-live-subset.json
```

The protected CI diagnostic uses the committed case-ID list and an exact branch binding.
Push the unchanged commit to `tier2-subset/<40-hex-sha>`; the separate
`agent-eval-live-subset` workflow runs only that bounded subset and retains only its sanitized
Boolean matrix. It never creates or impersonates the required full-live check.

Live Tier 2 has independent wall-clock and spend ceilings. Exhausting provider capacity,
the time ceiling, or the cost ceiling produces `INCONCLUSIVE` and exit code `2`; it never turns
an unevaluated case into a pass or a factual failure. Live results may retain only case IDs and
boolean rubric outcomes so a failed run is localizable; do not add fixture text, citations,
prompts, transcripts, provider payloads, model output, identifiers, credentials, or secrets.
The scanner normalizes typed operational counters only when the result is explicitly passed
with `--eval-result`; every other JSON document is scanned without normalization.

To produce a local candidate result for an exact reviewed commit:

```bash
SOURCE_SHA=<40-hex-commit-sha> \
  python -m evals.w2_runner run \
    --tier live \
    --max-cost-usd 10 \
    --max-seconds 1800 \
    --output evals/results-tier2.json
```

The protected full CI run uses the same binding on `tier2/<40-hex-sha>`. After that one run is
green, a fast-forward of the identical commit to `main` re-attests the successful exact-SHA
result instead of making a second provider call. Missing, ambiguous, expired, cross-run, or
non-green evidence fails closed and cannot trigger deployment.

A baseline candidate can be generated only by an explicit local command from a complete green
live result: all 50 cases must execute and pass, every deterministic category must be 100%,
factual consistency must be at least 90%, and the recorded cost/time must remain within the
declared ceilings.

```bash
python -m evals.w2_runner baseline \
  --results evals/results-tier2.json \
  --output evals/w2_baseline.json
```

Generation does not make the file reviewed. Review and merge the candidate through a normal PR;
its source SHA and canonical result hash bind it to the green run. CI and `main` require the
canonical `evals/w2_baseline.json` before a live gate can pass. They only read and compare that
file: the baseline-generation command refuses to run in CI.

Exit codes are `0` for `PASS`, `1` for a real gate/configuration failure, and `2` for
`INCONCLUSIVE`.
