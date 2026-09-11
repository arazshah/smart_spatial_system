"""
api.auth

Minimal shared-secret API key authentication.

This targets the "single team, self-hosted" deployment model documented in
CLAUDE.md: one deployer runs one backend instance for their own team, so a
single shared key is enough - there is no per-user login, session, or
multi-tenant model here.

Set SMART_SPATIAL_API_KEY to require every non-health request to send it
back as the X-API-Key header. Leaving it unset keeps the API open, matching
prior behavior, for local development and existing deployments that have
not opted in yet.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

_API_KEY_HEADER_NAME = "X-API-Key"

_api_key_header = APIKeyHeader(name=_API_KEY_HEADER_NAME, auto_error=False)


def require_api_key(
    request: Request,
    provided_key: str | None = Security(_api_key_header),
) -> None:
    """
    FastAPI dependency: reject the request unless it carries the
    X-API-Key header matching the server's configured key.

    No-op (request allowed) when the server has no api_key configured.
    """
    expected_key: str | None = getattr(request.app.state, "api_key", None)

    if not expected_key:
        return

    if not provided_key or not secrets.compare_digest(provided_key, expected_key):
        raise HTTPException(
            status_code=401,
            detail=f"Missing or invalid {_API_KEY_HEADER_NAME} header.",
        )
