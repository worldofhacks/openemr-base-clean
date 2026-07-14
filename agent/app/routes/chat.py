"""POST /chat — the UC1 pre-visit brief over HTTP (ARCHITECTURE.md §3 UC1, §5, §5a).

This route is the whole serving path made reachable: a SMART-authed session resolves to a
delegated token → the six FHIR reads fan out → EvidencePacket → orchestrator → verify-then-
flush → the response carries ONLY the verified, re-rendered content (never raw model prose)
plus the correlation id. The route depends on an injected `services` object (set on
`app.state.services`) so it is testable without live OpenEMR / Anthropic.

W2_ARCHITECTURE.md §2a: behind the default-OFF `W2_GRAPH_ENABLED` flag, both JSON and SSE
turns run through the LangGraph entrypoint. `Accept: text/event-stream` changes only the
presentation of the verified result; non-streaming callers retain the exact W1 JSON
envelope. Flag OFF keeps the W1 direct path bit-identical and never invokes the graph.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.middleware.correlation import correlation_id_var

# Module-attribute access (not `from ... import run_graph_turn`): the graph entrypoint
# is resolved at REQUEST time, so a test/tool that patches
# `app.orchestrator.graph.run_graph_turn` (the AC-4 tripwire, the AC-6 spy) always
# governs this route, regardless of module import order.
from app.orchestrator import graph as orchestrator_graph
from app.orchestrator.composer import VerifiedComposition
from app.orchestrator.loop import BriefResult
from app.schemas.citations import CitationSourceType, CitationV2
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
    brief: str  # the verified, re-rendered content — never raw model prose
    source: str  # "llm" | "deterministic_fallback" | "deterministic_refusal"
    degraded: bool
    verdicts: list[str]  # per-claim verification verdicts (§5)
    # W1 evidence ids remain accepted while graph-composed W2 blocks carry canonical
    # CitationV2 objects in the same unchanged envelope key (\u00a72a migration rule).
    citations: list[str | CitationV2]
    patient: dict[str, str] | None = (
        None  # chart-header demographics (presentation-only, T-E9 UI)
    )
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
    async def run_brief(
        self, session: Session, message: str, *, request_url: str
    ) -> BriefResult: ...
    async def run_graph_turn(
        self, session: Session, message: str, *, request_url: str
    ) -> orchestrator_graph.GraphTurnResult: ...


def _wants_event_stream(request: Request) -> bool:
    """§2a SSE opt-in: content negotiation on the same POST /chat body. Non-opted
    callers (every W1 caller) keep the JSON contract regardless of the flag."""
    return "text/event-stream" in request.headers.get("accept", "")


def _sse_event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _block_verdict(result: BriefResult) -> str:
    """The verdict of the single streamed claim block — the composed, verified brief.
    Served claims are PASS/FLAGGED only (§5 verify-then-flush), so the block is
    "flagged" if any served claim was flagged, else "pass"; refusal/fallback paths
    carry their own verdict/source label."""
    served = [v for v in result.verdicts if v in ("pass", "flagged")]
    if served:
        return "flagged" if "flagged" in served else "pass"
    if result.verdicts:
        return result.verdicts[0]  # e.g. "refused:<kind>" on the refusal path
    return result.source


def _composition_brief(result: BriefResult, composition: VerifiedComposition) -> str:
    """Append only composer-verified W2 blocks with explicit source separation."""

    sections: list[str] = []
    labels = (
        (CitationSourceType.UPLOADED_DOCUMENT, "Uploaded document"),
        (CitationSourceType.GUIDELINE, "Guideline evidence"),
        (CitationSourceType.PATIENT_RECORD, "Patient record"),
    )
    for source, label in labels:
        claims = composition.for_source(source)
        if claims:
            sections.append(
                f"{label}:\n" + "\n".join(f"- {claim.text}" for claim in claims)
            )
    if not sections:
        return result.text
    return result.text.rstrip() + "\n\n" + "\n\n".join(sections)


def _composition_citations(
    composition: VerifiedComposition,
) -> list[CitationV2]:
    return [claim.citation for claim in composition.claims]


def _sse_stream(
    result: BriefResult,
    correlation_id: str,
    composition: VerifiedComposition = VerifiedComposition(),
) -> Iterator[str]:
    """The §2a stream, via the named V2-spike fallback: only the FINAL COMPOSER STAGE
    is streamed — one verified claim-block event, then the terminal `done` event. The
    W1 verify-then-flush gate holds ON THE STREAM: nothing is emitted until the brief
    is verified, so an unsupported claim can never appear as a streamed token."""
    yield _sse_event(
        "claim_block",
        {
            "claim_block": result.text,
            "citations": _citations_for(result),
            "verdict": _block_verdict(result),
        },
    )
    for claim in composition.claims:
        event = {
            "claim_block": claim.text,
            "citations": [claim.citation.model_dump(mode="json")],
            "verdict": "pass",
            "source_class": claim.source_class.value,
        }
        if claim.overlay is not None:
            event["overlay"] = {
                "source_id": claim.overlay.source_id,
                "page": claim.overlay.page,
                "bbox": claim.overlay.bbox.model_dump(mode="json"),
            }
        yield _sse_event("claim_block", event)
    yield _sse_event(
        "done",
        {
            "correlation_id": correlation_id,
            "source": result.source,
            "degraded": result.degraded,
        },
    )


async def _run_graph(
    services: ChatService, session: Session, message: str, *, request_url: str
) -> orchestrator_graph.GraphTurnResult:
    """Use the production composition-root seam while retaining M3 fake compatibility.

    Frozen graph-spike tests inject a minimal W1 service, so their fallback still calls the
    request-time module attribute (and remains patchable by the flag-off tripwire). Runtime
    ``AgentServices`` always supplies the fully wired method.
    """

    service_runner = getattr(services, "run_graph_turn", None)
    if service_runner is not None:
        return await service_runner(session, message, request_url=request_url)

    async def run_brief_pinned() -> BriefResult:
        return await services.run_brief(session, message, request_url=request_url)

    return await orchestrator_graph.run_graph_turn(
        run_brief=run_brief_pinned,
        correlation_id=correlation_id_var.get(),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse | StreamingResponse:
    services: ChatService = request.app.state.services
    try:
        session = await services.resolve_session(req.session_id)
    except SessionNotFound:
        raise HTTPException(
            status_code=404, detail="session not found — start a SMART launch"
        )
    except SessionExpiredError:
        raise HTTPException(
            status_code=401, detail="session expired — re-launch the co-pilot"
        )
    except SessionStoreUnavailable:
        # Fail-closed (§6): never serve unpinned when the pin store is unreachable.
        raise HTTPException(
            status_code=503, detail="session store unavailable — refusing to serve"
        )

    # The session IS the patient (the pin). A caller naming a different patient is refused (F-S.2).
    if req.patient_id is not None:
        try:
            session.authorize_patient(req.patient_id)
        except CrossPatientError:
            raise HTTPException(
                status_code=403,
                detail="cross-patient request refused — a patient switch needs a fresh launch",
            )

    composition = VerifiedComposition()
    if orchestrator_graph.graph_enabled():
        # W2-D2 flag-ON path: JSON and SSE share one graph result. Session pin and refusal
        # mappings above remain unchanged — the graph changes routing, never authZ.
        request_url = str(request.url)
        graph_result = await _run_graph(
            services, session, req.message, request_url=request_url
        )
        result = graph_result.brief
        composition = graph_result.composition
        if _wants_event_stream(request):
            return StreamingResponse(
                _sse_stream(result, correlation_id_var.get(), composition),
                media_type="text/event-stream",
            )
    else:
        result = await services.run_brief(
            session, req.message, request_url=str(request.url)
        )

    return ChatResponse(
        brief=_composition_brief(result, composition),
        source=result.source,
        degraded=result.degraded,
        verdicts=list(result.verdicts),
        citations=[*_citations_for(result), *_composition_citations(composition)],
        patient=result.patient,
        correlation_id=correlation_id_var.get(),
    )
