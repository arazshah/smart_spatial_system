from __future__ import annotations

from typing import Any


def build_real_estate_ranking_artifacts(
    *,
    features: list[Any],
    ranked_features: list[dict[str, Any]],
    rejected_rows: list[dict[str, Any]],
    spatial_enrichment_summary: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    str,
]:
    ranked_features.sort(
        key=lambda f: float((f.get("properties") or {}).get("score") or 0),
        reverse=True,
    )

    table_rows: list[dict[str, Any]] = []
    for idx, feature in enumerate(ranked_features, start=1):
        props = feature.get("properties") or {}
        props["rank"] = idx

        table_rows.append(
            {
                "rank": idx,
                "id": props.get("id"),
                "name": props.get("name"),
                "kind": props.get("kind") or props.get("property_type"),
                "price": props.get("price"),
                "score": props.get("score"),
                "best_poi_distance_m": props.get("best_poi_distance_m"),
                "distance_to_metro_m": props.get("distance_to_metro_m"),
                "distance_to_mall_m": props.get("distance_to_mall_m"),
                "distance_to_main_road_m": props.get("distance_to_main_road_m"),
                "flood_risk": props.get("flood_risk"),
                "earthquake_risk": props.get("earthquake_risk"),
                "fire_risk": props.get("fire_risk"),
                "in_allowed_zone": (
                    props.get("in_allowed_zone")
                    if props.get("in_allowed_zone") is not None
                    else props.get("build_zone_allowed")
                    if props.get("build_zone_allowed") is not None
                    else props.get("construction_allowed")
                ),
            }
        )

    ranked_geojson = {
        "type": "FeatureCollection",
        "features": ranked_features,
    }

    top_row = table_rows[0] if table_rows else None

    summary = {
        "candidate_count": len(features),
        "eligible_count": len(ranked_features),
        "rejected_count": len(rejected_rows),
        "top_property": top_row.get("name") if top_row else None,
        "top_score": top_row.get("score") if top_row else None,
        "criteria": {
            "max_distance_to_metro_or_mall_m": 500,
            "max_distance_to_main_road_m": 150,
            "excluded_risk_level": "high",
            "medium_risk_policy": "allowed_with_score_penalty",
            "requires_allowed_construction_zone": True,
        },
    }

    if spatial_enrichment_summary.get("applied"):
        summary["spatial_enrichment"] = spatial_enrichment_summary

    report = {
        "title": "گزارش رتبه‌بندی و تحلیل سرمایه‌گذاری املاک",
        "language": "fa",
        "summary": summary,
        "ranking": table_rows,
        "rejected": rejected_rows,
        "notes": [
            "املاک با ریسک high یا خارج از محدوده مجاز ساخت‌وساز حذف شده‌اند.",
            "ریسک medium در MVP حذف نشده و به‌صورت جریمه امتیازی اعمال شده است.",
            "امتیاز نهایی بر اساس نزدیکی به مترو/مرکز خرید، خیابان اصلی، ریسک‌ها، محدوده مجاز و قیمت محاسبه شده است.",
        ],
    }

    message = (
        f"رتبه‌بندی املاک انجام شد. از {len(features)} ملک، "
        f"{len(ranked_features)} ملک واجد شرایط بودند."
    )
    if top_row:
        message += f" بهترین گزینه: {top_row.get('name')} با امتیاز {top_row.get('score')}."

    return table_rows, ranked_geojson, summary, report, message
