from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_real_estate_ranking_response(
    *,
    query: str,
    rid: str,
    message: str,
    features: list[Any],
    ranked_features: list[dict[str, Any]],
    ranked_geojson: dict[str, Any],
    rejected_rows: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    report: dict[str, Any],
    documents: list[dict[str, Any]],
    document_warnings: list[str],
    render_pdf_trace_step: dict[str, Any],
    spatial_enrichment_summary: dict[str, Any],
    llm_intent: Any,
    build_analysis_inspector: Callable[..., dict[str, Any]],
    llm_planning_enabled: Callable[[], Any],
) -> dict[str, Any]:
    outputs = {
        "vectors": [
            {
                "id": "ranked_properties",
                "name": "ranked_properties",
                "format": "geojson",
                "role": "map_layer",
                "geojson": ranked_geojson,
                "summary": summary,
            }
        ],
        "rasters": [],
        "tables": [
            {
                "id": "property_ranking",
                "name": "property_ranking",
                "role": "ranking_table",
                "columns": [
                    "rank",
                    "id",
                    "name",
                    "kind",
                    "price",
                    "score",
                    "best_poi_distance_m",
                    "distance_to_main_road_m",
                    "flood_risk",
                    "earthquake_risk",
                    "fire_risk",
                    "in_allowed_zone",
                ],
                "rows": table_rows,
            },
            {
                "id": "rejected_properties",
                "name": "rejected_properties",
                "role": "rejected_items",
                "columns": ["id", "name", "score", "reasons"],
                "rows": rejected_rows,
            },
        ],
        "reports": [
            {
                "id": "real_estate_ranking_report",
                "name": "real_estate_ranking_report",
                "format": "json",
                "role": "analysis_report",
                "data": report,
            }
        ],
        "documents": documents,
        "files": [],
        "artifacts": [],
    }

    layers = [
        {
            "id": "ranked_properties",
            "name": "املاک رتبه‌بندی‌شده",
            "type": "vector",
            "format": "geojson",
            "visible": True,
            "geojson": ranked_geojson,
            "summary": summary,
        }
    ]

    trace = [
        {
            "order": 1,
            "node_id": "node_001_filter_features",
            "capability_name": "filter_features",
            "plugin_id": "real_estate_ranking_bridge",
            "output_kind": "vector",
            "status": "success",
        },
        {
            "order": 2,
            "node_id": "node_002_score_features",
            "capability_name": "score_features",
            "plugin_id": "real_estate_ranking_bridge",
            "output_kind": "vector",
            "status": "success",
        },
        {
            "order": 3,
            "node_id": "node_003_rank_features",
            "capability_name": "rank_features",
            "plugin_id": "real_estate_ranking_bridge",
            "output_kind": "table",
            "status": "success",
        },
        {
            "order": 4,
            "node_id": "node_004_build_report",
            "capability_name": "build_report",
            "plugin_id": "real_estate_ranking_bridge",
            "output_kind": "json",
            "status": "success",
        },
        render_pdf_trace_step,
    ]

    if spatial_enrichment_summary.get("applied"):
        trace.insert(
            0,
            {
                "order": 0,
                "node_id": "node_000_spatial_enrichment",
                "capability_name": "feature_enrichment",
                "plugin_id": "real_estate_spatial_enrichment",
                "output_kind": "vector",
                "status": "success",
                "metrics": spatial_enrichment_summary,
            },
        )

    inspector = build_analysis_inspector(
        title=report.get("title") or "گزارش رتبه‌بندی املاک",
        status="succeeded",
        summary=summary,
        outputs=outputs,
        layers=layers,
        trace=trace,
        documents=documents,
        warnings=document_warnings,
    )

    return {
        "ok": True,
        "status": "succeeded",
        "request_id": rid,
        "query": query,
        "answer": message,
        "message": message,
        "summary": summary,
        "inspector": inspector,
        "outputs": outputs,
        "layers": layers,
        "documents": documents,
        "artifacts": outputs.get("artifacts", []),
        "files": outputs.get("files", []),
        "report": report,
        "trace": trace,
        "result": {
            "type": "real_estate_ranking",
            "summary": summary,
            "ranking": table_rows,
            "rejected": rejected_rows,
            "report": report,
            "layer_ids": ["ranked_properties"],
        },
        "warnings": document_warnings,
        "next_actions": [
            "برای تحلیل دقیق‌تر، فاصله‌ها می‌توانند با pluginهای nearest_neighbor و distance_calculator از لایه‌های واقعی محاسبه شوند.",
            "در صورت نیاز، خروجی PDF/HTML گزارش از outputs.documents قابل استفاده است.",
        ],
        "metadata": {
            "service": "OrchestratorService",
            "weighted_router": True,
            "llm_planning_enabled": llm_planning_enabled(),
            "llm_intent": llm_intent,
            "execution_mode": "real_estate_ranking_bridge",
            "capabilities": [
                "filter_features",
                "score_features",
                "rank_features",
                "build_report",
                "render_pdf",
            ],
        },
        "audit_record": {
            "status": "success",
            "execution_mode": "real_estate_ranking_bridge",
            "reason": "real estate ranking query with property features routed through MVP ranking bridge",
            "query": query,
            "request_id": rid,
            "capabilities": [
                "filter_features",
                "score_features",
                "rank_features",
                "build_report",
                "render_pdf",
            ],
            "trace": trace,
            "outputs": {
                "summary": summary,
                "ranking_table_id": "property_ranking",
                "layer_ids": ["ranked_properties"],
                "report_id": "real_estate_ranking_report",
                "document_ids": [doc.get("id") for doc in documents],
            },
        },
    }
