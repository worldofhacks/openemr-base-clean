"""Agent service entry point (ARCHITECTURE.md §2).

FastAPI application factory. Settings are validated at construction (fail-fast,
E1.1). Health/readiness routes (E1.2) and correlation-ID + logging middleware
(E1.3) are attached here so observability is wired from the first boot, not
retrofitted (§7).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.health import Probe, default_readiness_checks
from app.logging import configure_logging
from app.middleware.correlation import CorrelationIdMiddleware
from app.routes.chat import router as chat_router
from app.routes.health import router as health_router
from app.routes.sessions import router as sessions_router


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
        # PHI is never in URLs or query strings; docs stay on for the API collection.
    )
    app.state.settings = settings
    # Readiness probes are injectable so tests exercise the hard/soft classification
    # without real network (E1.2 / §7).
    app.state.readiness_checks = (
        readiness_checks if readiness_checks is not None else default_readiness_checks()
    )
    # Serving-path services (composition root, D-DI). Injectable so routes are testable
    # without live OpenEMR / Anthropic; built from config otherwise.
    if services is None:
        from app.service import AgentServices
        services = AgentServices(settings)
    app.state.services = services

    # Observability first (§7): correlation id on every request, from boot.
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "clinical-copilot-agent", "status": "ok"}

    return app


# Uvicorn entry point: `uvicorn app.main:app`
app = create_app()
