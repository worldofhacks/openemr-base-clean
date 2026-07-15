-- W2-D1/D9/D10 / W2_ARCHITECTURE §3: durable delegated-job credentials and
-- independent worker liveness. Token material is authenticated ciphertext only.
BEGIN;

CREATE TABLE IF NOT EXISTS agent_job_credentials (
    credential_ref       TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL UNIQUE,
    clinician_sub        TEXT NOT NULL,
    patient_id           TEXT NOT NULL,
    ciphertext           BYTEA NOT NULL,
    access_expires_at    TIMESTAMPTZ NOT NULL,
    refresh_expires_at   TIMESTAMPTZ NULL,
    revision             INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_ts           TIMESTAMPTZ NOT NULL,
    updated_ts           TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_job_credentials_binding
    ON agent_job_credentials (patient_id, clinician_sub);

-- Web readiness observes this table; only the dedicated worker updates it. Jobs keep
-- their own per-claim heartbeat in migration 003, while this row proves worker liveness
-- even when the queue is empty.
CREATE TABLE IF NOT EXISTS agent_document_worker_heartbeats (
    worker_id       TEXT PRIMARY KEY,
    heartbeat_at    TIMESTAMPTZ NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL
);

COMMIT;
