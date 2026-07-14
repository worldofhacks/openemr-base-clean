-- W2-D10 / W2_ARCHITECTURE §2/§3: permanent patient-scoped dedup + intents,
-- with purgeable queue attempts kept in a separate table.
BEGIN;

CREATE TABLE IF NOT EXISTS agent_document_dedup (
    document_id      TEXT PRIMARY KEY,
    patient_id       TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    doc_type         TEXT NOT NULL CHECK (doc_type IN ('lab_pdf','intake_form')),
    filename         TEXT NOT NULL,
    content_type     TEXT NOT NULL,
    encounter_id     TEXT NULL,
    job_id           TEXT NOT NULL UNIQUE,
    correlation_id   TEXT NOT NULL,
    credential_ref   TEXT NOT NULL,
    created_ts       TIMESTAMPTZ NOT NULL,
    updated_ts       TIMESTAMPTZ NOT NULL,
    UNIQUE (patient_id, content_hash)
);

CREATE TABLE IF NOT EXISTS agent_document_jobs (
    job_id              TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL UNIQUE REFERENCES agent_document_dedup(document_id),
    state               TEXT NOT NULL CHECK (state IN
        ('storing','reconciling','queued','extracting','grounding','writing','complete','failed')),
    reason              TEXT NULL,
    claim_owner         TEXT NULL,
    lease_expires_at    TIMESTAMPTZ NULL,
    heartbeat_at        TIMESTAMPTZ NULL,
    attempt_count       INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_retry_at       TIMESTAMPTZ NULL,
    fields_grounded     INTEGER NOT NULL DEFAULT 0 CHECK (fields_grounded >= 0),
    fields_unsupported  INTEGER NOT NULL DEFAULT 0 CHECK (fields_unsupported >= 0),
    created_ts          TIMESTAMPTZ NOT NULL,
    updated_ts          TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_jobs_claim
    ON agent_document_jobs (state, next_retry_at, lease_expires_at);

CREATE TABLE IF NOT EXISTS agent_write_intents (
    intent_id                    TEXT PRIMARY KEY,
    patient_id                   TEXT NOT NULL,
    document_id_or_content_hash  TEXT NOT NULL,
    leg                          TEXT NOT NULL CHECK
        (leg IN ('source_document','extraction_artifact','vital')),
    version                      INTEGER NOT NULL,
    field_id                     TEXT NULL,
    correlation_marker           TEXT NOT NULL,
    payload_hash                 TEXT NOT NULL,
    state                        TEXT NOT NULL CHECK (state IN ('pending','unknown','complete')),
    remote_id                    TEXT NULL,
    attempt_count                INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    updated_ts                   TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_write_intent_permanent_key
    ON agent_write_intents
       (patient_id, document_id_or_content_hash, leg, version, (COALESCE(field_id, '')));

CREATE TABLE IF NOT EXISTS agent_job_attempts (
    attempt_id       BIGSERIAL PRIMARY KEY,
    job_id           TEXT NOT NULL REFERENCES agent_document_jobs(job_id),
    worker_id        TEXT NULL,
    started_ts       TIMESTAMPTZ NOT NULL,
    completed_ts     TIMESTAMPTZ NULL,
    outcome          TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_job_attempts_started
    ON agent_job_attempts (started_ts);

COMMIT;
