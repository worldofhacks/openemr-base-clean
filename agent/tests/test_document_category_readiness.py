"""Bounded, content-free delegated document-category readiness checks."""

from __future__ import annotations

import asyncio

import pytest

from app.health import DependencyResult
from app.ingestion.repository import (
    DocumentReadinessBinding,
    InMemoryDocumentRepository,
    NewDocument,
    PostgresDocumentRepository,
)
from app.ingestion.runtime import _AuthorizedDocumentCategoryProbe
from app.service import AgentServices


@pytest.mark.asyncio
async def test_in_memory_readiness_binding_is_empty_then_minimal() -> None:
    repository = InMemoryDocumentRepository()
    assert await repository.readiness_binding() is None

    await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash="a" * 64,
            doc_type="lab_pdf",
            filename="must-not-cross-readiness.pdf",
            content_type="application/pdf",
            encounter_id="encounter-synthetic",
            correlation_id="corr-synthetic",
            credential_ref="credential:synthetic",
        )
    )

    assert await repository.readiness_binding() == DocumentReadinessBinding(
        patient_id="patient-synthetic", credential_ref="credential:synthetic"
    )


@pytest.mark.asyncio
async def test_postgres_readiness_binding_projects_no_document_metadata() -> None:
    class Connection:
        def __init__(self) -> None:
            self.sql = ""
            self.closed = False

        async def fetchrow(self, sql):
            self.sql = sql
            return {
                "patient_id": "patient-synthetic",
                "credential_ref": "credential:synthetic",
            }

        async def close(self):
            self.closed = True

    connection = Connection()
    repository = PostgresDocumentRepository(lambda: _return(connection))

    binding = await repository.readiness_binding()

    assert binding == DocumentReadinessBinding(
        patient_id="patient-synthetic", credential_ref="credential:synthetic"
    )
    projection = connection.sql.split("FROM", 1)[0]
    assert "d.patient_id, d.credential_ref" in projection
    assert all(
        forbidden not in projection
        for forbidden in ("filename", "content_hash", "correlation_id", "document_id")
    )
    assert "JOIN agent_job_credentials" in connection.sql
    assert connection.closed is True


@pytest.mark.asyncio
async def test_authorized_probe_uses_pinned_binding_and_both_fixed_paths() -> None:
    binding = DocumentReadinessBinding(
        patient_id="patient-synthetic", credential_ref="credential:synthetic"
    )

    class Repository:
        async def readiness_binding(self):
            return binding

    class Gateway:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def list_documents(self, *, patient_id: str, category_path: str):
            self.calls.append((patient_id, category_path))
            return [object()]  # response records are deliberately discarded

    gateway = Gateway()

    class Gateways:
        async def for_readiness_binding(self, selected):
            assert selected is binding
            return gateway

    probe = _AuthorizedDocumentCategoryProbe(
        repository=Repository(),  # type: ignore[arg-type]
        gateways=Gateways(),  # type: ignore[arg-type]
        category_paths=("/AI-Source-Documents", "/AI-Extractions"),
    )

    assert await probe.probe() == "authorized_read_ok"
    assert gateway.calls == [
        ("patient-synthetic", "/AI-Source-Documents"),
        ("patient-synthetic", "/AI-Extractions"),
    ]


@pytest.mark.asyncio
async def test_authorized_probe_preserves_fresh_deploy_without_ambient_patient() -> None:
    class Repository:
        async def readiness_binding(self):
            return None

    class Gateways:
        async def for_readiness_binding(self, _selected):
            raise AssertionError("no principal may be manufactured before a pinned job")

    probe = _AuthorizedDocumentCategoryProbe(
        repository=Repository(),  # type: ignore[arg-type]
        gateways=Gateways(),  # type: ignore[arg-type]
        category_paths=("/AI-Source-Documents", "/AI-Extractions"),
    )

    assert await probe.probe() == "pending_first_pinned_job"


@pytest.mark.asyncio
async def test_service_treats_absent_first_pinned_job_as_fresh_deploy_ready() -> None:
    class CategoryProbe:
        async def probe(self):
            return "pending_first_pinned_job"

    services = object.__new__(AgentServices)
    services.settings = type("Settings", (), {"w2_document_runtime_enabled": True})()
    services.document_runtime = type(
        "Runtime", (), {"category_read_probe": CategoryProbe()}
    )()

    result = await services.probe_document_category_read(services.settings)

    assert result == DependencyResult(
        "document_category_read", "hard", True, "pending_first_pinned_job"
    )


@pytest.mark.asyncio
async def test_service_probe_has_closed_content_free_failure_details() -> None:
    class CategoryProbe:
        async def probe(self):
            raise RuntimeError("patient identifier and remote response must not escape")

    services = object.__new__(AgentServices)
    services.settings = type("Settings", (), {"w2_document_runtime_enabled": True})()
    services.document_runtime = type(
        "Runtime", (), {"category_read_probe": CategoryProbe()}
    )()

    result = await services.probe_document_category_read(services.settings)

    assert result == DependencyResult(
        "document_category_read", "hard", False, "authorized_read_failed"
    )
    assert "identifier" not in repr(result)


@pytest.mark.asyncio
async def test_service_category_probe_is_locally_timeout_bounded(monkeypatch) -> None:
    import app.service as service_module

    class CategoryProbe:
        async def probe(self):
            await asyncio.sleep(1)
            return "authorized_read_ok"

    services = object.__new__(AgentServices)
    services.settings = type("Settings", (), {"w2_document_runtime_enabled": True})()
    services.document_runtime = type(
        "Runtime", (), {"category_read_probe": CategoryProbe()}
    )()
    real_timeout = asyncio.timeout
    monkeypatch.setattr(service_module.asyncio, "timeout", lambda _seconds: real_timeout(0.001))

    result = await services.probe_document_category_read(services.settings)

    assert result == DependencyResult(
        "document_category_read", "hard", False, "timeout"
    )


async def _return(value):
    return value
