"""POST /chat — the UC1 pre-visit brief over HTTP (ARCHITECTURE.md §3 UC1, §5, §5a).

This route is the whole serving path made reachable: a SMART-authed session resolves to a
delegated token → the six FHIR reads fan out → EvidencePacket → orchestrator → verify-then-
flush → the response carries ONLY the verified, re-rendered content (never raw model prose)
plus the correlation id. The route depends on an injected `services` object (set on
`app.state.services`) so it is testable without live OpenEMR / Anthropic.
"""

from __future__ import annotations

import re
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.middleware.correlation import correlation_id_var
from app.orchestrator.loop import BriefResult
from app.session.store import (
    CrossPatientError,
    Session,
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(default="Give me the pre-visit brief.", min_length=1)
    # Optional defence-in-depth: if the caller names a patient it must match the session pin.
    patient_id: str | None = None


class ChatResponse(BaseModel):
    brief: str                 # the verified, re-rendered content — never raw model prose
    source: str                # "llm" | "deterministic_fallback" | "deterministic_refusal"
    degraded: bool
    verdicts: list[str]        # per-claim verification verdicts (§5)
    citations: list[str]       # evidence ids backing the served lines (presentation-only, T-E9 UI)
    patient: dict[str, str] | None = None   # chart-header demographics (presentation-only, T-E9 UI)
    correlation_id: str


# The deterministic fallback render carries inline [ResourceType:id:hash8] tokens; extract them
# so the UI can show citation chips on that path too (the verified path plumbs them explicitly).
_INLINE_CITATION = re.compile(r"\[([A-Za-z]+:[^\]\[]+:[0-9a-f]{8})\]")


def _citations_for(result: BriefResult) -> list[str]:
    if result.citations:
        return list(result.citations)
    seen: list[str] = []
    for eid in _INLINE_CITATION.findall(result.text):
        if eid not in seen:
            seen.append(eid)
    return seen


class ChatService(Protocol):
    async def resolve_session(self, session_id: str) -> Session: ...
    async def run_brief(self, session: Session, message: str, *, request_url: str) -> BriefResult: ...


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    services: ChatService = request.app.state.services
    try:
        session = await services.resolve_session(req.session_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found — start a SMART launch")
    except SessionExpiredError:
        raise HTTPException(status_code=401, detail="session expired — re-launch the co-pilot")
    except SessionStoreUnavailable:
        # Fail-closed (§6): never serve unpinned when the pin store is unreachable.
        raise HTTPException(status_code=503, detail="session store unavailable — refusing to serve")

    # The session IS the patient (the pin). A caller naming a different patient is refused (F-S.2).
    if req.patient_id is not None:
        try:
            session.authorize_patient(req.patient_id)
        except CrossPatientError:
            raise HTTPException(status_code=403,
                                detail="cross-patient request refused — a patient switch needs a fresh launch")

    result = await services.run_brief(session, req.message, request_url=str(request.url))
    return ChatResponse(
        brief=result.text,
        source=result.source,
        degraded=result.degraded,
        verdicts=list(result.verdicts),
        citations=_citations_for(result),
        patient=result.patient,
        correlation_id=correlation_id_var.get(),
    )
