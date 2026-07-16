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
import hashlib
import hmac
import time
from typing import Any, Protocol, cast

from app.auth.job_credentials import (
    JobCredentialAuthExpired,
    JobCredentialBindingError,
    JobCredentialUnavailable,
)
from app.auth.smart_client import TokenResponse
from app.config import Settings
from app.ingestion.artifacts import PostgresArtifactStore
from app.ingestion.readback import (
    BinaryReadbackVerification,
    DocumentReadbackVerification,
)
from app.ingestion.reports import (
    ExtractionReportIntegrityError,
    project_extraction_report,
)
from app.ingestion.pages import EphemeralPageRenderer
from app.ingestion.lab_trends import LabTrendIntegrityError, project_lab_trends
from app.ingestion.pipeline import DocumentExtractionPipeline, PipelineFailure
from app.ingestion.processor import DocumentProcessor
from app.ingestion.repository import (
    DocumentReadinessBinding,
    DocumentRecord,
    PostgresDocumentRepository,
)
from app.ingestion.service import (
    DocumentAdmissionSnapshot,
    DocumentCoordinator,
    DocumentOperations,
    DocumentSubmission,
    ExtractionReportNotReady,
    ExtractionReportUnavailable,
    LabTrendsUnavailable,
)
from app.ingestion.telemetry import DocumentTelemetry
from app.llm.vlm import AnthropicVlmExtractor, VlmMessageProvider
from app.llm.provider import LLMResponse
from app.observability.events import EventEmitter
from app.schemas.documents import (
    DocumentStatus,
    FailureReason,
    RetryAccepted,
    RetryRequest,
)
from app.schemas.extraction import ExtractionArtifact
from app.schemas.extraction_report import DocumentExtractionReport
from app.schemas.lab_trends import LabTrendResponse
from app.schemas.writeback import WriteLeg
from app.session.store import Session
from app.tools.fhir_client import FhirClient
from app.writeback.documents_api import OpenEMRDocumentBackend
from app.writeback.intents import ExactlyOnceWriter, PostgresIntentRepository
from app.writeback.live_gateway import (
    BinaryReadGuard,
    CategoryAttestation,
    EncounterRouteMismatch,
    LegacyRouteAttestation,
    OpenEMRLiveGateway,
    PatientRouteMismatch,
)
from app.writeback.preflight import CategoryExpectation
from app.writeback.rest_client import DelegatedPrincipal
from app.writeback.route_attestations import (
    EncounterRouteBinding,
    PatientRouteBinding,
    PostgresRouteAttestationRepository,
    RouteAttestationNotFound,
)
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


class RouteAttestationResolver(Protocol):
    async def resolve_patient(
        self, patient_uuid: str, *, generation_id: str | None = None
    ) -> PatientRouteBinding: ...

    async def resolve_encounter(
        self,
        patient_uuid: str,
        encounter_uuid: str,
        *,
        generation_id: str | None = None,
    ) -> EncounterRouteBinding: ...

    async def healthcheck(self) -> bool: ...


