"""
Unit tests for plugins.real_estate_spatial_enrichment (REFACTOR_PLAN.md
Phase 5, step 3 -- see docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md).

Exercises the enrich_real_estate_spatial_context capability directly,
independent of tests/test_real_estate_ranking_golden.py, which still goes
through the old direct-handler path unchanged (this plugin is not wired
into that path yet -- that happens in later steps of the Phase 5
migration).

Run:
    pytest tests/test_real_estate_spatial_enrichment_plugin.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.real_estate_spatial_enrichment import (  # noqa: E402
    enrich_real_estate_spatial_context,
)


def _property_without_metrics() -> dict:
    return {
        "type": "Feature",
        "properties": {"id": "p1", "name": "test"},
        "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
    }


def _property_with_existing_metrics() -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": "p2",
            "name": "already-has-metrics",
            "distance_to_metro_m": 999,
        },
        "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
    }


def _metro_station_at_origin_offset() -> dict:
    return {
        "type": "Feature",
        "properties": {"name": "Metro A"},
        "geometry": {"type": "Point", "coordinates": [0.0, 0.01]},
    }


def _allowed_zone_polygon_covering_origin() -> dict:
    return {
        "type": "Feature",
        "properties": {"name": "Zone A"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-1.0, -1.0],
                    [1.0, -1.0],
                    [1.0, 1.0],
                    [-1.0, 1.0],
                    [-1.0, -1.0],
                ]
            ],
        },
    }


def test_fills_missing_distance_to_metro_when_layer_provided() -> None:
    result = enrich_real_estate_spatial_context(
        {"type": "FeatureCollection", "features": [_property_without_metrics()]},
        metro={
            "type": "FeatureCollection",
            "features": [_metro_station_at_origin_offset()],
        },
    )

    props = result.features[0]["properties"]
    assert props["distance_to_metro_m"] > 0
    assert props["spatial_enrichment_applied"] is True


def test_does_not_overwrite_existing_distance_value() -> None:
    result = enrich_real_estate_spatial_context(
        {
            "type": "FeatureCollection",
            "features": [_property_with_existing_metrics()],
        },
        metro={
            "type": "FeatureCollection",
            "features": [_metro_station_at_origin_offset()],
        },
    )

    props = result.features[0]["properties"]
    assert props["distance_to_metro_m"] == 999


def test_no_layers_leaves_properties_untouched() -> None:
    original = _property_without_metrics()
    result = enrich_real_estate_spatial_context(
        {"type": "FeatureCollection", "features": [original]},
    )

    props = result.features[0]["properties"]
    assert "distance_to_metro_m" not in props
    assert "spatial_enrichment_applied" not in props
    assert props == original["properties"]


def test_fills_in_allowed_zone_from_polygon_layer() -> None:
    result = enrich_real_estate_spatial_context(
        {"type": "FeatureCollection", "features": [_property_without_metrics()]},
        allowed_zones={
            "type": "FeatureCollection",
            "features": [_allowed_zone_polygon_covering_origin()],
        },
    )

    props = result.features[0]["properties"]
    assert props["in_allowed_zone"] is True
    assert props["build_zone_allowed"] is True
    assert props["construction_allowed"] is True


def test_accepts_bare_feature_list_for_layers() -> None:
    result = enrich_real_estate_spatial_context(
        [_property_without_metrics()],
        metro=[_metro_station_at_origin_offset()],
    )

    assert result.features[0]["properties"]["distance_to_metro_m"] > 0


def test_output_metadata_carries_enrichment_summary() -> None:
    result = enrich_real_estate_spatial_context(
        {"type": "FeatureCollection", "features": [_property_without_metrics()]},
        metro={
            "type": "FeatureCollection",
            "features": [_metro_station_at_origin_offset()],
        },
    )

    summary = result.metadata["enrichment_summary"]
    assert summary["applied"] is True
    assert summary["touched_property_count"] == 1
    assert summary["context_counts"]["metro"] == 1
    assert summary["context_counts"]["malls"] == 0
    assert result.metadata["operation"] == "enrich_real_estate_spatial_context"


def test_preserves_feature_count_and_geometry() -> None:
    features = [_property_without_metrics(), _property_with_existing_metrics()]
    result = enrich_real_estate_spatial_context(
        {"type": "FeatureCollection", "features": features},
    )

    assert len(result.features) == 2
    assert result.features[0]["geometry"] == {
        "type": "Point",
        "coordinates": [0.0, 0.0],
    }


def test_rejects_non_dict_metadata() -> None:
    import pytest

    with pytest.raises(ValueError, match="metadata must be a dict"):
        enrich_real_estate_spatial_context(
            {"type": "FeatureCollection", "features": []},
            metadata="not-a-dict",
        )


def test_matches_direct_call_to_underlying_enrichment_function() -> None:
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

    features = [_property_without_metrics()]
    metro_features = [_metro_station_at_origin_offset()]

    expected_fc, expected_summary = enrich_property_feature_collection_with_spatial_context(
        {"type": "FeatureCollection", "features": features},
        {
            "metro": metro_features,
            "malls": [],
            "main_roads": [],
            "allowed_zones": [],
        },
        feature_point_lonlat=feature_point_lonlat,
        has_metric_value=has_metric_value,
        nearest_distance_to_features_m=nearest_distance_to_features_m,
        has_bool_like_value=has_bool_like_value,
        point_in_polygon_feature_lonlat=point_in_polygon_feature_lonlat,
    )

    result = enrich_real_estate_spatial_context(
        {"type": "FeatureCollection", "features": [_property_without_metrics()]},
        metro={"type": "FeatureCollection", "features": [_metro_station_at_origin_offset()]},
    )

    assert result.features == expected_fc["features"]
    assert result.metadata["enrichment_summary"] == expected_summary
