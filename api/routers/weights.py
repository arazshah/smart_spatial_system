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


@router.get("/weights")
def get_weights(request: Request) -> dict[str, Any]:
    svc = _service(request)
    return _json_safe(svc.get_weights())


@router.post("/weights/save")
def save_weights(request: Request) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.save_weights())
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=500,
            detail=_http_error_detail(exc),
        ) from exc


@router.post("/weights/reload")
def reload_weights(request: Request) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.reload_weights())
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=500,
            detail=_http_error_detail(exc),
        ) from exc


@router.post("/weights/proposals/apply")
def apply_weight_proposal(
    request: Request,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Approve and apply a weight proposal.

    Expected body:
        {
            "proposal": {...},
            "save": true
        }
    """
    svc = _service(request)

    proposal = body.get("proposal")

    if not isinstance(proposal, dict):
        raise HTTPException(
            status_code=400,
            detail="'proposal' must be an object.",
        )

    save = body.get("save", True)

    if not isinstance(save, bool):
        raise HTTPException(
            status_code=400,
            detail="'save' must be boolean when provided.",
        )

    try:
        payload = svc.approve_and_apply_proposal(
            proposal,
            save=save,
        )
    except (OrchestratorServiceError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc

    return _json_safe(payload)