def document_worker_identity(settings: Settings) -> str:
    """Bind a worker claimant and heartbeat to the exact deployed source SHA."""

    return f"{settings.document_worker_id}@{settings.deployment_sha}"


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

    async def readiness(
        self, *, worker_id: str, max_age_seconds: float
    ) -> tuple[bool, str]:
        if not worker_id:
            raise ValueError("worker id must not be empty")
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
                 WHERE worker_id=$2
                """,
                max_age_seconds,
                worker_id,
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
    def __init__(
        self,
        settings: Settings,
        credentials: CredentialVault,
        route_resolver: RouteAttestationResolver,
    ) -> None:
        self._settings = settings
        self._credentials = credentials
        self._route_resolver = route_resolver
        source_id = settings.source_document_category_id
        artifact_id = settings.artifact_document_category_id
        rest_base = settings.openemr_rest_base_url
        if source_id is None or artifact_id is None or rest_base is None:
            raise ValueError(
                "document runtime is missing attested gateway configuration"
            )
        self._base_url = str(rest_base)
        self._attestations = (
            CategoryAttestation(
                settings.source_document_path,
                source_id,
                settings.source_document_category_acl == "patients|docs",
            ),
            CategoryAttestation(
                settings.artifact_document_path,
                artifact_id,
                settings.artifact_document_category_acl == "patients|docs",
            ),
        )
    async def for_record(self, record: DocumentRecord) -> OpenEMRLiveGateway:
        principal = await self._credentials.principal_for(
            record.credential_ref, expected_patient_id=record.patient_id
        )
        routes = await self._resolved_routes(record.patient_id, record.encounter_id)
        return self._new(principal, routes)

    async def for_readiness_binding(
        self, binding: DocumentReadinessBinding
    ) -> OpenEMRLiveGateway:
        """Build a patient-pinned read gateway without selecting encounter authority."""

        principal = await self._credentials.principal_for(
            binding.credential_ref, expected_patient_id=binding.patient_id
        )
        routes = await self._resolved_routes(binding.patient_id, None)
        return self._new(principal, routes)

    async def for_session(
        self, session: Session, *, encounter_id: str | None
    ) -> tuple[str, DelegatedPrincipal, OpenEMRLiveGateway]:
        credential_ref = await self._credentials.reference_for_session(session)
        principal = await self._credentials.principal_for(
            credential_ref, expected_patient_id=session.patient_id
        )
        routes = await self._resolved_routes(session.patient_id, encounter_id)
        return credential_ref, principal, self._new(principal, routes)

    async def _resolved_routes(
        self, patient_id: str, encounter_id: str | None
    ) -> LegacyRouteAttestation:
        try:
            patient = await self._route_resolver.resolve_patient(patient_id)
        except RouteAttestationNotFound as exc:
            raise PatientRouteMismatch(
                "selected patient has no attested OpenEMR route"
            ) from exc
        encounter = None
        if encounter_id is not None:
            try:
                encounter = await self._route_resolver.resolve_encounter(
                    patient_id,
                    encounter_id,
                    generation_id=patient.generation_id,
                )
            except RouteAttestationNotFound as exc:
                raise EncounterRouteMismatch(
                    "encounter has no route attested for the pinned patient"
                ) from exc
        return LegacyRouteAttestation(
            patient_uuid=patient.patient_uuid,
            patient_id=patient.legacy_patient_id,
            encounter_uuid=(None if encounter is None else encounter.encounter_uuid),
            encounter_id=(
                None if encounter is None else encounter.legacy_encounter_id
            ),
        )

    def _new(
        self, principal: DelegatedPrincipal, routes: LegacyRouteAttestation
    ) -> OpenEMRLiveGateway:
        return OpenEMRLiveGateway(
            base_url=self._base_url,
            principal=principal,
            category_attestations=self._attestations,
            legacy_route_attestation=routes,
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
        route_resolver: RouteAttestationResolver,
        provider: VlmMessageProvider,
        events: EventEmitter | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._intents = intent_repository
        self._artifacts = artifact_store
        self._gateways = _GatewayFactory(settings, credentials, route_resolver)
        self._provider = provider
        self._events = events

    async def extract_document(
        self,
        document_ref: str,
        *,
        patient_ref: str,
        correlation_id: str,
        on_stage=None,
    ):
        record = await self._repository.get(document_ref)
        telemetry = DocumentTelemetry(
            getattr(self, "_events", None),
            correlation_id=getattr(record, "correlation_id", correlation_id),
            job_id=getattr(record, "job_id", document_ref),
        )
        success = False
        try:
            try:
                gateway = await self._gateways.for_record(record)
            except JobCredentialAuthExpired as exc:
                raise PipelineFailure(FailureReason.AUTH_EXPIRED) from exc
            except JobCredentialBindingError as exc:
                raise PipelineFailure(FailureReason.PATIENT_MISMATCH) from exc
            except (PatientRouteMismatch, EncounterRouteMismatch) as exc:
                raise PipelineFailure(exc.reason) from exc
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
            # Only an intake form can enter the frozen vitals mapper.  Medication-list
            # documents are source + grounded artifact only even if a stale caller supplied
            # encounter context; no clinical-resource writer is constructed for them.
            if record.doc_type == "intake_form" and record.encounter_id is not None:
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
                vlm_extractor=AnthropicVlmExtractor(
                    _ObservedVlmProvider(self._provider, telemetry)
                ),
                artifact_writer=artifact_writer,
                vital_writer=vital_writer,
                artifact_store=self._artifacts,
                agent_version=self._settings.agent_version,
            )
            result = await pipeline.extract_document(
                document_ref,
                patient_ref=patient_ref,
                correlation_id=telemetry.correlation_id,
                on_stage=on_stage,
                telemetry=telemetry,
            )
            success = True
            return result
        finally:
            # The concrete pipeline normally finishes this summary; the outer guard also
            # covers credential/route failures before that pipeline can be constructed.
            telemetry.finish(success=success)


class _ObservedVlmProvider:
    """Capture aggregate VLM usage while forwarding no prompt or response content."""

    def __init__(
        self, provider: VlmMessageProvider, telemetry: DocumentTelemetry
    ) -> None:
        self._provider = provider
        self._telemetry = telemetry

    async def complete(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        response = await self._provider.complete(
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        self._telemetry.record_usage(response.usage, response.model)
        return response


class _DocumentOperationsFacade:
    """Session-bound upload facade and credential-bound ephemeral page reader."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: PostgresDocumentRepository,
        intent_repository: PostgresIntentRepository,
        artifact_store: PostgresArtifactStore,
        credentials: CredentialVault,
        route_resolver: RouteAttestationResolver,
        events: EventEmitter | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._intents = intent_repository
        self._artifacts = artifact_store
        self._events = events
        self._gateways = _GatewayFactory(settings, credentials, route_resolver)
        self._renderer = EphemeralPageRenderer(
            repository, fetch_source=self._fetch_source
        )

    async def admission_snapshot(
        self, session: Session, content_hash: str
    ) -> DocumentAdmissionSnapshot:
        """Consult only local durable authority before any delegated remote setup."""

        coordinator = self._read_coordinator()
        return await coordinator.admission_snapshot(session, content_hash)

    async def submit(
        self,
        session: Session,
        upload,
        *,
        encounter_id: str | None,
        correlation_id: str,
    ) -> DocumentSubmission:
        credential_ref, principal, gateway = await self._gateways.for_session(
            session, encounter_id=encounter_id
        )
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
            events=getattr(self, "_events", None),
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

    async def verify_readback(
        self, session: Session, document_id: str
    ) -> DocumentReadbackVerification:
        """Independently re-read both OpenEMR Binaries and expose digests only.

        The record check happens before credential or network access. The existing
        record-bound delegated credential then performs the OpenEMR reads, preserving
        both the patient pin and the non-DEBUG Binary-readback guard.
        """

        record = await self._repository.get(document_id)
        if record.patient_id != session.patient_id:
            from app.ingestion.service import DocumentAccessError

            raise DocumentAccessError(document_id)
        gateway = await self._gateways.for_record(record)
        telemetry = DocumentTelemetry(
            getattr(self, "_events", None),
            correlation_id=record.correlation_id,
            job_id=record.job_id,
        )
        source_started = time.perf_counter()
        source = await _verify_binary_digest(
            gateway,
            patient_id=record.patient_id,
            category_path=self._settings.source_document_path,
            marker=f"document:{record.document_id}:source:v1",
            expected_hash=record.content_hash,
        )
        telemetry.record_readback(
            WriteLeg.SOURCE_DOCUMENT,
            verified=source.verified,
            latency_ms=(time.perf_counter() - source_started) * 1000,
        )

        artifact_result: BinaryReadbackVerification | None = None
        refs = await self._artifacts.refs_for_document(record.document_id)
        if refs is not None:
            artifact = self._artifacts.resolve(refs.artifact_ref)
            if not isinstance(artifact, ExtractionArtifact):
                raise ValueError("persisted extraction artifact is unavailable")
            artifact_bytes = artifact.model_dump_json(warnings=False).encode("utf-8")
            artifact_started = time.perf_counter()
            artifact_result = await _verify_binary_digest(
                gateway,
                patient_id=record.patient_id,
                category_path=self._settings.artifact_document_path,
                marker=(
                    f"document:{record.document_id}:artifact:"
                    f"v{artifact.artifact_version}"
                ),
                expected_hash=hashlib.sha256(artifact_bytes).hexdigest(),
            )
            telemetry.record_readback(
                WriteLeg.EXTRACTION_ARTIFACT,
                verified=artifact_result.verified,
                latency_ms=(time.perf_counter() - artifact_started) * 1000,
            )
        return DocumentReadbackVerification(
            document_id=record.document_id,
            source=source,
            artifact=artifact_result,
        )

    async def extraction_report(
        self, session: Session, document_id: str
    ) -> DocumentExtractionReport:
        """Return a redacted report only after the patient-bound job is complete."""

        record = await self._repository.get(document_id)
        if record.patient_id != session.patient_id:
            from app.ingestion.service import DocumentAccessError

            raise DocumentAccessError(document_id)
        if record.state != "complete":
            raise ExtractionReportNotReady(document_id)
        try:
            refs = await self._artifacts.refs_for_document(record.document_id)
            artifact = (
                None if refs is None else self._artifacts.resolve(refs.artifact_ref)
            )
        except Exception:  # noqa: BLE001 - public route remains sanitized/fail-closed
            raise ExtractionReportUnavailable(document_id) from None
        if not isinstance(artifact, ExtractionArtifact):
            raise ExtractionReportUnavailable(document_id)
        if (
            artifact.document_id != record.document_id
            or artifact.content_hash != record.content_hash
            or artifact.doc_type != record.doc_type
        ):
            raise ExtractionReportUnavailable(document_id)
        try:
            report = project_extraction_report(artifact)
        except (ExtractionReportIntegrityError, ValueError):
            raise ExtractionReportUnavailable(document_id) from None
        if (
            report.fields_grounded != record.fields_grounded
            or report.fields_unsupported != record.fields_unsupported
        ):
            raise ExtractionReportUnavailable(document_id)
        return report

    async def lab_trends(self, session: Session) -> LabTrendResponse:
        """Return a read-only projection scoped solely by the opaque session pin."""

        try:
            return await project_lab_trends(
                repository=self._repository,
                artifact_store=self._artifacts,
                patient_id=session.patient_id,
            )
        except LabTrendIntegrityError:
            raise LabTrendsUnavailable from None

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
            events=getattr(self, "_events", None),
        )


