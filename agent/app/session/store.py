"""Session store — pinned to (clinician, patient) (ARCHITECTURE.md §3a, §4, §6a, D12).

A session is created at SMART launch and pinned to the launching clinician and
patient. The pin is the *real* clinician↔patient enforcer: OpenEMR's own
`checkUserHasAccessToPatient()` is a stub returning true (F-S.2), so we do not rely
on the server to keep the agent on one patient — the session does. A patient switch
requires a fresh launch; a cross-patient request is refused (`CrossPatientError`).

Lifetime is MIN(token expiry, idle timeout, turn cap) (§3a). If the backing store is
unreachable the store FAILS CLOSED (`SessionStoreUnavailable`) — it never returns an
unpinned session or silently serves without a pin (§6).

Two implementations: `InMemorySessionStore` (dev/tests) and `PostgresSessionStore`
(D-O2; the connection is injected so the fail-closed path is testable without a live DB).
The Postgres schema lives in `migrations/001_sessions.sql`.
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable


class SessionError(Exception):
    """Base for session-layer errors."""


class CrossPatientError(SessionError):
    """A request targeted a patient other than the session's pinned patient (D12)."""


class SessionExpiredError(SessionError):
    """The session has passed its lifetime bound (§3a) — re-launch required."""


class SessionNotFound(SessionError):
    """No session for the given id."""


class SessionStoreUnavailable(SessionError):
    """The backing store is unreachable — fail closed, refuse to serve (§6)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Idempotent DDL, mirrored from migrations/001_sessions.sql. Embedded so the startup
# schema-ensure never depends on the migrations directory being packaged into the container.
SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id        TEXT        PRIMARY KEY,
    clinician_sub     TEXT        NOT NULL,
    patient_id        TEXT        NOT NULL,
    encounter_id      TEXT        NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    last_activity_at  TIMESTAMPTZ NOT NULL,
    token_expires_at  TIMESTAMPTZ NOT NULL,
    idle_timeout_s    INTEGER     NOT NULL,
    turn_cap          INTEGER     NOT NULL,
    turns_used        INTEGER     NOT NULL DEFAULT 0
);
ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS encounter_id TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_sessions_token_expires_at ON agent_sessions (token_expires_at);
"""


@dataclass
class Session:
    session_id: str
    clinician_sub: str
    patient_id: str
    created_at: datetime
    last_activity_at: datetime
    token_expires_at: datetime
    idle_timeout_s: int
    turn_cap: int
    turns_used: int = 0
    encounter_id: str | None = None

    def expires_at(self) -> datetime:
        """The time-based lifetime bound: MIN(token expiry, last activity + idle)."""
        idle_deadline = self.last_activity_at + timedelta(seconds=self.idle_timeout_s)
        return min(self.token_expires_at, idle_deadline)

    def is_expired(self, now: datetime) -> bool:
        """Expired if the turn cap is reached OR the time bound has passed (§3a)."""
        if self.turns_used >= self.turn_cap:
            return True
        return now >= self.expires_at()

    def authorize_patient(self, requested_patient_id: str) -> None:
        """Enforce the pin (D12/F-S.2): only the launched patient is allowed."""
        if requested_patient_id != self.patient_id:
            raise CrossPatientError(
                "request is scoped to a different patient than this session's launch "
                "context — a patient switch requires a fresh SMART launch (D12)"
            )


class SessionStore(ABC):
    @abstractmethod
    async def create(self, *, clinician_sub: str, patient_id: str,
                     token_expires_at: datetime,
                     encounter_id: str | None = None) -> Session: ...

    @abstractmethod
    async def get(self, session_id: str) -> Session: ...

    @abstractmethod
    async def record_turn(self, session_id: str) -> Session: ...


class InMemorySessionStore(SessionStore):
    """In-process store for local dev and tests. Not for multi-replica production
    (D-O2 uses Postgres); the pin/expiry semantics are identical."""

    def __init__(self, *, now: Callable[[], datetime] = _utcnow,
                 idle_timeout_s: int = 1800, turn_cap: int = 20) -> None:
        self._now = now
        self._idle_timeout_s = idle_timeout_s
        self._turn_cap = turn_cap
        self._rows: dict[str, Session] = {}

    async def create(self, *, clinician_sub: str, patient_id: str,
                     token_expires_at: datetime,
                     encounter_id: str | None = None) -> Session:
        now = self._now()
        s = Session(
            session_id=secrets.token_urlsafe(24),
            clinician_sub=clinician_sub,
            patient_id=patient_id,
            created_at=now,
            last_activity_at=now,
            token_expires_at=token_expires_at,
            idle_timeout_s=self._idle_timeout_s,
            turn_cap=self._turn_cap,
            encounter_id=encounter_id,
        )
        self._rows[s.session_id] = s
        return s

    async def get(self, session_id: str) -> Session:
        s = self._rows.get(session_id)
        if s is None:
            raise SessionNotFound(session_id)
        if s.is_expired(self._now()):
            raise SessionExpiredError(session_id)
        return s

    async def record_turn(self, session_id: str) -> Session:
        s = await self.get(session_id)
        s.turns_used += 1
        s.last_activity_at = self._now()
        return s


