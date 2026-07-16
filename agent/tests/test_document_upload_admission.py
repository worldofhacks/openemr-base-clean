"""Focused overload and privacy checks for document-upload admission."""

from __future__ import annotations

import pytest

from app.ingestion.admission import (
    UploadAdmissionController,
    UploadAdmissionLimits,
    UploadCapacityExceeded,
    UploadQuotaExceeded,
)


def _controller(
    *,
    session_count: int = 5,
    clinician_count: int = 5,
    session_bytes: int = 1_000,
    clinician_bytes: int = 1_000,
    per_session: int = 1,
    global_concurrent: int = 2,
    outstanding: int = 10,
) -> UploadAdmissionController:
    return UploadAdmissionController(
        limits=UploadAdmissionLimits(
            session_daily_count=session_count,
            session_daily_bytes=session_bytes,
            clinician_daily_count=clinician_count,
            clinician_daily_bytes=clinician_bytes,
            per_session_concurrent=per_session,
            global_concurrent=global_concurrent,
            global_outstanding_jobs=outstanding,
            max_daily_meter_keys=20,
        ),
        hash_key=b"synthetic-admission-key-for-tests",
    )


@pytest.mark.asyncio
async def test_per_session_and_global_concurrency_fail_closed() -> None:
    controller = _controller(global_concurrent=1)
    first = controller.admit(
        session_id="opaque-session-a",
        clinician_sub="clinician-a",
        byte_count=10,
        duplicate=False,
        outstanding_jobs=0,
    )
    await first.__aenter__()
    try:
        same_session = controller.admit(
            session_id="opaque-session-a",
            clinician_sub="clinician-a",
            byte_count=10,
            duplicate=False,
            outstanding_jobs=0,
        )
        with pytest.raises(UploadQuotaExceeded):
            await same_session.__aenter__()

        other_session = controller.admit(
            session_id="opaque-session-b",
            clinician_sub="clinician-b",
            byte_count=10,
            duplicate=False,
            outstanding_jobs=0,
        )
        with pytest.raises(UploadCapacityExceeded):
            await other_session.__aenter__()
    finally:
        await first.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_clinician_count_quota_spans_multiple_opaque_sessions() -> None:
    controller = _controller(clinician_count=1)
    async with controller.admit(
        session_id="opaque-session-a",
        clinician_sub="clinician-shared",
        byte_count=10,
        duplicate=False,
        outstanding_jobs=0,
    ):
        pass

    second = controller.admit(
        session_id="opaque-session-b",
        clinician_sub="clinician-shared",
        byte_count=10,
        duplicate=False,
        outstanding_jobs=0,
    )
    with pytest.raises(UploadQuotaExceeded):
        await second.__aenter__()


@pytest.mark.asyncio
async def test_byte_quota_and_repository_workload_cap_are_independent() -> None:
    byte_limited = _controller(session_bytes=9)
    over_bytes = byte_limited.admit(
        session_id="opaque-session-a",
        clinician_sub="clinician-a",
        byte_count=10,
        duplicate=False,
        outstanding_jobs=0,
    )
    with pytest.raises(UploadQuotaExceeded):
        await over_bytes.__aenter__()

    capacity_limited = _controller(outstanding=1)
    full = capacity_limited.admit(
        session_id="opaque-session-a",
        clinician_sub="clinician-a",
        byte_count=1,
        duplicate=False,
        outstanding_jobs=1,
    )
    with pytest.raises(UploadCapacityExceeded):
        await full.__aenter__()


@pytest.mark.asyncio
async def test_meter_retains_only_keyed_hashes_not_raw_principals() -> None:
    controller = _controller()
    session_id = "opaque-session-must-not-be-retained"
    clinician_sub = "clinician-sub-must-not-be-retained"
    async with controller.admit(
        session_id=session_id,
        clinician_sub=clinician_sub,
        byte_count=10,
        duplicate=False,
        outstanding_jobs=0,
    ):
        pass

    retained_state = repr(vars(controller))
    assert session_id not in retained_state
    assert clinician_sub not in retained_state
    assert all(
        isinstance(key, bytes)
        for key in (
            *controller._session_usage,  # noqa: SLF001 - privacy invariant
            *controller._clinician_usage,  # noqa: SLF001 - privacy invariant
        )
    )
