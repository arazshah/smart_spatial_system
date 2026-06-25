from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from api.support import json_safe, service


router = APIRouter()


@router.get("/")
def root() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "Smart Spatial System API",
        "docs": "/docs",
        "health": "/health",
    }


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    svc = service(request)
    return json_safe(svc.get_health())
