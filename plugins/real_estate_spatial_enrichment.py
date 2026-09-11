"""
real_estate_spatial_enrichment.py

GeoChat SDK Plugin
==================

Plugin ID:
    real_estate_spatial_enrichment

Purpose:
    Fill missing real-estate ranking metrics (distance to metro/mall/main
    road, allowed construction zone) from optional spatial layers
    (REFACTOR_PLAN.md Phase 5 -- see docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md,
    step 3, Open Question 2).

    This is an additive fallback pass over multiple optional layers in one
    call: existing property distance/risk fields are preserved, only
    missing values are computed. Per the plan's recommendation, this stays
    one capability rather than decomposing into independent
    nearest_neighbor/spatial_predicate DAG nodes, since that would change
    the "only fill what's missing" semantics; revisit only if a later use
    case needs the individual steps addressable separately.

    This plugin wraps the existing, unchanged enrichment logic from
    smart_spatial_system.application.services.query_execution.real_estate_context::enrich_property_feature_collection_with_spatial_context
    -- the enrichment behavior itself is not modified, only exposed as a
    capability.

Capability:
    enrich_real_estate_spatial_context
"""

from __future__ import annotations

from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from smart_spatial_system.application.services.query_execution.real_estate_context import (
    enrich_property_feature_collection_with_spatial_context,
)
from smart_spatial_system.application.services.real_estate_spatial_helpers import (
    feature_point_lonlat,
    has_bool_like_value,
    has_metric_value,
    nearest_distance_to_features_m,
    point_in_polygon_feature_lonlat,
)

PLUGIN_ID = "real_estate_spatial_enrichment"


def _extract_feature_collection(input_data: Any) -> dict[str, Any]:
    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        return {
            "type": "FeatureCollection",
            "features": list(getattr(input_data, "features") or []),
        }

    if isinstance(input_data, dict):
        gtype = input_data.get("type")
        if gtype == "FeatureCollection":
            return input_data
        if gtype == "Feature":
            return {"type": "FeatureCollection", "features": [input_data]}
        raise ValueError("features dict must be FeatureCollection or Feature.")

    if isinstance(input_data, list):
        return {"type": "FeatureCollection", "features": input_data}

    raise ValueError(
        "features must be VectorOut-like, FeatureCollection, Feature, or list[Feature]."
    )


def _extract_layer_features(input_data: Any) -> list[dict[str, Any]]:
    if input_data is None:
        return []
    fc = _extract_feature_collection(input_data)
    features = fc.get("features") or []
    return [f for f in features if isinstance(f, dict)]


@capability(
    name="enrich_real_estate_spatial_context",
    keywords=[
        "spatial enrichment",
        "distance to metro",
        "distance to mall",
        "distance to main road",
        "allowed construction zone",
        "real estate context",
        "غنی‌سازی مکانی",
        "فاصله تا مترو",
        "فاصله تا مرکز خرید",
        "فاصله تا خیابان اصلی",
        "محدوده مجاز ساخت",
    ],
    description=(
        "Fill missing real-estate ranking metrics (distance_to_metro_m, "
        "distance_to_mall_m, distance_to_main_road_m, in_allowed_zone) on "
        "property features from optional metro/mall/main_road/allowed_zone "
        "layers. Additive fallback: existing values on a property are "
        "preserved, only missing ones are computed."
    ),
    required_inputs=["features"],
    optional_inputs=[
        "metro",
        "malls",
        "main_roads",
        "allowed_zones",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "domain": "real_estate",
        "operation": "real_estate_spatial_enrichment",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.real_estate_spatial_enrichment",
    },
)
def enrich_real_estate_spatial_context(
    features: Any,
    metro: Any = None,
    malls: Any = None,
    main_roads: Any = None,
    allowed_zones: Any = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Enrich property features with spatial context from optional layers.

    Args:
        features:
            VectorOut, FeatureCollection, Feature, or list[Feature] of
            property features.
        metro:
            Optional metro station features (points).
        malls:
            Optional shopping-center features (points/polygons).
        main_roads:
            Optional main-road features (lines).
        allowed_zones:
            Optional allowed-construction-zone features (polygons).
        metadata:
            Optional metadata to merge into the output.

    Returns:
        VectorOut with every input feature carrying distance_to_metro_m,
        distance_to_mall_m, distance_to_main_road_m and in_allowed_zone
        (plus build_zone_allowed/construction_allowed aliases) filled in
        where they were missing and a layer was provided.
    """
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    feature_collection = _extract_feature_collection(features)

    spatial_context = {
        "metro": _extract_layer_features(metro),
        "malls": _extract_layer_features(malls),
        "main_roads": _extract_layer_features(main_roads),
        "allowed_zones": _extract_layer_features(allowed_zones),
    }

    enriched_fc, enrichment_summary = enrich_property_feature_collection_with_spatial_context(
        feature_collection,
        spatial_context,
        feature_point_lonlat=feature_point_lonlat,
        has_metric_value=has_metric_value,
        nearest_distance_to_features_m=nearest_distance_to_features_m,
        has_bool_like_value=has_bool_like_value,
        point_in_polygon_feature_lonlat=point_in_polygon_feature_lonlat,
    )

    output_metadata = {
        "source": "real_estate_spatial_enrichment",
        "operation": "enrich_real_estate_spatial_context",
        "enrichment_summary": enrichment_summary,
        **(metadata or {}),
    }

    return VectorOut(
        features=enriched_fc.get("features") or [],
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Real Estate Spatial Enrichment",
    description=(
        "Fills missing real-estate ranking metrics from optional metro/"
        "mall/main-road/allowed-zone spatial layers."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
