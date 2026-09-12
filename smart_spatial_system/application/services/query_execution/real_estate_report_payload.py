from __future__ import annotations

from typing import Any


def build_real_estate_pdf_report_payload(
    *,
    report: dict[str, Any],
    table_rows: list[dict[str, Any]],
    ranked_geojson: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    pdf_rows: list[dict[str, Any]] = []
    score_values: list[float] = []

    for row in table_rows:
        pdf_row = dict(row)

        score_value = pdf_row.get("score")
        try:
            numeric_score = float(score_value)
            score_values.append(numeric_score)
        except Exception:
            numeric_score = 0.0

        # Compatibility aliases expected by the default real_estate_report.html template.
        pdf_row.setdefault("investment_score", numeric_score)
        pdf_row.setdefault("property_name", pdf_row.get("name"))
        pdf_row.setdefault("asset_type", pdf_row.get("kind"))
        pdf_row.setdefault("nearest_poi_distance_m", pdf_row.get("best_poi_distance_m"))
        pdf_row.setdefault("main_road_distance_m", pdf_row.get("distance_to_main_road_m"))
        pdf_row.setdefault("allowed_zone", pdf_row.get("in_allowed_zone"))

        pdf_rows.append(pdf_row)

    top_row = pdf_rows[0] if pdf_rows else {}
    avg_score = round(sum(score_values) / len(score_values), 2) if score_values else None
    min_score = round(min(score_values), 2) if score_values else None
    max_score = round(max(score_values), 2) if score_values else None

    pdf_summary = {
        **summary,
        "title": report.get("title") or "Property Ranking Report",
        "notes": report.get("notes") or [],
        # ReportOut/report_builder compatible fields:
        "total_count": summary.get("eligible_count", len(pdf_rows)),
        "top_name": summary.get("top_property") or top_row.get("name"),
        "top_rank": top_row.get("rank"),
        "top_score_value": summary.get("top_score") or top_row.get("score"),
        "top_score": summary.get("top_score") or max_score,
        "avg_score": avg_score,
        "min_score": min_score,
        "max_score": max_score,
        "language": "fa",
    }

    columns = [
        {"key": "rank", "field": "rank", "label": "Rank"},
        {"key": "name", "field": "name", "label": "Property name"},
        {"key": "kind", "field": "kind", "label": "Type"},
        {"key": "price", "field": "price", "label": "Price"},
        {"key": "score", "field": "score", "label": "Score"},
        {"key": "investment_score", "field": "investment_score", "label": "Investment score"},
        {"key": "best_poi_distance_m", "field": "best_poi_distance_m", "label": "Nearest distance to metro/shopping centre"},
        {"key": "distance_to_main_road_m", "field": "distance_to_main_road_m", "label": "Distance to main road"},
        {"key": "flood_risk", "field": "flood_risk", "label": "Flood risk"},
        {"key": "earthquake_risk", "field": "earthquake_risk", "label": "Earthquake risk"},
        {"key": "fire_risk", "field": "fire_risk", "label": "Fire risk"},
        {"key": "in_allowed_zone", "field": "in_allowed_zone", "label": "In permitted zone"},
    ]

    return {
        "meta": {
            "title": report.get("title") or "Property Ranking Report",
            "language": "fa",
            "format": "pdf",
            "domain": "real_estate_spatial_ranking",
            "score_field": "score",
            "rank_field": "rank",
            "name_field": "name",
        },
        "summary": pdf_summary,
        "table": {
            "title": "Property ranking table",
            "columns": columns,
            "rows": pdf_rows,
            "total_rows": len(pdf_rows),
        },
        "map_layers": [
            {
                "id": "ranked_properties",
                "name": "Ranked properties",
                "label": "Ranked properties",
                "type": "vector",
                "format": "geojson",
                "feature_count": len(ranked_geojson.get("features") or []),
                "geojson": ranked_geojson,
            }
        ],
        "spec": {
            "report_type": "real_estate_ranking",
            "score_field": "score",
            "rank_field": "rank",
            "criteria": summary.get("criteria") or {},
        },
        "success": True,
        "errors": [],
    }
