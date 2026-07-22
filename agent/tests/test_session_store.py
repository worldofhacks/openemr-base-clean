"""E2.2 — session store pinned to (clinician, patient) (§3a, §4, §6a, D12, F-S.2, §6).

The session pin is the REAL clinician↔patient enforcer, because OpenEMR's own
checkUserHasAccessToPatient() is a stub returning true (F-S.2). These tests pin:
a session refuses any patient other than the one it was launched with; its lifetime
is MIN(token exp, idle timeout, turn cap) (§3a); and if the store's backend is
unreachable the store FAILS CLOSED — it refuses to serve, never returns an unpinned
session (§6).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.session.store import (
    CrossPatientError,
    InMemorySessionStore,
    PostgresSessionStore,
    Session,
    SessionExpiredError,
    SessionStoreUnavailable,
)

T0 = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)


def _session(**kw) -> Session:
    base = dict(
        session_id="s1",
        clinician_sub="clinician-A",
        patient_id="patient-A",
        created_at=T0,
        last_activity_at=T0,
        token_expires_at=T0 + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
        turns_used=0,
        encounter_id=None,
    )
    base.update(kw)
    return Session(**base)


# --- the pin: cross-patient refusal (invariant) ---------------------------


def test_session_authorizes_its_own_patient_and_refuses_any_other():
    s = _session(patient_id="patient-A")
    s.authorize_patient("patient-A")  # no raise
    with pytest.raises(CrossPatientError):
        s.authorize_patient("patient-B")  # a different patient requires a fresh launch


# --- lifetime = MIN(token exp, idle timeout, turn cap) --------------------


def test_expiry_is_bounded_by_token_expiry():
    s = _session(token_expires_at=T0 + timedelta(minutes=10), idle_timeout_s=3600)
    assert s.expires_at() == T0 + timedelta(minutes=10)
    assert not s.is_expired(T0 + timedelta(minutes=9))
    assert s.is_expired(T0 + timedelta(minutes=11))


def test_expiry_is_bounded_by_idle_timeout():
    s = _session(
        token_expires_at=T0 + timedelta(hours=2),
        idle_timeout_s=300,
        last_activity_at=T0,
    )
    assert s.expires_at() == T0 + timedelta(seconds=300)
    assert s.is_expired(T0 + timedelta(seconds=301))


def test_expiry_is_bounded_by_turn_cap():
    s = _session(turn_cap=3, turns_used=3, token_expires_at=T0 + timedelta(hours=1))
    # Turn cap reached ⇒ expired regardless of wall-clock time.
    assert s.is_expired(T0)


# --- store roundtrip pins to (clinician, patient) -------------------------


@pytest.mark.asyncio
async def test_create_pins_session_to_clinician_and_patient():
    store = InMemorySessionStore(now=lambda: T0)
    s = await store.create(
        clinician_sub="clinician-A",
        patient_id="patient-A",
        token_expires_at=T0 + timedelta(hours=1),
    )
    assert s.clinician_sub == "clinician-A" and s.patient_id == "patient-A"
    got = await store.get(s.session_id)
    assert got.patient_id == "patient-A"


@pytest.mark.asyncio
async def test_create_persists_optional_smart_encounter_context():
    store = InMemorySessionStore(now=lambda: T0)
    session = await store.create(
        clinician_sub="clinician-A",
        patient_id="patient-A",
        encounter_id="encounter-A",
        token_expires_at=T0 + timedelta(hours=1),
    )

    assert session.encounter_id == "encounter-A"
    assert (await store.get(session.session_id)).encounter_id == "encounter-A"


@pytest.mark.asyncio
async def test_get_expired_session_is_refused():
    store = InMemorySessionStore(now=lambda: T0)
    s = await store.create(
        clinician_sub="c", patient_id="p", token_expires_at=T0 + timedelta(minutes=1)
    )
    store._now = lambda: T0 + timedelta(minutes=2)  # advance past token expiry
    with pytest.raises(SessionExpiredError):
        await store.get(s.session_id)


@pytest.mark.asyncio
async def test_delegated_token_can_renew_without_changing_the_session_pin():
    now = T0
    store = InMemorySessionStore(now=lambda: now, idle_timeout_s=1800)
    session = await store.create(
        clinician_sub="clinician-A",
        patient_id="patient-A",
        token_expires_at=T0 + timedelta(minutes=1),
    )
    now = T0 + timedelta(minutes=2)

    refreshable = await store.get_for_delegated_renewal(session.session_id)
    renewed = await store.renew_delegated_token(
        session.session_id,
        token_expires_at=now + timedelta(hours=1),
    )

    assert refreshable.session_id == session.session_id == renewed.session_id
    assert renewed.clinician_sub == "clinician-A"
    assert renewed.patient_id == "patient-A"
    assert await store.get(session.session_id) is renewed


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_bound", ("idle", "turn_cap"))
async def test_delegated_token_renewal_never_bypasses_terminal_session_bounds(
    terminal_bound: str,
):
    now = T0
    store = InMemorySessionStore(now=lambda: now, idle_timeout_s=60, turn_cap=1)
    session = await store.create(
        clinician_sub="clinician-A",
        patient_id="patient-A",
        token_expires_at=T0 + timedelta(seconds=1),
    )
    if terminal_bound == "idle":
        now = T0 + timedelta(seconds=61)
    else:
        session.turns_used = 1

    with pytest.raises(SessionExpiredError):
        await store.get_for_delegated_renewal(session.session_id)


# --- fail-closed when the backend is down (§6) ----------------------------


@pytest.mark.asyncio
async def test_store_fails_closed_when_backend_unreachable():
    async def broken_connect(_dsn):
        raise ConnectionError("could not connect to session store")

    store = PostgresSessionStore(
        dsn="postgresql://u:p@127.0.0.1:5999/agent",
        connect=broken_connect,
        now=lambda: T0,
    )
    # Creating a session against a down store must RAISE (refuse to serve),
    # never silently return an unpinned/None session.
    with pytest.raises(SessionStoreUnavailable):
        await store.create(
            clinician_sub="c", patient_id="p", token_expires_at=T0 + timedelta(hours=1)
        )
    with pytest.raises(SessionStoreUnavailable):
        await store.get("any-id")


# --- success path against a fake asyncpg backend (covers the SQL seams + release) ---------


class _FakeConn:
    """An asyncpg-shaped connection over a shared in-memory `agent_sessions` dict. Proves the
    store passes the right columns/args to INSERT/SELECT/UPDATE and reconstructs a Session."""

    _COLS = (
        "session_id",
        "clinician_sub",
        "patient_id",
        "encounter_id",
        "created_at",
        "last_activity_at",
        "token_expires_at",
        "idle_timeout_s",
        "turn_cap",
        "turns_used",
    )

    def __init__(self, table: dict) -> None:
        self.table = table
        self.closed = False

    async def execute(self, sql: str, *args):
        head = sql.strip().split(None, 1)[0].upper()
        if head == "INSERT":
            self.table[args[0]] = dict(zip(self._COLS, args))
        elif head == "UPDATE":
            row = self.table.get(args[0])
            if row is not None:
                if "token_expires_at" in sql:
                    row["token_expires_at"] = args[1]
                else:  # (session_id, turns_used, last_activity_at)
                    row["turns_used"], row["last_activity_at"] = args[1], args[2]
        # CREATE (schema DDL) → no-op

    async def fetchrow(self, _sql: str, *args):
        row = self.table.get(args[0])
        return (
            dict(row) if row is not None else None
        )  # dict(row) mirrors asyncpg Record

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_postgres_store_roundtrip_persists_and_enforces_lifetime():
    table: dict = {}
    conns: list[_FakeConn] = []

    async def connect(_dsn):
        c = _FakeConn(table)
        conns.append(c)
        return c

    store = PostgresSessionStore(
        dsn="postgresql://u:p@h/db",
        connect=connect,
        now=lambda: T0,
        idle_timeout_s=1800,
        turn_cap=2,
    )
    await store.ensure_schema()  # idempotent DDL flows through the seam

    s = await store.create(
        clinician_sub="clin-A",
        patient_id="pat-A",
        encounter_id="enc-A",
        token_expires_at=T0 + timedelta(hours=1),
    )
    # the pin survives the create call (durable across operations — the point of CXR-07)
    got = await store.get(s.session_id)
    assert (
        got.clinician_sub == "clin-A"
        and got.patient_id == "pat-A"
        and got.turns_used == 0
    )
    assert got.encounter_id == "enc-A"
    got.authorize_patient("pat-A")  # pin holds for the launched patient…
    with pytest.raises(CrossPatientError):
        got.authorize_patient("pat-B")  # …and refuses any other (F-S.2/D12)

    renewed = await store.renew_delegated_token(
        s.session_id,
        token_expires_at=T0 + timedelta(hours=2),
    )
    assert renewed.token_expires_at == T0 + timedelta(hours=2)
    assert (await store.get(s.session_id)).token_expires_at == renewed.token_expires_at

    await store.record_turn(s.session_id)
    r2 = await store.record_turn(s.session_id)
    assert r2.turns_used == 2  # bump persisted through the UPDATE seam
    with pytest.raises(SessionExpiredError):  # turn cap (2) now expires the session
        await store.get(s.session_id)

    # every per-operation connection was released — connect-per-request must not leak
    assert conns and all(c.closed for c in conns)
