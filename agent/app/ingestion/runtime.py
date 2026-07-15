"""Executable document-runtime composition (W2-D1/D3/D9/D10; §2/§3/§5).

The web facade writes the source and enqueues only.  The durable worker resolves the
job's opaque credential reference, then constructs a fresh patient-bound OpenEMR gateway
and encounter-bound vital transport for that one claimed job.  No gateway or clinical
pipeline singleton can accidentally cross patient or encounter boundaries.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from app.auth.job_credentials import (
    JobCredentialAuthExpired,
    JobCredentialBindingError,
    JobCredentialUnavailable,
)
from app.auth.smart_client import TokenResponse
from app.config import Settings
from app.ingestion.artifacts import PostgresArtifactStore
from app.ingestion.pages import EphemeralPageRenderer
from app.ingestion.pipeline import DocumentExtractionPipeline, PipelineFailure
from app.ingestion.processor import DocumentProcessor
from app.ingestion.repository import (
    DocumentRecord,
    PostgresDocumentRepository,
)
from app.ingestion.service import (
    DocumentCoordinator,
    DocumentOperations,
    DocumentSubmission,
)
from app.llm.vlm import AnthropicVlmExtractor, VlmMessageProvider
from app.schemas.documents import (
    DocumentStatus,
    FailureReason,
    RetryAccepted,
    RetryRequest,
)
from app.session.store import Session
from app.tools.fhir_client import FhirClient
from app.writeback.documents_api import OpenEMRDocumentBackend
from app.writeback.intents import ExactlyOnceWriter, PostgresIntentRepository
from app.writeback.live_gateway import (
    BinaryReadGuard,
    CategoryAttestation,
    OpenEMRLiveGateway,
)
from app.writeback.preflight import CategoryExpectation
from app.writeback.rest_client import DelegatedPrincipal
from app.writeback.source_loader import OpenEMRSourceLoader
from app.writeback.transports import (
    ExtractionArtifactTransport,
    SourceDocumentTransport,
    VitalIntentTransport,
)
from app.writeback.vitals_api import OpenEMRVitalBackend


Connect = Callable[[], Awaitable[object]]


class CredentialVault(Protocol):
    async def store(
        self,
        session: Session,
        token: TokenResponse,
        *,
        access_expires_at: datetime,
    ) -> str: ...

    async def reference_for_session(self, session: Session) -> str: ...

    async def principal_for(
        self, credential_ref: str, *, expected_patient_id: str
    ) -> DelegatedPrincipal: ...

    async def probe(self) -> bool: ...


class PostgresDocumentWorkerHeartbeatStore:
    """Dedicated worker liveness over the shared W2 runtime migration."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def heartbeat(self, worker_id: str) -> None:
        if not worker_id:
            raise ValueError("worker id must not be empty")
        connection = await self._connect()
        try:
            await connection.execute(  # type: ignore[attr-defined]
                """
                INSERT INTO agent_document_worker_heartbeats
                    (worker_id, heartbeat_at, started_at)
                VALUES ($1,NOW(),NOW())
                ON CONFLICT (worker_id) DO UPDATE
                    SET heartbeat_at=EXCLUDED.heartbeat_at
                """,
                worker_id,
            )
        finally:
            await _close(connection)

    async def readiness(self, *, max_age_seconds: float) -> tuple[bool, str]:
        if max_age_seconds <= 0:
            raise ValueError("heartbeat max age must be positive")
        connection = await self._connect()
        try:
            row = await connection.fetchrow(  # type: ignore[attr-defined]
                """
                SELECT MAX(heartbeat_at) IS NOT NULL AS worker_seen,
                       COALESCE(
                         EXTRACT(EPOCH FROM (NOW() - MAX(heartbeat_at)))::double precision,
                         $1::double precision + 1
                       ) AS heartbeat_age,
                       EXISTS (
                         SELECT 1 FROM agent_document_jobs
                          WHERE state IN ('extracting','grounding','writing')
                            AND (claim_owner IS NULL OR lease_expires_at IS NULL)
                       ) AS invalid_lease
                  FROM agent_document_worker_heartbeats
                """,
                max_age_seconds,
            )
        finally:
            await _close(connection)
        values = dict(cast(Mapping[str, object], row))
        if bool(values.get("invalid_lease")):
            return False, "worker_lease_invariant_failed"
        if not bool(values.get("worker_seen")):
            return False, "worker_heartbeat_missing"
        try:
            age = float(str(values["heartbeat_age"]))
        except (KeyError, TypeError, ValueError):
            return False, "worker_heartbeat_unavailable"
        if age < 0 or age > max_age_seconds:
            return False, "worker_heartbeat_stale"
        return True, "ready"


