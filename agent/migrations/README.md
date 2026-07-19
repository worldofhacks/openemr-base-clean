# Agent PostgreSQL migrations — inventory, notes, and data authority

Requirement (AgentForge PDF p.6): "Any schema change from Week 1 must be
accompanied by a migration note. Data authority must be explicit: one source of
truth per data type, no silent overwrites." (AF-P1-03; W2-REQ-56/57/79.)

## Real inventory

The complete, applied inventory is **001, 003, 004, 005, 006, 007**.
**There has never been a migration `002`.** An earlier architecture note
referenced a nonexistent `002_oauth_state.sql`; that was incorrect — OAuth
launch state is deliberately in-memory and one-use
(`app/service.py` `_pkce`), and durable job credentials are migration 005.
The numbering gap is preserved so historical references stay unambiguous.

How they are applied (idempotent, safe across concurrent web/worker startup):

- `001` is embedded in `app/session/store.py` (`ensure_schema`, run at boot).
- `003`–`007` are applied in order by
  `app/ingestion/migrations.py::apply_document_migrations` when the W2 document
  runtime is enabled (pinned by `tests/test_w2_runtime_migrations.py`).

## Migration notes (Week 1 → Week 2)

| Migration | Tables | Note |
|---|---|---|
| `001_sessions.sql` | `agent_sessions` | Week 1. Durable (clinician, patient) SMART session pin; expiry enforced in code. |
| `003_document_jobs.sql` | `agent_document_dedup`, `agent_write_intents`, `agent_document_jobs` | New in Week 2. Permanent patient-scoped document dedup + exactly-once write intents; purgeable queue attempts are a separate table. |
| `004_extraction_refs.sql` | `agent_extraction_refs` | New in Week 2. Canonical extraction-artifact/citation ref authority (see ledger below). |
| `005_job_credentials.sql` | `agent_job_credentials`, `agent_document_worker_heartbeats` | New in Week 2. Encrypted delegated-job credentials (authenticated ciphertext only) + independent worker liveness. |
| `006_route_attestations.sql` | `agent_route_attestation_generations`, `agent_patient_route_attestations`, `agent_encounter_route_attestations` | New in Week 2. Immutable attested UUID→legacy OpenEMR routes; fail-closed resolution. |
| `007_medication_list.sql` | (alters `agent_document_dedup`) | New in Week 2. Adds the `medication_list` doc type — source + grounded artifact only; no clinical-resource write leg. |

## Authority ledger — one source of truth per data type

Conservative declaration pending the AF-P2-04 grader answer. Every read/write
path must be checkable against this table.

| Data type | Authoritative system | Lineage | Access control | Validation / overwrite rule |
|---|---|---|---|---|
| Source clinical documents | **OpenEMR** (`/AI-Source-Documents`) | Clinician upload through the session-pinned agent UI | Delegated SMART token, patient-pinned; agent writes are append-only exactly-once intents (003) | Byte-digest readback must reproduce the admitted `content_hash`; ambiguous commits park as `unknown`, never auto-retried |
| Written vitals | **OpenEMR** (encounter vitals + FHIR Observation) | Grounded intake-form extraction, mapped by the frozen vitals mapper | Delegated token; encounter must be attested for the pinned patient (006) | Standard row AND FHIR projection must reproduce the intent fingerprint; no update/delete surface exists |
| Extraction artifacts + citation refs | **Agent PostgreSQL** (`agent_extraction_refs`, 004; `PostgresArtifactStore`) | VLM extraction over the OpenEMR source bytes, keyed by `document_id`/`content_hash`/version | Written only by the dedicated worker under the job credential (005); reads are session patient-pinned | Strict Pydantic (`ExtractionArtifact`/`CitationV2`); insert-conflict readback comparison rejects divergent re-inserts — no silent overwrite |
| OpenEMR artifact copy (`/AI-Extractions`) | *None — verified projection only* | Serialized from the Postgres-authoritative artifact at write time | Same delegated document path as sources | SHA-256 digest readback against the authoritative serialization; divergence fails closed (`tests/test_artifact_authority_divergence.py`) and the copy is never served as a read path |
| Sessions | **Agent PostgreSQL** (`agent_sessions`, 001) | SMART launch token exchange | Opaque session id; expiry = MIN(token, idle, turn cap) | Expired rows refused on read |
| Job credentials | **Agent PostgreSQL** (`agent_job_credentials`, 005) | Delegated token captured at launch, encrypted | Fernet key held only by the runtime; binding checked against the job's patient | Ciphertext authenticated; binding mismatch fails closed |
| Route attestations | **Agent PostgreSQL** (006), attesting OpenEMR identities | Operator-run registry import from OpenEMR | Read-only at runtime; fail-closed on missing routes | Generation-hash checked; encounters owned by exactly one attested patient |

Divergence between the Postgres authority and the OpenEMR projection is
detected by independent digest readback and **fails closed** — neither copy is
silently served (pinned by `tests/test_artifact_authority_divergence.py` and
`tests/test_document_readback_verification.py`).

## Adding a migration

Next free number: `008` (`002` stays unused). Files must be idempotent
(`CREATE ... IF NOT EXISTS` / guarded `ALTER`), own their transaction, and be
added to `_MIGRATIONS` in `app/ingestion/migrations.py` plus the ordered
assertions in `tests/test_w2_runtime_migrations.py`, with a row in both tables
above.
