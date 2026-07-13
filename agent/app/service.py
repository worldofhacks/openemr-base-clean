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

import secrets
from dataclasses import replace
from datetime import datetime, timezone

from app.auth.scopes import requested_scope_string
from app.auth.smart_client import SmartClient, TokenResponse, generate_pkce
from app.config import Settings
from app.evidence.packet import build_evidence_packet
from app.llm.cost import DailyCostCap
from app.llm.provider import AnthropicLLMProvider
from app.middleware.correlation import correlation_id_var
from app.observability.langfuse import LangfuseSink, NullTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator.loop import BriefResult, Orchestrator, ToolRegistry
from app.session.store import InMemorySessionStore, Session
from app.tools.fhir_client import FhirClient
from app.tools.fhir_tools import run_previsit_fanout


def _build_tracer(settings: Settings) -> RequestTracer:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        sink = LangfuseSink(
            host=str(settings.langfuse_host) if settings.langfuse_host else None,
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value())
    else:
        sink = NullTraceSink()  # observability optional (§6 soft dep) — serving is unaffected
    return RequestTracer(sink)


def _patient_header(packet) -> dict[str, str] | None:
    """Presentation-only chart header (T-E9 UI): name/gender/birth_date from the packet's Patient
    record (age is computed client-side). None if no Patient record was returned."""
    records = packet.by_type("Patient")
    if not records:
        return None
    fields = records[0].fields
    header = {k: str(fields[k]) for k in ("name", "gender", "birth_date") if fields.get(k)}
    return header or None


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
        self.sessions = InMemorySessionStore(
            idle_timeout_s=settings.session_idle_timeout_seconds, turn_cap=settings.session_turn_cap)
        self._tokens: dict[str, TokenResponse] = {}   # per-session delegated-token cache (§2)
        self._pkce: dict[str, str] = {}               # oauth state → PKCE code_verifier
        self.provider = AnthropicLLMProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model, max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds)
        self.cost_cap = DailyCostCap(cap_usd=settings.daily_cost_cap_usd)
        self.tracer = _build_tracer(settings)
        self.orchestrator = Orchestrator(self.provider, cost_cap=self.cost_cap,
                                         max_tool_iterations=settings.llm_max_tool_iterations)

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
            state=state, code_challenge=challenge, scope=scope, launch=launch)

    async def complete_callback(self, *, code: str, state: str) -> Session:
        """Exchange the code (PKCE) and create a session pinned to (clinician, launched patient)."""
        verifier = self._pkce.pop(state, None)
        if verifier is None:
            raise ValueError("unknown or replayed OAuth state")
        token = await self.smart.exchange_code(code=code, code_verifier=verifier)
        patient_id = token.patient or ""
        if not patient_id:
            raise ValueError("no launch/patient context in the token — cannot pin a session")
        # Provider attribution (D9/D5): pin the session to the REAL launching clinician decoded
        # from the token's id_token (fhirUser preferred, else sub), falling back to the demo
        # placeholder only when the token carried no decodable id_token.
        session = await self.sessions.create(
            clinician_sub=token.clinician_sub or "openemr-clinician", patient_id=patient_id,
            token_expires_at=self._token_deadline())
        self._tokens[session.session_id] = token
        return session

    def _token_deadline(self) -> datetime:
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(seconds=self.settings.token_lifetime_seconds)

    # --- the two operations the routes call -----------------------------------

    async def resolve_session(self, session_id: str) -> Session:
        return await self.sessions.get(session_id)

    async def run_brief(self, session: Session, message: str, *, request_url: str) -> BriefResult:
        token = self._tokens.get(session.session_id)
        if token is None:
            raise ValueError("no delegated token cached for this session — re-launch")
        client = FhirClient(
            base_url=str(self.settings.openemr_fhir_base_url).rstrip("/"),
            access_token=token.access_token.get_secret_value(),
            per_call_timeout=self.settings.fhir_per_call_timeout_seconds)
        # Begin the accountable trace BEFORE the FHIR fan-out (CXR-05/§7): all accountability is
        # already known (client, exercised scopes, pinned clinician + patient), so the six PHI
        # reads can each be captured as a span. Tracing is a soft dependency (§6) — begun always
        # (a NullTraceSink discards when Langfuse is unconfigured), never affecting serving.
        accountability = AccountabilityContext(
            correlation_id=correlation_id_var.get(),
            client_id=self.settings.smart_client_id,
            exercised_scopes=tuple(token.scopes),
            request_url=request_url,
            user_id=session.clinician_sub,
            patient_id=session.patient_id,
            utc_timestamp=datetime.now(timezone.utc).isoformat())
        builder = self.tracer.begin(accountability)

        def _record_fhir(name: str, latency_ms: float, result) -> None:
            # One accountability span per outbound FHIR read (CXR-05): resource, latency, and
            # tri-state outcome — so the trace localizes FHIR work and every PHI read is logged,
            # including timeouts/budget failures. No resource id is emitted (D5 PHI-minimization).
            builder.step(f"fhir.{name}", latency_ms=latency_ms,
                         status=result.status.value, records=len(result.records),
                         missing_reason=result.missing_reason or "")

        fanout = await run_previsit_fanout(
            client, session.patient_id,
            per_call_timeout=self.settings.fhir_per_call_timeout_seconds,
            turn_budget=self.settings.turn_total_budget_seconds,
            on_call=_record_fhir)
        packet = build_evidence_packet(session.patient_id, fanout)
        await self.sessions.record_turn(session.session_id)
        # UC1: the packet is pre-built deterministically (D10); the LLM narrates it in typed
        # claims (empty tool registry → only submit_claims), which are verified and re-rendered.
        # The trace begun above is threaded in so the LLM/verify spans join the FHIR spans.
        result = await self.orchestrator.run_previsit_brief(
            packet, message, tools=ToolRegistry([]), builder=builder)
        # Attach the patient header (presentation-only, T-E9 UI) from the already-fetched Patient
        # record — the UI draws a chart header from it; verification/serving are untouched.
        return replace(result, patient=_patient_header(packet))
