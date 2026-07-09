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
    s = _session(token_expires_at=T0 + timedelta(hours=2), idle_timeout_s=300,
                 last_activity_at=T0)
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
    s = await store.create(clinician_sub="clinician-A", patient_id="patient-A",
                           token_expires_at=T0 + timedelta(hours=1))
    assert s.clinician_sub == "clinician-A" and s.patient_id == "patient-A"
    got = await store.get(s.session_id)
    assert got.patient_id == "patient-A"


@pytest.mark.asyncio
async def test_get_expired_session_is_refused():
    store = InMemorySessionStore(now=lambda: T0)
    s = await store.create(clinician_sub="c", patient_id="p",
                           token_expires_at=T0 + timedelta(minutes=1))
    store._now = lambda: T0 + timedelta(minutes=2)  # advance past token expiry
    with pytest.raises(SessionExpiredError):
        await store.get(s.session_id)


# --- fail-closed when the backend is down (§6) ----------------------------

@pytest.mark.asyncio
async def test_store_fails_closed_when_backend_unreachable():
    async def broken_connect(_dsn):
        raise ConnectionError("could not connect to session store")

    store = PostgresSessionStore(dsn="postgresql://u:p@127.0.0.1:5999/agent",
                                 connect=broken_connect, now=lambda: T0)
    # Creating a session against a down store must RAISE (refuse to serve),
    # never silently return an unpinned/None session.
    with pytest.raises(SessionStoreUnavailable):
        await store.create(clinician_sub="c", patient_id="p",
                           token_expires_at=T0 + timedelta(hours=1))
    with pytest.raises(SessionStoreUnavailable):
        await store.get("any-id")
