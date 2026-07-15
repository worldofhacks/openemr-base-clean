"""Patient-scoped permanent document dedup and durable job repository (§3).

The production adapter targets migration 003. Queue attempts are separate from the
permanent dedup/intent authority, so attempt retention can never erase idempotency.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Literal, Mapping, Protocol, cast

from app.schemas.documents import DocumentStatus, FailureReason

DocumentType = Literal["lab_pdf", "intake_form"]


Clock = Callable[[], datetime]
ACTIVE_STATES = frozenset({"extracting", "grounding", "writing"})
SOURCE_STATES = frozenset({"storing", "reconciling"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_at(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("queue timestamps must be timezone-aware")
    return value.isoformat()


@dataclass(frozen=True)
class NewDocument:
    patient_id: str
    content_hash: str
    doc_type: DocumentType
    filename: str
    content_type: str
    encounter_id: str | None
    correlation_id: str
    credential_ref: str


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    job_id: str
    patient_id: str
    content_hash: str
    doc_type: DocumentType
    filename: str
    content_type: str
    encounter_id: str | None
    correlation_id: str
    credential_ref: str
    state: str
    reason: FailureReason | None
    fields_grounded: int
    fields_unsupported: int
    attempt_count: int
    next_retry_at: str | None
    claim_owner: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    created_ts: str
    updated_ts: str

    def to_status(self) -> DocumentStatus:
        return DocumentStatus(
            document_id=self.document_id,
            state=self.state,
            reason=self.reason,
            correlation_id=self.correlation_id,
            updated_ts=self.updated_ts,
            fields_grounded=self.fields_grounded,
            fields_unsupported=self.fields_unsupported,
            attempt_count=self.attempt_count,
            next_retry_at=self.next_retry_at,
        )


class DocumentNotFound(KeyError):
    pass


class DocumentPatientMismatch(PermissionError):
    pass


class DocumentNotRetryable(RuntimeError):
    pass


class DocumentLeaseLost(RuntimeError):
    """A worker tried to mutate a job it no longer owns."""


class DocumentRepository(Protocol):
    async def get_or_create(self, new: NewDocument) -> tuple[DocumentRecord, bool]: ...

    async def get(self, document_id: str) -> DocumentRecord: ...

    async def set_state(
        self,
        document_id: str,
        *,
        state: str,
        reason: FailureReason | None = None,
        fields_grounded: int | None = None,
        fields_unsupported: int | None = None,
    ) -> DocumentRecord: ...

    async def claim_source_storage(
        self, document_id: str, *, owner: str, lease_seconds: int
    ) -> DocumentRecord | None: ...

    async def finish_source_storage(
        self,
        document_id: str,
        *,
        owner: str,
        state: str,
        reason: FailureReason | None = None,
    ) -> DocumentRecord: ...

    async def requeue_failed(
        self, document_id: str, *, patient_id: str
    ) -> DocumentRecord: ...

    async def list_for_patient(
        self, patient_id: str, *, state: str | None = None
    ) -> list[DocumentRecord]: ...

    async def claim_next(
        self, worker_id: str, *, lease_seconds: int
    ) -> DocumentRecord | None: ...

    async def heartbeat(
        self, document_id: str, *, worker_id: str, lease_seconds: int
    ) -> DocumentRecord: ...

    async def transition_claimed(
        self, document_id: str, *, worker_id: str, state: str
    ) -> DocumentRecord: ...

    async def reschedule_claimed(
        self,
        document_id: str,
        *,
        worker_id: str,
        reason: FailureReason | str,
        next_retry_at: datetime,
    ) -> DocumentRecord: ...

    async def complete_claimed(
        self,
        document_id: str,
        *,
        worker_id: str,
        fields_grounded: int,
        fields_unsupported: int,
    ) -> DocumentRecord: ...

    async def fail_claimed(
        self, document_id: str, *, worker_id: str, reason: FailureReason | str
    ) -> DocumentRecord: ...

    async def recover_stale(self) -> int: ...


class InMemoryDocumentRepository:
    """Behaviorally equivalent fake for route/service tests."""

    def __init__(self, *, now: Clock | None = None) -> None:
        self._by_id: dict[str, DocumentRecord] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._unknown_documents: set[str] = set()
        self._now = now or _utcnow

    def _timestamp(self) -> str:
        return _iso_at(self._now())

    async def get_or_create(self, new: NewDocument) -> tuple[DocumentRecord, bool]:
        existing_id = self._by_key.get((new.patient_id, new.content_hash))
        if existing_id is not None:
            return self._by_id[existing_id], False
        now = self._timestamp()
        row = DocumentRecord(
            document_id=str(uuid.uuid4()),
            job_id=str(uuid.uuid4()),
            patient_id=new.patient_id,
            content_hash=new.content_hash,
            doc_type=new.doc_type,
            filename=new.filename,
            content_type=new.content_type,
            encounter_id=new.encounter_id,
            correlation_id=new.correlation_id,
            credential_ref=new.credential_ref,
            state="storing",
            reason=None,
            fields_grounded=0,
            fields_unsupported=0,
            attempt_count=0,
            next_retry_at=None,
            claim_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            created_ts=now,
            updated_ts=now,
        )
        self._by_id[row.document_id] = row
        self._by_key[(new.patient_id, new.content_hash)] = row.document_id
        return row, True

    async def get(self, document_id: str) -> DocumentRecord:
        try:
            return self._by_id[document_id]
        except KeyError as exc:
            raise DocumentNotFound(document_id) from exc

    async def set_state(
        self,
        document_id: str,
        *,
        state: str,
        reason: FailureReason | None = None,
        fields_grounded: int | None = None,
        fields_unsupported: int | None = None,
    ) -> DocumentRecord:
        current = await self.get(document_id)
        updated = replace(
            current,
            state=state,
            reason=reason,
            fields_grounded=(
                current.fields_grounded if fields_grounded is None else fields_grounded
            ),
            fields_unsupported=(
                current.fields_unsupported
                if fields_unsupported is None
                else fields_unsupported
            ),
            updated_ts=self._timestamp(),
        )
        self._by_id[document_id] = updated
        return updated

    async def claim_source_storage(
        self, document_id: str, *, owner: str, lease_seconds: int
    ) -> DocumentRecord | None:
        if not owner or lease_seconds <= 0:
            raise ValueError("source-storage owner and lease must be valid")
        current = await self.get(document_id)
        if current.state not in SOURCE_STATES:
            return None
        now = self._now()
        if current.claim_owner is not None and (
            current.lease_expires_at is None
            or datetime.fromisoformat(current.lease_expires_at) > now
        ):
            return None
        updated = replace(
            current,
            claim_owner=owner,
            heartbeat_at=_iso_at(now),
            lease_expires_at=_iso_at(now + timedelta(seconds=lease_seconds)),
            updated_ts=_iso_at(now),
        )
        self._by_id[document_id] = updated
        return updated

    async def finish_source_storage(
        self,
        document_id: str,
        *,
        owner: str,
        state: str,
        reason: FailureReason | None = None,
    ) -> DocumentRecord:
        if state not in {"queued", "reconciling", "failed"}:
            raise ValueError("invalid source-storage terminal state")
        current = await self.get(document_id)
        if current.state not in SOURCE_STATES or current.claim_owner != owner:
            raise DocumentLeaseLost(document_id)
        updated = replace(
            current,
            state=state,
            reason=reason,
            claim_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            updated_ts=self._timestamp(),
        )
        self._by_id[document_id] = updated
        return updated

    async def requeue_failed(
        self, document_id: str, *, patient_id: str
    ) -> DocumentRecord:
        current = await self.get(document_id)
        if current.patient_id != patient_id:
            raise DocumentPatientMismatch(document_id)
        if current.state != "failed" or document_id in self._unknown_documents:
            raise DocumentNotRetryable(document_id)
        updated = replace(
            current,
            state="queued",
            reason=None,
            attempt_count=current.attempt_count + 1,
            next_retry_at=None,
            updated_ts=self._timestamp(),
        )
        self._by_id[document_id] = updated
        return updated

    async def list_for_patient(
        self, patient_id: str, *, state: str | None = None
    ) -> list[DocumentRecord]:
        return sorted(
            (
                row
                for row in self._by_id.values()
                if row.patient_id == patient_id
                and (state is None or row.state == state)
            ),
            key=lambda row: (row.created_ts, row.document_id),
        )

    async def claim_next(
        self, worker_id: str, *, lease_seconds: int
    ) -> DocumentRecord | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self._now()
        eligible = [
            row
            for row in self._by_id.values()
            if row.state == "queued"
            and (
                row.next_retry_at is None
                or datetime.fromisoformat(row.next_retry_at) <= now
            )
        ]
        if not eligible:
            return None
        current = min(eligible, key=lambda row: (row.created_ts, row.document_id))
        updated = replace(
            current,
            state="extracting",
            reason=None,
            claim_owner=worker_id,
            heartbeat_at=_iso_at(now),
            lease_expires_at=_iso_at(now + timedelta(seconds=lease_seconds)),
            attempt_count=current.attempt_count + 1,
            next_retry_at=None,
            updated_ts=_iso_at(now),
        )
        self._by_id[current.document_id] = updated
        return updated

    def _claimed(self, document_id: str, worker_id: str) -> DocumentRecord:
        try:
            current = self._by_id[document_id]
        except KeyError as exc:
            raise DocumentNotFound(document_id) from exc
        if current.claim_owner != worker_id or current.state not in ACTIVE_STATES:
            raise DocumentLeaseLost(document_id)
        return current

    async def heartbeat(
        self, document_id: str, *, worker_id: str, lease_seconds: int
    ) -> DocumentRecord:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        current = self._claimed(document_id, worker_id)
        now = self._now()
        updated = replace(
            current,
            heartbeat_at=_iso_at(now),
            lease_expires_at=_iso_at(now + timedelta(seconds=lease_seconds)),
            updated_ts=_iso_at(now),
        )
        self._by_id[document_id] = updated
        return updated

    async def transition_claimed(
        self, document_id: str, *, worker_id: str, state: str
    ) -> DocumentRecord:
        if state not in ACTIVE_STATES:
            raise ValueError(f"invalid claimed state: {state}")
        current = self._claimed(document_id, worker_id)
        updated = replace(current, state=state, updated_ts=self._timestamp())
        self._by_id[document_id] = updated
        return updated

    async def reschedule_claimed(
        self,
        document_id: str,
        *,
        worker_id: str,
        reason: FailureReason | str,
        next_retry_at: datetime,
    ) -> DocumentRecord:
        current = self._claimed(document_id, worker_id)
        updated = replace(
            current,
            state="queued",
            reason=_reason(reason),
            claim_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            next_retry_at=_iso_at(next_retry_at),
            updated_ts=self._timestamp(),
        )
        self._by_id[document_id] = updated
        return updated

    async def complete_claimed(
        self,
        document_id: str,
        *,
        worker_id: str,
        fields_grounded: int,
        fields_unsupported: int,
    ) -> DocumentRecord:
        current = self._claimed(document_id, worker_id)
        updated = replace(
            current,
            state="complete",
            reason=None,
            fields_grounded=fields_grounded,
            fields_unsupported=fields_unsupported,
            claim_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            next_retry_at=None,
            updated_ts=self._timestamp(),
        )
        self._by_id[document_id] = updated
        return updated

    async def fail_claimed(
        self, document_id: str, *, worker_id: str, reason: FailureReason | str
    ) -> DocumentRecord:
        current = self._claimed(document_id, worker_id)
        updated = replace(
            current,
            state="failed",
            reason=_reason(reason),
            claim_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            next_retry_at=None,
            updated_ts=self._timestamp(),
        )
        self._by_id[document_id] = updated
        return updated

    async def recover_stale(self) -> int:
        now = self._now()
        recovered = 0
        for document_id, current in tuple(self._by_id.items()):
            if (
                current.state not in ACTIVE_STATES
                or current.lease_expires_at is None
                or datetime.fromisoformat(current.lease_expires_at) > now
            ):
                continue
            self._by_id[document_id] = replace(
                current,
                state="queued",
                reason=FailureReason.WORKER_RESTART,
                claim_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                next_retry_at=_iso_at(now),
                updated_ts=_iso_at(now),
            )
            recovered += 1
        return recovered

    def mark_unknown(self, document_id: str) -> None:
        self._unknown_documents.add(document_id)


class PostgresDocumentRepository:
    """Multi-replica durable repository using migration 003."""

    def __init__(self, connect: Callable[[], Awaitable[object]]) -> None:
        self._connect = connect

    async def get_or_create(self, new: NewDocument) -> tuple[DocumentRecord, bool]:
        conn = await self._connect()
        try:
            async with conn.transaction():  # type: ignore[attr-defined]
                document_id = str(uuid.uuid4())
                job_id = str(uuid.uuid4())
                now = _utcnow()
                inserted = await conn.fetchrow(  # type: ignore[attr-defined]
                    """
                    INSERT INTO agent_document_dedup
                        (document_id, patient_id, content_hash, doc_type, filename,
                         content_type, encounter_id, job_id, correlation_id,
                         credential_ref, created_ts, updated_ts)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11)
                    ON CONFLICT (patient_id, content_hash) DO NOTHING
                    RETURNING document_id
                    """,
                    document_id,
                    new.patient_id,
                    new.content_hash,
                    new.doc_type,
                    new.filename,
                    new.content_type,
                    new.encounter_id,
                    job_id,
                    new.correlation_id,
                    new.credential_ref,
                    now,
                )
                created = inserted is not None
                if created:
                    await conn.execute(  # type: ignore[attr-defined]
                        """
                        INSERT INTO agent_document_jobs
                            (job_id, document_id, state, reason, claim_owner,
                             lease_expires_at, heartbeat_at, attempt_count,
                             next_retry_at, fields_grounded, fields_unsupported,
                             created_ts, updated_ts)
                        VALUES ($1,$2,'storing',NULL,NULL,NULL,NULL,0,NULL,0,0,$3,$3)
                        """,
                        job_id,
                        document_id,
                        now,
                    )
                row = await self._fetch_by_key(conn, new.patient_id, new.content_hash)
                return _record(row), created
        finally:
            await _close(conn)

    async def get(self, document_id: str) -> DocumentRecord:
        conn = await self._connect()
        try:
            row = await self._fetch_by_id(conn, document_id)
            if row is None:
                raise DocumentNotFound(document_id)
            return _record(row)
        finally:
            await _close(conn)

    async def set_state(
        self,
        document_id: str,
        *,
        state: str,
        reason: FailureReason | None = None,
        fields_grounded: int | None = None,
        fields_unsupported: int | None = None,
    ) -> DocumentRecord:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                UPDATE agent_document_jobs
                   SET state=$2, reason=$3,
                       fields_grounded=COALESCE($4, fields_grounded),
                       fields_unsupported=COALESCE($5, fields_unsupported),
                       updated_ts=$6
                 WHERE document_id=$1
                RETURNING document_id
                """,
                document_id,
                state,
                reason.value if reason else None,
                fields_grounded,
                fields_unsupported,
                _utcnow(),
            )
            if row is None:
                raise DocumentNotFound(document_id)
            loaded = await self._fetch_by_id(conn, document_id)
            return _record(loaded)
        finally:
            await _close(conn)

    async def claim_source_storage(
        self, document_id: str, *, owner: str, lease_seconds: int
    ) -> DocumentRecord | None:
        if not owner or lease_seconds <= 0:
            raise ValueError("source-storage owner and lease must be valid")
        conn = await self._connect()
        try:
            now = _utcnow()
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                UPDATE agent_document_jobs
                   SET claim_owner=$2, heartbeat_at=$3,
                       lease_expires_at=$3 + ($4 * interval '1 second'),
                       updated_ts=$3
                 WHERE document_id=$1
                   AND state IN ('storing','reconciling')
                   AND (claim_owner IS NULL OR lease_expires_at <= $3)
                RETURNING document_id
                """,
                document_id,
                owner,
                now,
                lease_seconds,
            )
            if row is None:
                return None
            loaded = await self._fetch_by_id(conn, document_id)
            return _record(loaded)
        finally:
            await _close(conn)

    async def finish_source_storage(
        self,
        document_id: str,
        *,
        owner: str,
        state: str,
        reason: FailureReason | None = None,
    ) -> DocumentRecord:
        if state not in {"queued", "reconciling", "failed"}:
            raise ValueError("invalid source-storage terminal state")
        conn = await self._connect()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                UPDATE agent_document_jobs
                   SET state=$3, reason=$4, claim_owner=NULL,
                       lease_expires_at=NULL, heartbeat_at=NULL, updated_ts=$5
                 WHERE document_id=$1 AND claim_owner=$2
                   AND state IN ('storing','reconciling')
                RETURNING document_id
                """,
                document_id,
                owner,
                state,
                reason.value if reason else None,
                _utcnow(),
            )
            if row is None:
                raise DocumentLeaseLost(document_id)
            loaded = await self._fetch_by_id(conn, document_id)
            return _record(loaded)
        finally:
            await _close(conn)

    async def requeue_failed(
        self, document_id: str, *, patient_id: str
    ) -> DocumentRecord:
        conn = await self._connect()
        try:
            async with conn.transaction():  # type: ignore[attr-defined]
                current = await self._fetch_by_id(conn, document_id, for_update=True)
                if current is None:
                    raise DocumentNotFound(document_id)
                record = _record(current)
                if record.patient_id != patient_id:
                    raise DocumentPatientMismatch(document_id)
                unknown = await conn.fetchval(  # type: ignore[attr-defined]
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM agent_write_intents
                         WHERE patient_id=$1
                           AND document_id_or_content_hash IN ($2,$3)
                           AND state='unknown'
                    )
                    """,
                    patient_id,
                    record.document_id,
                    record.content_hash,
                )
                if record.state != "failed" or unknown:
                    raise DocumentNotRetryable(document_id)
                await conn.execute(  # type: ignore[attr-defined]
                    """
                    UPDATE agent_document_jobs
                       SET state='queued', reason=NULL,
                           attempt_count=attempt_count+1, next_retry_at=NULL,
                           updated_ts=$2
                     WHERE document_id=$1 AND state='failed'
                    """,
                    document_id,
                    _utcnow(),
                )
                loaded = await self._fetch_by_id(conn, document_id)
                return _record(loaded)
        finally:
            await _close(conn)

    async def list_for_patient(
        self, patient_id: str, *, state: str | None = None
    ) -> list[DocumentRecord]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(  # type: ignore[attr-defined]
                _SELECT
                + " WHERE d.patient_id=$1 AND ($2::text IS NULL OR j.state=$2)"
                + " ORDER BY d.created_ts, d.document_id",
                patient_id,
                state,
            )
            return [_record(row) for row in rows]
        finally:
            await _close(conn)

    async def claim_next(
        self, worker_id: str, *, lease_seconds: int
    ) -> DocumentRecord | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        conn = await self._connect()
        try:
            now = _utcnow()
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                WITH candidate AS (
                    SELECT j.job_id
                      FROM agent_document_jobs j
                     WHERE j.state='queued'
                       AND (j.next_retry_at IS NULL OR j.next_retry_at <= $1)
                     ORDER BY j.created_ts, j.job_id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                ), claimed AS (
                    UPDATE agent_document_jobs j
                       SET state='extracting', reason=NULL, claim_owner=$2,
                           heartbeat_at=$1,
                           lease_expires_at=$1 + ($3 * interval '1 second'),
                           attempt_count=j.attempt_count+1,
                           next_retry_at=NULL, updated_ts=$1
                      FROM candidate c
                     WHERE j.job_id=c.job_id
                    RETURNING j.document_id
                )
                SELECT d.document_id, d.job_id, d.patient_id, d.content_hash,
                       d.doc_type, d.filename, d.content_type, d.encounter_id,
                       d.correlation_id, d.credential_ref, j.state, j.reason,
                       j.fields_grounded, j.fields_unsupported, j.attempt_count,
                       j.next_retry_at, j.claim_owner, j.lease_expires_at,
                       j.heartbeat_at, d.created_ts, j.updated_ts
                  FROM claimed c
                  JOIN agent_document_dedup d ON d.document_id=c.document_id
                  JOIN agent_document_jobs j ON j.job_id=d.job_id
                """,
                now,
                worker_id,
                lease_seconds,
            )
            return _record(row) if row is not None else None
        finally:
            await _close(conn)

    async def heartbeat(
        self, document_id: str, *, worker_id: str, lease_seconds: int
    ) -> DocumentRecord:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        conn = await self._connect()
        try:
            now = _utcnow()
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                UPDATE agent_document_jobs
                   SET heartbeat_at=$3,
                       lease_expires_at=$3 + ($4 * interval '1 second'),
                       updated_ts=$3
                 WHERE document_id=$1 AND claim_owner=$2
                   AND state IN ('extracting','grounding','writing')
                RETURNING document_id
                """,
                document_id,
                worker_id,
                now,
                lease_seconds,
            )
            if row is None:
                raise DocumentLeaseLost(document_id)
            return _record(await self._fetch_by_id(conn, document_id))
        finally:
            await _close(conn)

    async def transition_claimed(
        self, document_id: str, *, worker_id: str, state: str
    ) -> DocumentRecord:
        if state not in ACTIVE_STATES:
            raise ValueError(f"invalid claimed state: {state}")
        return await self._finish_claimed_update(
            document_id,
            worker_id=worker_id,
            assignments="state=$3, updated_ts=$4",
            arguments=(state, _utcnow()),
        )

    async def reschedule_claimed(
        self,
        document_id: str,
        *,
        worker_id: str,
        reason: FailureReason | str,
        next_retry_at: datetime,
    ) -> DocumentRecord:
        return await self._finish_claimed_update(
            document_id,
            worker_id=worker_id,
            assignments=(
                "state='queued', reason=$3, next_retry_at=$4, "
                "claim_owner=NULL, lease_expires_at=NULL, heartbeat_at=NULL, "
                "updated_ts=$5"
            ),
            arguments=(_reason(reason).value, next_retry_at, _utcnow()),
        )

    async def complete_claimed(
        self,
        document_id: str,
        *,
        worker_id: str,
        fields_grounded: int,
        fields_unsupported: int,
    ) -> DocumentRecord:
        return await self._finish_claimed_update(
            document_id,
            worker_id=worker_id,
            assignments=(
                "state='complete', reason=NULL, fields_grounded=$3, "
                "fields_unsupported=$4, next_retry_at=NULL, claim_owner=NULL, "
                "lease_expires_at=NULL, heartbeat_at=NULL, updated_ts=$5"
            ),
            arguments=(fields_grounded, fields_unsupported, _utcnow()),
        )

    async def fail_claimed(
        self, document_id: str, *, worker_id: str, reason: FailureReason | str
    ) -> DocumentRecord:
        return await self._finish_claimed_update(
            document_id,
            worker_id=worker_id,
            assignments=(
                "state='failed', reason=$3, next_retry_at=NULL, claim_owner=NULL, "
                "lease_expires_at=NULL, heartbeat_at=NULL, updated_ts=$4"
            ),
            arguments=(_reason(reason).value, _utcnow()),
        )

    async def recover_stale(self) -> int:
        conn = await self._connect()
        try:
            rows = await conn.fetch(  # type: ignore[attr-defined]
                """
                UPDATE agent_document_jobs
                   SET state='queued', reason=$1, next_retry_at=$2,
                       claim_owner=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                       updated_ts=$2
                 WHERE state IN ('extracting','grounding','writing')
                   AND lease_expires_at <= $2
                RETURNING job_id
                """,
                FailureReason.WORKER_RESTART.value,
                _utcnow(),
            )
            return len(rows)
        finally:
            await _close(conn)

    async def _finish_claimed_update(
        self,
        document_id: str,
        *,
        worker_id: str,
        assignments: str,
        arguments: tuple[object, ...],
    ) -> DocumentRecord:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                f"""
                UPDATE agent_document_jobs
                   SET {assignments}
                 WHERE document_id=$1 AND claim_owner=$2
                   AND state IN ('extracting','grounding','writing')
                RETURNING document_id
                """,
                document_id,
                worker_id,
                *arguments,
            )
            if row is None:
                raise DocumentLeaseLost(document_id)
            return _record(await self._fetch_by_id(conn, document_id))
        finally:
            await _close(conn)

    @staticmethod
    async def _fetch_by_key(conn: object, patient_id: str, content_hash: str):
        return await conn.fetchrow(  # type: ignore[attr-defined]
            _SELECT + " WHERE d.patient_id=$1 AND d.content_hash=$2",
            patient_id,
            content_hash,
        )

    @staticmethod
    async def _fetch_by_id(conn: object, document_id: str, *, for_update: bool = False):
        suffix = " FOR UPDATE" if for_update else ""
        return await conn.fetchrow(  # type: ignore[attr-defined]
            _SELECT + " WHERE d.document_id=$1" + suffix,
            document_id,
        )


_SELECT = """
SELECT d.document_id, d.job_id, d.patient_id, d.content_hash, d.doc_type,
       d.filename, d.content_type, d.encounter_id, d.correlation_id,
       d.credential_ref, j.state, j.reason, j.fields_grounded,
       j.fields_unsupported, j.attempt_count, j.next_retry_at,
       j.claim_owner, j.lease_expires_at, j.heartbeat_at,
       d.created_ts, j.updated_ts
  FROM agent_document_dedup d
  JOIN agent_document_jobs j ON j.job_id=d.job_id
