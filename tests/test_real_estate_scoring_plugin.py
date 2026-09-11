"""
Unit tests for plugins.real_estate_scoring (REFACTOR_PLAN.md Phase 5, step 2
-- see docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md).

Exercises the score_real_estate_properties capability directly, independent
of tests/test_real_estate_ranking_golden.py, which still goes through the
old direct-handler path unchanged (this plugin is not wired into that path
yet -- that happens in later steps of the Phase 5 migration).

Run:
    pytest tests/test_real_estate_scoring_plugin.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.real_estate_scoring import score_real_estate_properties  # noqa: E402


def _sample_features() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "p1",
                    "name": "آپارتمان مرکز",
                    "price": 12000000000,
                    "kind": "apartment",
                    "distance_to_metro_m": 420,
                    "distance_to_mall_m": 620,
                    "distance_to_main_road_m": 90,
                    "flood_risk": "low",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                    "in_allowed_zone": True,
                },
                "geometry": {"type": "Point", "coordinates": [51.389, 35.689]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "p2",
                    "name": "ویلای لوکس",
                    "price": 18000000000,
                    "kind": "villa",
                    "distance_to_metro_m": 700,
                    "distance_to_mall_m": 310,
                    "distance_to_main_road_m": 60,
                    "flood_risk": "low",
                    "earthquake_risk": "low",
                    "fire_risk": "low",
                    "in_allowed_zone": True,
                },
                "geometry": {"type": "Point", "coordinates": [51.395, 35.692]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "p3",
                    "name": "زمین حاشیه‌ای",
                    "price": 7500000000,
                    "kind": "land",
                    "distance_to_metro_m": 1200,
                    "distance_to_mall_m": 950,
                    "distance_to_main_road_m": 220,
                    "flood_risk": "high",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                    "in_allowed_zone": False,
                },
                "geometry": {"type": "Point", "coordinates": [51.401, 35.684]},
            },
        ],
    }


def test_annotates_every_feature_and_preserves_order() -> None:
    result = score_real_estate_properties(_sample_features())

    assert len(result.features) == 3
    assert [f["properties"]["id"] for f in result.features] == ["p1", "p2", "p3"]


def test_annotates_eligible_and_score_fields() -> None:
    result = score_real_estate_properties(_sample_features())
    by_id = {f["properties"]["id"]: f["properties"] for f in result.features}

    assert by_id["p1"]["eligible"] is True
    assert by_id["p1"]["eligibility_reasons"] == []
    assert by_id["p1"]["score"] == 75.0

    assert by_id["p2"]["eligible"] is True
    assert by_id["p2"]["score"] == 91.0

    assert by_id["p3"]["eligible"] is False
    assert by_id["p3"]["eligibility_reasons"] == [
        "farther_than_500m_from_metro_or_mall",
        "far_from_main_road",
        "high_flood_risk",
        "outside_allowed_construction_zone",
    ]


def test_preserves_geometry_unchanged() -> None:
    result = score_real_estate_properties(_sample_features())

    assert result.features[0]["geometry"] == {
        "type": "Point",
        "coordinates": [51.389, 35.689],
    }


def test_matches_direct_calls_to_underlying_scoring_functions() -> None:
    from smart_spatial_system.application.services.query_execution.real_estate_scoring import (
        evaluate_real_estate_eligibility,
        score_real_estate_property,
    )

    features = _sample_features()["features"]
    result = score_real_estate_properties({"type": "FeatureCollection", "features": features})

    for original, scored in zip(features, result.features, strict=True):
        props = original["properties"]
        expected_eligible, expected_reasons, _ = evaluate_real_estate_eligibility(props)
        expected_score, _ = score_real_estate_property(props)

        assert scored["properties"]["eligible"] == expected_eligible
        assert scored["properties"]["eligibility_reasons"] == expected_reasons
        assert scored["properties"]["score"] == expected_score


def test_output_metadata_reports_counts() -> None:
    result = score_real_estate_properties(_sample_features())

    assert result.metadata["feature_count"] == 3
    assert result.metadata["eligible_count"] == 2
    assert result.metadata["rejected_count"] == 1
    assert result.metadata["operation"] == "score_real_estate_properties"


def test_accepts_bare_feature_list() -> None:
    features = _sample_features()["features"]

    result = score_real_estate_properties(features)

    assert len(result.features) == 3


def test_rejects_non_dict_metadata() -> None:
    import pytest

    with pytest.raises(ValueError, match="metadata must be a dict"):
        score_real_estate_properties(_sample_features(), metadata="not-a-dict")


def test_empty_feature_collection_returns_empty_output() -> None:
    result = score_real_estate_properties({"type": "FeatureCollection", "features": []})

    assert result.features == []
    assert result.metadata["feature_count"] == 0
    assert result.metadata["eligible_count"] == 0
    assert result.metadata["rejected_count"] == 0
