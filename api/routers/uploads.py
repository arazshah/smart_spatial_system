from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from api.support import (
    http_error_detail as _http_error_detail,
    json_safe as _json_safe,
    service as _service,
)
from orchestrator.service import OrchestratorServiceError


router = APIRouter()


@router.post("/uploads/raster")
async def upload_raster(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("raster"),
    project_id: str | None = Form(None),
) -> dict[str, Any]:
    """
    Upload a raster file.

    MVP:
        - JSON raster files can be used directly through raster_ref.
        - GeoTIFF files are stored for future rasterio/local_raster_loader integration.
    """
    svc = _service(request)

    content = await file.read()

    try:
        payload = svc.save_upload(
            filename=file.filename or "upload.bin",
            content=content,
            content_type=file.content_type,
            kind=kind,
            user_context={
                "source": "api_upload",
            },
            project_id=project_id,
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc

    return _json_safe(payload)


@router.post("/uploads/vector")
async def upload_vector(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("vector"),
    project_id: str | None = Form(None),
) -> dict[str, Any]:
    """
    Upload a vector file.

    MVP:
        - GeoJSON/JSON can be used directly.
        - GPKG/SHP ZIP/KML are stored and should be resolved by local_vector_loader.
    """
    svc = _service(request)

    content = await file.read()

    try:
        payload = svc.save_upload(
            filename=file.filename or "upload_vector.bin",
            content=content,
            content_type=file.content_type,
            kind=kind,
            user_context={
                "source": "api_vector_upload",
            },
            project_id=project_id,
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc

    return _json_safe(payload)


@router.get("/uploads")
def list_uploads(
    request: Request,
) -> list[dict[str, Any]]:
    svc = _service(request)
    return _json_safe(svc.list_uploads())


@router.get("/uploads/{upload_id}")
def get_upload_metadata(
    request: Request,
    upload_id: str,
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.get_upload_metadata(upload_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc


@router.get("/uploads/{upload_id}/file")
def download_upload_file(
    request: Request,
    upload_id: str,
) -> FileResponse:
    svc = _service(request)

    try:
        file_path = svc.get_upload_file_path(upload_id)
        media_type = svc.get_upload_file_media_type(upload_id)
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=file_path.name,
    )

