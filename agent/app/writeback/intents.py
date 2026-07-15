"""Durable, patient-scoped exactly-once remote-write protocol.

Every execution reconciles the remote surface before a possible POST.  A response
that may have committed moves the permanent intent to ``unknown`` and stops; an
unknown intent is never posted automatically.  This is deliberately not described
as a distributed transaction (W2-D10 / W2_ARCHITECTURE §3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping, Protocol, cast

from app.schemas.documents import FailureReason
from app.schemas.writeback import WriteIntent, WriteLeg, WriteResult, WriteState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_timestamp(value: str | None = None) -> datetime:
    timestamp = (
        datetime.now(timezone.utc)
        if value is None
        else datetime.fromisoformat(value)
    )
    if timestamp.tzinfo is None:
        raise ValueError("intent timestamps must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class IntentSpec:
    patient_id: str
    document_id_or_content_hash: str
    leg: WriteLeg
    version: int
    field_id: str | None
    correlation_marker: str
    payload_hash: str

    @property
    def key(self) -> tuple[str, str, WriteLeg, int, str | None]:
        return (
            self.patient_id,
            self.document_id_or_content_hash,
            self.leg,
            self.version,
            self.field_id,
        )


@dataclass(frozen=True)
class RemoteMatch:
    remote_id: str
    payload_hash: str


class AmbiguousCommitError(Exception):
    """The POST may have committed remotely; automatic work must stop."""


class ReconciliationRequired(Exception):
    """An unknown intent has not yet reconciled to one verified remote object."""


class ReconciliationConflict(ReconciliationRequired):
    """Multiple or fingerprint-conflicting remote objects were discovered."""


class IntentRepository(Protocol):
    async def get_or_create(self, spec: IntentSpec) -> WriteIntent: ...

    async def save(self, intent: WriteIntent) -> WriteIntent: ...


class WriteTransport(Protocol):
    async def discover(self, intent: WriteIntent) -> list[RemoteMatch]: ...

    async def post(self, intent: WriteIntent, payload: object) -> str | None: ...

    async def verify(
        self, intent: WriteIntent, match: RemoteMatch, payload_hash: str
    ) -> bool: ...


class InMemoryIntentRepository:
    """Deterministic repository for unit tests; production uses Postgres below."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, WriteLeg, int, str | None], WriteIntent] = {}
        self._keys_by_id: dict[str, tuple[str, str, WriteLeg, int, str | None]] = {}

    async def get_or_create(self, spec: IntentSpec) -> WriteIntent:
        existing = self._by_key.get(spec.key)
        if existing is not None:
            if (
                existing.payload_hash != spec.payload_hash
                or existing.correlation_marker != spec.correlation_marker
            ):
                raise ReconciliationConflict("permanent intent key reused with new payload")
            return existing
        intent = WriteIntent(
            intent_id=str(uuid.uuid4()),
            patient_id=spec.patient_id,
            document_id_or_content_hash=spec.document_id_or_content_hash,
            leg=spec.leg,
            version=spec.version,
            field_id=spec.field_id,
            correlation_marker=spec.correlation_marker,
            payload_hash=spec.payload_hash,
            state=WriteState.PENDING,
            remote_id=None,
            attempt_count=0,
            updated_ts=_now(),
        )
        self._by_key[spec.key] = intent
        self._keys_by_id[intent.intent_id] = spec.key
        return intent

    async def save(self, intent: WriteIntent) -> WriteIntent:
        key = self._keys_by_id[intent.intent_id]
        self._by_key[key] = intent
        return intent


class PostgresIntentRepository:
    """Permanent WriteIntent authority backed by migration 003.

    A fresh connection is acquired per method so web and worker replicas share the
    same UNIQUE patient-scoped key.  The connection factory is injectable and may
    return either an asyncpg connection or a test double.
    """

    def __init__(self, connect: Callable[[], Awaitable[object]]) -> None:
        self._connect = connect

    async def get_or_create(self, spec: IntentSpec) -> WriteIntent:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                INSERT INTO agent_write_intents
                    (intent_id, patient_id, document_id_or_content_hash, leg, version,
                     field_id, correlation_marker, payload_hash, state, remote_id,
                     attempt_count, updated_ts)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NULL,0,$10)
                ON CONFLICT
                    (patient_id, document_id_or_content_hash, leg, version,
                     (COALESCE(field_id, '')))
                DO UPDATE SET updated_ts = agent_write_intents.updated_ts
                RETURNING *
                """,
                str(uuid.uuid4()),
                spec.patient_id,
                spec.document_id_or_content_hash,
                spec.leg.value,
                spec.version,
                spec.field_id,
                spec.correlation_marker,
                spec.payload_hash,
                WriteState.PENDING.value,
                _db_timestamp(),
            )
            intent = _intent_from_row(row)
            if (
                intent.payload_hash != spec.payload_hash
                or intent.correlation_marker != spec.correlation_marker
            ):
                raise ReconciliationConflict("permanent intent key reused with new payload")
            return intent
        finally:
            await _close(conn)

    async def save(self, intent: WriteIntent) -> WriteIntent:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                UPDATE agent_write_intents
                   SET state=$2, remote_id=$3, attempt_count=$4, updated_ts=$5
                 WHERE intent_id=$1
                RETURNING *
                """,
                intent.intent_id,
                intent.state.value,
                intent.remote_id,
                intent.attempt_count,
                _db_timestamp(intent.updated_ts),
            )
            if row is None:
                raise KeyError(intent.intent_id)
            return _intent_from_row(row)
        finally:
            await _close(conn)


