"""
smart_spatial_system.interfaces.mcp.auth

ASGI shared-secret check for the HTTP transport. Mirrors ``api/auth.py``
(same ``SMART_SPATIAL_API_KEY``, same constant-time compare) and also accepts
``Authorization: Bearer <key>`` since that is what MCP clients send.
"""

from __future__ import annotations

import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def _extract_key(scope: Scope) -> str | None:
    headers = {k.lower(): v for k, v in scope.get("headers", [])}
    bearer = headers.get(b"authorization", b"").decode("latin-1")
    if bearer[:7].lower() == "bearer ":
        return bearer[7:].strip()
    api_key = headers.get(b"x-api-key")
    return api_key.decode("latin-1") if api_key else None


class ApiKeyMiddleware:
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        if not api_key:
            raise ValueError("ApiKeyMiddleware requires a non-empty api_key.")
        self.app = app
        self._api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan / websocket pass through
            await self.app(scope, receive, send)
            return
        provided = _extract_key(scope)
        if not provided or not secrets.compare_digest(provided, self._api_key):
            response = JSONResponse(
                {"error": "Missing or invalid API key."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
