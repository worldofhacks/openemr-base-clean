"""Composition root — build the serving-path services from config (ARCHITECTURE.md §2, D-DI).

Configuration is wired here, never in business logic. `AgentServices` holds the SMART client,
the (clinician,patient)-pinned session store, a per-session delegated-token cache (§2), the LLM
provider behind its seam (D4), the cost cap, and the observability tracer, and exposes the two
operations the HTTP routes need: complete a SMART launch into a pinned session, and run the
UC1 verify-then-flush brief for a session.

Demo posture: the session store is in-process (single Railway instance); production (D-O2,
multi-replica) swaps in the Postgres store — the pin/expiry/fail-closed semantics are identical,
only the backend differs. The delegated token is cached in-process per session; production would
persist it encrypted alongside the pinned session.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app.auth.scopes import requested_scope_string
from app.auth.smart_client import SmartClient, TokenResponse, generate_pkce
from app.config import Settings
from app.evidence.packet import build_evidence_packet
from app.ingestion.migrations import apply_document_migrations
from app.llm.cost import DailyCostCap
from app.llm.provider import AnthropicLLMProvider
from app.middleware.correlation import correlation_id_var
from app.observability.langfuse import LangfuseSink, NullTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator import graph as orchestrator_graph
from app.orchestrator.loop import BriefResult, Orchestrator, ToolRegistry
from app.orchestrator.refs import CompositeRefResolver, RefResolver, TurnRefRegistry
from app.orchestrator.workers.extraction_adapter import build_extraction_worker
from app.orchestrator.workers.evidence_retriever import build_evidence_worker
from app.schemas.retrieval import EvidenceSearchRequest
from app.schemas.workers import WorkerInput, WorkerOutput
from app.session.store import PostgresSessionStore, Session
from app.tools.fhir_client import FhirClient
from app.tools.fhir_tools import run_previsit_fanout
from corpus.retrieval import (
    HybridRetriever,
    QueryContractError,
    RetrievalUnavailableError,
    build_clinical_query,
)


def _build_tracer(settings: Settings) -> RequestTracer:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        sink = LangfuseSink(
            host=str(settings.langfuse_host) if settings.langfuse_host else None,
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            log_content=settings.langfuse_log_content,
        )
    else:
        sink = (
            NullTraceSink()
        )  # observability optional (§6 soft dep) — serving is unaffected
    return RequestTracer(sink)


def _patient_header(packet) -> dict[str, str] | None:
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


async def _pg_connect(dsn: str):
    """Open one asyncpg connection for a single session-store operation. asyncpg is imported
    lazily so it is never a hard import dependency for the route tests, which inject a fake
    service and never construct AgentServices."""
    import asyncpg

    return await asyncpg.connect(dsn)


def _fhir_trace_content(result) -> dict:
    """Exact typed FHIR tool result for D16 tracing; the sink mask owns disclosure policy."""
    return result.model_dump(mode="json")


class AgentServices:
    def __init__(self, settings: Settings):
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
        self._document_connect = lambda: _pg_connect(
            settings.session_store_dsn.get_secret_value()
        )
        self._document_schema_ready = not settings.w2_document_runtime_enabled
        # The delegated token + PKCE verifier stay in-process (per-instance): the token is a
        # bearer credential (encrypting it into the pin row is the documented next step, §5
        # known-limitations). A restart therefore keeps the durable pin but requires a re-launch
        # to re-mint the token — the canonical expiry/re-launch path (§3a), never a silent serve.
        self._tokens: dict[
            str, TokenResponse
        ] = {}  # per-session delegated-token cache (§2)
        self._pkce: dict[str, str] = {}  # oauth state → PKCE code_verifier
        self.provider = AnthropicLLMProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
        )
        self.cost_cap = DailyCostCap(cap_usd=settings.daily_cost_cap_usd)
        self.tracer = _build_tracer(settings)
        self.orchestrator = Orchestrator(
            self.provider,
            cost_cap=self.cost_cap,
            max_tool_iterations=settings.llm_max_tool_iterations,
        )
        # W2-D4: shared by POST /evidence/search and the graph retrieval worker, but built
        # only on the first feature-flagged turn/search request. App boot remains model-free.
        self._evidence_retriever: HybridRetriever | None = None
        self._evidence_retriever_lock = threading.Lock()

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

    # --- SMART launch → pinned session ---------------------------------------

    def begin_launch(self, *, launch: str | None = None) -> str:
        """Build the authorize URL for an EHR launch (or standalone) and stash the PKCE verifier."""
        verifier, challenge, _method = generate_pkce()
        state = secrets.token_urlsafe(24)
        self._pkce[state] = verifier
        scope = requested_scope_string()
        if launch is None:
            # Standalone launch: request patient context so OpenEMR presents a patient selector
            # (EHR launch instead carries a launch token → build_authorize_url adds `launch` scope).
            scope += " launch/patient"
        return self.smart.build_authorize_url(
            state=state, code_challenge=challenge, scope=scope, launch=launch
        )

    async def complete_callback(self, *, code: str, state: str) -> Session:
        """Exchange the code (PKCE) and create a session pinned to (clinician, launched patient)."""
        verifier = self._pkce.pop(state, None)
        if verifier is None:
            raise ValueError("unknown or replayed OAuth state")
        token = await self.smart.exchange_code(code=code, code_verifier=verifier)
        patient_id = token.patient or ""
        if not patient_id:
            raise ValueError(
                "no launch/patient context in the token — cannot pin a session"
            )
        # Provider attribution (D9/D5): pin the session to the REAL launching clinician decoded
        # from the token's id_token (fhirUser preferred, else sub), falling back to the demo
        # placeholder only when the token carried no decodable id_token.
        session = await self.sessions.create(
            clinician_sub=token.clinician_sub or "openemr-clinician",
            patient_id=patient_id,
            token_expires_at=self._token_deadline(),
        )
        self._tokens[session.session_id] = token
        return session

    def _token_deadline(self) -> datetime:
        from datetime import timedelta

        return datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.token_lifetime_seconds
        )

    # --- the two operations the routes call -----------------------------------

    async def resolve_session(self, session_id: str) -> Session:
        return await self.sessions.get(session_id)

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
        B2 extraction is intentionally left on the graph's compatibility worker in this
        integration unit and is mounted by its separately owned unit.
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

        try:
            retrieval_worker = build_evidence_worker(
                self.get_evidence_retriever(), refs
            )
        except RetrievalUnavailableError:
            # Retrieval is a soft dependency (§6). Preserve the W1 grounded chart brief
            # while making the worker degradation explicit in its canonical envelope.
            async def retrieval_worker(payload: WorkerInput) -> WorkerOutput:
                return WorkerOutput(
                    correlation_id=payload.correlation_id,
                    worker="evidence_retriever",
                    status="degraded",
                    artifact_refs=[],
                    citation_refs=[],
                    reason_code=None,
                )

        async def run_brief_pinned() -> BriefResult:
            return await self.run_brief(session, message, request_url=request_url)

        graph_kwargs = {
            "run_brief": run_brief_pinned,
            "correlation_id": correlation_id,
            "tracer": self.tracer,
            "accountability": self._accountability_context(
                session, token, request_url=request_url
            ),
            "worker_input": worker_input,
            "retrieval_worker": retrieval_worker,
            "ref_registry": refs,
        }
        if extraction_worker is not None:
            graph_kwargs["extraction_worker"] = extraction_worker
        return await orchestrator_graph.run_graph_turn(
            **graph_kwargs,
        )

    async def run_brief(
        self, session: Session, message: str, *, request_url: str
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

        def _record_fhir(name: str, latency_ms: float, result) -> None:
            # One accountability span per outbound FHIR read (CXR-05): resource, latency, and
            # tri-state outcome — so the trace localizes FHIR work and every PHI read is logged,
            # including timeouts/budget failures. Operational fields remain PHI-minimized; the
            # exact typed result is a D16-marked payload that exports only under the synthetic
            # deployment's explicit content opt-in.
            builder.step(
                f"fhir.{name}",
                latency_ms=latency_ms,
                status=result.status.value,
                records=len(result.records),
                missing_reason=result.missing_reason or "",
                content=_fhir_trace_content(result),
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
            packet, message, tools=ToolRegistry([]), builder=builder
        )
        # Attach the patient header (presentation-only, T-E9 UI) from the already-fetched Patient
        # record — the UI draws a chart header from it; verification/serving are untouched.
        return replace(result, patient=_patient_header(packet))
