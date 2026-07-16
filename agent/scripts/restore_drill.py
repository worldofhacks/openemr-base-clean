#!/usr/bin/env python3
"""Run an isolated, adapter-backed W2 restore drill and emit aggregate evidence only."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
import asyncio


class RestoreDrillError(RuntimeError):
    """A required isolated restore assertion failed."""


class RestoreDrillAdapter(Protocol):
    async def assert_isolated_target(self) -> bool: ...

    async def backup_created_at(self) -> datetime: ...

    async def restore_openemr_mysql_and_volume(self) -> bool: ...

    async def restore_agent_postgres(self) -> bool: ...

    async def verify_migrations(self) -> bool: ...

    async def probe_credential_vault(self) -> bool: ...

    async def probe_readiness(self) -> bool: ...

    async def verify_synthetic_binary_digest(self) -> bool: ...

    async def verify_duplicate_reconciliation(self) -> bool: ...


@dataclass(frozen=True)
class DrillReport:
    schema_version: int
    status: str
    rpo_hours: float
    rto_minutes: float
    rpo_target_hours: float
    rto_target_minutes: float
    checks: dict[str, bool]


async def run_drill(
    adapter: RestoreDrillAdapter,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> DrillReport:
    if not await adapter.assert_isolated_target():
        raise RestoreDrillError("target_not_isolated")
    backup_at = await adapter.backup_created_at()
    if backup_at.tzinfo is None:
        raise RestoreDrillError("backup_timestamp_not_aware")
    observed_now = now()
    if observed_now.tzinfo is None:
        raise RestoreDrillError("clock_timestamp_not_aware")
    backup_age_seconds = (observed_now - backup_at).total_seconds()
    if backup_age_seconds < 0:
        raise RestoreDrillError("backup_timestamp_in_future")
    rpo_hours = backup_age_seconds / 3600.0
    started = monotonic()
    checks: dict[str, bool] = {}
    operations = (
        ("openemr_mysql_volume", adapter.restore_openemr_mysql_and_volume),
        ("agent_postgres", adapter.restore_agent_postgres),
        ("migrations", adapter.verify_migrations),
        ("credential_vault", adapter.probe_credential_vault),
        ("readiness", adapter.probe_readiness),
        ("binary_digest", adapter.verify_synthetic_binary_digest),
        ("duplicate_reconciliation", adapter.verify_duplicate_reconciliation),
    )
    for name, operation in operations:
        checks[name] = bool(await operation())
        if not checks[name]:
            raise RestoreDrillError(name + "_failed")
    rto_minutes = max(0.0, (monotonic() - started) / 60.0)
    status = "PASS" if rpo_hours <= 24.0 and rto_minutes <= 60.0 else "FAIL"
    return DrillReport(
        schema_version=1,
        status=status,
        rpo_hours=round(rpo_hours, 3),
        rto_minutes=round(rto_minutes, 3),
        rpo_target_hours=24.0,
        rto_target_minutes=60.0,
        checks=checks,
    )


async def _load_adapter(path: str) -> RestoreDrillAdapter:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("adapter must use module:function syntax")
    factory = getattr(importlib.import_module(module_name), attribute)
    adapter = factory()
    if inspect.isawaitable(adapter):
        adapter = await adapter
    return adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--output", default="restore-drill-results.json")
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.name != output.as_posix() or output.suffix != ".json":
        raise ValueError("output must be a local JSON filename")

    async def execute() -> DrillReport:
        return await run_drill(await _load_adapter(args.adapter))

    report = asyncio.run(execute())
    output.write_text(
        json.dumps(asdict(report), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print("PASS:isolated_restore_drill" if report.status == "PASS" else "FAIL:slo")
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
