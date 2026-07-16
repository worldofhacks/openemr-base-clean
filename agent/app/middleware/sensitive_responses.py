"""Response headers for routes that carry session-bound or clinical data."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SENSITIVE_PREFIXES = (
    "/app",
    "/callback",
    "/chat",
    "/documents",
    "/evidence",
    "/launch",
    "/sessions",
    "/week2",
)
_CACHE_CONTROL = b"cache-control"


class SensitiveResponseHeadersMiddleware:
    """Prevent browser/proxy caching of authenticated and session-bearing output."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(
            _SENSITIVE_PREFIXES
        ):
            await self.app(scope, receive, send)
            return

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _CACHE_CONTROL
                ]
                headers.append((_CACHE_CONTROL, b"private, no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_private)
