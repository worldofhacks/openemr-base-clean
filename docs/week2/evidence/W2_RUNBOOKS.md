# Week 2 response runbooks

These runbooks consume only aggregate metrics and opaque refs. Never paste a document,
query, prompt, transcript, patient/user identifier, token, exception body, or provider
response into an incident channel.

## Extraction or ingestion

1. Confirm whether the failure-rate or p95 alert is isolated to OCR, VLM, grounding,
   artifact write, or readback using the correlation-linked step names.
2. Stop new uploads if write/readback verification is failing. Existing leases may finish;
   do not cancel an in-flight clinical write.
3. Check worker heartbeat, queue age, provider readiness, schema attestation, and category
   attestation. Retry only the durable job; never replay a write outside the intent machine.
4. If a write outcome is unknown, reconcile by correlation marker and byte digest before any
   retry. Escalate ambiguous duplicates for manual review.

## Retrieval or reranker

1. Compare vector-integrity, static-search, and active-reranker readiness checks.
2. Open the dependency breaker if the active reranker is repeatedly unavailable. The W1
   chart path remains available; guideline augmentation is explicitly degraded.
3. Confirm outbound queries pass the production PHI-free validator. Never inspect or copy a
   rejected query. Rebuild the index only from the pinned manifest and verify its hashes.

## Queue or worker

1. If heartbeat age exceeds twice the lease, prevent new claims and inspect the dedicated
   worker deployment identity.
2. Recover stale leases through the repository operation; do not mutate queue rows manually.
3. A growing oldest-item age with a fresh heartbeat indicates downstream saturation. Check
   VLM, OpenEMR, and Postgres readiness before scaling one worker at a time.

## Breaker

1. After five minutes open, confirm the dependency-specific synthetic probe fails.
2. Keep the breaker open until one bounded half-open request succeeds. Do not disable the
   PHI screen, citation verifier, patient pin, or exactly-once controls to restore traffic.

## Evaluation

1. Deterministic score below 100%, factual below 90%, or factual more than five percentage
   points below baseline blocks merge and deployment.
2. Inspect aggregate case booleans and closed failure reasons. Provider exhaustion or a
   corrupt/missing recording is `INCONCLUSIVE`, not a pass.
3. Reproduce the exact SHA. Never update the baseline from CI or reduce the case count.

## Alert-to-runbook mapping

Every rule in `agent/ops/w2_alerts.json` links its response actions here. The scheduled
evaluator (`agent/ops/w2_alert_checker.py`, cron: `.github/workflows/agent-w2-alerts.yml`)
includes the matching link in each notification.

- `extraction-failure-rate` → [Extraction or ingestion](#extraction-or-ingestion)
- `ingestion-p95` → [Extraction or ingestion](#extraction-or-ingestion)
- `retrieval-p95` → [Retrieval or reranker](#retrieval-or-reranker)
- `queue-oldest` → [Queue or worker](#queue-or-worker)
- `worker-heartbeat` → [Queue or worker](#queue-or-worker)
- `breaker-open` → [Breaker](#breaker)
- `deterministic-eval` → [Evaluation](#evaluation)
- `factual-eval-threshold` → [Evaluation](#evaluation)
- `factual-eval-delta` → [Evaluation](#evaluation)
