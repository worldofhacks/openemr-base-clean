# R05 production verification — structured event lane live at the release SHA (2026-07-19)

Release SHA `293f18bb9a8203af58c0159f02e218e74ee1edd1`; captured during the O01 journey.

## Sink liveness

`AgentServices` now defaults to `StructuredLogEventSink` (PR #36): every W2 event is one
registry-validated JSON line on stdout beside the app's request logs. Verified live via
`railway logs --service agent` / `--service document-worker` — no `NullEventSink`
behavior remains in production.

## One-correlation-ID reconstruction (W2-REQ-62/73 — event-lane half)

Export method (repeatable):
`railway logs --service agent | grep -E 'w2\.[0-9a-f]{32}'` (and the same for
`document-worker`). During the journey, four distinct correlation IDs were captured;
the richest, `w2.111002f4a74acd388f67d4dffc9bb97a`, reconstructs the full document
pipeline from the ID alone:

- `api` — `request_start route="documents" method="POST"` → `queue.state`
  (`storing` → `queued`, queue_age_ms recorded)
- `worker` — 21 `ingestion.stage`-class events (stage `started`/`completed` with
  per-stage `latency_ms`: source_write, OCR/VLM/grounding stages, artifact write)
- `writeback` — 6 leg events: `source_document` and `extraction_artifact` writes with
  `verified: true` digest readback and per-leg latency

Sample lines (sanitized by construction — the closed event registry admits only
IDs/enums/numbers; no clinical values can enter the lane):

    [INFO] request_start route="documents" correlation_id="w2.111002f4…" method="POST"
    [INFO] attributes={"attempt_count":0,"queue_age_ms":26.375,"state":"storing"} component="api" correlation_id="w2.111002f4…" event_type="queue.state"
    [INFO] attributes={"latency_ms":3050.02…,"stage":"source_write","state":"completed"} component="worker" correlation_id="w2.23278219…"
    [INFO] attributes={"attempt_count":0,"leg":"source_document","state":"complete","verified":true} component="writeback" correlation_id="w2.23278219…"
    [INFO] attributes={"latency_ms":1154.31…,"leg":"extraction_artifact","verified":true} component="writeback" correlation_id="w2.23278219…"

Chain sizes observed: 36 and 24 events (full uploads), 6 and 6 (shorter operations),
spanning both services — the correlation ID alone joins them.

## Remaining owner half (punch list)

- Langfuse UI: resolve one of the captured correlation IDs in the Langfuse project and
  archive the trace screenshot (supervisor → worker → sub-span nesting, R03).
- Alert delivery: set `COPILOT_ALERT_WEBHOOK_URL` (the scheduled checker
  `agent-w2-alerts.yml` runs 6-hourly and dry-runs without it); the three synthetic
  drills are already test-pinned (PR #36).
- Dashboard panels: the ten-panel mapping is documented in `agent/ops/README.md`.