"""


def _iso(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _record(row: object) -> DocumentRecord:
    values = dict(cast(Mapping[str, object], row))
    reason = values.get("reason")
    doc_type_value = str(values["doc_type"])
    if doc_type_value not in {"lab_pdf", "intake_form"}:
        raise ValueError(f"invalid persisted doc_type: {doc_type_value!r}")
    return DocumentRecord(
        document_id=str(values["document_id"]),
        job_id=str(values["job_id"]),
        patient_id=str(values["patient_id"]),
        content_hash=str(values["content_hash"]),
        doc_type=cast(DocumentType, doc_type_value),
        filename=str(values["filename"]),
        content_type=str(values["content_type"]),
        encounter_id=(
            str(values["encounter_id"]) if values.get("encounter_id") else None
        ),
        correlation_id=str(values["correlation_id"]),
        credential_ref=str(values["credential_ref"]),
        state=str(values["state"]),
        reason=FailureReason(reason) if reason else None,
        fields_grounded=int(str(values["fields_grounded"])),
        fields_unsupported=int(str(values["fields_unsupported"])),
        attempt_count=int(str(values["attempt_count"])),
        next_retry_at=_iso(values.get("next_retry_at")),
        claim_owner=(str(values["claim_owner"]) if values.get("claim_owner") else None),
        lease_expires_at=_iso(values.get("lease_expires_at")),
        heartbeat_at=_iso(values.get("heartbeat_at")),
        created_ts=_iso(values["created_ts"]) or "",
        updated_ts=_iso(values["updated_ts"]) or "",
    )


def _reason(value: FailureReason | str) -> FailureReason:
    return value if isinstance(value, FailureReason) else FailureReason(value)


async def _close(conn: object) -> None:
    close = getattr(conn, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result
