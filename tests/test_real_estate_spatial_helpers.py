import pytest

from smart_spatial_system.application.services.real_estate_spatial_helpers import (
    distance_point_to_geometry_m,
    distance_point_to_point_m,
    distance_point_to_segment_m,
    feature_point_lonlat,
    has_bool_like_value,
    has_metric_value,
    nearest_distance_to_features_m,
    normalize_risk_level,
    point_in_polygon_feature_lonlat,
    point_in_ring_lonlat,
    to_float_or_none,
)


def test_to_float_or_none() -> None:
    assert to_float_or_none("12.5") == 12.5
    assert to_float_or_none(3) == 3.0
    assert to_float_or_none(None) is None
    assert to_float_or_none("bad") is None


def test_feature_point_lonlat_extracts_point_coordinates() -> None:
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": ["51.4", "35.7"],
        },
    }

    assert feature_point_lonlat(feature) == (51.4, 35.7)


def test_feature_point_lonlat_rejects_non_point() -> None:
    assert feature_point_lonlat({"geometry": {"type": "Polygon", "coordinates": []}}) is None
    assert feature_point_lonlat({"geometry": {"type": "Point", "coordinates": ["bad"]}}) is None


def test_point_in_ring_lonlat_detects_inside_and_outside() -> None:
    ring = [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 10],
        [0, 0],
    ]

    assert point_in_ring_lonlat((5, 5), ring) is True
    assert point_in_ring_lonlat((15, 5), ring) is False


def test_point_in_polygon_feature_lonlat_supports_holes() -> None:
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [0, 0],
                    [10, 0],
                    [10, 10],
                    [0, 10],
                    [0, 0],
                ],
                [
                    [4, 4],
                    [6, 4],
                    [6, 6],
                    [4, 6],
                    [4, 4],
                ],
            ],
        },
    }

    assert point_in_polygon_feature_lonlat((2, 2), feature) is True
    assert point_in_polygon_feature_lonlat((5, 5), feature) is False
    assert point_in_polygon_feature_lonlat((20, 20), feature) is False


def test_distance_point_to_point_m_is_zero_for_same_point() -> None:
    assert distance_point_to_point_m((51.4, 35.7), (51.4, 35.7)) == pytest.approx(0.0)


def test_distance_point_to_segment_m_returns_positive_distance() -> None:
    dist = distance_point_to_segment_m(
        (0, 1),
        (0, 0),
        (1, 0),
    )

    assert dist > 0


def test_distance_point_to_geometry_m_returns_zero_inside_polygon() -> None:
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [0, 0],
                    [10, 0],
                    [10, 10],
                    [0, 10],
                    [0, 0],
                ]
            ],
        },
    }

    assert distance_point_to_geometry_m((5, 5), feature) == 0.0


def test_distance_point_to_geometry_m_handles_point_and_line() -> None:
    point_feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [0, 0],
        },
    }
    line_feature = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[0, 0], [1, 0]],
        },
    }

    assert distance_point_to_geometry_m((0, 0), point_feature) == pytest.approx(0.0)
    assert distance_point_to_geometry_m((0, 1), line_feature) is not None


def test_nearest_distance_to_features_m_returns_min_distance() -> None:
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [10, 10],
            },
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [0, 0],
            },
        },
    ]

    assert nearest_distance_to_features_m((0, 0), features) == pytest.approx(0.0)


def test_metric_and_bool_helpers() -> None:
    props = {
        "distance": "12.5",
        "empty": "",
        "flag": False,
    }

    assert has_metric_value(props, "distance") is True
    assert has_metric_value(props, "empty") is False
    assert has_metric_value(props, "missing") is False

    assert has_bool_like_value(props, "flag") is True
    assert has_bool_like_value(props, "missing", "flag") is True
    assert has_bool_like_value(props, "missing") is False


def test_normalize_risk_level() -> None:
    assert normalize_risk_level("low") == "low"
    assert normalize_risk_level("پایین") == "low"
    assert normalize_risk_level("medium") == "medium"
    assert normalize_risk_level("متوسط") == "medium"
    assert normalize_risk_level("high") == "high"
    assert normalize_risk_level("بالا") == "high"
    assert normalize_risk_level("unknown-value") == "medium"
