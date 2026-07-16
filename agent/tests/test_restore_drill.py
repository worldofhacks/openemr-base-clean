"""Sanitized restore-drill orchestration and SLO arithmetic."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.restore_drill import RestoreDrillError, run_drill


class _Adapter:
    def __init__(self, *, isolated: bool = True, fail: str | None = None):
        self.isolated = isolated
        self.fail = fail
        self.order: list[str] = []

    async def assert_isolated_target(self):
        return self.isolated

    async def backup_created_at(self):
        return datetime(2026, 7, 15, 0, tzinfo=timezone.utc)

    async def _check(self, name):
        self.order.append(name)
        return self.fail != name

    async def restore_openemr_mysql_and_volume(self):
        return await self._check("openemr_mysql_volume")

    async def restore_agent_postgres(self):
        return await self._check("agent_postgres")

    async def verify_migrations(self):
        return await self._check("migrations")

    async def probe_credential_vault(self):
        return await self._check("credential_vault")

    async def probe_readiness(self):
        return await self._check("readiness")

    async def verify_synthetic_binary_digest(self):
        return await self._check("binary_digest")

    async def verify_duplicate_reconciliation(self):
        return await self._check("duplicate_reconciliation")


@pytest.mark.asyncio
async def test_restore_drill_runs_every_required_check_in_order_and_meets_slos():
    adapter = _Adapter()
    ticks = iter([100.0, 700.0])
    report = await run_drill(
        adapter,
        now=lambda: datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
        monotonic=lambda: next(ticks),
    )
    assert report.status == "PASS"
    assert report.rpo_hours == 12.0
    assert report.rto_minutes == 10.0
    assert adapter.order == [
        "openemr_mysql_volume",
        "agent_postgres",
        "migrations",
        "credential_vault",
        "readiness",
        "binary_digest",
        "duplicate_reconciliation",
    ]


@pytest.mark.asyncio
async def test_restore_drill_refuses_nonisolated_target_before_restore():
    adapter = _Adapter(isolated=False)
    with pytest.raises(RestoreDrillError, match="target_not_isolated"):
        await run_drill(adapter)
    assert adapter.order == []


@pytest.mark.asyncio
async def test_restore_drill_rejects_future_dated_backup_before_restore():
    adapter = _Adapter()
    with pytest.raises(RestoreDrillError, match="backup_timestamp_in_future"):
        await run_drill(
            adapter,
            now=lambda: datetime(2026, 7, 14, 23, 59, tzinfo=timezone.utc),
        )
    assert adapter.order == []


@pytest.mark.asyncio
async def test_restore_drill_fails_closed_on_first_failed_check():
    adapter = _Adapter(fail="migrations")
    with pytest.raises(RestoreDrillError, match="migrations_failed"):
        await run_drill(adapter)
    assert adapter.order == ["openemr_mysql_volume", "agent_postgres", "migrations"]


@pytest.mark.asyncio
async def test_restore_drill_reports_slo_failure_without_hiding_green_checks():
    adapter = _Adapter()
    ticks = iter([0.0, 3660.0])
    report = await run_drill(
        adapter,
        now=lambda: datetime(2026, 7, 16, 1, tzinfo=timezone.utc),
        monotonic=lambda: next(ticks),
    )
    assert report.status == "FAIL"
    assert report.rpo_hours == 25.0
    assert report.rto_minutes == 61.0
    assert all(report.checks.values())
