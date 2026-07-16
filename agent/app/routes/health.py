"""/health (liveness) and /ready (readiness) routes (ARCHITECTURE.md §2, §7)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.health import CachedReadinessRunner, Probe
from app.routes.openapi_contract import documented_response

router = APIRouter()


class HealthResponse(BaseModel):
    """Bounded liveness contract; it intentionally contains no dependency detail."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["alive"]
    sha: str = Field(min_length=1)


class ReadinessCheck(BaseModel):
    """One content-free readiness probe result."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["hard", "soft"]
    ok: bool
    detail: str


class ReadinessResponse(BaseModel):
    """Hard/soft readiness aggregate shared by HTTP 200 and HTTP 503."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "degraded", "not_ready"]
    checks: list[ReadinessCheck]


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        200: documented_response("Process liveness and deployed SHA."),
    },
)
def health(request: Request) -> dict[str, str]:
    """Liveness: the process is up. No dependency checks (§7)."""
    return {
        "status": "alive",
        "sha": request.app.state.settings.deployment_sha,
    }


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        200: documented_response(
            "All hard probes are healthy; soft probes may be degraded."
        ),
        503: documented_response(
            "At least one hard readiness probe failed.",
            model=ReadinessResponse,
        ),
    },
)
async def ready(request: Request, response: Response) -> dict:
    """Readiness: real per-dependency probes; 503 if any hard dep is down (§7/§6)."""
    settings = request.app.state.settings
    checks: list[Probe] = request.app.state.readiness_checks
    runner: CachedReadinessRunner = request.app.state.readiness_runner
    report = await runner.run(settings, checks)
    response.status_code = report.http_status
    return report.to_body()
