from __future__ import annotations

from collections.abc import Callable
from typing import Any


def persist_query_spec_planning_record(
    *,
    request_id: str,
    query: str,
    resolved_inputs: dict[str, Any],
    original_inputs: dict[str, Any] | None,
    band_map: dict[str, int] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    planning_metadata: dict[str, Any],
    project_id: str | None,
    query_spec: Any,
    planning_result: Any,
    production_response: dict[str, Any],
    success: bool,
    planning_error: Any,
    planning_structured_error: Any,
    remember: Callable[..., Any],
    get_request: Callable[[str], dict[str, Any] | None],
    project_service: Any,
    persist_outputs_for_record: Callable[[dict[str, Any]], Any],
    persist_outputs: bool,
    json_safe: Callable[[Any], Any],
    redact_sensitive_json: Callable[[Any], Any],
    query_spec_to_dict_func: Callable[[Any], dict[str, Any]],
) -> None:
    remember(
        request_id=request_id,
        record={
            "request_id": request_id,
            "query": query,
            "inputs": json_safe(resolved_inputs),
            "original_inputs": json_safe(original_inputs or {}),
            "band_map": json_safe(band_map or {}),
            "user_context": json_safe(user_context or {}),
            "metadata": json_safe(metadata or {}),
            "final_metadata": json_safe(planning_metadata),
            "project_id": project_id,
            "query_spec": redact_sensitive_json(query_spec_to_dict_func(query_spec)),
            "planning_result": {
                "success": success,
                "error": planning_error,
                "structured_error": json_safe(planning_structured_error),
                "outputs": json_safe(getattr(planning_result, "outputs", {})),
                "output_nodes": json_safe(
                    getattr(planning_result, "output_nodes", {})
                ),
                "trace": json_safe(
                    [
                        {
                            "node_id": getattr(t, "node_id", None),
                            "capability_name": getattr(t, "capability_name", None),
                            "status": getattr(t, "status", None),
                            "started_at": getattr(t, "started_at", None),
                            "finished_at": getattr(t, "finished_at", None),
                            "error": getattr(t, "error", None),
                            "input_keys": getattr(t, "input_keys", None),
                            "output_summary": getattr(t, "output_summary", None),
                        }
                        for t in (getattr(planning_result, "trace", []) or [])
                    ]
                ),
            },
            "production_response": production_response,
        },
    )

    stored_record = get_request(request_id)

    if stored_record is None:
        return

    stored_project_id = stored_record.get("project_id")

    if stored_project_id:
        try:
            project_service.attach_request(
                stored_project_id,
                request_id,
            )
        except Exception:
            pass

    if persist_outputs:
        manifest = persist_outputs_for_record(stored_record)

        if stored_project_id and isinstance(manifest, dict):
            try:
                project_service.attach_output(
                    stored_project_id,
                    request_id,
                )
            except Exception:
                pass
