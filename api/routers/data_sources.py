from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from api.support import (
    http_error_detail as _http_error_detail,
    json_safe as _json_safe,
    service as _service,
)
from orchestrator.service import OrchestratorServiceError


router = APIRouter()


@router.get("/data-sources/{upload_id}")
def get_data_source(
    request: Request,
    upload_id: str,
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.get_data_source(upload_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc


@router.delete("/data-sources/{upload_id}")
def delete_data_source(
    request: Request,
    upload_id: str,
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.delete_data_source(upload_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.patch("/data-sources/{upload_id}")
def update_data_source(
    request: Request,
    upload_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.update_data_source(upload_id, payload))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.get("/data-sources/{upload_id}/preview")
def preview_data_source(
    request: Request,
    upload_id: str,
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.preview_data_source(upload_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc

