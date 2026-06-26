from __future__ import annotations

from collections.abc import Callable
from typing import Any


def prepare_natural_query_context(
    *,
    query: str,
    request_id: str | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    project_id: str | None,
    use_weighted_router: bool,
    new_request_id: Callable[[], str],
    maybe_plan_llm_intent: Callable[[str], Any],
    apply_intent_to_query: Callable[[str, Any], str],
    llm_planning_enabled: Callable[[], bool],
    json_safe: Callable[[Any], Any],
) -> dict[str, Any]:
    final_request_id = request_id or new_request_id()

    final_metadata = {
        "service": "OrchestratorService",
        "weighted_router": use_weighted_router,
    }

    # Propagate project_id so _remember can link this request to its project.
    resolved_project_id = str(project_id or "").strip() or None
    if resolved_project_id:
        final_metadata["project_id"] = resolved_project_id

    if user_context:
        final_metadata["user_context"] = json_safe(user_context)

    if metadata:
        final_metadata.update(dict(metadata))

    llm_intent = maybe_plan_llm_intent(query)
    effective_query = apply_intent_to_query(query, llm_intent)

    final_metadata["llm_planning_enabled"] = llm_planning_enabled()
    if llm_intent is not None:
        final_metadata["llm_intent"] = json_safe(llm_intent)
        final_metadata["original_query"] = query
        final_metadata["effective_query"] = effective_query

    return {
        "final_request_id": final_request_id,
        "final_metadata": final_metadata,
        "resolved_project_id": resolved_project_id,
        "llm_intent": llm_intent,
        "effective_query": effective_query,
    }
