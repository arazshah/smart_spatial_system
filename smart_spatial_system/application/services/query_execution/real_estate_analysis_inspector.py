from __future__ import annotations

from typing import Any


def build_real_estate_analysis_inspector(
    *,
    title: str,
    status: str,
    summary: dict[str, Any],
    outputs: dict[str, Any],
    layers: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build a frontend-friendly Analysis Inspector payload.

    This is intentionally additive/non-breaking:
    existing response.summary / outputs / layers / audit_record remain unchanged.
    The frontend can prefer response.inspector when available.
    """

    def _feature_count_from_vector(vector: dict[str, Any]) -> int:
        geojson = vector.get("geojson") or {}
        features = geojson.get("features") if isinstance(geojson, dict) else None
        return len(features) if isinstance(features, list) else 0

    def _row_count_from_table(table: dict[str, Any]) -> int:
        rows = table.get("rows")
        return len(rows) if isinstance(rows, list) else 0

    def _trace_label(step: dict[str, Any]) -> str:
        capability = step.get("capability_name")
        labels = {
            "filter_features": "Filter properties",
            "score_features": "Scoring",
            "rank_features": "Ranking",
            "build_report": "Build report",
            "render_pdf": "Render PDF",
        }
        return labels.get(str(capability), str(capability or step.get("node_id") or "Step"))

    summary_cards = [
        {
            "id": "candidate_count",
            "label": "Total properties",
            "value": summary.get("candidate_count", 0),
            "tone": "neutral",
            "icon": "⌂",
        },
        {
            "id": "eligible_count",
            "label": "Eligible",
            "value": summary.get("eligible_count", 0),
            "tone": "success",
            "icon": "✓",
        },
        {
            "id": "rejected_count",
            "label": "Rejected",
            "value": summary.get("rejected_count", 0),
            "tone": "warning",
            "icon": "!",
        },
        {
            "id": "top_property",
            "label": "Top choice",
            "value": summary.get("top_property") or "—",
            "tone": "primary",
            "icon": "★",
        },
        {
            "id": "top_score",
            "label": "Top score",
            "value": summary.get("top_score") if summary.get("top_score") is not None else "—",
            "tone": "primary",
            "icon": "↗",
        },
    ]

    inspector_outputs: list[dict[str, Any]] = []

    for vector in outputs.get("vectors") or []:
        if not isinstance(vector, dict):
            continue
        inspector_outputs.append(
            {
                "id": vector.get("id") or vector.get("name"),
                "type": "vector",
                "label": vector.get("label") or vector.get("name") or vector.get("id") or "Vector layer",
                "name": vector.get("name") or vector.get("id"),
                "role": vector.get("role") or "map_layer",
                "format": vector.get("format") or "geojson",
                "count": _feature_count_from_vector(vector),
                "source": "outputs.vectors",
            }
        )

    for table in outputs.get("tables") or []:
        if not isinstance(table, dict):
            continue
        inspector_outputs.append(
            {
                "id": table.get("id") or table.get("name"),
                "type": "table",
                "label": table.get("label") or table.get("name") or table.get("id") or "Table",
                "name": table.get("name") or table.get("id"),
                "role": table.get("role") or "table",
                "format": "table",
                "count": _row_count_from_table(table),
                "source": "outputs.tables",
            }
        )

    for report_item in outputs.get("reports") or []:
        if not isinstance(report_item, dict):
            continue
        inspector_outputs.append(
            {
                "id": report_item.get("id") or report_item.get("name"),
                "type": "report",
                "label": report_item.get("label") or report_item.get("name") or report_item.get("id") or "Report",
                "name": report_item.get("name") or report_item.get("id"),
                "role": report_item.get("role") or "analysis_report",
                "format": report_item.get("format") or "json",
                "count": 1,
                "source": "outputs.reports",
            }
        )

    inspector_documents: list[dict[str, Any]] = []
    primary_actions: list[dict[str, Any]] = []

    for doc in documents or []:
        if not isinstance(doc, dict):
            continue

        doc_id = doc.get("id") or doc.get("name") or f"document_{len(inspector_documents) + 1}"
        doc_format = doc.get("format") or "document"
        doc_path = (
            doc.get("download_url")
            or doc.get("preview_url")
            or doc.get("url")
            or doc.get("path")
            or doc.get("file_path")
        )

        normalized_doc = {
            "id": doc_id,
            "type": "document",
            "label": doc.get("label") or doc.get("name") or ("PDF report" if doc_format == "pdf" else "Report document"),
            "name": doc.get("name") or doc_id,
            "role": doc.get("role") or "document",
            "format": doc_format,
            "mime_type": doc.get("mime_type"),
            "path": doc_path,
            "file_path": doc.get("file_path"),
            "download_url": doc.get("download_url"),
            "preview_url": doc.get("preview_url"),
            "size_bytes": doc.get("size_bytes"),
            "source": "outputs.documents",
        }
        inspector_documents.append(normalized_doc)
        inspector_outputs.append(
            {
                "id": doc_id,
                "type": "document",
                "label": normalized_doc["label"],
                "name": normalized_doc["name"],
                "role": normalized_doc["role"],
                "format": normalized_doc["format"],
                "path": normalized_doc["path"],
                "download_url": normalized_doc.get("download_url"),
                "preview_url": normalized_doc.get("preview_url"),
                "count": 1,
                "source": "outputs.documents",
            }
        )

        if doc_path:
            action_label = "Download PDF report" if doc_format == "pdf" else "View report document"
            primary_actions.append(
                {
                    "id": "download_pdf" if doc_format == "pdf" else f"open_{doc_id}",
                    "label": action_label,
                    "type": "download" if doc_format == "pdf" else "open",
                    "target_output_id": doc_id,
                    "path": doc_path,
                    "download_url": doc.get("download_url"),
                    "preview_url": doc.get("preview_url"),
                    "mime_type": doc.get("mime_type"),
                }
            )

    inspector_layers: list[dict[str, Any]] = []
    for layer in layers or []:
        if not isinstance(layer, dict):
            continue
        inspector_layers.append(
            {
                "id": layer.get("id") or layer.get("name"),
                "label": layer.get("label") or layer.get("name") or layer.get("id") or "Layer",
                "name": layer.get("name") or layer.get("id"),
                "type": layer.get("type") or "vector",
                "format": layer.get("format") or "geojson",
                "visible": layer.get("visible", True),
                "count": _feature_count_from_vector(layer),
            }
        )

    inspector_trace: list[dict[str, Any]] = []
    for step in trace or []:
        if not isinstance(step, dict):
            continue
        inspector_trace.append(
            {
                "order": step.get("order"),
                "id": step.get("node_id") or step.get("capability_name"),
                "label": _trace_label(step),
                "capability_name": step.get("capability_name"),
                "plugin_id": step.get("plugin_id"),
                "output_kind": step.get("output_kind"),
                "status": step.get("status") or "unknown",
                "artifact_id": step.get("artifact_id"),
                "path": step.get("path"),
                "errors": step.get("errors") or ([] if not step.get("error") else [step.get("error")]),
            }
        )

    return {
        "kind": "analysis_inspector",
        "schema_version": "1.0",
        "domain": "real_estate_spatial_ranking",
        "title": title,
        "status": status,
        "language": "fa",
        "summary_cards": summary_cards,
        "outputs": inspector_outputs,
        "tables": outputs.get("tables") or [],
        "documents": inspector_documents,
        "layers": inspector_layers,
        "trace": inspector_trace,
        "primary_actions": primary_actions,
        "warnings": warnings or [],
        "tabs": [
            {"id": "summary", "label": "Summary", "count": len(summary_cards)},
            {"id": "outputs", "label": "Outputs", "count": len(inspector_outputs)},
            {"id": "tables", "label": "Tables", "count": len(outputs.get("tables") or [])},
            {"id": "documents", "label": "Documents", "count": len(inspector_documents)},
            {"id": "layers", "label": "Layers", "count": len(inspector_layers)},
            {"id": "trace", "label": "Trace", "count": len(inspector_trace)},
        ],
    }
