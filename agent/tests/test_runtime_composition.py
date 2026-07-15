"""Executable document-runtime composition (W2-D1/D3/D6/D9/D10; §2/§3/§5)."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


_KEY = base64.urlsafe_b64encode(b"r" * 32).decode("ascii")


def _settings_values() -> dict[str, object]:
    return {
        "openemr_fhir_base_url": "https://openemr.test/apis/default/fhir",
        "openemr_oauth_base_url": "https://openemr.test/oauth2/default",
        "smart_client_id": "synthetic-client",
        "smart_client_secret": "synthetic-secret",
        "anthropic_api_key": "synthetic-provider-key",
        "session_store_dsn": "postgresql://u:p@localhost:5432/agent",
        "agent_callback_url": "https://agent.test/callback",
        "w2_document_runtime_enabled": True,
        "openemr_rest_base_url": "https://openemr.test/apis/default",
        "source_document_category_id": "source-category-synthetic",
        "source_document_category_acl": "patients|docs",
        "artifact_document_category_id": "artifact-category-synthetic",
        "artifact_document_category_acl": "patients|docs",
        "openemr_legacy_patient_uuid": "11111111-1111-4111-8111-111111111111",
        "openemr_legacy_patient_id": "731",
        "openemr_legacy_encounter_uuid": "22222222-2222-4222-8222-222222222222",
        "openemr_legacy_encounter_id": "912",
        "openemr_binary_readback_safe": True,
        "document_credential_key": _KEY,
    }


def test_enabled_agent_services_wires_durable_runtime_without_starting_web_worker(
    monkeypatch,
) -> None:
    import app.service as service_module
    from app.config import Settings

    sentinel = object()

    @dataclass
    class Runtime:
        repository: object = sentinel
        artifact_store: object = sentinel
        pipeline: object = sentinel
        processor: object = sentinel
        documents: object = sentinel
        credential_vault: object = sentinel
        heartbeat_store: object = sentinel

    monkeypatch.setattr(
        service_module, "build_document_runtime", lambda **_kw: Runtime()
    )
    services = service_module.AgentServices(Settings(**_settings_values()))

    assert services.document_repository is sentinel
    assert services.artifact_store is sentinel
    assert services.extraction_pipeline is sentinel
    assert services.document_processor is sentinel
    assert services.documents is sentinel
    # §3: Uvicorn enqueues only; the dedicated worker is a separate entrypoint.
    assert services._document_worker_task is None


def test_real_enabled_agent_services_builds_durable_components_without_io() -> None:
    from app.auth.job_credentials import JobCredentialVault
    from app.config import Settings
    from app.ingestion.artifacts import PostgresArtifactStore
    from app.ingestion.processor import DocumentProcessor
    from app.ingestion.repository import PostgresDocumentRepository
    from app.service import AgentServices

    services = AgentServices(Settings(**_settings_values()))

    assert isinstance(services.document_repository, PostgresDocumentRepository)
    assert isinstance(services.artifact_store, PostgresArtifactStore)
    assert isinstance(services.document_processor, DocumentProcessor)
    assert isinstance(
        services.document_runtime.credential_vault,
        JobCredentialVault,  # type: ignore[union-attr]
    )
    assert services.documents is services.document_runtime.documents  # type: ignore[union-attr]
    assert services._document_worker_task is None


@pytest.mark.asyncio
async def test_composed_vault_refresh_adapter_calls_keyword_only_smart_seam(
    monkeypatch,
) -> None:
    import app.auth.job_credentials as credentials_module
    import app.service as service_module
    from app.auth.scopes import W2_REQUESTED_SCOPES
    from app.auth.smart_client import TokenResponse
    from app.config import Settings
    from app.session.store import Session

    monkeypatch.setattr(
        credentials_module,
        "PostgresJobCredentialRepository",
        lambda _connect: credentials_module.InMemoryJobCredentialRepository(),
    )
    refreshes: list[str] = []

    class Smart:
        async def refresh_token(self, *, refresh_token):
            refreshes.append(refresh_token.get_secret_value())
            return TokenResponse(
                access_token="access-rotated",
                refresh_token="refresh-rotated",
                expires_in=300,
                scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
                patient="patient-synthetic",
                clinician_sub="Practitioner/clinician-synthetic",
            )

    settings = Settings(**_settings_values())
    vault = service_module._build_job_credential_vault(
        settings=settings,
        connect=lambda: _return(None),
        smart=Smart(),
    )
    now = datetime.now(timezone.utc)
    session = Session(
        session_id="session-synthetic",
        clinician_sub="Practitioner/clinician-synthetic",
        patient_id="patient-synthetic",
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(minutes=5),
        idle_timeout_s=60,
        turn_cap=20,
    )
    reference = await vault.store(
        session,
        TokenResponse(
            access_token="access-expired",
            refresh_token="refresh-synthetic",
            expires_in=1,
            scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
            patient="patient-synthetic",
            clinician_sub="Practitioner/clinician-synthetic",
        ),
        access_expires_at=now - timedelta(seconds=1),
    )

    principal = await vault.principal_for(
        reference, expected_patient_id="patient-synthetic"
    )

    assert refreshes == ["refresh-synthetic"]
    assert principal.access_token.get_secret_value() == "access-rotated"


@pytest.mark.asyncio
async def test_worker_loop_pulses_readiness_and_stops_before_next_claim() -> None:
    from app.ingestion.processor import DocumentProcessor, run_worker

    calls: list[str] = []
    stop = asyncio.Event()

    class Repository:
        async def recover_stale(self):
            calls.append("recover")
            return 0

        async def claim_next(self, *_args, **_kwargs):
            calls.append("claim")
            stop.set()
            return None

    class Pipeline:
        pass

    async def heartbeat(worker_id: str) -> None:
        calls.append(f"heartbeat:{worker_id}")

    processor = DocumentProcessor(
        repository=Repository(),
        pipeline=Pipeline(),
        worker_id="worker-synthetic",
        worker_heartbeat=heartbeat,
    )
    await run_worker(
        processor,
        stop_event=stop,
        poll_seconds=0.01,
        heartbeat_seconds=0.01,
    )

    assert calls[0] == "heartbeat:worker-synthetic"
    assert calls.count("claim") == 1


@pytest.mark.asyncio
async def test_document_readiness_fails_closed_without_schema_crypto_or_worker() -> (
    None
):
    from app.health import DependencyResult
    from app.service import AgentServices

    services = object.__new__(AgentServices)
    services.settings = type(
        "Settings",
        (),
        {"w2_document_runtime_enabled": True, "document_worker_lease_seconds": 60},
    )()
    services._document_schema_ready = False
    services.document_runtime = None

    result = await services.probe_document_runtime(services.settings)
    assert result == DependencyResult(
        "document_runtime", "hard", False, "schema_unavailable"
    )


def test_fastapi_lifespan_calls_service_shutdown_even_without_inline_worker(
    complete_env,
) -> None:
    from app.main import create_app

    class Services:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def startup(self) -> None:
            self.events.append("startup")

        async def shutdown(self) -> None:
            self.events.append("shutdown")

    services = Services()
    with TestClient(create_app(services=services, readiness_checks=[])) as client:
        assert client.get("/").status_code == 200
        assert services.events == ["startup"]
    assert services.events == ["startup", "shutdown"]


@pytest.mark.asyncio
async def test_document_runtime_probe_requires_fresh_dedicated_worker_heartbeat() -> (
    None
):
    from app.health import DependencyResult
    from app.service import AgentServices

    class Vault:
        async def probe(self) -> None:
            return None

    class Heartbeats:
        async def readiness(self, *, max_age_seconds: float):
            assert max_age_seconds == 120
            return False, "worker_heartbeat_missing"

    runtime = type(
        "Runtime", (), {"credential_vault": Vault(), "heartbeat_store": Heartbeats()}
    )()
    services = object.__new__(AgentServices)
    services.settings = type(
        "Settings",
        (),
        {"w2_document_runtime_enabled": True, "document_worker_lease_seconds": 60},
    )()
    services._document_schema_ready = True
    services.document_runtime = runtime

    result = await services.probe_document_runtime(services.settings)
    assert result == DependencyResult(
        "document_runtime", "hard", False, "worker_heartbeat_missing"
    )


@pytest.mark.asyncio
async def test_worker_heartbeat_store_reports_fresh_and_rejects_invalid_lease() -> None:
    from app.ingestion.runtime import PostgresDocumentWorkerHeartbeatStore

    class Connection:
        def __init__(self) -> None:
            self.row = {
                "worker_seen": True,
                "heartbeat_age": 2.0,
                "invalid_lease": False,
            }
            self.executed: list[tuple[str, tuple[object, ...]]] = []
            self.closed = 0

        async def execute(self, sql, *args):
            self.executed.append((sql, args))

        async def fetchrow(self, _sql, *_args):
            return self.row

        async def close(self):
            self.closed += 1

    connection = Connection()
    store = PostgresDocumentWorkerHeartbeatStore(lambda: _return(connection))

    await store.heartbeat("worker-synthetic")
    assert await store.readiness(max_age_seconds=60) == (True, "ready")
    connection.row["invalid_lease"] = True
    assert await store.readiness(max_age_seconds=60) == (
        False,
        "worker_lease_invariant_failed",
    )
    assert "agent_document_worker_heartbeats" in connection.executed[0][0]
    assert connection.executed[0][1] == ("worker-synthetic",)
    assert connection.closed == 3


@pytest.mark.asyncio
async def test_encounter_ownership_uses_delegated_fhir_patient_binding(
    monkeypatch,
) -> None:
    import app.ingestion.runtime as runtime_module
    from app.writeback.rest_client import DelegatedPrincipal

    searches: list[tuple[str, dict[str, str]]] = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def search(self, resource_type, params):
            searches.append((resource_type, params))
            return {
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Encounter",
                            "id": "encounter-synthetic",
                            "subject": {"reference": "Patient/patient-synthetic"},
                        }
                    }
                ]
            }

    monkeypatch.setattr(runtime_module, "FhirClient", Client)
    settings = type(
        "Settings",
        (),
        {
            "openemr_fhir_base_url": "https://openemr.test/fhir",
            "fhir_per_call_timeout_seconds": 8.0,
        },
    )()
    principal = DelegatedPrincipal(
        clinician_sub="Practitioner/clinician-synthetic",
        patient_id="patient-synthetic",
        access_token=SecretStr("delegated-synthetic"),
    )

    assert await runtime_module._encounter_belongs_to_patient(
        settings, principal, "patient-synthetic", "encounter-synthetic"
    )
    assert not await runtime_module._encounter_belongs_to_patient(
        settings, principal, "patient-other", "encounter-synthetic"
    )
    assert searches == [
        (
            "Encounter",
            {
                "_id": "encounter-synthetic",
                "patient": "patient-synthetic",
                "_count": "2",
            },
        )
    ]


@pytest.mark.asyncio
async def test_dynamic_worker_maps_expired_delegation_to_typed_auth_failure() -> None:
    from app.auth.job_credentials import JobCredentialAuthExpired
    from app.ingestion.pipeline import PipelineFailure
    from app.ingestion.runtime import _DynamicDocumentPipeline
    from app.schemas.documents import FailureReason

    class Repository:
        async def get(self, _document_ref):
            return type("Record", (), {"credential_ref": "opaque", "patient_id": "p"})()

    class Gateways:
        async def for_record(self, _record):
            raise JobCredentialAuthExpired("reauthorization required")

    pipeline = object.__new__(_DynamicDocumentPipeline)
    pipeline._repository = Repository()
    pipeline._gateways = Gateways()

    with pytest.raises(PipelineFailure) as caught:
        await pipeline.extract_document(
            "document-synthetic",
            patient_ref="patient:p",
            correlation_id="corr-synthetic",
        )
    assert caught.value.reason is FailureReason.AUTH_EXPIRED


@pytest.mark.asyncio
async def test_expired_delegation_fails_job_without_blind_retry() -> None:
    from app.ingestion.pipeline import PipelineFailure
    from app.ingestion.processor import DocumentProcessor
    from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
    from app.schemas.documents import FailureReason

    class Pipeline:
        async def extract_document(self, *_args, **_kwargs):
            raise PipelineFailure(FailureReason.AUTH_EXPIRED)

    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash="f" * 64,
            doc_type="lab_pdf",
            filename="synthetic.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id="corr-synthetic",
            credential_ref="credential:opaque",
        )
    )
    await repository.set_state(record.document_id, state="queued")
    processor = DocumentProcessor(
        repository=repository,
        pipeline=Pipeline(),
        worker_id="worker-synthetic",
        max_attempts=3,
    )

    failed = await processor.process_once()

    assert failed is not None and failed.state == "failed"
    assert failed.reason is FailureReason.AUTH_EXPIRED
    assert failed.next_retry_at is None


@pytest.mark.asyncio
async def test_dedicated_worker_factory_prepares_schema_before_returning_processor(
    monkeypatch,
) -> None:
    import app.config as config_module
    import app.service as service_module

    processor = object()
    events: list[str] = []
    runtime = type(
        "Runtime",
        (),
        {
            "processor": processor,
            "credential_vault": type(
                "Vault", (), {"probe": lambda self: _return(True)}
            )(),
        },
    )()

    class Services:
        def __init__(self, settings) -> None:
            assert settings.w2_document_runtime_enabled is True
            self._document_schema_ready = False
            self.document_runtime = runtime

        async def startup(self) -> None:
            events.append("startup")
            self._document_schema_ready = True

    settings = type("Settings", (), {"w2_document_runtime_enabled": True})()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(service_module, "AgentServices", Services)

    assert await service_module.build_document_processor() is processor
    assert events == ["startup"]


async def _return(value):
    return value
