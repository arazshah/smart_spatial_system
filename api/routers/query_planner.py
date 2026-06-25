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


@router.post("/planner/intent")
def plan_intent(
    request: Request,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Plan natural geospatial query intent using LLM.

    Does not execute plugins.
    """
    svc = _service(request)

    query = payload.get("query")

    try:
        return _json_safe(svc.plan_intent_with_llm(query))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.post("/query")
def query_endpoint(
    request: Request,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Execute natural geospatial query.

    Expected body:
        {
            "query": "...",
            "inputs": {...},
            "band_map": {...},
            "request_id": "optional",
            "user_context": {...},
            "metadata": {...},
            "min_score": 0.01
        }
    """
    svc = _service(request)

    query_text = body.get("query")
    inputs = body.get("inputs")

    if not isinstance(query_text, str) or not query_text.strip():
        raise HTTPException(
            status_code=400,
            detail="'query' must be a non-empty string.",
        )

    if not isinstance(inputs, dict):
        raise HTTPException(
            status_code=400,
            detail="'inputs' must be an object.",
        )

    band_map = body.get("band_map") or {}

    if not isinstance(band_map, dict):
        raise HTTPException(
            status_code=400,
            detail="'band_map' must be an object when provided.",
        )

    user_context = body.get("user_context") or {}

    if not isinstance(user_context, dict):
        raise HTTPException(
            status_code=400,
            detail="'user_context' must be an object when provided.",
        )

    metadata = body.get("metadata") or {}

    if not isinstance(metadata, dict):
        raise HTTPException(
            status_code=400,
            detail="'metadata' must be an object when provided.",
        )

    min_score = body.get("min_score")

    if min_score is not None and not isinstance(min_score, (int, float)):
        raise HTTPException(
            status_code=400,
            detail="'min_score' must be numeric when provided.",
        )

    response = svc.handle_query(
        query=query_text,
        inputs=inputs,
        band_map={
            str(key): int(value)
            for key, value in band_map.items()
        },
        request_id=body.get("request_id"),
        user_context=user_context,
        metadata=metadata,
        min_score=float(min_score) if min_score is not None else None,
        project_id=str(body.get("project_id") or "").strip() or None,
    )

    return _json_safe(response)


@router.post("/feedback")
def feedback_endpoint(
    request: Request,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Submit user feedback for a previous request.

    Expected body:
        {
            "request_id": "...",
            "rating": "correct|incorrect|partial",
            "issue_types": ["route_error"],
            "expected_capability": "threshold_raster",
            "expected_plugin_id": "raster_threshold",
            "comment": "...",
            "user_context": {...}
        }
    """
    svc = _service(request)

    request_id = body.get("request_id")
    rating = body.get("rating")

    if not isinstance(request_id, str) or not request_id.strip():
        raise HTTPException(
            status_code=400,
            detail="'request_id' must be a non-empty string.",
        )

    if not isinstance(rating, str) or not rating.strip():
        raise HTTPException(
            status_code=400,
            detail="'rating' must be a non-empty string.",
        )

    issue_types = body.get("issue_types")

    if issue_types is not None and not isinstance(issue_types, list):
        raise HTTPException(
            status_code=400,
            detail="'issue_types' must be a list when provided.",
        )

    user_context = body.get("user_context")

    if user_context is not None and not isinstance(user_context, dict):
        raise HTTPException(
            status_code=400,
            detail="'user_context' must be an object when provided.",
        )

    try:
        payload = svc.submit_feedback(
            request_id=request_id,
            rating=rating,
            issue_types=issue_types,
            expected_capability=body.get("expected_capability"),
            expected_plugin_id=body.get("expected_plugin_id"),
            comment=body.get("comment"),
            user_context=user_context,
        )
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

    return _json_safe(payload)

