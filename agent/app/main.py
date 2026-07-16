"""Agent service entry point (ARCHITECTURE.md §2).

FastAPI application factory. Settings are validated at construction (fail-fast,
E1.1). Health/readiness routes (E1.2) and correlation-ID + logging middleware
(E1.3) are attached here so observability is wired from the first boot, not
retrofitted (§7).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.health import CachedReadinessRunner, Probe, default_readiness_checks
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.request_limits import RequestBodyLimitMiddleware
from app.middleware.sensitive_responses import SensitiveResponseHeadersMiddleware
from app.routes.chat import router as chat_router
from app.routes.documents import router as documents_router
from app.routes.evidence import router as evidence_router
from app.routes.health import router as health_router
from app.routes.sessions import router as sessions_router
from app.routes.ui import router as ui_router
from app.routes.week2_ui import router as week2_ui_router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # At boot, let the composition root prepare itself — ensures the Postgres session-store
    # schema (idempotent, best-effort; CXR-07/§3a). A fake service (route tests) has no
    # `startup` and is skipped; a DB-down boot is absorbed there, never crashing the app.
    startup = getattr(getattr(app.state, "services", None), "startup", None)
    if startup is not None:
        await startup()
    try:
        yield
    finally:
        shutdown = getattr(getattr(app.state, "services", None), "shutdown", None)
        if shutdown is not None:
            await shutdown()


def create_app(
    settings: Settings | None = None,
    readiness_checks: list[Probe] | None = None,
    services: object | None = None,
) -> FastAPI:
    # Validate configuration now — a missing secret fails here, at boot, not at
    # request time (E1.1 / §2).
    settings = settings or get_settings()

    app = FastAPI(
        title="Clinical Co-Pilot Agent",
        version="0.1.0",
        lifespan=_lifespan,
        # PHI is never in URLs or query strings; docs stay on for the API collection.
    )
    app.state.settings = settings
    # Readiness probes are injectable so tests exercise the hard/soft classification
    # without real network (E1.2 / §7).
    app.state.readiness_checks = (
        readiness_checks if readiness_checks is not None else default_readiness_checks()
    )
    app.state.readiness_runner = CachedReadinessRunner()
    # Serving-path services (composition root, D-DI). Injectable so routes are testable
    # without live OpenEMR / Anthropic; built from config otherwise.
    if services is None:
        from app.service import AgentServices

        services = AgentServices(settings)
    app.state.services = services
    # W2 §3: document enablement is a hard readiness dependency. The service-owned
    # probe verifies schema, credential crypto/store, and a fresh *dedicated worker*
    # heartbeat. Injected fakes without this capability keep the W1 test seam unchanged.
    document_runtime_probe = getattr(services, "probe_document_runtime", None)
    if document_runtime_probe is not None:
        app.state.readiness_checks.append(document_runtime_probe)
    document_category_probe = getattr(services, "probe_document_category_read", None)
    if document_category_probe is not None:
        app.state.readiness_checks.append(document_category_probe)
    # One lazy retrieval instance is shared by the graph worker and the public evidence
    # endpoint (W2-D4). Merely constructing the app never loads an ONNX model or vendor client.
    evidence_retriever_factory = getattr(services, "get_evidence_retriever", None)
    if evidence_retriever_factory is not None:
        app.state.evidence_retriever_factory = evidence_retriever_factory

    # Observability first (§7): correlation id on every request, from boot.
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(SensitiveResponseHeadersMiddleware)

    app.include_router(health_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(documents_router)
    app.include_router(evidence_router)
    app.include_router(ui_router)
    app.include_router(week2_ui_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "clinical-copilot-agent", "status": "ok"}

    return app


# Uvicorn entry point: `uvicorn app.main:app`
app = create_app()
