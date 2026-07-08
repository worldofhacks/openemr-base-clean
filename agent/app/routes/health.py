"""/health (liveness) and /ready (readiness) routes (ARCHITECTURE.md §2, §7)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.health import Probe, run_readiness

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up. No dependency checks (§7)."""
    return {"status": "alive"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    """Readiness: real per-dependency probes; 503 if any hard dep is down (§7/§6)."""
    settings = request.app.state.settings
    checks: list[Probe] = request.app.state.readiness_checks
    report = await run_readiness(settings, checks)
    response.status_code = report.http_status
    return report.to_body()
