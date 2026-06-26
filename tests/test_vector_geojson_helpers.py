import json
from types import SimpleNamespace

from smart_spatial_system.application.services.vector_geojson_helpers import (
    find_geojson_like,
    read_geojson_path_if_possible,
    summarize_feature_collection,
)


def test_read_geojson_path_if_possible_reads_geojson_file(tmp_path) -> None:
    path = tmp_path / "data.geojson"
    payload = {
        "type": "FeatureCollection",
        "features": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert read_geojson_path_if_possible(str(path)) == payload


def test_read_geojson_path_if_possible_returns_none_for_non_geojson_path(tmp_path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("not geojson", encoding="utf-8")

    assert read_geojson_path_if_possible(str(path)) is None
    assert read_geojson_path_if_possible(123) is None
    assert read_geojson_path_if_possible(str(tmp_path / "missing.geojson")) is None


def test_find_geojson_like_returns_feature_collection() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [],
    }

    assert find_geojson_like({"payload": payload}) == payload


def test_find_geojson_like_wraps_feature() -> None:
    feature = {
        "type": "Feature",
        "properties": {"name": "A"},
        "geometry": {
            "type": "Point",
            "coordinates": [51.4, 35.7],
        },
    }

    result = find_geojson_like({"data": feature})

    assert result == {
        "type": "FeatureCollection",
        "features": [feature],
    }


def test_find_geojson_like_wraps_geometry() -> None:
    geometry = {
        "type": "Point",
        "coordinates": [51.4, 35.7],
    }

    result = find_geojson_like({"geometry": geometry})

    assert result is not None
    assert result["type"] == "FeatureCollection"
    assert result["features"][0]["geometry"] == geometry


def test_find_geojson_like_reads_path_from_nested_payload(tmp_path) -> None:
    path = tmp_path / "data.geojson"
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.4, 35.7],
                },
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = find_geojson_like({"inputs": {"path": str(path)}})

    assert result == payload


def test_find_geojson_like_supports_object_with_dict() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [],
    }

    obj = SimpleNamespace(payload=payload)

    assert find_geojson_like(obj) == payload


def test_summarize_feature_collection_counts_features_and_properties() -> None:
    summary = summarize_feature_collection(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "A", "score": 10},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [51.4, 35.7],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"name": "B"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": None,
                },
            ],
        }
    )

    assert summary == {
        "feature_count": 3,
        "geometry_counts": {
            "Point": 1,
            "Polygon": 1,
            "Unknown": 1,
        },
        "property_keys": ["name", "score"],
    }
