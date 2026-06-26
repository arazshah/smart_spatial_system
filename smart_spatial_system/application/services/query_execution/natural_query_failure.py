from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_and_persist_failed_natural_query_response(
    *,
    exc: Exception,
    request_id: str,
    query: str,
    inputs: dict[str, Any],
    band_map: dict[str, int] | None,
    user_context: dict[str, Any] | None,
    final_metadata: dict[str, Any],
    project_id: str | None,
    response_builder: Any,
    remember: Callable[..., Any],
    json_safe: Callable[[Any], Any],
    service_exception_to_structured_error: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    service_structured_error = service_exception_to_structured_error(
        exc,
        stage="handle_query",
    )
    final_metadata["structured_error"] = service_structured_error
    final_metadata["service_structured_error"] = service_structured_error

    failed_response = response_builder.build_dict(
        response={
            "status": "failed",
            "request_id": request_id,
        },
        error=exc,
        metadata=final_metadata,
    )

    failed_response["structured_error"] = json_safe(service_structured_error)
    failed_metadata = failed_response.setdefault("metadata", {})
    if isinstance(failed_metadata, dict):
        failed_metadata["structured_error"] = json_safe(service_structured_error)
        failed_metadata["service_structured_error"] = json_safe(service_structured_error)

    remember(
        request_id=request_id,
        record={
            "request_id": request_id,
            "query": query,
            "inputs": json_safe(inputs),
            "band_map": json_safe(band_map or {}),
            "user_context": json_safe(user_context or {}),
            "metadata": json_safe(final_metadata),
            "error": repr(exc),
            "production_response": failed_response,
            "project_id": project_id,
        },
    )

    return failed_response
