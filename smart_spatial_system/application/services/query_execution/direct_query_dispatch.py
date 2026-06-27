from __future__ import annotations

from collections.abc import Callable
from typing import Any


def try_dispatch_direct_query_response(
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
    preflight_direct_response = preflight_direct_response_handler(
        query=query,
        inputs=inputs,
        resolved_inputs=resolved_inputs,
        final_request_id=final_request_id,
        final_metadata=final_metadata,
        band_map=band_map,
        user_context=user_context,
        llm_intent=llm_intent,
    )

    if preflight_direct_response is not None:
        return preflight_direct_response

    # Try real-estate ranking after upload/input references are resolved.
    # UI auto_project_data often provides only upload refs at first.
    direct_response = direct_response_handler(
        query=query,
        inputs=resolved_inputs,
        request_id=final_request_id,
        llm_intent=llm_intent,
    )
    if direct_response is not None:
        return direct_response

    direct_vector_response = vector_display_handler(
        query=query,
        inputs=inputs,
        resolved_inputs=resolved_inputs,
        final_request_id=final_request_id,
        final_metadata=final_metadata,
        band_map=band_map,
        user_context=user_context,
        llm_intent=llm_intent,
    )

    if direct_vector_response is not None:
        return direct_vector_response

    planning_enabled = query_spec_planning_enabled()
    final_metadata["query_spec_planning_enabled"] = planning_enabled

    if planning_enabled:
        planning_response = query_spec_planning_handler(
            query=effective_query,
            resolved_inputs=resolved_inputs,
            final_request_id=final_request_id,
            final_metadata=final_metadata,
            user_context=user_context,
            original_inputs=inputs,
            band_map=band_map,
            metadata=metadata,
            project_id=project_id,
        )

        if planning_response is not None:
            return planning_response

    return None
