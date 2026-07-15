-- W2-D9/D10 / W2_ARCHITECTURE §2/§3: immutable, per-patient attested
-- UUID -> legacy numeric OpenEMR routes.  Runtime resolution is fail-closed and
-- encounter routes remain owned by exactly one attested patient.
BEGIN;

CREATE TABLE IF NOT EXISTS agent_route_attestation_generations (
    generation_id   TEXT PRIMARY KEY
        CHECK (generation_id ~ '^[0-9a-f]{64}$'),
    patient_count   INTEGER NOT NULL CHECK (patient_count > 0),
    encounter_count INTEGER NOT NULL CHECK (encounter_count >= 0),
    imported_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_patient_route_attestations (
    patient_uuid       UUID PRIMARY KEY,
    legacy_patient_id  BIGINT NOT NULL UNIQUE CHECK (legacy_patient_id > 0),
    first_generation_id TEXT NOT NULL
        REFERENCES agent_route_attestation_generations(generation_id),
    attested_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_encounter_route_attestations (
    encounter_uuid       UUID PRIMARY KEY,
    legacy_encounter_id  BIGINT NOT NULL UNIQUE CHECK (legacy_encounter_id > 0),
    patient_uuid         UUID NOT NULL
        REFERENCES agent_patient_route_attestations(patient_uuid),
    first_generation_id  TEXT NOT NULL
        REFERENCES agent_route_attestation_generations(generation_id),
    attested_at          TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_encounter_route_patient
    ON agent_encounter_route_attestations (patient_uuid);

-- Each generation is a complete immutable view of the additive registry.  Old
-- memberships remain available for audit while the singleton pointer advances.
CREATE TABLE IF NOT EXISTS agent_patient_route_generation_membership (
    generation_id  TEXT NOT NULL
        REFERENCES agent_route_attestation_generations(generation_id),
    patient_uuid   UUID NOT NULL
        REFERENCES agent_patient_route_attestations(patient_uuid),
    PRIMARY KEY (generation_id, patient_uuid)
);

CREATE TABLE IF NOT EXISTS agent_encounter_route_generation_membership (
    generation_id   TEXT NOT NULL
        REFERENCES agent_route_attestation_generations(generation_id),
    encounter_uuid  UUID NOT NULL
        REFERENCES agent_encounter_route_attestations(encounter_uuid),
    PRIMARY KEY (generation_id, encounter_uuid)
);

CREATE TABLE IF NOT EXISTS agent_route_attestation_state (
    singleton             SMALLINT PRIMARY KEY DEFAULT 1 CHECK (singleton = 1),
    active_generation_id  TEXT NOT NULL
        REFERENCES agent_route_attestation_generations(generation_id),
    updated_at            TIMESTAMPTZ NOT NULL
);

-- SMART encounter context is optional.  Absence means source/artifact-only;
-- callers and workers must never invent an encounter.
ALTER TABLE agent_sessions
    ADD COLUMN IF NOT EXISTS encounter_id TEXT NULL;

COMMIT;
