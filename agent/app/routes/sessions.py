"""SMART launch → pinned session (ARCHITECTURE.md §4, §5a, D2, D9, D12).

`GET /launch` starts the authorization_code + PKCE(S256) flow (EHR launch when a `launch`
token is present) and redirects the browser to OpenEMR's authorize endpoint. `GET /callback`
receives the code, exchanges it for a delegated token, and creates a session pinned to the
launched (clinician, patient) — returning the `session_id` the client then passes to /chat.
The token never leaves the server; only the opaque session id is handed back.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.scopes import ScopeCoverageError
from app.auth.smart_client import CoPilotNotEnabledError, SmartAuthError
from app.routes.openapi_contract import documented_errors, documented_response
from app.service import LaunchRateLimited

router = APIRouter()


class SessionCreated(BaseModel):
    session_id: str
    patient_id: str


@router.get(
    "/launch",
    status_code=302,
    response_class=RedirectResponse,
    responses={
        302: documented_response(
            "Redirect to the trusted SMART authorization origin.", location=True
        ),
        **documented_errors(422, 429),
    },
)
async def launch(
    request: Request, launch: str | None = None, iss: str | None = None
) -> RedirectResponse:
    services = request.app.state.services
    try:
        authorize_url = services.begin_launch(launch=launch, destination="week1")
    except LaunchRateLimited:
        raise HTTPException(
            status_code=429,
            detail="SMART launch rate limit exceeded",
            headers={"Retry-After": "60"},
        ) from None
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get(
    "/week2/launch",
    status_code=302,
    response_class=RedirectResponse,
    responses={
        302: documented_response(
            "Redirect to the trusted SMART authorization origin.", location=True
        ),
        **documented_errors(422, 429, 503),
    },
)
async def week2_launch(
    request: Request, launch: str | None = None, iss: str | None = None
) -> RedirectResponse:
    """Start the separate Week 2 SMART flow; the callback target stays server-owned."""

    services = request.app.state.services
    try:
        authorize_url = services.begin_launch(launch=launch, destination="week2")
    except LaunchRateLimited:
        raise HTTPException(
            status_code=429,
            detail="SMART launch rate limit exceeded",
            headers={"Retry-After": "60"},
        ) from None
    except RuntimeError:
        raise HTTPException(
            status_code=503, detail="Week 2 document runtime is not enabled"
        ) from None
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get(
    "/callback",
    status_code=302,
    response_class=RedirectResponse,
    responses={
        302: documented_response(
            "Redirect to the server-owned UI for the completed launch.", location=True
        ),
        **documented_errors(400, 403, 422),
    },
)
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail="authorization failed")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state on callback")
    services = request.app.state.services
    try:
        routed_callback = getattr(
            services, "complete_callback_with_destination", None
        )
        if routed_callback is None:
            session = await services.complete_callback(code=code, state=state)
            destination = "week1"
        else:
            session, destination = await routed_callback(code=code, state=state)
    except CoPilotNotEnabledError:
        # Disabled SMART client (D14) — explicit, not a hang (§6).
        raise HTTPException(status_code=403, detail="co-pilot OAuth client is not enabled")
    except ScopeCoverageError:
        raise HTTPException(
            status_code=403,
            detail=(
                "SMART authorization did not grant the exact required scopes; "
                "correct the client permissions and launch again"
            ),
        ) from None
    except (SmartAuthError, ValueError):
        raise HTTPException(status_code=400, detail="could not complete the launch") from None
    # Only a closed server-side mapping controls the destination. The token stays server-side;
    # only the opaque session id rides the redirect.
    targets = {"week1": "/app", "week2": "/week2"}
    target = targets.get(destination)
    if target is None:
        raise HTTPException(status_code=400, detail="invalid SMART launch destination")
    return RedirectResponse(
        url=f"{target}?sid={quote(session.session_id)}", status_code=302
    )
