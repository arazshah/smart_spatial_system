"""
Tests for spatial_query_filter plugin.

Run:
    pytest tests/test_spatial_query_filter.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.spatial_query_filter import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    MISSING,
    _bbox_within,
    _bboxes_intersect,
    _compare_values,
    _eval_where,
    _extract_features,
    _geometry_bbox,
    _get_path,
    _normalize_bbox,
    _normalize_geometry_types,
    _validate_bbox_mode,
    _validate_limit,
    _validate_offset,
    _validate_sort_order,
    filter_features,
)


FEATURES = [
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [51.0, 35.0]},
        "properties": {
            "id": 1,
            "name": "Tehran",
            "type": "city",
            "population": 9000000,
            "active": True,
            "address": {"country": "Iran", "province": "Tehran"},
        },
    },
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [51.67, 32.65]},
        "properties": {
            "id": 2,
            "name": "Isfahan",
            "type": "city",
            "population": 2000000,
            "active": True,
            "address": {"country": "Iran", "province": "Isfahan"},
        },
    },
    {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [0.0, 0.0],
                [10.0, 0.0],
                [10.0, 10.0],
                [0.0, 10.0],
                [0.0, 0.0],
            ]],
        },
        "properties": {
            "id": 3,
            "name": "Flood Zone A",
            "type": "hazard",
            "risk": "high",
            "active": False,
        },
    },
    {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "id": 4,
            "name": None,
            "type": "unknown",
            "active": False,
        },
    },
]


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "spatial_query_filter"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Spatial Query Filter"


def test_validate_bbox_mode() -> None:
    assert _validate_bbox_mode("intersects") == "intersects"
    assert _validate_bbox_mode("within") == "within"

    with pytest.raises(ValueError):
        _validate_bbox_mode("bad")


def test_validate_sort_order() -> None:
    assert _validate_sort_order("asc") == "asc"
    assert _validate_sort_order("desc") == "desc"

    with pytest.raises(ValueError):
        _validate_sort_order("bad")


def test_validate_limit() -> None:
    assert _validate_limit(None) is None
    assert _validate_limit(10, max_limit=100) == 10

    with pytest.raises(ValueError):
        _validate_limit(-1)

    with pytest.raises(ValueError):
        _validate_limit(101, max_limit=100)


def test_validate_offset() -> None:
    assert _validate_offset(0) == 0
    assert _validate_offset("2") == 2

    with pytest.raises(ValueError):
        _validate_offset(-1)


def test_get_path() -> None:
    props = FEATURES[0]["properties"]

    assert _get_path(props, "name") == "Tehran"
    assert _get_path(props, "address.country") == "Iran"
    assert _get_path(props, "missing") is MISSING


def test_compare_values_basic_ops() -> None:
    assert _compare_values(10, "gt", 5, case_sensitive=False) is True
    assert _compare_values(10, "gte", 10, case_sensitive=False) is True
    assert _compare_values(10, "lt", 20, case_sensitive=False) is True
    assert _compare_values("Tehran", "eq", "tehran", case_sensitive=False) is True
    assert _compare_values("Tehran", "eq", "tehran", case_sensitive=True) is False


def test_compare_values_collection_ops() -> None:
    assert _compare_values("city", "in", ["city", "town"], case_sensitive=False) is True
    assert _compare_values("village", "not_in", ["city", "town"], case_sensitive=False) is True
    assert _compare_values("Tehran City", "contains", "tehran", case_sensitive=False) is True
    assert _compare_values("Tehran", "startswith", "teh", case_sensitive=False) is True
    assert _compare_values("Tehran", "endswith", "ran", case_sensitive=False) is True


def test_compare_values_regex_between_exists_null() -> None:
    assert _compare_values("Tehran", "regex", "^teh", case_sensitive=False) is True
    assert _compare_values(10, "between", [5, 20], case_sensitive=False) is True
    assert _compare_values(MISSING, "exists", False, case_sensitive=False) is True
    assert _compare_values(None, "is_null", True, case_sensitive=False) is True


def test_eval_where_canonical() -> None:
    props = FEATURES[0]["properties"]

    where = {"field": "population", "op": "gt", "value": 1000000}
    assert _eval_where(props, where, case_sensitive=False) is True


def test_eval_where_shortcut() -> None:
    props = FEATURES[0]["properties"]

    assert _eval_where(props, {"type": "city"}, case_sensitive=False) is True
    assert _eval_where(props, {"population": {"gt": 1000000}}, case_sensitive=False) is True


def test_eval_where_logical() -> None:
    props = FEATURES[0]["properties"]

    where = {
        "and": [
            {"type": "city"},
            {"population": {"gt": 1000000}},
            {"not": {"name": "Isfahan"}},
        ]
    }

    assert _eval_where(props, where, case_sensitive=False) is True


def test_normalize_bbox_list() -> None:
    assert _normalize_bbox([0, 1, 2, 3]) == [0.0, 1.0, 2.0, 3.0]


def test_normalize_bbox_dict() -> None:
    bbox = {"minx": 0, "miny": 1, "maxx": 2, "maxy": 3}
    assert _normalize_bbox(bbox) == [0.0, 1.0, 2.0, 3.0]


def test_bboxes_intersect_and_within() -> None:
    assert _bboxes_intersect([0, 0, 10, 10], [5, 5, 15, 15]) is True
    assert _bboxes_intersect([0, 0, 10, 10], [20, 20, 30, 30]) is False
    assert _bbox_within([1, 1, 2, 2], [0, 0, 10, 10]) is True
    assert _bbox_within([1, 1, 20, 2], [0, 0, 10, 10]) is False


def test_geometry_bbox_polygon() -> None:
    bbox = _geometry_bbox(FEATURES[2]["geometry"])
    assert bbox == [0.0, 0.0, 10.0, 10.0]


def test_normalize_geometry_types() -> None:
    assert _normalize_geometry_types("Point") == {"Point"}
    assert _normalize_geometry_types(["Point", "Polygon"]) == {"Point", "Polygon"}


def test_extract_features_from_list() -> None:
    features, info = _extract_features(FEATURES)

    assert len(features) == 4
    assert info["input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": FEATURES,
    }

    features, info = _extract_features(collection)

    assert len(features) == 4
    assert info["input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(
        features=FEATURES,
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector)

    assert len(features) == 4
    assert info["input_type"] == "VectorOut"
    assert info["input_metadata"]["source"] == "test"


def test_filter_features_where_eq() -> None:
    result = filter_features(
        features=FEATURES,
        where={"type": "city"},
    )

    assert len(result.features) == 2
    assert result.metadata["input_feature_count"] == 4
    assert result.metadata["matched_before_pagination"] == 2
    assert result.metadata["output_feature_count"] == 2
    assert result.features[0]["properties"]["name"] == "Tehran"
    assert result.features[0]["properties"]["_filter_status"] == "matched"


def test_filter_features_where_gt() -> None:
    result = filter_features(
        features=FEATURES,
        where={"population": {"gt": 5000000}},
    )

    assert len(result.features) == 1
    assert result.features[0]["properties"]["name"] == "Tehran"


def test_filter_features_where_contains_case_insensitive() -> None:
    result = filter_features(
        features=FEATURES,
        where={"field": "name", "op": "contains", "value": "teh"},
        case_sensitive=False,
    )

    assert len(result.features) == 1
    assert result.features[0]["properties"]["name"] == "Tehran"


def test_filter_features_where_logical_or() -> None:
    result = filter_features(
        features=FEATURES,
        where={
            "or": [
                {"name": "Tehran"},
                {"name": "Isfahan"},
            ]
        },
    )

    assert len(result.features) == 2


def test_filter_features_nested_property() -> None:
    result = filter_features(
        features=FEATURES,
        where={"address.country": "Iran"},
    )

    assert len(result.features) == 2


def test_filter_features_geometry_type_point() -> None:
    result = filter_features(
        features=FEATURES,
        geometry_types="Point",
    )

    assert len(result.features) == 2
    assert result.metadata["geometry_types_applied"] is True
    assert result.metadata["geometry_types"] == ["Point"]


def test_filter_features_bbox_intersects() -> None:
    result = filter_features(
        features=FEATURES,
        bbox=[50.0, 30.0, 52.0, 36.0],
        bbox_mode="intersects",
    )

    assert len(result.features) == 2
    names = [item["properties"]["name"] for item in result.features]
    assert "Tehran" in names
    assert "Isfahan" in names


def test_filter_features_bbox_within() -> None:
    result = filter_features(
        features=FEATURES,
        bbox=[50.0, 34.0, 52.0, 36.0],
        bbox_mode="within",
    )

    assert len(result.features) == 1
    assert result.features[0]["properties"]["name"] == "Tehran"


def test_filter_features_sort_limit_offset() -> None:
    result = filter_features(
        features=FEATURES,
        where={"type": "city"},
        sort_by="population",
        sort_order="asc",
        offset=0,
        limit=1,
    )

    assert len(result.features) == 1
    assert result.features[0]["properties"]["name"] == "Isfahan"
    assert result.metadata["limit"] == 1
    assert result.metadata["offset"] == 0


def test_filter_features_sort_desc() -> None:
    result = filter_features(
        features=FEATURES,
        where={"type": "city"},
        sort_by="population",
        sort_order="desc",
    )

    assert len(result.features) == 2
    assert result.features[0]["properties"]["name"] == "Tehran"


def test_filter_features_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spatial_query_filter.yaml").write_text(
        """
