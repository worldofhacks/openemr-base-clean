"""Composition root — build the serving-path services from config (ARCHITECTURE.md §2, D-DI).

Configuration is wired here, never in business logic. ``AgentServices`` holds the SMART
client, durable clinician/patient session pin, foreground delegated-token cache, LLM and
observability seams, and the enabled W2 document runtime. Background document work uses a
separate encrypted Postgres credential reference and a dedicated worker process (§3); the
Uvicorn process writes the source/enqueues but never claims clinical jobs.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import SecretStr

from app.auth.scopes import (
    ScopeCoverageError,
    assert_w2_scopes_granted,
    requested_scope_string,
    requested_w2_scope_string,
)
from app.auth.job_credentials import (
    JobCredentialAuthExpired,
    JobCredentialBindingError,
    JobCredentialUnavailable,
    JobCredentialVault,
)
from app.auth.smart_client import SmartClient, TokenResponse, generate_pkce
from app.config import Settings
from app.evidence.packet import EvidencePacket, build_evidence_packet
from app.ingestion.migrations import apply_document_migrations
from app.ingestion.processor import DocumentProcessor
from app.ingestion.runtime import DocumentRuntime, build_document_runtime
from app.health import DependencyResult
from app.llm.cost import DailyCostCap
from app.llm.provider import AnthropicLLMProvider
from app.middleware.correlation import correlation_id_var
from app.observability.langfuse import (
    LangfuseSink,
    NullTraceSink,
    RequestTracer,
    TraceSink,
)
from app.observability.events import EventEmitter, EventSink, StructuredLogEventSink
from app.observability.retrieval import observe_retrieval_worker, resolve_reranker_mode
from app.observability.trace import AccountabilityContext
from app.orchestrator import graph as orchestrator_graph
from app.orchestrator.loop import BriefResult, Orchestrator, ToolRegistry
from app.orchestrator.refs import CompositeRefResolver, RefResolver, TurnRefRegistry
from app.orchestrator.workers.extraction_adapter import build_extraction_worker
from app.orchestrator.workers.evidence_retriever import build_evidence_worker
from app.schemas.answers import GroundedAnswerContext
from app.schemas.retrieval import EvidenceSearchRequest
from app.schemas.workers import WorkerInput, WorkerOutput
from app.session.store import PostgresSessionStore, Session
from app.tools.contracts import ToolResult
from app.tools.fhir_client import FhirClient
from app.tools.fhir_tools import run_previsit_fanout
from app.writeback.route_attestations import RouteAttestationNotFound
from app.writeback.live_gateway import PatientRouteMismatch
from corpus.retrieval import (
    HybridRetriever,
    QueryContractError,
    RetrievalUnavailableError,
    build_clinical_query,
)

if TYPE_CHECKING:
    import asyncpg


LaunchDestination = Literal["week1", "week2"]

# One asyncpg connection factory per durable-store operation (AF-P1-03: the seam is
# typed here once instead of appearing as anonymous untyped callables).
PgConnect = Callable[[], Awaitable["asyncpg.Connection"]]


@dataclass(frozen=True)
class _PendingLaunch:
    """Server-side OAuth state; the browser can never supply a redirect target."""

    verifier: str
    destination: LaunchDestination
    created_monotonic: float


class LaunchRateLimited(RuntimeError):
    """Too many OAuth launch attempts reached one serving instance."""


_PENDING_LAUNCH_TTL_SECONDS = 300.0
_MAX_PENDING_LAUNCHES = 256
_LAUNCH_RATE_WINDOW_SECONDS = 60.0
_MAX_LAUNCHES_PER_WINDOW = 60


def _build_tracer(
    settings: Settings, *, events: EventEmitter | None = None
) -> RequestTracer:
    sink: TraceSink
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        sink = LangfuseSink(
            host=str(settings.langfuse_host) if settings.langfuse_host else None,
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
        )
    else:
        sink = (
            NullTraceSink()
        )  # observability optional (§6 soft dep) — serving is unaffected
    return RequestTracer(sink, events=events)


def _build_job_credential_vault(
    *, settings: Settings, connect: PgConnect, smart: SmartClient
) -> JobCredentialVault:
    """Build the separately encrypted delegated-job credential authority (§3)."""

    from app.auth.job_credentials import (
        CredentialCipher,
        PostgresJobCredentialRepository,
    )

    key = settings.document_credential_key
    if key is None:  # Settings rejects this first; retain a local fail-closed guard.
        raise ValueError("document credential key is required")

    async def refresh(refresh_token: SecretStr) -> TokenResponse:
        return await smart.refresh_token(refresh_token=refresh_token)

    return JobCredentialVault(
        repository=PostgresJobCredentialRepository(connect),
        cipher=CredentialCipher(key),
        refresh_access_token=refresh,
    )


def _patient_header(packet: EvidencePacket) -> dict[str, str] | None:
    """Presentation-only chart header (T-E9 UI): name/gender/birth_date from the packet's Patient
    record (age is computed client-side). None if no Patient record was returned."""
    records = packet.by_type("Patient")
    if not records:
        return None
    fields = records[0].fields
    header = {
        k: str(fields[k]) for k in ("name", "gender", "birth_date") if fields.get(k)
    }
    return header or None


async def _pg_connect(dsn: str) -> "asyncpg.Connection":
    """Open one asyncpg connection for a single session-store operation. asyncpg is imported
    lazily so it is never a hard import dependency for the route tests, which inject a fake
    service and never construct AgentServices."""
    import asyncpg

    return await asyncpg.connect(dsn)


class AgentServices:
    def __init__(self, settings: Settings, *, event_sink: EventSink | None = None):
        self.settings = settings
        oauth = str(settings.openemr_oauth_base_url).rstrip("/")
        self.smart = SmartClient(
            client_id=settings.smart_client_id,
            client_secret=settings.smart_client_secret.get_secret_value(),
            authorize_endpoint=f"{oauth}/authorize",
            token_endpoint=f"{oauth}/token",
            fhir_base_url=str(settings.openemr_fhir_base_url).rstrip("/"),
            redirect_uri=settings.agent_callback_url,
        )
        # Durable (clinician, patient) pin in Postgres (D-O2 / §3a / CXR-07): the composition
        # root and the /ready probe are now aligned on the SAME backend — no more serving from
        # memory while probing an unused DB. Fails closed if unreachable (SessionStoreUnavailable).
        self.sessions = PostgresSessionStore(
            dsn=settings.session_store_dsn.get_secret_value(),
            connect=_pg_connect,
            idle_timeout_s=settings.session_idle_timeout_seconds,
            turn_cap=settings.session_turn_cap,
        )
        self._document_connect: PgConnect = lambda: _pg_connect(
            settings.session_store_dsn.get_secret_value()
        )
        self._document_schema_ready = not settings.w2_document_runtime_enabled
        self.document_runtime: DocumentRuntime | None = None
        self._document_worker_task: asyncio.Task[None] | None = None
        # Foreground graph/FHIR turns retain their session-scoped token in memory. Enabled
        # document jobs separately use the encrypted credential vault composed below, so their
        # worker lifetime does not depend on this cache or the UI idle timer (§3).
        self._tokens: dict[
            str, TokenResponse
        ] = {}  # per-session delegated-token cache (§2)
        # OAuth state remains random and one-use.  The fixed UI destination is held only
        # beside its PKCE verifier server-side; no callback ``next`` parameter is trusted.
        # ``str`` remains accepted on read for the frozen W1 auth tests/rolling deploy.
        self._pkce: dict[str, str | _PendingLaunch] = {}
        self._launch_attempts: deque[float] = deque()
        self._launch_clock = time.monotonic
        self.provider = AnthropicLLMProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
        )
        self.cost_cap = DailyCostCap(cap_usd=settings.daily_cost_cap_usd)
        # R05 / AF-P1-04: production composition previously defaulted to NullEventSink,
        # silently discarding every W2 structured event. The default is now the PHI-safe
        # structured-log lane (stdout JSON lines, §7); tests keep the injectable seam.
        self.events = EventEmitter(
            event_sink if event_sink is not None else StructuredLogEventSink()
        )
        self.tracer = _build_tracer(settings, events=self.events)
        self.orchestrator = Orchestrator(
            self.provider,
            cost_cap=self.cost_cap,
            max_tool_iterations=settings.llm_max_tool_iterations,
        )
        # W2-D4: shared by POST /evidence/search and the graph retrieval worker, but built
        # only on the first feature-flagged turn/search request. App boot remains model-free
        # unless the deploy opts into the R07 background warmup (RETRIEVAL_WARMUP).
        self._evidence_retriever: HybridRetriever | None = None
        self._evidence_retriever_lock = threading.Lock()
        self._retrieval_warmup_thread: threading.Thread | None = None
        if settings.w2_document_runtime_enabled:
            credential_vault = _build_job_credential_vault(
                settings=settings,
                connect=self._document_connect,
                smart=self.smart,
            )
            runtime = build_document_runtime(
                settings=settings,
                provider=self.provider,
                connect=self._document_connect,
                credential_vault=credential_vault,
                events=self.events,
            )
            self.document_runtime = runtime
            self.document_repository = runtime.repository
            self.artifact_store = runtime.artifact_store
            self.extraction_pipeline = runtime.pipeline
            self.document_processor = runtime.processor
            self.documents = runtime.documents

    async def startup(self) -> None:
        """Ensure the session-store schema at boot (idempotent). Best-effort: a DB-down boot must
        not crash the app — the hard readiness probe reports it and requests fail closed (§6)."""
        ensure = getattr(self.sessions, "ensure_schema", None)
        if ensure is not None:
            try:
                await ensure()
            except Exception:  # noqa: BLE001 - readiness owns transient DB failures
                pass
        if self.settings.w2_document_runtime_enabled:
            try:
                await apply_document_migrations(self._document_connect)
            except Exception:  # noqa: BLE001 - hard readiness reports schema failure
                self._document_schema_ready = False
            else:
                self._document_schema_ready = True
        # R07: opt-in background retrieval warmup. A cold container paid the
        # first-use ONNX model load inside the active_reranker soft-probe budget
        # (`/ready` -> `active_reranker: timeout`). The deploy image sets
        # RETRIEVAL_WARMUP=1 so boot loads the pre-baked weights off the boot
        # path; default-off keeps local/test boots model-free (W2-D4).
        if os.getenv("RETRIEVAL_WARMUP", "").strip().casefold() in {"1", "true", "yes", "on"}:
            thread = threading.Thread(
                target=self._warm_retrieval_sync,
                name="retrieval-warmup",
                daemon=True,
            )
            self._retrieval_warmup_thread = thread
            thread.start()

    def _warm_retrieval_sync(self) -> None:
        """Best-effort load of the pinned retrieval models (R07).

        Runs the same synthetic, non-clinical search as the `active_reranker`
        readiness probe (`"hypertension"`, k=2), so the shared retriever, the
        pinned bge-small embedder, and the local mxbai ONNX reranker are loaded
        (and the weight files OS-cache warm) before the first cache-busted
        `/ready` probe. Failures stay silent by design: warmup must never crash
        or delay boot — the soft readiness probe owns failure reporting.
        """
        try:
            self.get_evidence_retriever().search("hypertension", k=2)
        except Exception:  # noqa: BLE001 - warmup is best-effort by design
            pass

    async def shutdown(self) -> None:
        """Web owns no clinical worker task; kept for explicit lifespan symmetry."""

        # §3: the Uvicorn process enqueues but never claims. A non-None task would mean
        # an unsafe future composition accidentally started a clinical worker in web.
        if self._document_worker_task is not None:
            raise RuntimeError(
                "document worker must run in the dedicated worker process"
            )

    async def probe_document_runtime(self, _settings: Settings) -> DependencyResult:
        """Hard readiness: schema + credential crypto/store + fresh worker heartbeat."""

        if not self.settings.w2_document_runtime_enabled:
            return DependencyResult("document_runtime", "hard", True, "disabled")
        if not self._document_schema_ready:
            return DependencyResult(
                "document_runtime", "hard", False, "schema_unavailable"
            )
        runtime = self.document_runtime
        if runtime is None:
            return DependencyResult(
                "document_runtime", "hard", False, "composition_unavailable"
            )
        try:
            routes_ready = await runtime.route_resolver.healthcheck()
        except Exception:  # noqa: BLE001 - content-free fail-closed diagnostic
            routes_ready = False
        if not routes_ready:
            return DependencyResult(
                "document_runtime", "hard", False, "route_attestations_unavailable"
            )
        try:
            crypto_ready = await runtime.credential_vault.probe()
        except Exception:  # noqa: BLE001 - readiness emits no secret-bearing diagnostic
            return DependencyResult(
                "document_runtime", "hard", False, "credential_store_unavailable"
            )
        if crypto_ready is False:
            return DependencyResult(
                "document_runtime", "hard", False, "credential_crypto_unavailable"
            )
        try:
            ready, detail = await runtime.heartbeat_store.readiness(
                worker_id=runtime.worker_identity,
                max_age_seconds=float(
                    max(2 * self.settings.document_worker_lease_seconds, 1)
                )
            )
        except Exception:  # noqa: BLE001 - diagnostic is deliberately content-free
            ready, detail = False, "worker_heartbeat_unavailable"
        return DependencyResult("document_runtime", "hard", ready, detail)

    async def probe_graph_state(self, _settings: Settings) -> DependencyResult:
        """Readiness reports the W2 graph state (R03/AF-P1-02).

        The probe always reports whether ``W2_GRAPH_ENABLED`` is on, making the
        deployed graph externally observable. It fails readiness ONLY where the
        deployment *declares* the graph required (``W2_GRAPH_REQUIRED``); otherwise
        the probe stays soft, preserving the fail-closed W1 fallback mode.
        """

        required = bool(getattr(self.settings, "w2_graph_required", False))
        enabled = orchestrator_graph.graph_enabled()
        if required and not enabled:
            return DependencyResult(
                "graph_state", "hard", False, "graph_required_but_disabled"
            )
        if required:
            return DependencyResult("graph_state", "hard", True, "graph_enabled")
        if enabled:
            return DependencyResult("graph_state", "soft", True, "graph_enabled")
        return DependencyResult("graph_state", "soft", True, "disabled_w1_fallback")

    async def probe_document_category_read(
        self, _settings: Settings
    ) -> DependencyResult:
        """Hard, bounded, delegated read of both attested document categories.

        The runtime selects only a previously stored patient/credential binding.  It
        never accepts a readiness patient setting and never returns remote contents or
        identifier-bearing exception text.
        """

        if not self.settings.w2_document_runtime_enabled:
            return DependencyResult("document_category_read", "hard", True, "disabled")
        runtime = self.document_runtime
        if runtime is None:
            return DependencyResult(
                "document_category_read", "hard", False, "composition_unavailable"
            )
        try:
            async with asyncio.timeout(5.0):
                detail = await runtime.category_read_probe.probe()
        except TimeoutError:
            detail = "timeout"
        except JobCredentialAuthExpired:
            detail = "delegation_expired"
        except JobCredentialBindingError:
            detail = "patient_binding_failed"
        except JobCredentialUnavailable:
            detail = "credential_unavailable"
        except (PatientRouteMismatch, RouteAttestationNotFound):
            detail = "patient_route_unavailable"
        except Exception:  # noqa: BLE001 - readiness detail is a closed, content-free code
            detail = "authorized_read_failed"
        ok = detail in {"authorized_read_ok", "pending_first_pinned_job"}
        return DependencyResult("document_category_read", "hard", ok, detail)

    # --- SMART launch → pinned session ---------------------------------------

    def _prepare_launch_slot(self) -> float:
        """Expire abandoned state, bound memory, and apply a per-instance rate cap."""

        clock = getattr(self, "_launch_clock", time.monotonic)
        now = clock()
        expired = [
            state
            for state, pending in self._pkce.items()
            if isinstance(pending, _PendingLaunch)
            and now - pending.created_monotonic >= _PENDING_LAUNCH_TTL_SECONDS
        ]
        for state in expired:
            self._pkce.pop(state, None)

        attempts = getattr(self, "_launch_attempts", None)
        if attempts is None:
            attempts = deque()
            self._launch_attempts = attempts
        cutoff = now - _LAUNCH_RATE_WINDOW_SECONDS
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= _MAX_LAUNCHES_PER_WINDOW:
            raise LaunchRateLimited("SMART launch rate limit exceeded")
        attempts.append(now)

        while len(self._pkce) >= _MAX_PENDING_LAUNCHES:
            self._pkce.pop(next(iter(self._pkce)))
        return now

    def begin_launch(
        self,
        *,
        launch: str | None = None,
        destination: LaunchDestination = "week1",
    ) -> str:
        """Build OAuth+PKCE and bind it to one closed, server-side UI destination."""

        if destination not in {"week1", "week2"}:
            raise ValueError("unknown SMART launch destination")
        if destination == "week2" and not self.settings.w2_document_runtime_enabled:
            raise RuntimeError("Week 2 document runtime is disabled")
        now = self._prepare_launch_slot()
        verifier, challenge, _method = generate_pkce()
        state = secrets.token_urlsafe(24)
        self._pkce[state] = _PendingLaunch(verifier, destination, now)
        scope = (
            requested_w2_scope_string()
            if self.settings.w2_document_runtime_enabled
            else requested_scope_string()
        )
        if launch is None:
            # Standalone launch: request patient context so OpenEMR presents a patient selector
            # (EHR launch instead carries a launch token → build_authorize_url adds `launch` scope).
            if "launch/patient" not in scope.split():
                scope += " launch/patient"
        return self.smart.build_authorize_url(
            state=state, code_challenge=challenge, scope=scope, launch=launch
        )

    async def complete_callback_with_destination(
        self, *, code: str, state: str
    ) -> tuple[Session, LaunchDestination]:
        """Consume one OAuth state and return its fixed server-owned UI destination."""

        clock = getattr(self, "_launch_clock", time.monotonic)
        now = clock()
        pending = self._pkce.pop(state, None)
        if pending is None:
            raise ValueError("unknown or replayed OAuth state")
        if isinstance(pending, str):
            # Rolling/W1 compatibility: the pre-split representation had only a verifier.
            verifier = pending
            destination: LaunchDestination = "week1"
        else:
            if (
                now - pending.created_monotonic
                >= _PENDING_LAUNCH_TTL_SECONDS
            ):
                raise ValueError("unknown or replayed OAuth state")
            verifier = pending.verifier
            destination = pending.destination
        token = await self.smart.exchange_code(code=code, code_verifier=verifier)
        if self.settings.w2_document_runtime_enabled:
            try:
                granted_scopes = token.attested_scopes
            except ValueError:
                raise ScopeCoverageError(
                    "OpenEMR bearer scope attestation was unavailable; refusing the "
                    "delegated document runtime session"
                ) from None
            assert_w2_scopes_granted(granted_scopes)
            # Persist the canonical, exercised bearer authority rather than
            # OpenEMR's intentionally filtered display field.
            token = token.with_attested_scope()
        patient_id = token.patient or ""
        if not patient_id:
            raise ValueError(
                "no launch/patient context in the token — cannot pin a session"
            )
        # Provider attribution (D9/D5): pin the session to the REAL launching clinician decoded
        # from the token's id_token (fhirUser preferred, else sub), falling back to the demo
        # placeholder only when the token carried no decodable id_token.
        token_deadline = self._token_deadline()
        if token.expires_in is not None and token.expires_in > 0:
            token_deadline = min(
                token_deadline,
                datetime.now(timezone.utc) + timedelta(seconds=token.expires_in),
            )
        session = await self.sessions.create(
            clinician_sub=token.clinician_sub or "openemr-clinician",
            patient_id=patient_id,
            encounter_id=token.encounter,
            token_expires_at=token_deadline,
        )
        runtime = getattr(self, "document_runtime", None)
        if runtime is not None:
            await runtime.credential_vault.store(
                session, token, access_expires_at=token_deadline
            )
        self._tokens[session.session_id] = token
        return session, destination

    async def complete_callback(self, *, code: str, state: str) -> Session:
        """Compatibility API for non-browser callers; W1 remains the default target."""

        session, _destination = await self.complete_callback_with_destination(
            code=code, state=state
        )
        return session

    def _token_deadline(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.token_lifetime_seconds
        )

    # --- the two operations the routes call -----------------------------------

    async def resolve_session(self, session_id: str) -> Session:
        return await self.sessions.get(session_id)

    async def resolve_document_route_context(
        self, session: Session
    ) -> tuple[bool, str | None]:
        """Resolve only the pinned SMART patient and optional SMART encounter.

        Unknown patients remain read-only. An absent or newly-created/unattested
        encounter never becomes an ambient "latest" encounter; the UI stays
        artifact-only until activation refreshes the registry.
        """

        runtime = self.document_runtime
        if runtime is None:
            return False, None
        try:
            patient = await runtime.route_resolver.resolve_patient(session.patient_id)
        except RouteAttestationNotFound:
            return False, None
        if session.encounter_id is None:
            return True, None
        try:
            await runtime.route_resolver.resolve_encounter(
                session.patient_id,
                session.encounter_id,
                generation_id=patient.generation_id,
            )
        except RouteAttestationNotFound:
            return True, None
        return True, session.encounter_id

    def get_evidence_retriever(self) -> HybridRetriever:
        """Return the process-wide, integrity-checked retriever, initialized lazily."""

        if self._evidence_retriever is None:
            with self._evidence_retriever_lock:
                if self._evidence_retriever is None:
                    default_corpus = Path(__file__).resolve().parents[1] / "corpus"
                    corpus_dir = Path(
                        os.getenv("EVIDENCE_CORPUS_DIR", str(default_corpus))
                    )
                    self._evidence_retriever = HybridRetriever(corpus_dir)
        return self._evidence_retriever

    def _accountability_context(
        self, session: Session, token: TokenResponse, *, request_url: str
    ) -> AccountabilityContext:
        return AccountabilityContext(
            correlation_id=correlation_id_var.get(),
            client_id=self.settings.smart_client_id,
            exercised_scopes=tuple(token.scopes),
            request_url=request_url,
            user_id=session.clinician_sub,
            patient_id=session.patient_id,
            utc_timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def run_graph_turn(
        self, session: Session, message: str, *, request_url: str
    ) -> orchestrator_graph.GraphTurnResult:
        """Run one W2-D2 graph turn with refs-only real guideline retrieval.

        The user message reaches retrieval only if the deterministic builder can reduce it
        to condition/test terms. A conversational or identifier-shaped message simply gives
        the worker no evidence request; it is never forwarded to Cohere or another vendor.
        Completed document refs hydrate from Postgres and bind the real persisted extraction
        pipeline; no LangGraph checkpointer or in-memory VLM result becomes an authority.
        """

        token = self._tokens.get(session.session_id)
        if token is None:
            raise ValueError("no delegated token cached for this session — re-launch")

        correlation_id = correlation_id_var.get()
        turn_refs = TurnRefRegistry(correlation_id)
        refs: RefResolver = turn_refs
        document_refs: list[str] = []
        extraction_worker = None
        document_repository = getattr(self, "document_repository", None)
        artifact_store = getattr(self, "artifact_store", None)
        extraction_pipeline = getattr(self, "extraction_pipeline", None)
        if document_repository is not None and artifact_store is not None:
            completed = await document_repository.list_for_patient(
                session.patient_id, state="complete"
            )
            document_refs = [record.document_id for record in completed]
            await artifact_store.warm_for_documents(document_refs)
            refs = CompositeRefResolver(turn_refs, artifact_store)
        if extraction_pipeline is not None:
            bound_extraction = build_extraction_worker(extraction_pipeline)

            async def extraction_worker(payload: WorkerInput) -> WorkerOutput:
                # The supervisor carries only the opaque session ref. Patient resolution
                # happens inside the worker boundary before B2 enforces its patient pin.
                pinned = payload.model_copy(
                    update={"patient_ref": f"patient:{session.patient_id}"}
                )
                return await bound_extraction(pinned)

        evidence_refs: list[str] = []
        try:
            query = build_clinical_query(re.split(r"[,;|]+", message))
        except QueryContractError:
            pass
        else:
            evidence_refs.append(
                refs.put(
                    EvidenceSearchRequest(query=query, k=5),
                    kind="evidence-request",
                )
            )

        worker_input = WorkerInput(
            correlation_id=correlation_id,
            turn=0,
            patient_ref=f"session:{session.session_id}",
            document_refs=document_refs,
            evidence_refs=evidence_refs,
            request_kind="previsit_brief",
        )

        # R05 / AF-P1-04: retrieval completion emits the registered retrieval.completed
        # event. Wrapping happens here in the composition root; partial test fixtures
        # without an event emitter keep the unwrapped worker (same seam as the graph's
        # `events=getattr(...)` below).
        events = getattr(self, "events", None)
        try:
            retrieval_worker = build_evidence_worker(
                self.get_evidence_retriever(), refs
            )
            if events is not None:
                retrieval_worker = observe_retrieval_worker(
                    retrieval_worker,
                    events=events,
                    reranker_mode=resolve_reranker_mode(os.environ),
                )
        except RetrievalUnavailableError:
            # Retrieval is a soft dependency (§6). Preserve the W1 grounded chart brief
            # while making the worker degradation explicit in its canonical envelope.
            async def degraded_retrieval(payload: WorkerInput) -> WorkerOutput:
                return WorkerOutput(
                    correlation_id=payload.correlation_id,
                    worker="evidence_retriever",
                    status="degraded",
                    artifact_refs=[],
                    citation_refs=[],
                    reason_code=None,
                )

            # Even an unavailable retriever completes retrieval (degraded), so the
            # registered retrieval.completed event still records the outcome.
            retrieval_worker = degraded_retrieval
            if events is not None:
                retrieval_worker = observe_retrieval_worker(
                    degraded_retrieval, events=events, reranker_mode="disabled"
                )

        async def run_brief_pinned() -> BriefResult:
            return await self.run_brief(session, message, request_url=request_url)

        async def run_brief_with_context(
            answer_context: GroundedAnswerContext,
        ) -> BriefResult:
            return await self.run_brief(
                session,
                message,
                request_url=request_url,
                answer_context=answer_context,
                emit_summary=False,
            )

        return await orchestrator_graph.run_graph_turn(
            run_brief=run_brief_pinned,
            run_brief_with_context=run_brief_with_context,
            correlation_id=correlation_id,
            tracer=self.tracer,
            accountability=self._accountability_context(
                session, token, request_url=request_url
            ),
            worker_input=worker_input,
            extraction_worker=extraction_worker,
            retrieval_worker=retrieval_worker,
            ref_registry=refs,
            events=getattr(self, "events", None),
        )

    async def run_brief(
        self,
        session: Session,
        message: str,
        *,
        request_url: str,
        answer_context: GroundedAnswerContext | None = None,
        emit_summary: bool = True,
    ) -> BriefResult:
        token = self._tokens.get(session.session_id)
        if token is None:
            raise ValueError("no delegated token cached for this session — re-launch")
        client = FhirClient(
            base_url=str(self.settings.openemr_fhir_base_url).rstrip("/"),
            access_token=token.access_token.get_secret_value(),
            per_call_timeout=self.settings.fhir_per_call_timeout_seconds,
        )
        # Begin the accountable trace BEFORE the FHIR fan-out (CXR-05/§7): all accountability is
        # already known (client, exercised scopes, pinned clinician + patient), so the six PHI
        # reads can each be captured as a span. Tracing is a soft dependency (§6) — begun always
        # (a NullTraceSink discards when Langfuse is unconfigured), never affecting serving.
        accountability = self._accountability_context(
            session, token, request_url=request_url
        )
        builder = self.tracer.begin(accountability)

        def _record_fhir(name: str, latency_ms: float, result: ToolResult) -> None:
            # One accountability span per outbound FHIR read (CXR-05): resource, latency, and
            # tri-state outcome — so the trace localizes FHIR work and every PHI read is logged,
            # including timeouts/budget failures. Only the closed outcome and record count enter
            # the trace; the typed result and free-text failure reason never cross this boundary.
            builder.step(
                f"fhir.{name}",
                latency_ms=latency_ms,
                status=result.status.value,
                records=len(result.records),
            )

        fanout = await run_previsit_fanout(
            client,
            session.patient_id,
            per_call_timeout=self.settings.fhir_per_call_timeout_seconds,
            turn_budget=self.settings.turn_total_budget_seconds,
            on_call=_record_fhir,
        )
        packet = build_evidence_packet(session.patient_id, fanout)
        await self.sessions.record_turn(session.session_id)
        # UC1: the packet is pre-built deterministically (D10); the LLM narrates it in typed
        # claims (empty tool registry → only submit_claims), which are verified and re-rendered.
        # The trace begun above is threaded in so the LLM/verify spans join the FHIR spans.
        result = await self.orchestrator.run_previsit_brief(
            packet,
            message,
            tools=ToolRegistry([]),
            builder=builder,
            answer_context=answer_context,
            emit_summary=emit_summary,
        )
        # Attach the patient header (presentation-only, T-E9 UI) from the already-fetched Patient
        # record — the UI draws a chart header from it; verification/serving are untouched.
        return replace(result, patient=_patient_header(packet))


async def build_document_processor() -> DocumentProcessor:
    """Dedicated-worker CLI factory: ``app.service:build_document_processor``.

    This is intentionally separate from FastAPI lifespan. The web process only writes
    the source/enqueues; this factory applies the shared schema and returns the sole
    claimed-job processor for ``python -m app.ingestion.processor --factory ...``.
    """

    from app.config import get_settings

    settings = get_settings()
    if not settings.w2_document_runtime_enabled:
        raise RuntimeError("document runtime is disabled")
    services = AgentServices(settings)
    await services.startup()
    if not services._document_schema_ready or services.document_runtime is None:
        raise RuntimeError("document runtime schema or composition is unavailable")
    await services.document_runtime.credential_vault.probe()
    return services.document_runtime.processor
