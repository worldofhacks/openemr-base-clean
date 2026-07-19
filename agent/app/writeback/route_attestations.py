"""Durable UUID-to-legacy-route attestations for OpenEMR writes.

OpenEMR's SMART/FHIR surface identifies patients and encounters with UUIDs while
its document and vital write routes require legacy numeric identifiers.  This
registry is the fail-closed bridge: activation imports an attested synthetic-only
snapshot, mappings are immutable and additive, and encounter lookup is always
qualified by the owning patient (W2-D9/D10; W2_ARCHITECTURE §2/§3).

The command ``python -m app.writeback.route_attestations import-stdin`` accepts
one JSON object on stdin and uses ``SESSION_STORE_DSN``.  Its output contains
only status, counts, and the deterministic generation hash.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

Connect = Callable[[], Awaitable[object]]
_GENERATION_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_BIGINT = 9_223_372_036_854_775_807
_ADVISORY_LOCK_NAME = "agent_route_attestations_v1"


class RouteAttestationError(RuntimeError):
    """Base class for fail-closed registry failures."""


class RouteAttestationUnavailable(RouteAttestationError):
    """The registry cannot establish an authoritative answer."""


class RouteAttestationRegistryEmpty(RouteAttestationUnavailable):
    """No active attested patient set exists."""


class RouteAttestationNotFound(RouteAttestationError):
    """The requested patient-qualified route has not been attested."""


class RouteAttestationConflict(RouteAttestationError):
    """An import attempted to change an immutable route binding."""


def _canonical_uuid(value: str) -> str:
    try:
        canonical = str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("route UUID must be canonical") from exc
    if canonical != value:
        raise ValueError("route UUID must be canonical")
    return value


def _canonical_legacy_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or str(int(value)) != value
        or not 0 < int(value) <= _MAX_BIGINT
    ):
        raise ValueError("legacy route ID must be a positive canonical decimal")
    return value


def _canonical_generation(value: str) -> str:
    if not isinstance(value, str) or _GENERATION_RE.fullmatch(value) is None:
        raise ValueError("route generation must be a SHA-256 hex digest")
    return value


class PatientRouteInput(BaseModel):
    """One canonical patient route accepted by activation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patient_uuid: str
    legacy_patient_id: str

    @field_validator("patient_uuid")
    @classmethod
    def _validate_patient_uuid(cls, value: str) -> str:
        return _canonical_uuid(value)

    @field_validator("legacy_patient_id")
    @classmethod
    def _validate_legacy_patient_id(cls, value: str) -> str:
        return _canonical_legacy_id(value)


class EncounterRouteInput(BaseModel):
    """One canonical encounter route and its attested patient owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    encounter_uuid: str
    legacy_encounter_id: str
    patient_uuid: str

    @field_validator("encounter_uuid", "patient_uuid")
    @classmethod
    def _validate_uuid(cls, value: str) -> str:
        return _canonical_uuid(value)

    @field_validator("legacy_encounter_id")
    @classmethod
    def _validate_legacy_encounter_id(cls, value: str) -> str:
        return _canonical_legacy_id(value)


class RouteAttestationBatch(BaseModel):
    """Validated additive activation payload.

    Encounter owners may be omitted from ``patients`` only when that patient is
    already in the registry; repository import verifies that condition inside the
    same serialized transaction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    patients: tuple[PatientRouteInput, ...]
    encounters: tuple[EncounterRouteInput, ...] = ()

    @model_validator(mode="after")
    def _reject_payload_duplicates(self) -> "RouteAttestationBatch":
        if not self.patients:
            raise ValueError("at least one patient attestation is required")
        _require_unique(
            ((row.patient_uuid, row.legacy_patient_id) for row in self.patients),
            kind="patient",
        )
        _require_unique(
            (
                (row.encounter_uuid, row.legacy_encounter_id)
                for row in self.encounters
            ),
            kind="encounter",
        )
        return self


