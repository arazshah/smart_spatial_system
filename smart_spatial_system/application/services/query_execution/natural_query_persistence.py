from __future__ import annotations

from collections.abc import Callable
from typing import Any


def persist_natural_query_record(
    *,
    request_id: str,
    query: str,
    resolved_inputs: dict[str, Any],
    original_inputs: dict[str, Any],
    band_map: dict[str, int] | None,
    user_context: dict[str, Any] | None,
    final_metadata: dict[str, Any],
    project_id: str | None,
    run_result: dict[str, Any],
    production_response: dict[str, Any],
    remember: Callable[..., Any],
    get_request: Callable[[str], dict[str, Any] | None],
    project_service: Any,
    persist_outputs_for_record: Callable[[dict[str, Any]], Any],
    persist_outputs: bool,
    json_safe: Callable[[Any], Any],
) -> None:
    remember(
        request_id=request_id,
        record={
            "request_id": request_id,
            "query": query,
            "inputs": json_safe(resolved_inputs),
            "original_inputs": json_safe(original_inputs),
            "band_map": json_safe(band_map or {}),
            "user_context": json_safe(user_context or {}),
            "metadata": json_safe(final_metadata),
            "project_id": project_id,
            "run_result": run_result,
            "audit_record": run_result.get("audit_record"),
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
