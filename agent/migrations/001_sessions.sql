-- Agent session store (ARCHITECTURE.md §3a, D-O2, D12).
-- One row per SMART launch, pinned to (clinician, patient). PHI note: this table
-- holds the FHIR patient id (a PHI identifier) — it is an in-boundary PHI store
-- (§6a) and inherits the deployment's encryption-at-rest + retention obligations.
-- Lifetime is enforced in code as MIN(token expiry, idle timeout, turn cap);
-- expired rows are refused on read and should be purged by a retention job.

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id        TEXT        PRIMARY KEY,
    clinician_sub     TEXT        NOT NULL,   -- the delegated clinician (OIDC sub)
    patient_id        TEXT        NOT NULL,   -- pinned SMART launch/patient (FHIR id)
    encounter_id      TEXT        NULL,       -- optional SMART launch/encounter (FHIR id)
    created_at        TIMESTAMPTZ NOT NULL,
    last_activity_at  TIMESTAMPTZ NOT NULL,
    token_expires_at  TIMESTAMPTZ NOT NULL,
    idle_timeout_s    INTEGER     NOT NULL,
    turn_cap          INTEGER     NOT NULL,
    turns_used        INTEGER     NOT NULL DEFAULT 0
);

-- Retention/purge support: find expired sessions cheaply.
CREATE INDEX IF NOT EXISTS idx_agent_sessions_token_expires_at
    ON agent_sessions (token_expires_at);
