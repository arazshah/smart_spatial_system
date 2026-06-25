from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from api.support import (
    http_error_detail as _http_error_detail,
    json_safe as _json_safe,
    service as _service,
)
from orchestrator.service import OrchestratorServiceError


router = APIRouter()


@router.post("/projects")
def create_project(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(
            svc.create_project(
                name=str(payload.get("name") or "").strip(),
                description=payload.get("description"),
                metadata=payload.get("metadata") or {},
            )
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.get("/projects")
def list_projects(
    request: Request,
) -> list[dict[str, Any]]:
    svc = _service(request)
    return _json_safe(svc.list_projects())


@router.get("/projects/{project_id}")
def get_project(
    request: Request,
    project_id: str,
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.get_project(project_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc


@router.get("/projects/{project_id}/data-sources")
def list_project_data_sources(
    request: Request,
    project_id: str,
) -> list[dict[str, Any]]:
    svc = _service(request)

    try:
        return _json_safe(svc.list_project_data_sources(project_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc

