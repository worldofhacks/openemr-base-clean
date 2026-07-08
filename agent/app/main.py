"""Agent service entry point (ARCHITECTURE.md §2).

FastAPI application factory. Settings are validated at construction (fail-fast,
E1.1). Health/readiness routes (E1.2) and correlation-ID + logging middleware
(E1.3) are attached here so observability is wired from the first boot, not
retrofitted (§7).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    # Validate configuration now — a missing secret fails here, at boot, not at
    # request time (E1.1 / §2).
    settings = settings or get_settings()

    app = FastAPI(
        title="Clinical Co-Pilot Agent",
        version="0.1.0",
        # PHI is never in URLs or query strings; docs stay on for the API collection.
    )
    app.state.settings = settings

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "clinical-copilot-agent", "status": "ok"}

    return app


# Uvicorn entry point: `uvicorn app.main:app`
app = create_app()
