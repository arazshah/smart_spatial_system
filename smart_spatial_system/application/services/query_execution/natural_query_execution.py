from __future__ import annotations

from collections.abc import Callable
from typing import Any

from smart_spatial_system.application.services.query_execution.direct_query_dispatch import (
    try_dispatch_direct_query_response,
)
from smart_spatial_system.application.services.query_execution.natural_query_persistence import (
    persist_natural_query_record,
)


def execute_and_persist_natural_query_success_path(
    *,
    query: str,
    effective_query: str,
    inputs: dict[str, Any],
    band_map: dict[str, int] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    min_score: float | None,
    final_request_id: str,
    final_metadata: dict[str, Any],
    project_id: str | None,
    llm_intent: Any,
    config_min_score: float,
    persist_outputs: bool,
    response_builder: Any,
    project_service: Any,
    build_router: Callable[[], Any],
    resolve_input_references: Callable[[dict[str, Any]], dict[str, Any]],
    natural_query_runner: Callable[..., dict[str, Any]],
    preflight_direct_response_handler: Callable[..., dict[str, Any] | None],
    direct_response_handler: Callable[..., dict[str, Any] | None],
    vector_display_handler: Callable[..., dict[str, Any] | None],
    query_spec_planning_enabled: Callable[[], bool],
    query_spec_planning_handler: Callable[..., dict[str, Any] | None],
    remember: Callable[..., Any],
    get_request: Callable[[str], dict[str, Any] | None],
    persist_outputs_for_record: Callable[[dict[str, Any]], Any],
    json_safe: Callable[[Any], Any],
) -> dict[str, Any]:
    router = build_router()
    resolved_inputs = resolve_input_references(inputs)

    direct_or_planning_response = try_dispatch_direct_query_response(
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

    if direct_or_planning_response is not None:
        return direct_or_planning_response

    run_result = natural_query_runner(
        effective_query,
        inputs=resolved_inputs,
        band_map=band_map or {},
        router=router,
        min_score=config_min_score if min_score is None else min_score,
        request_id=final_request_id,
    )

    production_response = response_builder.build_dict(
        run_result=run_result,
        metadata=final_metadata,
    )

    persist_natural_query_record(
        request_id=final_request_id,
        query=query,
        resolved_inputs=resolved_inputs,
        original_inputs=inputs,
        band_map=band_map,
        user_context=user_context,
        final_metadata=final_metadata,
        project_id=project_id,
        run_result=run_result,
        production_response=production_response,
        remember=remember,
        get_request=get_request,
        project_service=project_service,
        persist_outputs_for_record=persist_outputs_for_record,
        persist_outputs=persist_outputs,
        json_safe=json_safe,
    )

    return production_response