class _GatewayFactory:
    def __init__(self, settings: Settings, credentials: CredentialVault) -> None:
        self._settings = settings
        self._credentials = credentials
        source_id = settings.source_document_category_id
        artifact_id = settings.artifact_document_category_id
        rest_base = settings.openemr_rest_base_url
        if source_id is None or artifact_id is None or rest_base is None:
            raise ValueError(
                "document runtime is missing attested gateway configuration"
            )
        self._base_url = str(rest_base)
        self._attestations = (
            CategoryAttestation(settings.source_document_path, source_id, True),
            CategoryAttestation(settings.artifact_document_path, artifact_id, True),
        )

    async def for_record(self, record: DocumentRecord) -> OpenEMRLiveGateway:
        principal = await self._credentials.principal_for(
            record.credential_ref, expected_patient_id=record.patient_id
        )
        return self._new(principal)

    async def for_session(
        self, session: Session
    ) -> tuple[str, DelegatedPrincipal, OpenEMRLiveGateway]:
        credential_ref = await self._credentials.reference_for_session(session)
        principal = await self._credentials.principal_for(
            credential_ref, expected_patient_id=session.patient_id
        )
        return credential_ref, principal, self._new(principal)

    def _new(self, principal: DelegatedPrincipal) -> OpenEMRLiveGateway:
        return OpenEMRLiveGateway(
            base_url=self._base_url,
            principal=principal,
            category_attestations=self._attestations,
            # Settings validation admits enabled runtime only after the deploy attests
            # non-DEBUG Binary readback. No raw setting or response enters logs.
            binary_guard=BinaryReadGuard("attested-non-debug"),
        )


class _DynamicDocumentPipeline:
    """Build the live source/artifact/vital chain for exactly one persisted job."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: PostgresDocumentRepository,
        intent_repository: PostgresIntentRepository,
        artifact_store: PostgresArtifactStore,
        credentials: CredentialVault,
        vlm: AnthropicVlmExtractor,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._intents = intent_repository
        self._artifacts = artifact_store
        self._gateways = _GatewayFactory(settings, credentials)
        self._vlm = vlm

    async def extract_document(
        self,
        document_ref: str,
        *,
        patient_ref: str,
        correlation_id: str,
        on_stage=None,
    ):
        record = await self._repository.get(document_ref)
        try:
            gateway = await self._gateways.for_record(record)
        except JobCredentialAuthExpired as exc:
            raise PipelineFailure(FailureReason.AUTH_EXPIRED) from exc
        except JobCredentialBindingError as exc:
            raise PipelineFailure(FailureReason.PATIENT_MISMATCH) from exc
        except JobCredentialUnavailable:
            # Storage/crypto availability may recover; the processor's bounded generic
            # failure path releases the lease and retries. It is never mislabeled as a
            # revoked delegation and never falls through to an OpenEMR call.
            raise
        artifact_backend = OpenEMRDocumentBackend(
            gateway, category_path=self._settings.artifact_document_path
        )
        artifact_writer = ExactlyOnceWriter(
            self._intents,
            ExtractionArtifactTransport(
                artifact_backend,
                category=CategoryExpectation(
                    path=self._settings.artifact_document_path,
                    category_id=_required(
                        self._settings.artifact_document_category_id,
                        "artifact category id",
                    ),
                ),
            ),
        )
        vital_writer = None
        if record.encounter_id is not None:
            vital_writer = ExactlyOnceWriter(
                self._intents,
                VitalIntentTransport(
                    OpenEMRVitalBackend(gateway, encounter_id=record.encounter_id)
                ),
            )
        pipeline = DocumentExtractionPipeline(
            repository=self._repository,
            source_loader=OpenEMRSourceLoader(
                gateway, category_path=self._settings.source_document_path
            ),
            vlm_extractor=self._vlm,
            artifact_writer=artifact_writer,
            vital_writer=vital_writer,
            artifact_store=self._artifacts,
            agent_version=self._settings.agent_version,
        )
        return await pipeline.extract_document(
            document_ref,
            patient_ref=patient_ref,
            correlation_id=correlation_id,
            on_stage=on_stage,
        )


class _DocumentOperationsFacade:
    """Session-bound upload facade and credential-bound ephemeral page reader."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: PostgresDocumentRepository,
        intent_repository: PostgresIntentRepository,
        credentials: CredentialVault,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._intents = intent_repository
        self._gateways = _GatewayFactory(settings, credentials)
        self._renderer = EphemeralPageRenderer(
            repository, fetch_source=self._fetch_source
        )

    async def submit(
        self,
        session: Session,
        upload,
        *,
        encounter_id: str | None,
        correlation_id: str,
    ) -> DocumentSubmission:
        credential_ref, principal, gateway = await self._gateways.for_session(session)
        backend = OpenEMRDocumentBackend(
            gateway, category_path=self._settings.source_document_path
        )
        writer = ExactlyOnceWriter(
            self._intents,
            SourceDocumentTransport(
                backend,
                category=CategoryExpectation(
                    path=self._settings.source_document_path,
                    category_id=_required(
                        self._settings.source_document_category_id,
                        "source category id",
                    ),
                ),
            ),
        )

        async def encounter_owned(patient_id: str, candidate: str) -> bool:
            return await _encounter_belongs_to_patient(
                self._settings, principal, patient_id, candidate
            )

        async def credential_for(_session: Session) -> str:
            return credential_ref

        coordinator = DocumentCoordinator(
            repository=self._repository,
            source_writer=writer,
            encounter_belongs_to_patient=encounter_owned,
            credential_ref_for_session=credential_for,
            page_renderer=self._renderer,
        )
        return await coordinator.submit(
            session,
            upload,
            encounter_id=encounter_id,
            correlation_id=correlation_id,
        )

    async def status(self, session: Session, document_id: str) -> DocumentStatus:
        coordinator = self._read_coordinator()
        return await coordinator.status(session, document_id)

    async def retry(
        self,
        session: Session,
        document_id: str,
        request: RetryRequest,
        *,
        correlation_id: str,
    ) -> RetryAccepted:
        coordinator = self._read_coordinator()
        return await coordinator.retry(
            session, document_id, request, correlation_id=correlation_id
        )

    async def page_png(
        self, session: Session, document_id: str, page_number: int
    ) -> object:
        return await self._renderer.page_png(session, document_id, page_number)

    async def _fetch_source(self, record: DocumentRecord) -> bytes:
        gateway = await self._gateways.for_record(record)
        return await OpenEMRSourceLoader(
            gateway, category_path=self._settings.source_document_path
        ).fetch(record)

    def _read_coordinator(self) -> DocumentCoordinator:
        async def unused(*_args, **_kwargs):
            raise RuntimeError("write operation unavailable on read coordinator")

        # These seams are unreachable from status/retry; keeping them rejecting makes an
        # accidental future write fail closed rather than selecting an ambient principal.
        return DocumentCoordinator(
            repository=self._repository,
            source_writer=cast(ExactlyOnceWriter, _RejectingWriter()),
            encounter_belongs_to_patient=unused,
            credential_ref_for_session=unused,
            page_renderer=self._renderer,
        )


