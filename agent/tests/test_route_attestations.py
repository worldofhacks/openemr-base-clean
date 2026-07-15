"""Per-patient immutable route registry (W2-D9/D10; §2/§3)."""

from __future__ import annotations

import hashlib
import io
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.writeback.route_attestations import (
    EncounterRouteInput,
    PatientRouteInput,
    PostgresRouteAttestationRepository,
    RouteAttestationBatch,
    RouteAttestationConflict,
    RouteAttestationImportResult,
    RouteAttestationNotFound,
    RouteAttestationRegistryEmpty,
    RouteAttestationUnavailable,
    import_from_stream,
)

PATIENT_A = "11111111-1111-4111-8111-111111111111"
PATIENT_B = "22222222-2222-4222-8222-222222222222"
ENCOUNTER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ENCOUNTER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
GENERATION = "a" * 64


def _patient(uuid: str = PATIENT_A, legacy: str = "10") -> PatientRouteInput:
    return PatientRouteInput(patient_uuid=uuid, legacy_patient_id=legacy)


def _encounter(
    uuid: str = ENCOUNTER_A,
    legacy: str = "100",
    owner: str = PATIENT_A,
) -> EncounterRouteInput:
    return EncounterRouteInput(
        encounter_uuid=uuid,
        legacy_encounter_id=legacy,
        patient_uuid=owner,
    )


class _Transaction:
    def __init__(self) -> None:
        self.entered = False
        self.exit_error: type[BaseException] | None = None

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, _exc, _tb):
        self.exit_error = exc_type
        return False


class _ImportConnection:
    def __init__(self, *, patients=(), encounters=(), active=None) -> None:
        self.patients = list(patients)
        self.encounters = list(encounters)
        self.active = active
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.executed_many: list[tuple[str, list[tuple[object, ...]]]] = []
        self.tx = _Transaction()
        self.closed = False

    def transaction(self):
        return self.tx

    async def fetch(self, sql: str):
        if "FROM agent_patient_route_attestations" in sql:
            return self.patients
        if "FROM agent_encounter_route_attestations" in sql:
            return self.encounters
        raise AssertionError("unexpected fetch")

    async def fetchval(self, _sql: str):
        return self.active

    async def execute(self, sql: str, *args: object):
        self.executed.append((sql, args))

    async def executemany(self, sql: str, args):
        self.executed_many.append((sql, list(args)))

    async def close(self):
        self.closed = True


async def _return(value):
    return value


def _batch(*, patients=(_patient(),), encounters=(_encounter(),)):
    return RouteAttestationBatch(patients=patients, encounters=encounters)


