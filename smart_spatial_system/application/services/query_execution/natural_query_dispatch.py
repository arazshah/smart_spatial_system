from __future__ import annotations

from collections.abc import Callable
from typing import Any

from smart_spatial_system.application.services.query_execution.direct_query_dispatch import (
    try_dispatch_direct_query_response,
)


def try_dispatch_natural_query_direct_response(
    *,
    query: str,
    effective_query: str,
    inputs: dict[str, Any],
    resolved_inputs: dict[str, Any],
    final_request_id: str,
    final_metadata: dict[str, Any],
    band_map: dict[str, int] | None,
    user_context: dict[str, Any] | None,
    llm_intent: Any,
    metadata: dict[str, Any] | None,
    project_id: str | None,
    preflight_direct_response_handler: Callable[..., dict[str, Any] | None],
    direct_response_handler: Callable[..., dict[str, Any] | None],
    vector_display_handler: Callable[..., dict[str, Any] | None],
    query_spec_planning_enabled: Callable[[], bool],
    query_spec_planning_handler: Callable[..., dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Backward-compatible wrapper for the direct query dispatch bridge."""
    return try_dispatch_direct_query_response(
        query=query,
        effective_query=effective_query,
        inputs=inputs,
        resolved_inputs=resolved_inputs,
        final_request_id=final_request_id,
        final_metadata=final_metadata,
        band_map=band_map,
        user_context=user_context,
        llm_intent=llm_intent,
        metadata=metadata,
        project_id=project_id,
        preflight_direct_response_handler=preflight_direct_response_handler,
        direct_response_handler=direct_response_handler,
        vector_display_handler=vector_display_handler,
        query_spec_planning_enabled=query_spec_planning_enabled,
        query_spec_planning_handler=query_spec_planning_handler,
    )
