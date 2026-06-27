from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from api.support import (
    http_error_detail as _http_error_detail,
    json_safe as _json_safe,
    service as _service,
)
from orchestrator.service import OrchestratorServiceError


router = APIRouter()


@router.get("/requests")
def list_requests(request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return _json_safe(svc.list_requests())


@router.get("/requests/{request_id}")
def get_request(
    request: Request,
    request_id: str,
) -> dict[str, Any]:
    svc = _service(request)
    record = svc.get_request(request_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown request_id: {request_id}",
        )

    return _json_safe(record)


@router.get("/requests/{request_id}/map-layers")
def get_request_map_layers(
    request: Request,
    request_id: str,
) -> dict[str, Any]:
    """
    Return Leaflet-ready map layers for a previous request.
    """
    svc = _service(request)

    try:
        return _json_safe(svc.get_map_layers(request_id))
    except OrchestratorServiceError as exc:
        if "Unknown request_id" in str(exc):
            raise HTTPException(
                status_code=404,
                detail=_http_error_detail(exc),
            ) from exc

        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.get("/requests/{request_id}/outputs")
def get_request_outputs(
    request: Request,
    request_id: str,
) -> dict[str, Any]:
    """
    Return persisted output manifest for a request.
    """
    svc = _service(request)

    try:
        return _json_safe(svc.get_output_manifest(request_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc


@router.post("/requests/{request_id}/outputs/save")
def save_request_outputs(
    request: Request,
    request_id: str,
) -> dict[str, Any]:
    """
    Persist outputs for a request again.
    """
    svc = _service(request)

    try:
        return _json_safe(svc.save_request_outputs(request_id))
    except OrchestratorServiceError as exc:
        if "Unknown request_id" in str(exc):
            raise HTTPException(
                status_code=404,
                detail=_http_error_detail(exc),
            ) from exc

        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.get("/requests/{request_id}/outputs/files")
def list_request_output_files(
    request: Request,
    request_id: str,
) -> list[dict[str, Any]]:
    """
    List persisted output files for a request.
    """
    svc = _service(request)

    try:
        return _json_safe(svc.list_output_files(request_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc


@router.get("/requests/{request_id}/outputs/files/{filename}")
def download_request_output_file(
    request: Request,
    request_id: str,
    filename: str,
) -> FileResponse:
    """
    Download one persisted output file.
    """
    svc = _service(request)

    try:
        file_path = svc.get_output_file_path(
            request_id,
            filename,
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc

    return FileResponse(
        path=file_path,
        media_type=svc.get_output_file_media_type(filename),
        filename=filename,
    )


@router.get("/requests/{request_id}/documents/{filename}")
def download_request_document(
    request: Request,
    request_id: str,
    filename: str,
) -> FileResponse:
    """
    Download a generated document for a request.

    Security policy:
    - only serves files from artifacts/reports
    - blocks path traversal
    - requires the filename to be associated with the same request_id
    """
    safe_filename = Path(filename).name
    if safe_filename != filename:
        raise HTTPException(
            status_code=404,
            detail="Unknown document file.",
        )

    if not request_id or request_id not in safe_filename:
        raise HTTPException(
            status_code=404,
            detail="Unknown document file.",
        )

    reports_dir = Path("artifacts") / "reports"
    file_path = reports_dir / safe_filename

    try:
        resolved_reports_dir = reports_dir.resolve()
        resolved_file_path = file_path.resolve()
    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail="Unknown document file.",
        ) from exc

    if (
        resolved_reports_dir not in resolved_file_path.parents
        or not resolved_file_path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="Unknown document file.",
        )

    media_types = {
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".json": "application/json",
        ".geojson": "application/geo+json",
        ".csv": "text/csv",
        ".txt": "text/plain",
    }
    media_type = media_types.get(
        resolved_file_path.suffix.lower(),
        "application/octet-stream",
    )

    return FileResponse(
        path=resolved_file_path,
        media_type=media_type,
        filename=safe_filename,
    )

