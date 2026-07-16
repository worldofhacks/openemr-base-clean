"""Correlation-ID middleware (ARCHITECTURE.md §3.1, §7, D10-rev).

Every request carries a correlation id: the inbound `X-Copilot-Request-Id` header
is honored only when it matches the bounded log-safe format, otherwise one is
minted. The id is stored in a context variable for the duration of the request so
log lines pick it up, is echoed on the response, and is exposed via
`outbound_headers()` for the FHIR client (E3) to attach to every outbound call —
which is how the agent's Langfuse trace joins across the service boundary (D10-rev;
there is no hard OpenEMR api_log join, F-C.2).

Note: E2 mints the id at SMART launch and threads it through the session; until
then this middleware guarantees every request has one.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging import get_logger

HEADER_NAME = "X-Copilot-Request-Id"
_HEADER_BYTES = HEADER_NAME.lower().encode()
_SAFE_CORRELATION_ID = re.compile(rb"[A-Za-z0-9_.:-]{1,128}\Z")

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")

_log = get_logger("agent.request")


def _route_label(path: str) -> str:
    """Collapse dynamic path parameters to a closed, identifier-free label."""

    if path in {"/", "/health", "/ready"}:
        return {"/": "root", "/health": "health", "/ready": "ready"}[path]
    if path in {"/launch", "/week2/launch", "/callback"}:
        return "smart"
    for prefix, label in (
        ("/documents", "documents"),
        ("/evidence", "evidence"),
        ("/chat", "chat"),
        ("/week2", "week2_ui"),
        ("/app", "week1_ui"),
    ):
        if path.startswith(prefix):
            return label
    return "other"


def outbound_headers() -> dict[str, str]:
    """Headers to attach to outbound FHIR/LLM calls so the correlation id crosses
    the service boundary (§3.1, D10-rev)."""
    return {HEADER_NAME: correlation_id_var.get()}


def _inbound_id(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key == _HEADER_BYTES and _SAFE_CORRELATION_ID.fullmatch(value):
            return value.decode("ascii")
    return uuid.uuid4().hex


class CorrelationIdMiddleware:
    """Pure-ASGI middleware: sets the correlation id, echoes it on the response,
    and logs request start/completion as structured JSON (no PHI in the message)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = _inbound_id(scope)
        token = correlation_id_var.set(correlation_id)
        # Route labels are closed and omit dynamic path/query/body identifiers (§7).
        route = _route_label(str(scope.get("path", "")))
        _log.info(
            "request_start",
            extra={"method": scope.get("method"), "route": route},
        )

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((_HEADER_BYTES, correlation_id.encode()))
                _log.info(
                    "request_complete",
                    extra={"status": message["status"], "route": route},
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            correlation_id_var.reset(token)
