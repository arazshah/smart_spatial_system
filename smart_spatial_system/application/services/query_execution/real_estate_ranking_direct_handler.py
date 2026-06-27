from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from smart_spatial_system.application.services.query_execution.real_estate_analysis_inspector import (
    build_real_estate_analysis_inspector,
)
from smart_spatial_system.application.services.query_execution.real_estate_classifier import (
    looks_like_real_estate_ranking_query,
)
from smart_spatial_system.application.services.query_execution.real_estate_context import (
    enrich_property_feature_collection_with_spatial_context,
    extract_property_feature_collection_from_inputs,
    extract_real_estate_spatial_context_from_inputs,
)
from smart_spatial_system.application.services.query_execution.real_estate_document_renderer import (
    try_render_real_estate_ranking_document,
)
from smart_spatial_system.application.services.query_execution.real_estate_ranking_artifacts import (
    build_real_estate_ranking_artifacts,
)
from smart_spatial_system.application.services.query_execution.real_estate_ranking_execution import (
    execute_real_estate_ranking,
)
from smart_spatial_system.application.services.query_execution.real_estate_ranking_response import (
    build_real_estate_ranking_response,
)
from smart_spatial_system.application.services.query_execution.real_estate_report_payload import (
    build_real_estate_pdf_report_payload,
)
from smart_spatial_system.application.services.query_execution.real_estate_scoring import (
    evaluate_real_estate_eligibility,
    score_real_estate_property,
)
from smart_spatial_system.application.services.real_estate_spatial_helpers import (
    feature_point_lonlat,
    has_bool_like_value,
    has_metric_value,
    nearest_distance_to_features_m,
    point_in_polygon_feature_lonlat,
)


def try_handle_real_estate_ranking_directly(
    *,
    query: str,
    inputs: dict[str, Any] | None,
    request_id: str | None = None,
    llm_intent: Any = None,
    llm_planning_enabled: Callable[[], bool],
) -> dict[str, Any] | None:
    if not looks_like_real_estate_ranking_query(query):
        return None

    feature_collection = extract_property_feature_collection_from_inputs(inputs)
    if not isinstance(feature_collection, dict):
        return None

    spatial_context = extract_real_estate_spatial_context_from_inputs(inputs)
    feature_collection, spatial_enrichment_summary = (
        enrich_property_feature_collection_with_spatial_context(
            feature_collection,
            spatial_context,
            feature_point_lonlat=feature_point_lonlat,
            has_metric_value=has_metric_value,
            nearest_distance_to_features_m=nearest_distance_to_features_m,
            has_bool_like_value=has_bool_like_value,
            point_in_polygon_feature_lonlat=point_in_polygon_feature_lonlat,
        )
    )

    features = feature_collection.get("features") or []
    if not isinstance(features, list):
        features = []

    ranked_features, rejected_rows = execute_real_estate_ranking(
        features=features,
        evaluate_eligibility=evaluate_real_estate_eligibility,
        score_property=score_real_estate_property,
    )

    table_rows, ranked_geojson, summary, report, message = build_real_estate_ranking_artifacts(
        features=features,
        ranked_features=ranked_features,
        rejected_rows=rejected_rows,
        spatial_enrichment_summary=spatial_enrichment_summary,
    )

    rid = request_id or f"req-{uuid.uuid4()}"

    documents, document_warnings, render_pdf_trace_step = try_render_real_estate_ranking_document(
        report=report,
        table_rows=table_rows,
        ranked_geojson=ranked_geojson,
        summary=summary,
        request_id=rid,
        build_pdf_report_payload=build_real_estate_pdf_report_payload,
    )

    return build_real_estate_ranking_response(
        query=query,
        rid=rid,
        message=message,
        features=features,
        ranked_features=ranked_features,
        ranked_geojson=ranked_geojson,
        rejected_rows=rejected_rows,
        table_rows=table_rows,
        summary=summary,
        report=report,
        documents=documents,
        document_warnings=document_warnings,
        render_pdf_trace_step=render_pdf_trace_step,
        spatial_enrichment_summary=spatial_enrichment_summary,
        llm_intent=llm_intent,
        build_analysis_inspector=build_real_estate_analysis_inspector,
        llm_planning_enabled=llm_planning_enabled,
    )