class RouteAttestationEnvelope(BaseModel):
    """Exact activation wire contract, including tamper-evident metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    patients: tuple[PatientRouteInput, ...]
    encounters: tuple[EncounterRouteInput, ...]
    patient_count: int
    encounter_count: int
    snapshot_hash: str

    @field_validator("schema_version")
    @classmethod
    def _require_schema_v1(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported route snapshot schema")
        return value

    @field_validator("snapshot_hash")
    @classmethod
    def _validate_snapshot_hash(cls, value: str) -> str:
        return _canonical_generation(value)

    @model_validator(mode="after")
    def _verify_metadata(self) -> "RouteAttestationEnvelope":
        if self.patient_count != len(self.patients) or self.encounter_count != len(
            self.encounters
        ):
            raise ValueError("route snapshot count mismatch")
        # Validate duplicate semantics through the programmatic batch contract too.
        RouteAttestationBatch(patients=self.patients, encounters=self.encounters)
        signed_payload = {
            "schema_version": self.schema_version,
            "patients": [row.model_dump(mode="json") for row in self.patients],
            "encounters": [row.model_dump(mode="json") for row in self.encounters],
        }
        canonical = json.dumps(
            signed_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        if hashlib.sha256(canonical).hexdigest() != self.snapshot_hash:
            raise ValueError("route snapshot hash mismatch")
        return self

    def batch(self) -> RouteAttestationBatch:
        return RouteAttestationBatch(
            patients=self.patients,
            encounters=self.encounters,
        )


class PatientRouteBinding(PatientRouteInput):
    """Resolved patient route from one active/audited generation."""

    generation_id: str

    @field_validator("generation_id")
    @classmethod
    def _validate_generation(cls, value: str) -> str:
        return _canonical_generation(value)


class EncounterRouteBinding(EncounterRouteInput):
    """Resolved encounter route, qualified by its patient owner."""

    generation_id: str

    @field_validator("generation_id")
    @classmethod
    def _validate_generation(cls, value: str) -> str:
        return _canonical_generation(value)


class RouteAttestationImportResult(BaseModel):
    """Content-minimal activation result safe to emit in operational logs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_id: str
    patient_count: int
    encounter_count: int
    patients_added: int
    encounters_added: int
    changed: bool

    @field_validator("generation_id")
    @classmethod
    def _validate_generation(cls, value: str) -> str:
        return _canonical_generation(value)


def _require_unique(rows: Any, *, kind: str) -> None:
    uuids: set[str] = set()
    legacy_ids: set[str] = set()
    for route_uuid, legacy_id in rows:
        if route_uuid in uuids or legacy_id in legacy_ids:
            raise ValueError(f"duplicate {kind} route in activation payload")
        uuids.add(route_uuid)
        legacy_ids.add(legacy_id)


