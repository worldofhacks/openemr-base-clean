-- W2-D3/D10 / W2_ARCHITECTURE §2/§3: canonical extraction/citation ref authority.
BEGIN;

CREATE TABLE IF NOT EXISTS agent_extraction_refs (
    ref          TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES agent_document_dedup(document_id),
    kind         TEXT NOT NULL CHECK (kind IN ('artifact','citation')),
    ordinal      INTEGER NOT NULL CHECK (ordinal >= 0),
    payload      JSONB NOT NULL,
    created_ts   TIMESTAMPTZ NOT NULL,
    UNIQUE (document_id, kind, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_agent_extraction_refs_document
    ON agent_extraction_refs (document_id, kind, ordinal);

COMMIT;
