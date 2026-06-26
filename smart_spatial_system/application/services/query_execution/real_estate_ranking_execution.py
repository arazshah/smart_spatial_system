from __future__ import annotations

from collections.abc import Callable
from typing import Any


def execute_real_estate_ranking(
    *,
    features: list[Any],
    evaluate_eligibility: Callable[[dict[str, Any]], tuple[bool, list[str], dict[str, Any]]],
    score_property: Callable[[dict[str, Any]], tuple[float, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked_features: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []

    for feature in features:
        if not isinstance(feature, dict):
            continue

        props = dict(feature.get("properties") or {})
        eligible, rejection_reasons, metrics = evaluate_eligibility(props)
        score, score_details = score_property(props)

        enriched_props = dict(props)
        enriched_props.update(
            {
                "eligible": eligible,
                "eligibility_reasons": rejection_reasons,
                "score": score,
                "score_details": score_details,
                "best_poi_distance_m": metrics.get("best_poi_distance_m"),
                "risk_summary": metrics.get("risk_levels"),
            }
        )

        enriched_feature = {
            "type": "Feature",
            "geometry": feature.get("geometry"),
            "properties": enriched_props,
        }

        if eligible:
            ranked_features.append(enriched_feature)
        else:
            rejected_rows.append(
                {
                    "id": props.get("id"),
                    "name": props.get("name"),
                    "score": score,
                    "reasons": rejection_reasons,
                }
            )

    return ranked_features, rejected_rows
