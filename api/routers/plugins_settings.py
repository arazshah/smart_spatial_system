from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from api.support import (
    http_error_detail as _http_error_detail,
    json_safe as _json_safe,
    service as _service,
)
from orchestrator.plugin_config_store import (
    PluginConfigStoreError,
    read_plugin_config,
    write_plugin_config,
)
from orchestrator.service import OrchestratorServiceError


router = APIRouter()


@router.get("/plugins")
def list_plugins(
    request: Request,
) -> list[dict[str, Any]]:
    svc = _service(request)
    return _json_safe(svc.list_plugins())


@router.get("/plugins/{plugin_id}")
def get_plugin(
    request: Request,
    plugin_id: str,
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.get_plugin(plugin_id))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=404,
            detail=_http_error_detail(exc),
        ) from exc


@router.patch("/plugins/{plugin_id}")
def patch_plugin(
    request: Request,
    plugin_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(
            svc.update_plugin_state(
                plugin_id,
                enabled=payload.get("enabled"),
            )
        )
    except OrchestratorServiceError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=404 if "Unknown plugin:" in message else 400,
            detail=message,
        ) from exc


@router.get("/plugins/{plugin_id}/config")
def get_plugin_config(
    request: Request,
    plugin_id: str,
) -> dict[str, Any]:
    svc = _service(request)

    # Ensure the plugin actually exists before exposing config.
    try:
        svc.get_plugin(plugin_id)
    except OrchestratorServiceError as exc:
        raise HTTPException(status_code=404, detail=_http_error_detail(exc)) from exc

    try:
        return _json_safe(read_plugin_config(plugin_id))
    except PluginConfigStoreError as exc:
        raise HTTPException(status_code=400, detail=_http_error_detail(exc)) from exc


@router.put("/plugins/{plugin_id}/config")
def put_plugin_config(
    request: Request,
    plugin_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    svc = _service(request)

    try:
        svc.get_plugin(plugin_id)
    except OrchestratorServiceError as exc:
        raise HTTPException(status_code=404, detail=_http_error_detail(exc)) from exc

    raw_yaml = payload.get("raw_yaml")
    parsed = payload.get("parsed")

    try:
        result = write_plugin_config(
            plugin_id,
            raw_yaml=raw_yaml,
            parsed=parsed,
        )
    except PluginConfigStoreError as exc:
        raise HTTPException(status_code=400, detail=_http_error_detail(exc)) from exc

    return _json_safe(result)


@router.get("/settings/runtime")
def get_runtime_settings(
    request: Request,
) -> dict[str, Any]:
    """
    Return non-sensitive runtime settings.

    This endpoint intentionally never returns secrets/API keys.
    """
    svc = _service(request)
    return _json_safe(svc.get_runtime_settings())


@router.post("/settings/llm/smoke-test")
def llm_smoke_test(
    request: Request,
) -> dict[str, Any]:
    """
    Verify backend-to-LLM connectivity.

    This endpoint never returns secrets.
    """
    svc = _service(request)

    try:
        return _json_safe(svc.run_llm_smoke_test())
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=_http_error_detail(exc),
        ) from exc