class PostgresSessionStore(SessionStore):
    """Postgres-backed store (D-O2). The async connect callable is injected so the
    fail-closed path (§6) is testable without a live database; any connection error
    surfaces as `SessionStoreUnavailable` rather than a None/unpinned session."""

    def __init__(self, *, dsn: str, connect: Callable[[str], Awaitable[object]],
                 now: Callable[[], datetime] = _utcnow,
                 idle_timeout_s: int = 1800, turn_cap: int = 20) -> None:
        self._dsn = dsn
        self._connect = connect
        self._now = now
        self._idle_timeout_s = idle_timeout_s
        self._turn_cap = turn_cap

    async def _conn(self):
        try:
            return await self._connect(self._dsn)
        except Exception as exc:  # noqa: BLE001 - any backend failure ⇒ fail closed
            raise SessionStoreUnavailable("session store unreachable — refusing to serve (§6)") from exc

    async def ensure_schema(self) -> None:
        """Create the sessions table if absent (idempotent DDL). Runs at startup; fails closed
        like any store operation, so a DB-down boot surfaces via /ready rather than crashing."""
        conn = await self._conn()
        try:
            await conn.execute(SESSIONS_SCHEMA)
        finally:
            await self._release(conn)

    async def create(self, *, clinician_sub: str, patient_id: str,
                     token_expires_at: datetime,
                     encounter_id: str | None = None) -> Session:
        now = self._now()
        s = Session(
            session_id=secrets.token_urlsafe(24),
            clinician_sub=clinician_sub, patient_id=patient_id,
            encounter_id=encounter_id,
            created_at=now, last_activity_at=now, token_expires_at=token_expires_at,
            idle_timeout_s=self._idle_timeout_s, turn_cap=self._turn_cap,
        )
        conn = await self._conn()
        try:
            await self._insert(conn, s)
        finally:
            await self._release(conn)
        return s

    async def get(self, session_id: str) -> Session:
        conn = await self._conn()
        try:
            s = await self._fetch(conn, session_id)
        finally:
            await self._release(conn)
        if s is None:
            raise SessionNotFound(session_id)
        if s.is_expired(self._now()):
            raise SessionExpiredError(session_id)
        return s

    async def record_turn(self, session_id: str) -> Session:
        s = await self.get(session_id)
        s.turns_used += 1
        s.last_activity_at = self._now()
        conn = await self._conn()
        try:
            await self._bump(conn, s)
        finally:
            await self._release(conn)
        return s

    @staticmethod
    async def _release(conn) -> None:
        """Close the per-operation connection (connect-per-request — leak-free and simple for a
        single instance; a pool is the production optimization). Defensive so a fake test conn
        without an async close never breaks the fail-closed path."""
        close = getattr(conn, "close", None)
        if close is None:
            return
        try:
            result = close()
            if hasattr(result, "__await__"):
                await result
        except Exception:  # noqa: BLE001 - releasing a connection must never surface an error
            pass

    # --- backend SQL seams (wired to a real driver at provisioning time) ---
    async def _insert(self, conn, s: Session) -> None:  # pragma: no cover - needs live DB
        await conn.execute(
            "INSERT INTO agent_sessions (session_id, clinician_sub, patient_id, encounter_id, created_at, "
            "last_activity_at, token_expires_at, idle_timeout_s, turn_cap, turns_used) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            s.session_id, s.clinician_sub, s.patient_id, s.encounter_id, s.created_at,
            s.last_activity_at, s.token_expires_at, s.idle_timeout_s, s.turn_cap,
            s.turns_used,
        )

    async def _fetch(self, conn, session_id: str):  # pragma: no cover - needs live DB
        row = await conn.fetchrow("SELECT * FROM agent_sessions WHERE session_id=$1", session_id)
        if row is None:
            return None
        return Session(**dict(row))

    async def _bump(self, conn, s: Session) -> None:  # pragma: no cover - needs live DB
        await conn.execute(
            "UPDATE agent_sessions SET turns_used=$2, last_activity_at=$3 WHERE session_id=$1",
            s.session_id, s.turns_used, s.last_activity_at,
        )
