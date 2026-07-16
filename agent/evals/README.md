# Week 2 graded gate

Run commands from `agent/`.

The recorded Tier 1 gate executes every manifest case with network access disabled and writes
category arithmetic, hashes, call counts, latency, and usage totals only. Its artifact contains
no case IDs or per-case rows:

```bash
python -m evals.w2_runner run --tier recorded
```

Live Tier 2 has independent wall-clock and spend ceilings. Exhausting provider capacity,
the time ceiling, or the cost ceiling produces `INCONCLUSIVE` and exit code `2`; it never turns
an unevaluated case into a pass or a factual failure. Results remain aggregate-only: do not add
fixture text, prompts, transcripts, provider payloads, model output, identifiers, credentials,
or secrets.

To produce a local candidate result for an exact reviewed commit:

```bash
SOURCE_SHA=<40-hex-commit-sha> \
  python -m evals.w2_runner run \
    --tier live \
    --max-cost-usd 10 \
    --max-seconds 1800 \
    --output evals/results-tier2.json
```

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