def _generation_id(
    patients: Mapping[str, str],
    encounters: Mapping[str, tuple[str, str]],
) -> str:
    """Hash the complete effective registry, independent of input ordering."""

    payload = {
        "patients": [
            {"legacy_patient_id": legacy_id, "patient_uuid": patient_uuid}
            for patient_uuid, legacy_id in sorted(patients.items())
        ],
        "encounters": [
            {
                "encounter_uuid": encounter_uuid,
                "legacy_encounter_id": legacy_id,
                "patient_uuid": patient_uuid,
            }
            for encounter_uuid, (legacy_id, patient_uuid) in sorted(
                encounters.items()
            )
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


class PostgresRouteAttestationRepository:
    """Async Postgres authority for immutable patient/encounter route bindings."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def ensure_schema(self) -> None:
        """Idempotently bootstrap migration 006 before the first activation import.

        Activation intentionally deploys the runtime disabled, so the normal document
        startup migration path has not run yet.  The importer owns this narrow bootstrap
        and uses the same injected connection without exposing its DSN.
        """

        migration = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "006_route_attestations.sql"
        ).read_text(encoding="utf-8")
        conn = await self._open()
        try:
            await conn.execute(migration)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - bootstrap ambiguity fails closed
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        finally:
            await _close(conn)

    async def active_generation(self) -> str:
        conn = await self._open()
        try:
            value = await conn.fetchval(  # type: ignore[attr-defined]
                "SELECT active_generation_id FROM agent_route_attestation_state "
                "WHERE singleton=1"
            )
        except Exception as exc:  # noqa: BLE001 - backend ambiguity must fail closed
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        finally:
            await _close(conn)
        if value is None:
            raise RouteAttestationRegistryEmpty("route registry is not activated")
        try:
            return _canonical_generation(str(value))
        except ValueError as exc:
            raise RouteAttestationUnavailable("route registry unavailable") from exc

    async def healthcheck(self) -> bool:
        """Require an active generation containing at least one patient.

        The boolean result is intentionally content-free: readiness must not emit
        patient routes, counts, UUIDs, or numeric OpenEMR identifiers.
        """

        conn = await self._open()
        try:
            healthy = await conn.fetchval(  # type: ignore[attr-defined]
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM agent_route_attestation_state s
                      JOIN agent_patient_route_generation_membership m
                        ON m.generation_id=s.active_generation_id
                     WHERE s.singleton=1
                )
                """
            )
        except Exception as exc:  # noqa: BLE001 - backend ambiguity must fail closed
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        finally:
            await _close(conn)
        if healthy is not True:
            raise RouteAttestationRegistryEmpty("route registry is not activated")
        return True

    async def resolve_patient(
        self, patient_uuid: str, generation_id: str | None = None
    ) -> PatientRouteBinding:
        patient_uuid = _lookup_uuid(patient_uuid)
        generation_id = _lookup_generation(generation_id)
        conn = await self._open()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                WITH target AS (
                    SELECT COALESCE($2::text, active_generation_id) AS generation_id
                      FROM agent_route_attestation_state
                     WHERE singleton=1
                )
                SELECT p.patient_uuid::text AS patient_uuid,
                       p.legacy_patient_id::text AS legacy_patient_id,
                       t.generation_id
                  FROM target t
                  JOIN agent_patient_route_generation_membership m
                    ON m.generation_id=t.generation_id
                  JOIN agent_patient_route_attestations p
                    ON p.patient_uuid=m.patient_uuid
                 WHERE p.patient_uuid=$1::uuid
                """,
                UUID(patient_uuid),
                generation_id,
            )
        except Exception as exc:  # noqa: BLE001 - backend ambiguity must fail closed
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        finally:
            await _close(conn)
        if row is None:
            raise RouteAttestationNotFound("attested route not found")
        try:
            binding = PatientRouteBinding.model_validate(dict(row))
        except Exception as exc:  # noqa: BLE001 - persisted corruption is unavailable
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        if binding.patient_uuid != patient_uuid:
            raise RouteAttestationNotFound("attested route not found")
        return binding

    async def resolve_encounter(
        self,
        patient_uuid: str,
        encounter_uuid: str,
        generation_id: str | None = None,
    ) -> EncounterRouteBinding:
        patient_uuid = _lookup_uuid(patient_uuid)
        encounter_uuid = _lookup_uuid(encounter_uuid)
        generation_id = _lookup_generation(generation_id)
        conn = await self._open()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                WITH target AS (
                    SELECT COALESCE($3::text, active_generation_id) AS generation_id
                      FROM agent_route_attestation_state
                     WHERE singleton=1
                )
                SELECT e.encounter_uuid::text AS encounter_uuid,
                       e.legacy_encounter_id::text AS legacy_encounter_id,
                       e.patient_uuid::text AS patient_uuid,
                       t.generation_id
                  FROM target t
                  JOIN agent_encounter_route_generation_membership m
                    ON m.generation_id=t.generation_id
                  JOIN agent_encounter_route_attestations e
                    ON e.encounter_uuid=m.encounter_uuid
                 WHERE e.encounter_uuid=$2::uuid
                   AND e.patient_uuid=$1::uuid
                """,
                UUID(patient_uuid),
                UUID(encounter_uuid),
                generation_id,
            )
        except Exception as exc:  # noqa: BLE001 - backend ambiguity must fail closed
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        finally:
            await _close(conn)
        if row is None:
            raise RouteAttestationNotFound("attested route not found")
        try:
            binding = EncounterRouteBinding.model_validate(dict(row))
        except Exception as exc:  # noqa: BLE001 - persisted corruption is unavailable
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        if (
            binding.patient_uuid != patient_uuid
            or binding.encounter_uuid != encounter_uuid
        ):
            raise RouteAttestationNotFound("attested route not found")
        return binding

    async def import_batch(
        self, batch: RouteAttestationBatch | Mapping[str, object]
    ) -> RouteAttestationImportResult:
        """Atomically merge and activate an attested batch.

        Raw mappings from the CLI boundary are parsed into the strict batch model
        here, before any database work (parse, don't validate).  The transaction
        takes a process-independent advisory lock before reading.  Exact repeats
        are idempotent; omissions preserve old rows; every UUID and numeric
        identifier is immutable in both directions.
        """

        parsed: RouteAttestationBatch = (
            batch
            if isinstance(batch, RouteAttestationBatch)
            else RouteAttestationBatch.model_validate(batch)
        )
        conn = await self._open()
        try:
            async with conn.transaction():  # type: ignore[attr-defined]
                await conn.execute(  # type: ignore[attr-defined]
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    _ADVISORY_LOCK_NAME,
                )
                patient_rows = await conn.fetch(  # type: ignore[attr-defined]
                    "SELECT patient_uuid::text AS patient_uuid, "
                    "legacy_patient_id::text AS legacy_patient_id "
                    "FROM agent_patient_route_attestations"
                )
                encounter_rows = await conn.fetch(  # type: ignore[attr-defined]
                    "SELECT encounter_uuid::text AS encounter_uuid, "
                    "legacy_encounter_id::text AS legacy_encounter_id, "
                    "patient_uuid::text AS patient_uuid "
                    "FROM agent_encounter_route_attestations"
                )
                previous_generation = await conn.fetchval(  # type: ignore[attr-defined]
                    "SELECT active_generation_id FROM agent_route_attestation_state "
                    "WHERE singleton=1"
                )

                existing_patients = _patient_map(patient_rows)
                existing_encounters = _encounter_map(encounter_rows)
                patients, encounters = _merge_batch(
                    existing_patients, existing_encounters, parsed
                )
                generation_id = _generation_id(patients, encounters)
                now = datetime.now(timezone.utc)
                new_patient_ids = set(patients) - set(existing_patients)
                new_encounter_ids = set(encounters) - set(existing_encounters)

                await conn.execute(  # type: ignore[attr-defined]
                    """
                    INSERT INTO agent_route_attestation_generations
                        (generation_id, patient_count, encounter_count, imported_at)
                    VALUES ($1,$2,$3,$4)
                    ON CONFLICT (generation_id) DO NOTHING
                    """,
                    generation_id,
                    len(patients),
                    len(encounters),
                    now,
                )
                if new_patient_ids:
                    await conn.executemany(  # type: ignore[attr-defined]
                        """
                        INSERT INTO agent_patient_route_attestations
                            (patient_uuid, legacy_patient_id,
                             first_generation_id, attested_at)
                        VALUES ($1::uuid,$2::bigint,$3,$4)
                        """,
                        [
                            (
                                UUID(route_uuid),
                                int(patients[route_uuid]),
                                generation_id,
                                now,
                            )
                            for route_uuid in sorted(new_patient_ids)
                        ],
                    )
                if new_encounter_ids:
                    await conn.executemany(  # type: ignore[attr-defined]
                        """
                        INSERT INTO agent_encounter_route_attestations
                            (encounter_uuid, legacy_encounter_id, patient_uuid,
                             first_generation_id, attested_at)
                        VALUES ($1::uuid,$2::bigint,$3::uuid,$4,$5)
                        """,
                        [
                            (
                                UUID(route_uuid),
                                int(encounters[route_uuid][0]),
                                UUID(encounters[route_uuid][1]),
                                generation_id,
                                now,
                            )
                            for route_uuid in sorted(new_encounter_ids)
                        ],
                    )
                await conn.executemany(  # type: ignore[attr-defined]
                    """
                    INSERT INTO agent_patient_route_generation_membership
                        (generation_id, patient_uuid)
                    VALUES ($1,$2::uuid)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (generation_id, UUID(route_uuid))
                        for route_uuid in sorted(patients)
                    ],
                )
                if encounters:
                    await conn.executemany(  # type: ignore[attr-defined]
                        """
                        INSERT INTO agent_encounter_route_generation_membership
                            (generation_id, encounter_uuid)
                        VALUES ($1,$2::uuid)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            (generation_id, UUID(route_uuid))
                            for route_uuid in sorted(encounters)
                        ],
                    )
                await conn.execute(  # type: ignore[attr-defined]
                    """
                    INSERT INTO agent_route_attestation_state
                        (singleton, active_generation_id, updated_at)
                    VALUES (1,$1,$2)
                    ON CONFLICT (singleton) DO UPDATE
                       SET active_generation_id=EXCLUDED.active_generation_id,
                           updated_at=EXCLUDED.updated_at
                    """,
                    generation_id,
                    now,
                )
        except RouteAttestationConflict:
            raise
        except Exception as exc:  # noqa: BLE001 - partial/ambiguous import fails closed
            raise RouteAttestationUnavailable("route registry unavailable") from exc
        finally:
            await _close(conn)

        return RouteAttestationImportResult(
            generation_id=generation_id,
            patient_count=len(patients),
            encounter_count=len(encounters),
            patients_added=len(new_patient_ids),
            encounters_added=len(new_encounter_ids),
            changed=str(previous_generation or "") != generation_id,
        )

    async def _open(self) -> object:
        try:
            return await self._connect()
        except Exception as exc:  # noqa: BLE001 - connection failure must fail closed
            raise RouteAttestationUnavailable("route registry unavailable") from exc