class _RejectingWriter:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("no delegated principal is bound")


class _AuthorizedDocumentCategoryProbe:
    """Issue fixed-path, delegated list reads without retaining response contents."""

    def __init__(
        self,
        *,
        repository: PostgresDocumentRepository,
        gateways: _GatewayFactory,
        category_paths: tuple[str, ...],
    ) -> None:
        self._repository = repository
        self._gateways = gateways
        self._category_paths = category_paths

    async def probe(self) -> str:
        binding = await self._repository.readiness_binding()
        if binding is None:
            # A new installation cannot perform a delegated patient read before its
            # first SMART-pinned upload. Readiness must not invent ambient authority.
            return "pending_first_pinned_job"
        gateway = await self._gateways.for_readiness_binding(binding)
        for category_path in self._category_paths:
            # Parsing proves the authorized endpoint is usable; no count, filename, ID,
            # body, or exception text crosses the readiness boundary.
            await gateway.list_documents(
                patient_id=binding.patient_id, category_path=category_path
            )
        return "authorized_read_ok"


@dataclass(frozen=True)
class DocumentRuntime:
    repository: PostgresDocumentRepository
    artifact_store: PostgresArtifactStore
    pipeline: _DynamicDocumentPipeline
    processor: DocumentProcessor
    documents: DocumentOperations
    credential_vault: CredentialVault
    heartbeat_store: PostgresDocumentWorkerHeartbeatStore
    worker_identity: str
    route_resolver: PostgresRouteAttestationRepository
    category_read_probe: _AuthorizedDocumentCategoryProbe