def _envelope(*, hash_override: str | None = None, patient_count: int = 1):
    core = {
        "schema_version": 1,
        "patients": [
            {"patient_uuid": PATIENT_A, "legacy_patient_id": "10"},
        ],
        "encounters": [
            {
                "encounter_uuid": ENCOUNTER_A,
                "legacy_encounter_id": "100",
                "patient_uuid": PATIENT_A,
            }
        ],
    }
    digest = hashlib.sha256(
        json.dumps(
            core, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    return {
        **core,
        "patient_count": patient_count,
        "encounter_count": 1,
        "snapshot_hash": hash_override or digest,
    }


def test_batch_rejects_noncanonical_and_duplicate_routes() -> None:
    with pytest.raises(ValidationError):
        PatientRouteInput(patient_uuid=ENCOUNTER_A.upper(), legacy_patient_id="10")
    with pytest.raises(ValidationError):
        PatientRouteInput(patient_uuid=PATIENT_A, legacy_patient_id="010")
    with pytest.raises(ValidationError):
        RouteAttestationBatch(
            patients=(_patient(), _patient(PATIENT_B, "10")),
            encounters=(),
        )


@pytest.mark.asyncio
async def test_import_is_additive_atomic_and_activates_complete_union() -> None:
    conn = _ImportConnection(
        patients=(
            {"patient_uuid": PATIENT_A, "legacy_patient_id": "10"},
        ),
        encounters=(
            {
                "encounter_uuid": ENCOUNTER_A,
                "legacy_encounter_id": "100",
                "patient_uuid": PATIENT_A,
            },
        ),
        active=GENERATION,
    )
    repository = PostgresRouteAttestationRepository(lambda: _return(conn))

    result = await repository.import_batch(
        _batch(
            patients=(_patient(PATIENT_B, "20"),),
            encounters=(_encounter(ENCOUNTER_B, "200", PATIENT_B),),
        )
    )

    assert result.patient_count == 2
    assert result.encounter_count == 2
    assert result.patients_added == 1
    assert result.encounters_added == 1
    assert result.generation_id != GENERATION
    assert conn.tx.entered is True
    assert conn.tx.exit_error is None
    membership_batches = [
        args
        for sql, args in conn.executed_many
        if "agent_patient_route_generation_membership" in sql
    ]
    assert len(membership_batches[0]) == 2
    patient_inserts = [
        args
        for sql, args in conn.executed_many
        if "INSERT INTO agent_patient_route_attestations" in sql
    ]
    encounter_inserts = [
        args
        for sql, args in conn.executed_many
        if "INSERT INTO agent_encounter_route_attestations" in sql
    ]
    assert len(patient_inserts) == 1 and len(patient_inserts[0]) == 1
    assert patient_inserts[0][0][:3] == (
        UUID(PATIENT_B),
        20,
        result.generation_id,
    )
    assert len(encounter_inserts) == 1 and len(encounter_inserts[0]) == 1
    assert encounter_inserts[0][0][:4] == (
        UUID(ENCOUNTER_B),
        200,
        UUID(PATIENT_B),
        result.generation_id,
    )
    assert all(isinstance(row[1], UUID) for row in membership_batches[0])
    assert conn.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "incoming",
    [
        _patient(PATIENT_A, "20"),  # UUID -> a different numeric ID
        _patient(PATIENT_B, "10"),  # numeric ID -> a different UUID
    ],
)
async def test_patient_remaps_abort_before_any_insert(incoming) -> None:
    conn = _ImportConnection(
        patients=(
            {"patient_uuid": PATIENT_A, "legacy_patient_id": "10"},
        )
    )
    repository = PostgresRouteAttestationRepository(lambda: _return(conn))

    with pytest.raises(RouteAttestationConflict, match="^immutable route mapping conflict$"):
        await repository.import_batch(
            RouteAttestationBatch(patients=(incoming,), encounters=())
        )

    assert conn.tx.exit_error is RouteAttestationConflict
    assert not any("INSERT INTO" in sql for sql, _args in conn.executed)


@pytest.mark.asyncio
async def test_encounter_remap_or_cross_patient_owner_aborts_atomically() -> None:
    conn = _ImportConnection(
        patients=(
            {"patient_uuid": PATIENT_A, "legacy_patient_id": "10"},
            {"patient_uuid": PATIENT_B, "legacy_patient_id": "20"},
        ),
        encounters=(
            {
                "encounter_uuid": ENCOUNTER_A,
                "legacy_encounter_id": "100",
                "patient_uuid": PATIENT_A,
            },
        ),
    )
    repository = PostgresRouteAttestationRepository(lambda: _return(conn))

    with pytest.raises(RouteAttestationConflict):
        await repository.import_batch(
            _batch(
                patients=(_patient(PATIENT_B, "20"),),
                encounters=(_encounter(ENCOUNTER_A, "100", PATIENT_B),),
            )
        )

    assert conn.tx.exit_error is RouteAttestationConflict
    assert not any("INSERT INTO" in sql for sql, _args in conn.executed)


class _ResolveConnection:
    def __init__(self, row=None, *, health=True) -> None:
        self.row = row
        self.health = health
        self.args = None
        self.closed = False

    async def fetchrow(self, _sql: str, *args: object):
        self.args = args
        return self.row

    async def fetchval(self, _sql: str):
        return self.health

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_encounter_resolution_is_patient_qualified_and_does_not_leak_ids() -> None:
    conn = _ResolveConnection(row=None)
    repository = PostgresRouteAttestationRepository(lambda: _return(conn))

    with pytest.raises(RouteAttestationNotFound) as exc_info:
        await repository.resolve_encounter(PATIENT_B, ENCOUNTER_A)

    assert conn.args == (UUID(PATIENT_B), UUID(ENCOUNTER_A), None)
    assert str(exc_info.value) == "attested route not found"
    assert PATIENT_B not in str(exc_info.value)
    assert ENCOUNTER_A not in str(exc_info.value)


@pytest.mark.asyncio
async def test_registry_database_failure_and_empty_health_fail_closed() -> None:
    async def fail():
        raise OSError("synthetic database outage")

    unavailable = PostgresRouteAttestationRepository(fail)
    with pytest.raises(RouteAttestationUnavailable, match="^route registry unavailable$"):
        await unavailable.resolve_patient(PATIENT_A)

    conn = _ResolveConnection(health=False)
    empty = PostgresRouteAttestationRepository(lambda: _return(conn))
    with pytest.raises(RouteAttestationRegistryEmpty):
        await empty.healthcheck()


@pytest.mark.asyncio
async def test_import_stream_validates_envelope_and_bootstraps_schema_first() -> None:
    calls: list[str] = []

    class Repository:
        async def ensure_schema(self):
            calls.append("schema")

        async def import_batch(self, batch):
            calls.append("import")
            assert batch.patients == (_patient(),)
            return RouteAttestationImportResult(
                generation_id=GENERATION,
                patient_count=1,
                encounter_count=1,
                patients_added=1,
                encounters_added=1,
                changed=True,
            )

    output = io.StringIO()
    code = await import_from_stream(
        Repository(),  # type: ignore[arg-type]
        io.StringIO(json.dumps(_envelope())),
        output,
    )

    assert code == 0
    assert calls == ["schema", "import"]
    assert json.loads(output.getvalue()) == {
        "status": "imported",
        "generation_hash": GENERATION,
        "patient_count": 1,
        "encounter_count": 1,
    }
    assert PATIENT_A not in output.getvalue()
    assert ENCOUNTER_A not in output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _envelope(patient_count=2),
        _envelope(hash_override="b" * 64),
        {**_envelope(), "schema_version": 2},
    ],
)
async def test_import_stream_rejects_bad_metadata_without_schema_or_id_leak(payload) -> None:
    class Repository:
        async def ensure_schema(self):
            raise AssertionError("invalid input must not touch the database")

    output = io.StringIO()
    code = await import_from_stream(
        Repository(),  # type: ignore[arg-type]
        io.StringIO(json.dumps(payload)),
        output,
    )

    assert code == 2
    assert json.loads(output.getvalue())["status"] == "invalid"
    assert PATIENT_A not in output.getvalue()


@pytest.mark.asyncio
async def test_schema_bootstrap_executes_packaged_migration() -> None:
    conn = _ImportConnection()
    repository = PostgresRouteAttestationRepository(lambda: _return(conn))

    await repository.ensure_schema()

    assert len(conn.executed) == 1
    assert "agent_patient_route_attestations" in conn.executed[0][0]
    assert "ADD COLUMN IF NOT EXISTS encounter_id" in conn.executed[0][0]
    assert conn.closed is True