def _lookup_uuid(value: str) -> str:
    try:
        return _canonical_uuid(value)
    except ValueError as exc:
        raise RouteAttestationNotFound("attested route not found") from exc


def _lookup_generation(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _canonical_generation(value)
    except ValueError as exc:
        raise RouteAttestationNotFound("attested route not found") from exc


def _patient_map(rows: Sequence[object]) -> dict[str, str]:
    result: dict[str, str] = {}
    legacy_ids: set[str] = set()
    try:
        for row in rows:
            values = dict(cast(Mapping[str, object], row))
            uuid = _canonical_uuid(str(values["patient_uuid"]))
            legacy_id = _canonical_legacy_id(str(values["legacy_patient_id"]))
            if uuid in result or legacy_id in legacy_ids:
                raise ValueError("duplicate persisted patient route")
            result[uuid] = legacy_id
            legacy_ids.add(legacy_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise RouteAttestationUnavailable("route registry unavailable") from exc
    return result


def _encounter_map(rows: Sequence[object]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    legacy_ids: set[str] = set()
    try:
        for row in rows:
            values = dict(cast(Mapping[str, object], row))
            uuid = _canonical_uuid(str(values["encounter_uuid"]))
            legacy_id = _canonical_legacy_id(str(values["legacy_encounter_id"]))
            patient_uuid = _canonical_uuid(str(values["patient_uuid"]))
            if uuid in result or legacy_id in legacy_ids:
                raise ValueError("duplicate persisted encounter route")
            result[uuid] = (legacy_id, patient_uuid)
            legacy_ids.add(legacy_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise RouteAttestationUnavailable("route registry unavailable") from exc
    return result


def _merge_batch(
    existing_patients: Mapping[str, str],
    existing_encounters: Mapping[str, tuple[str, str]],
    batch: RouteAttestationBatch,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    patients = dict(existing_patients)
    patient_ids = {legacy_id: uuid for uuid, legacy_id in patients.items()}
    for row in batch.patients:
        prior_id = patients.get(row.patient_uuid)
        prior_uuid = patient_ids.get(row.legacy_patient_id)
        if (prior_id is not None and prior_id != row.legacy_patient_id) or (
            prior_uuid is not None and prior_uuid != row.patient_uuid
        ):
            raise RouteAttestationConflict("immutable route mapping conflict")
        patients[row.patient_uuid] = row.legacy_patient_id
        patient_ids[row.legacy_patient_id] = row.patient_uuid

    encounters = dict(existing_encounters)
    encounter_ids = {
        legacy_id: uuid for uuid, (legacy_id, _owner) in encounters.items()
    }
    for row in batch.encounters:
        if row.patient_uuid not in patients:
            raise RouteAttestationConflict("immutable route mapping conflict")
        new_value = (row.legacy_encounter_id, row.patient_uuid)
        prior_value = encounters.get(row.encounter_uuid)
        prior_uuid = encounter_ids.get(row.legacy_encounter_id)
        if (prior_value is not None and prior_value != new_value) or (
            prior_uuid is not None and prior_uuid != row.encounter_uuid
        ):
            raise RouteAttestationConflict("immutable route mapping conflict")
        encounters[row.encounter_uuid] = new_value
        encounter_ids[row.legacy_encounter_id] = row.encounter_uuid

    # Persisted corruption (an encounter whose owner is absent) is not repairable by
    # activation and must not be activated as an apparently authoritative set.
    if any(owner not in patients for _legacy_id, owner in encounters.values()):
        raise RouteAttestationUnavailable("route registry unavailable")
    return patients, encounters


async def _close(conn: object) -> None:
    close = getattr(conn, "close", None)
    if close is None:
        return
    try:
        result = close()
        if hasattr(result, "__await__"):
            await result
    except Exception:  # noqa: BLE001 - release failure cannot alter the answer
        pass


async def import_from_stream(
    repository: PostgresRouteAttestationRepository,
    input_stream: IO[str],
    output_stream: IO[str],
) -> int:
    """Import one payload and emit content-minimal machine-readable status."""

    try:
        payload = json.load(input_stream)
        envelope = RouteAttestationEnvelope.model_validate(payload)
    except Exception:  # noqa: BLE001 - never echo malformed content or validation details
        _emit_status(output_stream, status="invalid", result=None)
        return 2
    try:
        await repository.ensure_schema()
        result = await repository.import_batch(envelope.batch())
    except RouteAttestationConflict:
        _emit_status(output_stream, status="conflict", result=None)
        return 3
    except RouteAttestationUnavailable:
        _emit_status(output_stream, status="unavailable", result=None)
        return 4
    _emit_status(
        output_stream,
        status="imported" if result.changed else "unchanged",
        result=result,
    )
    return 0


def _emit_status(
    output_stream: IO[str],
    *,
    status: str,
    result: RouteAttestationImportResult | None,
) -> None:
    output_stream.write(
        json.dumps(
            {
                "status": status,
                "generation_hash": result.generation_id if result else None,
                "patient_count": result.patient_count if result else 0,
                "encounter_count": result.encounter_count if result else 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


async def _connect_cli(dsn: str) -> object:
    import asyncpg

    return await asyncpg.connect(dsn)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage attested OpenEMR routes")
    parser.add_argument("command", choices=("import-stdin",))
    args = parser.parse_args(argv)
    if args.command != "import-stdin":  # pragma: no cover - argparse owns this branch
        return 2
    dsn = os.environ.get("SESSION_STORE_DSN")
    if not dsn:
        _emit_status(sys.stderr, status="unavailable", result=None)
        return 4
    repository = PostgresRouteAttestationRepository(
        lambda: _connect_cli(dsn)
    )
    return asyncio.run(import_from_stream(repository, sys.stdin, sys.stdout))


__all__ = [
    "EncounterRouteBinding",
    "EncounterRouteInput",
    "PatientRouteBinding",
    "PatientRouteInput",
    "PostgresRouteAttestationRepository",
    "RouteAttestationBatch",
    "RouteAttestationConflict",
    "RouteAttestationError",
    "RouteAttestationEnvelope",
    "RouteAttestationImportResult",
    "RouteAttestationNotFound",
    "RouteAttestationRegistryEmpty",
    "RouteAttestationUnavailable",
    "import_from_stream",
]


if __name__ == "__main__":  # pragma: no cover - exercised as a module subprocess
    raise SystemExit(main())