async def _close(conn: object) -> None:
    close = getattr(conn, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result


def _intent_from_row(row: object) -> WriteIntent:
    values = dict(cast(Mapping[str, object], row))
    updated = values.get("updated_ts")
    isoformat = getattr(updated, "isoformat", None)
    if callable(isoformat):
        values["updated_ts"] = isoformat()
    return WriteIntent.model_validate(values)


class ExactlyOnceWriter:
    """Reconcile-before-POST state machine shared by all three write legs."""

    def __init__(self, repository: IntentRepository, transport: WriteTransport) -> None:
        self._repository = repository
        self._transport = transport

    async def execute(self, spec: IntentSpec, *, payload: object) -> WriteResult:
        intent = await self._repository.get_or_create(spec)
        if intent.state is WriteState.COMPLETE:
            return self._result(intent, verified=True)

        matches = await self._transport.discover(intent)
        if len(matches) > 1:
            await self._mark_unknown(intent)
            raise ReconciliationConflict("multiple remote matches for one permanent intent")
        if matches:
            return await self._complete_from_match(intent, matches[0])

        if intent.state is WriteState.UNKNOWN:
            # Absence was observed, but automatic work still stops. An explicit operator/
            # retry transaction may return the same permanent intent to pending.
            raise ReconciliationRequired("unknown intent has no verified remote match")

        intent = intent.model_copy(
            update={
                "attempt_count": intent.attempt_count + 1,
                "updated_ts": _now(),
            }
        )
        intent = await self._repository.save(intent)
        try:
            remote_id = await self._transport.post(intent, payload)
        except AmbiguousCommitError:
            intent = await self._mark_unknown(intent)
            return self._result(
                intent,
                verified=False,
                failure_reason=FailureReason.WRITEBACK_FAILED,
            )

        if remote_id:
            return await self._complete_from_match(
                intent,
                RemoteMatch(remote_id=remote_id, payload_hash=intent.payload_hash),
            )

        # OpenEMR document upload commonly returns true without an id; discovery is
        # mandatory. Anything other than one verified match is ambiguous.
        matches = await self._transport.discover(intent)
        if len(matches) == 1:
            return await self._complete_from_match(intent, matches[0])
        intent = await self._mark_unknown(intent)
        if len(matches) > 1:
            raise ReconciliationConflict("post produced conflicting remote matches")
        return self._result(
            intent,
            verified=False,
            failure_reason=FailureReason.WRITEBACK_FAILED,
        )

    async def _complete_from_match(
        self, intent: WriteIntent, match: RemoteMatch
    ) -> WriteResult:
        if match.payload_hash != intent.payload_hash:
            await self._mark_unknown(intent)
            raise ReconciliationConflict("remote fingerprint does not match intent payload")
        verified = await self._transport.verify(intent, match, intent.payload_hash)
        if not verified:
            unknown = await self._mark_unknown(intent)
            return self._result(
                unknown,
                verified=False,
                failure_reason=FailureReason.WRITEBACK_VERIFY_FAILED,
            )
        complete = intent.model_copy(
            update={
                "state": WriteState.COMPLETE,
                "remote_id": match.remote_id,
                "updated_ts": _now(),
            }
        )
        complete = await self._repository.save(complete)
        return self._result(complete, verified=True)

    async def _mark_unknown(self, intent: WriteIntent) -> WriteIntent:
        return await self._repository.save(
            intent.model_copy(
                update={"state": WriteState.UNKNOWN, "updated_ts": _now()}
            )
        )

    @staticmethod
    def _result(
        intent: WriteIntent,
        *,
        verified: bool,
        failure_reason: FailureReason | None = None,
    ) -> WriteResult:
        return WriteResult(
            intent_id=intent.intent_id,
            state=intent.state,
            remote_id=intent.remote_id,
            payload_hash=intent.payload_hash,
            verified=verified,
            failure_reason=failure_reason,
        )