case_sensitive: false
default_bbox_mode: within
preserve_properties: true
default_limit: 1
default_offset: 0
max_limit: 100
fields:
  add_source_index: true
  add_filter_status: true
  source_index_field: src_idx
  filter_status_field: filter_status
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = filter_features(
        features=FEATURES,
        where={"type": "city"},
        sort_by="population",
        sort_order="desc",
    )

    assert len(result.features) == 1
    props = result.features[0]["properties"]
    assert props["name"] == "Tehran"
    assert props["src_idx"] == 0
    assert props["filter_status"] == "matched"
    assert result.metadata["limit"] == 1
    assert result.metadata["bbox_mode"] == "within"


def test_filter_features_metadata_merge() -> None:
    result = filter_features(
        features=FEATURES,
        where={"type": "city"},
        metadata={"request_id": "filter-1"},
    )

    assert result.metadata["request_id"] == "filter-1"


def test_filter_features_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        filter_features(
            features=FEATURES,
            metadata="bad",
        )


def test_filter_features_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        filter_features(
            features={"type": "Point", "coordinates": [1, 2]},
        )


def test_vectorout_to_artifact() -> None:
    result = filter_features(
        features=FEATURES,
        where={"type": "city"},
    )

    artifact = result.to_artifact(produced_by="test_spatial_query_filter")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_spatial_query_filter"
    assert artifact.payload["source"] == "spatial_query_filter"
    assert artifact.payload["operation"] == "filter"
    assert len(artifact.payload["features"]) == 2


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "filter_features" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "filter_features")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "filter_features"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "where" in descriptor.optional_inputs
    assert "bbox" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "filter"
    assert descriptor.metadata["attribute_filter"] is True
    assert descriptor.metadata["bbox_filter"] is True
