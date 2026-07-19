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
from collections.abc import Iterator
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_validator,
    model_serializer,
)

from app.logging import get_logger
from app.middleware.correlation import correlation_id_var

# Module-attribute access (not `from ... import run_graph_turn`): the graph entrypoint
# is resolved at REQUEST time, so a test/tool that patches
# `app.orchestrator.graph.run_graph_turn` (the AC-4 tripwire, the AC-6 spy) always
# governs this route, regardless of module import order.
from app.orchestrator import graph as orchestrator_graph
from app.orchestrator.composer import VerifiedComposition
from app.orchestrator.loop import BriefResult
from app.routes.openapi_contract import documented_errors, documented_response
from app.schemas.answers import CitationOverlay, ResponseClaim
from app.schemas.citations import CitationSourceType, CitationV2
from app.session.store import (
    CrossPatientError,
    Session,
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)

router = APIRouter()
_log = get_logger("agent.routes.chat")

MAX_CHAT_MESSAGE_CHARS = 4_000
MAX_CHAT_MESSAGE_BYTES = 12_000

_SSE_EVENT_SCHEMAS: dict[str, object] = {
    "claim_block": {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_block", "citations", "verdict"],
        "properties": {
            "claim_block": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/CitationV2"},
            },
            "verdict": {"type": "string"},
            "claims": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/ResponseClaim"},
            },
            "source_class": {
                "anyOf": [
                    {"$ref": "#/components/schemas/CitationSourceType"},
                    {"type": "null"},
                ]
            },
            "overlay": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["source_id", "page", "bbox"],
                        "properties": {
                            "source_id": {"type": "string", "minLength": 1},
                            "page": {"type": "integer", "minimum": 1},
                            "bbox": {"$ref": "#/components/schemas/NormBBox"},
                        },
                    },
                    {"type": "null"},
                ]
            },
        },
    },
    "done": {
        "type": "object",
        "additionalProperties": False,
        "required": ["correlation_id", "source", "degraded"],
        "properties": {
            "correlation_id": {"type": "string", "minLength": 1},
            "source": {
                "type": "string",
                "enum": [
                    "llm",
                    "deterministic_fallback",
                    "deterministic_refusal",
                ],
            },
            "degraded": {"type": "boolean"},
        },
    },
}


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(
        default="Give me the pre-visit brief.",
        min_length=1,
        max_length=MAX_CHAT_MESSAGE_CHARS,
    )
    # Optional defence-in-depth: if the caller names a patient it must match the session pin.
    patient_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_fits_utf8_budget(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_CHAT_MESSAGE_BYTES:
            raise ValueError("message exceeds the UTF-8 request budget")
        return value


class ChatResponse(BaseModel):
    """The /chat JSON envelope.

    ``claims`` is the AUTHORITATIVE machine-readable lane (AF-P0-03; W2-REQ-27/98):
    one entry per served W2 clinical claim, each owning exactly its CitationV2 set.
    It is present iff at least one composed claim was served, which keeps the frozen
    W1 envelope bit-identical on paths without a composition (AC-4/AC-6).

    ``brief`` and ``citations`` are DERIVED, non-authoritative compatibility fields:
    a display projection and a flattened de-duplicated union of the same citations.
    Machine consumers must read claim→citation association from ``claims`` only.
    """

    brief: str  # DERIVED display text — verified, re-rendered; never raw model prose
    source: str  # "llm" | "deterministic_fallback" | "deterministic_refusal"
    degraded: bool
    verdicts: list[str]  # per-claim verification verdicts (§5)
    citations: list[CitationV2]  # DERIVED flat compatibility list (non-authoritative)
    claims: list[ResponseClaim] = Field(default_factory=list)
    patient: dict[str, str] | None = (
        None  # chart-header demographics (presentation-only, T-E9 UI)
    )
    correlation_id: str

    @model_serializer(mode="wrap")
    def _omit_empty_claims_lane(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        """Serialize `claims` only when a composed claim was served (W1 bit-identity)."""
        data = handler(self)
        if isinstance(data, dict) and not data.get("claims"):
            data.pop("claims", None)
        return data


class ClaimContractViolation(ValueError):
    """A served claim lost its citation or its source-class assignment (fail closed)."""


def _response_claims(composition: VerifiedComposition) -> list[ResponseClaim]:
    """Map the composed per-claim structure to the public claims[] contract.

    One entry per rendered claim, carrying exactly its CitationV2 set (currently one
    citation per composed claim — the composer's one-claim/one-citation invariant) and
    the optional click-to-source overlay.  Any structural violation — an uncited claim,
    a citation from a different source class, an overlay for an uncited document —
    raises ``ClaimContractViolation`` so the route refuses to serve (REQ-27 fail-closed)
    instead of presenting an uncited or ambiguous claim as fact.
    """

    claims: list[ResponseClaim] = []
    for claim in composition.claims:
        if claim.citation is None:
            raise ClaimContractViolation("composed claim has zero citations")
        try:
            overlay = None
            if claim.overlay is not None:
                overlay = CitationOverlay(
                    source_id=claim.overlay.source_id,
                    page=claim.overlay.page,
                    bbox=claim.overlay.bbox,
                )
            claims.append(
                ResponseClaim(
                    text=claim.text,
                    source_class=claim.source_class,
                    # Composed claims passed the final render gate; the per-claim SSE
                    # lane serves the same constant verdict (§5 verify-then-flush).
                    verdict="pass",
                    citations=[claim.citation],
                    overlay=overlay,
                )
            )
        except ValidationError as exc:
            raise ClaimContractViolation(
                "composed claim violates the citation contract"
            ) from exc
    return claims


def _claims_or_refuse(composition: VerifiedComposition) -> list[ResponseClaim]:
    try:
        return _response_claims(composition)
    except ClaimContractViolation:
        _log.warning(
            "chat_claims_refusal",
            extra={"reason_code": "claim_contract_violation"},
        )
        raise HTTPException(
            status_code=503,
            detail="claim citation contract failed — refusing to serve",
        )


def _citations_for(result: BriefResult) -> list[CitationV2]:
    """The HTTP boundary is CitationV2-only; legacy strings are never serialized."""

    return [citation for citation in result.citations if isinstance(citation, CitationV2)]


def _dedupe_citations(citations: list[CitationV2]) -> list[CitationV2]:
    seen: set[tuple[object, ...]] = set()
    result: list[CitationV2] = []
    for citation in citations:
        key = (
            citation.source_type,
            citation.source_id,
            citation.page_or_section,
            citation.field_or_chunk_id,
            citation.quote_or_value,
        )
        if key not in seen:
            seen.add(key)
            result.append(citation)
    return result


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
    claims: list[ResponseClaim] | None = None,
) -> Iterator[str]:
    """The §2a stream, via the named V2-spike fallback: only the FINAL COMPOSER STAGE
    is streamed — one verified claim-block event, then the terminal `done` event. The
    W1 verify-then-flush gate holds ON THE STREAM: nothing is emitted until the brief
    is verified, so an unsupported claim can never appear as a streamed token.

    The initial block carries the AUTHORITATIVE ``claims`` lane (when non-empty),
    serialized identically to the JSON envelope's ``claims`` field (AF-P0-03)."""
    initial: dict[str, object] = {
        "claim_block": result.text,
        "citations": [
            citation.model_dump(mode="json")
            for citation in _citations_for(result)
        ],
        "verdict": _block_verdict(result),
    }
    if claims:
        initial["claims"] = [claim.model_dump(mode="json") for claim in claims]
    yield _sse_event("claim_block", initial)
    for claim in composition.claims:
        event: dict[str, object] = {
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


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        200: documented_response(
            "Verified JSON or SSE answer; no clinical bytes flush before verification.",
            content={
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "x-event-schemas": _SSE_EVENT_SCHEMAS,
                }
            },
        ),
        **documented_errors(401, 403, 404, 413, 422, 503),
    },
)
async def chat(req: ChatRequest, request: Request) -> ChatResponse | StreamingResponse:
    services: ChatService = request.app.state.services
    try:
        session = await services.resolve_session(req.session_id)
    except SessionNotFound:
        _log.warning("chat_pin_refusal", extra={"reason_code": "pin_not_found"})
        raise HTTPException(
            status_code=404, detail="session not found — start a SMART launch"
        )
    except SessionExpiredError:
        _log.warning("chat_pin_refusal", extra={"reason_code": "pin_expired"})
        raise HTTPException(
            status_code=401, detail="session expired — re-launch the co-pilot"
        )
    except SessionStoreUnavailable:
        # Fail-closed (§6): never serve unpinned when the pin store is unreachable.
        _log.warning(
            "chat_pin_refusal", extra={"reason_code": "pin_store_unavailable"}
        )
        raise HTTPException(
            status_code=503, detail="session store unavailable — refusing to serve"
        )

    if not isinstance(session.patient_id, str) or not session.patient_id.strip():
        _log.warning("chat_pin_refusal", extra={"reason_code": "pin_missing"})
        raise HTTPException(
            status_code=401,
            detail="session has no pinned patient — start a fresh SMART launch",
        )

    # The session IS the patient (the pin). A caller naming a different patient is refused (F-S.2).
    if req.patient_id is not None:
        try:
            session.authorize_patient(req.patient_id)
        except CrossPatientError:
            _log.warning("chat_pin_refusal", extra={"reason_code": "pin_mismatch"})
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
        # AF-P0-03: build the authoritative per-claim lane BEFORE any byte is flushed —
        # an uncited or ambiguously cited composed claim refuses the whole turn (503),
        # on the SSE stream exactly as on the JSON envelope.
        claims = _claims_or_refuse(composition)
        if _wants_event_stream(request):
            return StreamingResponse(
                _sse_stream(result, correlation_id_var.get(), composition, claims),
                media_type="text/event-stream",
            )
    else:
        result = await services.run_brief(
            session, req.message, request_url=str(request.url)
        )
        claims = _claims_or_refuse(composition)

    return ChatResponse(
        brief=_composition_brief(result, composition),
        source=result.source,
        degraded=result.degraded,
        verdicts=list(result.verdicts),
        citations=_dedupe_citations(
            [*_citations_for(result), *_composition_citations(composition)]
        ),
        claims=claims,
        patient=result.patient,
        correlation_id=correlation_id_var.get(),
    )