def build_document_runtime(
    *,
    settings: Settings,
    provider: VlmMessageProvider,
    connect: Connect,
    credential_vault: CredentialVault,
    events: EventEmitter | None = None,
) -> DocumentRuntime:
    repository = PostgresDocumentRepository(connect)
    intents = PostgresIntentRepository(connect)
    artifacts = PostgresArtifactStore(connect)
    routes = PostgresRouteAttestationRepository(connect)
    pipeline = _DynamicDocumentPipeline(
        settings=settings,
        repository=repository,
        intent_repository=intents,
        artifact_store=artifacts,
        credentials=credential_vault,
        route_resolver=routes,
        provider=provider,
        events=events,
    )
    heartbeats = PostgresDocumentWorkerHeartbeatStore(connect)
    worker_identity = document_worker_identity(settings)
    processor = DocumentProcessor(
        repository=repository,
        pipeline=cast(DocumentExtractionPipeline, pipeline),
        worker_id=worker_identity,
        lease_seconds=settings.document_worker_lease_seconds,
        max_attempts=settings.document_worker_max_attempts,
        base_backoff_seconds=settings.document_worker_base_backoff_seconds,
        worker_heartbeat=heartbeats.heartbeat,
        events=events,
    )
    documents = _DocumentOperationsFacade(
        settings=settings,
        repository=repository,
        intent_repository=intents,
        artifact_store=artifacts,
        credentials=credential_vault,
        route_resolver=routes,
        events=events,
    )
    category_read_probe = _AuthorizedDocumentCategoryProbe(
        repository=repository,
        gateways=_GatewayFactory(settings, credential_vault, routes),
        category_paths=(
            settings.source_document_path,
            settings.artifact_document_path,
        ),
    )
    return DocumentRuntime(
        repository=repository,
        artifact_store=artifacts,
        pipeline=pipeline,
        processor=processor,
        documents=documents,
        credential_vault=credential_vault,
        heartbeat_store=heartbeats,
        worker_identity=worker_identity,
        route_resolver=routes,
        category_read_probe=category_read_probe,
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


async def _verify_binary_digest(
    gateway: OpenEMRLiveGateway,
    *,
    patient_id: str,
    category_path: str,
    marker: str,
    expected_hash: str,
) -> BinaryReadbackVerification:
    """Require one marker candidate, then hash bytes re-read through FHIR Binary."""

    candidates = [
        item
        for item in await gateway.list_documents(
            patient_id=patient_id, category_path=category_path
        )
        if item.filename.startswith(f"{marker}-")
    ]
    if len(candidates) != 1:
        return BinaryReadbackVerification(
            expected_hash=expected_hash,
            observed_hash=None,
            verified=False,
        )
    content = await gateway.read_document_bytes(
        patient_id=patient_id, remote_id=candidates[0].remote_id
    )
    observed_hash = hashlib.sha256(content).hexdigest() if content is not None else None
    return BinaryReadbackVerification(
        expected_hash=expected_hash,
        observed_hash=observed_hash,
        verified=(
            observed_hash is not None
            and hmac.compare_digest(observed_hash, expected_hash)
        ),
    )


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
