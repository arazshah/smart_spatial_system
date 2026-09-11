"""
real_estate_scoring.py

GeoChat SDK Plugin
==================

Plugin ID:
    real_estate_scoring

Purpose:
    Score and evaluate eligibility of real-estate property features using
    the MVP real-estate scoring formula (REFACTOR_PLAN.md Phase 5 -- see
    docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md, step 2, Open Question 1).

    The scoring formula is a hardcoded, non-linear formula (distance
    clamps, tiered risk penalties, a hard zone-eligibility penalty) that
    does not fit the generic score_features capability's linear-weighted-
    factors model without extending that model first. Per the plan's
    recommendation, this stays its own capability for now rather than
    generalizing score_features's contract; that can be revisited if a
    second domain needs the same shape of formula.

    This plugin wraps the existing, unchanged scoring/eligibility logic
    from
    smart_spatial_system.application.services.query_execution.real_estate_scoring
    -- the formula itself is not modified, only exposed as a capability.

Capability:
    score_real_estate_properties

    Annotates every input feature with eligibility and score details
    (does not split eligible/rejected features -- that stays a separate
    concern, resolvable with the already-registered generic
    filter_features capability via a `where` on the `eligible` property,
    per Open Question 3 in the Phase 5 plan).
"""

from __future__ import annotations

from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from smart_spatial_system.application.services.query_execution.real_estate_scoring import (
    evaluate_real_estate_eligibility,
    score_real_estate_property,
)

PLUGIN_ID = "real_estate_scoring"


def _extract_features(input_data: Any) -> list[dict[str, Any]]:
    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw = getattr(input_data, "features")
    elif isinstance(input_data, dict):
        gtype = input_data.get("type")
        if gtype == "FeatureCollection":
            raw = input_data.get("features", [])
        elif gtype == "Feature":
            raw = [input_data]
        else:
            raise ValueError("features dict must be FeatureCollection or Feature.")
    elif isinstance(input_data, list):
        raw = input_data
    else:
        raise ValueError(
            "features must be VectorOut-like, FeatureCollection, Feature, or list[Feature]."
        )

    if not isinstance(raw, list):
        raise ValueError("features must be a list.")

    return [f for f in raw if isinstance(f, dict)]


def _annotate_feature(feature: dict[str, Any]) -> dict[str, Any]:
    props = dict(feature.get("properties") or {})

    eligible, rejection_reasons, eligibility_metrics = evaluate_real_estate_eligibility(
        props
    )
    score, score_details = score_real_estate_property(props)

    annotated_props = dict(props)
    annotated_props.update(
        {
            "eligible": eligible,
            "eligibility_reasons": rejection_reasons,
            "score": score,
            "score_details": score_details,
            "best_poi_distance_m": eligibility_metrics.get("best_poi_distance_m"),
            "risk_summary": eligibility_metrics.get("risk_levels"),
        }
    )

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": annotated_props,
    }


@capability(
    name="score_real_estate_properties",
    keywords=[
        "real estate score",
        "property score",
        "property eligibility",
        "investment score",
        "real estate ranking",
        "امتیاز ملک",
        "امتیاز املاک",
        "واجد شرایط",
        "امتیاز سرمایه‌گذاری",
        "رتبه‌بندی املاک",
    ],
    description=(
        "Score real-estate property features and evaluate eligibility using "
        "the MVP real-estate scoring formula (distance to metro/mall/main "
        "road, flood/earthquake/fire risk, allowed construction zone, "
        "price). Annotates every feature with eligible, eligibility_reasons, "
        "score and score_details; does not split eligible/rejected features."
    ),
    required_inputs=["features"],
    optional_inputs=["metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "domain": "real_estate",
        "operation": "real_estate_scoring",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.real_estate_scoring",
    },
)
def score_real_estate_properties(
    features: Any,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Score real-estate property features and evaluate eligibility.

    Args:
        features:
            VectorOut, FeatureCollection, Feature, or list[Feature] of
            property features.
        metadata:
            Optional metadata to merge into the output.

    Returns:
        VectorOut with every input feature annotated with eligible,
        eligibility_reasons, score, score_details, best_poi_distance_m and
        risk_summary properties. Feature count and order are preserved.
    """
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    input_features = _extract_features(features)
    output_features = [_annotate_feature(feature) for feature in input_features]

    eligible_count = sum(
        1 for f in output_features if f["properties"].get("eligible")
    )

    output_metadata = {
        "source": "real_estate_scoring",
        "operation": "score_real_estate_properties",
        "feature_count": len(output_features),
        "eligible_count": eligible_count,
        "rejected_count": len(output_features) - eligible_count,
        **(metadata or {}),
    }

    return VectorOut(features=output_features, metadata=output_metadata)


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Real Estate Scoring",
    description=(
        "Scores real-estate property features and evaluates eligibility "
        "using the MVP real-estate scoring formula."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
