"""Document runtime applies its idempotent durable schemas at boot (W2-D10; §3)."""

from __future__ import annotations

import pytest

from app.ingestion.migrations import apply_document_migrations


class _Connection:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.closed = False

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_document_migrations_apply_in_order_and_close_connection() -> None:
    connection = _Connection()

    await apply_document_migrations(lambda: _return(connection))

    assert len(connection.executed) == 5
    assert "agent_document_dedup" in connection.executed[0]
    assert "agent_write_intents" in connection.executed[0]
    assert "agent_extraction_refs" in connection.executed[1]
    assert "agent_job_credentials" in connection.executed[2]
    assert "agent_document_worker_heartbeats" in connection.executed[2]
    assert "agent_patient_route_attestations" in connection.executed[3]
    assert "agent_encounter_route_attestations" in connection.executed[3]
    assert "ADD COLUMN IF NOT EXISTS encounter_id" in connection.executed[3]
    assert "medication_list" in connection.executed[4]
    assert "agent_document_dedup_doc_type_check" in connection.executed[4]
    assert connection.closed is True


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_agent_startup_applies_document_schema_when_runtime_enabled(
    monkeypatch,
) -> None:
    import app.service as service_module

    calls: list[object] = []

    class Sessions:
        async def ensure_schema(self) -> None:
            calls.append("sessions")

    async def apply(connect) -> None:
        calls.append(connect)

    services = object.__new__(service_module.AgentServices)
    services.sessions = Sessions()
    services.settings = type("Settings", (), {"w2_document_runtime_enabled": True})()
    services._document_connect = object()
    services._document_schema_ready = False
    monkeypatch.setattr(service_module, "apply_document_migrations", apply)

    await services.startup()

    assert calls == ["sessions", services._document_connect]
    assert services._document_schema_ready is True


@pytest.mark.asyncio
async def test_agent_startup_records_migration_failure_for_readiness(
    monkeypatch,
) -> None:
    import app.service as service_module

    class Sessions:
        async def ensure_schema(self) -> None:
            return None

    async def fail(_connect) -> None:
        raise OSError("synthetic database outage")

    services = object.__new__(service_module.AgentServices)
    services.sessions = Sessions()
    services.settings = type("Settings", (), {"w2_document_runtime_enabled": True})()
    services._document_connect = object()
    services._document_schema_ready = False
    monkeypatch.setattr(service_module, "apply_document_migrations", fail)

    await services.startup()

    assert services._document_schema_ready is False
