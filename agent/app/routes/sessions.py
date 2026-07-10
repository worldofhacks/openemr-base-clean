"""SMART launch → pinned session (ARCHITECTURE.md §4, §5a, D2, D9, D12).

`GET /launch` starts the authorization_code + PKCE(S256) flow (EHR launch when a `launch`
token is present) and redirects the browser to OpenEMR's authorize endpoint. `GET /callback`
receives the code, exchanges it for a delegated token, and creates a session pinned to the
launched (clinician, patient) — returning the `session_id` the client then passes to /chat.
The token never leaves the server; only the opaque session id is handed back.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.smart_client import CoPilotNotEnabledError, SmartAuthError

router = APIRouter()


class SessionCreated(BaseModel):
    session_id: str
    patient_id: str


@router.get("/launch")
async def launch(request: Request, launch: str | None = None, iss: str | None = None) -> RedirectResponse:
    services = request.app.state.services
    authorize_url = services.begin_launch(launch=launch)
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/callback", response_model=SessionCreated)
async def callback(request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None) -> SessionCreated:
    if error:
        raise HTTPException(status_code=400, detail=f"authorization failed: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state on callback")
    services = request.app.state.services
    try:
        session = await services.complete_callback(code=code, state=state)
    except CoPilotNotEnabledError:
        # Disabled SMART client (D14) — explicit, not a hang (§6).
        raise HTTPException(status_code=403, detail="co-pilot OAuth client is not enabled")
    except (SmartAuthError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"could not complete the launch: {exc}")
    return SessionCreated(session_id=session.session_id, patient_id=session.patient_id)
