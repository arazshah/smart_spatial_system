"""
Vector display/summary direct handler.

This module bridges simple vector display/summary queries to official
capabilities while preserving the legacy frontend response contract.
"""

from __future__ import annotations

from typing import Any, Callable

from smart_spatial_system.application.services.vector_geojson_helpers import (
    find_geojson_like,
    summarize_feature_collection,
)
from smart_spatial_system.application.services.vector_query_classifier import (
    is_vector_display_query,
    is_vector_summary_query,
)
from smart_spatial_system.application.services.query_execution.real_estate_classifier import (
    is_real_estate_analysis_query as default_is_real_estate_analysis_query,
)


JsonSafe = Callable[[Any], Any]


def _noop_remember(*args: Any, **kwargs: Any) -> None:
    return None


def _call_real_estate_analysis_query_checker(
    checker: Callable[..., bool],
    query: str,
    llm_intent: dict[str, Any] | None,
) -> bool:
    try:
        return bool(checker(query, llm_intent))
    except TypeError:
        return bool(checker(query))


def try_handle_vector_display_directly(
    context: Any | None = None,
    *,
    query: str,
    inputs: dict[str, Any],
    resolved_inputs: dict[str, Any],
    final_request_id: str,
    final_metadata: dict[str, Any],
    json_safe: JsonSafe,
    band_map: dict[str, int] | None = None,
    user_context: dict[str, Any] | None = None,
    llm_intent: dict[str, Any] | None = None,
    build_enabled_router: Callable[[], Any] | None = None,
    remember: Callable[..., Any] | None = None,
    is_real_estate_analysis_query: Callable[..., bool] | None = None,
) -> dict[str, Any] | None:
    """
    Capability-backed bridge for simple vector display/summary queries.

    Historical note:
        This handler used to build the vector display/summary response directly
        inside QueryExecutionService. It now delegates actual work to official
        capabilities:

            - inspect_vector
            - display_vector_layer
            - summarize_vector_layer
    """
    analysis_query_checker = is_real_estate_analysis_query
    if analysis_query_checker is None and context is not None:
        analysis_query_checker = getattr(
            context,
            "_is_real_estate_analysis_query",
            None,
        )
    if analysis_query_checker is None:
        analysis_query_checker = default_is_real_estate_analysis_query

    router_builder = build_enabled_router
    if router_builder is None and context is not None:
        router_builder = getattr(context, "_build_enabled_router", None)

    remember_callback = remember
    if remember_callback is None and context is not None:
        remember_callback = getattr(context, "_remember", None)
    if remember_callback is None:
        remember_callback = _noop_remember

    # Do not let simple vector display/summary swallow real-estate
    # ranking/report/PDF queries. These must be handled by the
    # real-estate ranking/report pipeline.
    normalized_query_for_vector_guard = str(query or "").lower()
    real_estate_ranking_terms = (
        "ملک",
        "املاک",
        "امتیاز",
        "رتبه",
        "رتبه‌بندی",
        "رتبه بندی",
        "گزارش",
        "pdf",
        "پی دی اف",
        "جدول",
    )

    if (
        _call_real_estate_analysis_query_checker(
            analysis_query_checker,
            query,
            llm_intent,
        )
        and any(
            term in normalized_query_for_vector_guard
            for term in real_estate_ranking_terms
        )
    ):
        return None

    is_vector_display = is_vector_display_query(query, llm_intent)
    is_vector_summary = is_vector_summary_query(query, llm_intent)

    if not (is_vector_display or is_vector_summary):
        return None

    feature_collection = find_geojson_like(resolved_inputs)
    if feature_collection is None:
        feature_collection = find_geojson_like(inputs)

    if feature_collection is None:
        return None

    handler_name = "vector_summary" if is_vector_summary else "vector_display"
    target_capability = (
        "summarize_vector_layer"
        if is_vector_summary
        else "display_vector_layer"
    )

    if router_builder is None:
        raise ValueError(
            "Vector display handler requires a build_enabled_router callback "
            "or a context with _build_enabled_router()."
        )

    router = router_builder()

    inspect_binding = router.resolve("inspect_vector")
    target_binding = router.resolve(target_capability)

    trace: list[dict[str, Any]] = []

    inspection = inspect_binding.callable(
        vector=feature_collection,
    )

    trace.append(
        {
            "order": 1,
            "node_id": "node_001_inspect_vector",
            "capability_name": "inspect_vector",
            "plugin_id": inspect_binding.plugin_id,
            "output_kind": inspect_binding.output_kind,
            "status": "success",
        }
    )

    if is_vector_summary:
        capability_result = target_binding.callable(
            vector=feature_collection,
        )
    else:
        capability_result = target_binding.callable(
            vector=feature_collection,
            layer_id="active_vector",
            name="active_vector",
            visible=True,
        )

    trace.append(
        {
            "order": 2,
            "node_id": (
                "node_002_summarize_vector_layer"
                if is_vector_summary
                else "node_002_display_vector_layer"
            ),
            "capability_name": target_capability,
            "plugin_id": target_binding.plugin_id,
            "output_kind": target_binding.output_kind,
            "status": "success",
        }
    )

    summary = (
        capability_result.get("summary")
        if isinstance(capability_result, dict)
        else None
    )

    if not isinstance(summary, dict):
        summary = (
            inspection.get("summary")
            if isinstance(inspection, dict)
            else {}
        )

    if not isinstance(summary, dict):
        summary = summarize_feature_collection(feature_collection)

    if is_vector_summary:
        feature_count = summary.get("feature_count", 0)
        geometry_counts = summary.get("geometry_counts", {})
        geometry_text = ", ".join(
            f"{key}: {value}"
            for key, value in geometry_counts.items()
        ) or "No geometries"

        message = (
            capability_result.get("message")
            if isinstance(capability_result, dict)
            else None
        ) or f"Vector layer contains {feature_count} features. {geometry_text}."

        result_payload = {
            "type": "vector_summary",
            "feature_count": feature_count,
            "geometry_counts": geometry_counts,
            "property_keys": summary.get("property_keys", []),
            "summary": summary,
            "capability_result": json_safe(capability_result),
        }

        outputs = {
            "vectors": [
                {
                    "id": "active_vector",
                    "name": "active_vector",
                    "format": "geojson",
                    "role": "map_layer",
                    "geojson": feature_collection,
                    "summary": summary,
                }
            ],
            "rasters": [],
            "tables": [],
        }

        layers = [
            {
                "id": "active_vector",
                "name": "active_vector",
                "type": "vector",
                "format": "geojson",
                "visible": True,
                "geojson": feature_collection,
                "summary": summary,
            }
        ]

    else:
        message = (
            capability_result.get("message")
            if isinstance(capability_result, dict)
            else None
        ) or "Vector layer is ready for map display."

        result_payload = {
            "type": "vector_display",
            "layer_ids": ["active_vector"],
            "feature_count": summary.get("feature_count", 0),
            "geometry_counts": summary.get("geometry_counts", {}),
            "property_keys": summary.get("property_keys", []),
            "summary": summary,
            "capability_result": json_safe(capability_result),
        }

        if isinstance(capability_result, dict):
            outputs = capability_result.get("outputs") or {}
            layers = capability_result.get("layers") or []
        else:
            outputs = {}
            layers = []

        if not isinstance(outputs, dict) or "vectors" not in outputs:
            outputs = {
                "vectors": [
                    {
                        "id": "active_vector",
                        "name": "active_vector",
                        "format": "geojson",
                        "role": "map_layer",
                        "geojson": feature_collection,
                        "summary": summary,
                    }
                ],
                "rasters": [],
                "tables": [],
            }

        if not isinstance(layers, list) or not layers:
            layers = [
                {
                    "id": "active_vector",
                    "name": "active_vector",
                    "type": "vector",
                    "format": "geojson",
                    "visible": True,
                    "geojson": feature_collection,
                    "summary": summary,
                }
            ]

    metadata = dict(final_metadata)
    metadata["execution_mode"] = "capability_bridge"
    metadata["legacy_handler_name"] = handler_name
    metadata["original_query"] = query
    metadata["capabilities"] = {
        "inspection": "inspect_vector",
        "target": target_capability,
    }

    audit_record = {
        "status": "success",
        "execution_mode": "capability_bridge",
        "reason": "simple vector display/summary query routed through official capabilities",
        "query": query,
        "request_id": final_request_id,
        "legacy_handler_name": handler_name,
        "capabilities": [
            "inspect_vector",
            target_capability,
        ],
        "trace": trace,
        "outputs": {
            "summary": json_safe(summary),
        },
    }

    run_result = {
        "status": "succeeded",
        "execution_mode": "capability_bridge",
        "legacy_handler_name": handler_name,
        "outputs": {
            "inspection": json_safe(inspection),
            "result": json_safe(capability_result),
        },
        "trace": trace,
        "audit_record": audit_record,
    }

    response = {
        "ok": True,
        "status": "succeeded",
        "request_id": final_request_id,
        "query": query,
        "message": message,
        "summary": summary,
        "metadata": json_safe(metadata),
        "outputs": outputs,
        "layers": layers,
        "documents": [],
        "trace": trace,
        "result": result_payload,
        "audit_record": audit_record,
    }

    remember_callback(
        request_id=final_request_id,
        record={
            "request_id": final_request_id,
            "query": query,
            "inputs": json_safe(resolved_inputs),
            "original_inputs": json_safe(inputs),
            "band_map": json_safe(band_map or {}),
            "user_context": json_safe(user_context or {}),
            "metadata": json_safe(metadata),
            "run_result": json_safe(run_result),
            "audit_record": json_safe(audit_record),
            "production_response": json_safe(response),
        },
    )

    return json_safe(response)