class _RejectingWriter:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("no delegated principal is bound")


@dataclass(frozen=True)
class DocumentRuntime:
    repository: PostgresDocumentRepository
    artifact_store: PostgresArtifactStore
    pipeline: _DynamicDocumentPipeline
    processor: DocumentProcessor
    documents: DocumentOperations
    credential_vault: CredentialVault
    heartbeat_store: PostgresDocumentWorkerHeartbeatStore


def build_document_runtime(
    *,
    settings: Settings,
    provider: VlmMessageProvider,
    connect: Connect,
    credential_vault: CredentialVault,
) -> DocumentRuntime:
    repository = PostgresDocumentRepository(connect)
    intents = PostgresIntentRepository(connect)
    artifacts = PostgresArtifactStore(connect)
    vlm = AnthropicVlmExtractor(provider)
    pipeline = _DynamicDocumentPipeline(
        settings=settings,
        repository=repository,
        intent_repository=intents,
        artifact_store=artifacts,
        credentials=credential_vault,
        vlm=vlm,
    )
    heartbeats = PostgresDocumentWorkerHeartbeatStore(connect)
    processor = DocumentProcessor(
        repository=repository,
        pipeline=cast(DocumentExtractionPipeline, pipeline),
        worker_id=settings.document_worker_id,
        lease_seconds=settings.document_worker_lease_seconds,
        max_attempts=settings.document_worker_max_attempts,
        base_backoff_seconds=settings.document_worker_base_backoff_seconds,
        worker_heartbeat=heartbeats.heartbeat,
    )
    documents = _DocumentOperationsFacade(
        settings=settings,
        repository=repository,
        intent_repository=intents,
        credentials=credential_vault,
    )
    return DocumentRuntime(
        repository=repository,
        artifact_store=artifacts,
        pipeline=pipeline,
        processor=processor,
        documents=documents,
        credential_vault=credential_vault,
        heartbeat_store=heartbeats,
    )


async def _encounter_belongs_to_patient(
    settings: Settings,
    principal: DelegatedPrincipal,
    patient_id: str,
    encounter_id: str,
) -> bool:
    if patient_id != principal.patient_id or not encounter_id:
        return False
    client = FhirClient(
        base_url=str(settings.openemr_fhir_base_url),
        access_token=principal.access_token.get_secret_value(),
        per_call_timeout=settings.fhir_per_call_timeout_seconds,
    )
    try:
        bundle = await client.search(
            "Encounter",
            {"_id": encounter_id, "patient": patient_id, "_count": "2"},
        )
    except Exception:
        return False
    matches = 0
    for entry in bundle.get("entry", []):
        if not isinstance(entry, Mapping):
            continue
        resource = entry.get("resource")
        if not isinstance(resource, Mapping):
            continue
        subject = resource.get("subject")
        reference = subject.get("reference") if isinstance(subject, Mapping) else None
        if (
            resource.get("resourceType") == "Encounter"
            and str(resource.get("id") or "") == encounter_id
            and _patient_reference_matches(str(reference or ""), patient_id)
        ):
            matches += 1
    return matches == 1


def _patient_reference_matches(reference: str, patient_id: str) -> bool:
    parts = reference.rstrip("/").split("/")
    return len(parts) >= 2 and parts[-2:] == ["Patient", patient_id]


def _required(value: str | None, name: str) -> str:
    if value is None or not value:
        raise ValueError(f"document runtime is missing {name}")
    return value


async def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result
