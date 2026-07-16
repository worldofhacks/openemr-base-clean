"""ASGI request-body admission limits before JSON/multipart parsing."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CHAT_BODY_BYTES = 32 * 1024
_EVIDENCE_BODY_BYTES = 32 * 1024
_DOCUMENT_UPLOAD_BODY_BYTES = 11 * 1024 * 1024
_SMALL_MUTATION_BODY_BYTES = 256 * 1024


class _RequestTooLarge(Exception):
    pass


def _body_limit(scope: Scope) -> int | None:
    if scope.get("method") not in {"POST", "PUT", "PATCH"}:
        return None
    path = scope.get("path", "")
    if path == "/chat":
        return _CHAT_BODY_BYTES
    if path == "/evidence/search":
        return _EVIDENCE_BODY_BYTES
    if path == "/documents":
        return _DOCUMENT_UPLOAD_BODY_BYTES
    return _SMALL_MUTATION_BODY_BYTES


class RequestBodyLimitMiddleware:
    """Reject oversized declared or chunked bodies without echoing request data."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (limit := _body_limit(scope)) is None:
            await self.app(scope, receive, send)
            return

        declared_lengths: list[int] = []
        try:
            for name, value in scope.get("headers", []):
                if name.lower() == b"content-length":
                    declared_lengths.append(int(value))
        except (TypeError, ValueError):
            declared_lengths = [limit + 1]
        if (
            len(declared_lengths) > 1
            or any(length < 0 or length > limit for length in declared_lengths)
        ):
            await self._reject(scope, receive, send)
            return

        received = 0
        response_started = False

        async def receive_limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestTooLarge
            return message

        async def track_response(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive_limited, track_response)
        except _RequestTooLarge:
            if response_started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "request body exceeds the accepted size"},
        )
        await response(scope, receive, send)
