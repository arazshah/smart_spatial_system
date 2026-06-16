"""
Tests for dissolve_aggregator plugin.

Run:
    pytest tests/test_dissolve_aggregator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.dissolve_aggregator import (  # noqa: E402
    MISSING,
    PLUGIN,
    PLUGIN_ID,
    _aggregate_values,
    _bbox_to_geometry,
    _configured_precision,
    _dissolve_geometries,
    _extract_features,
    _geometry_bbox,
    _get_path,
    _group_key_for_feature,
    _is_geographic_crs,
    _is_number,
    _merge_bbox_arrays,
    _normalize_aggregate_fields,
    _normalize_group_by,
    _python_dissolve_geometries,
    _validate_engine,
    _validate_precision,
    dissolve_features,
)


FEATURES = [
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
            "id": 1,
            "landuse": "residential",
            "district": "A",
            "population": 100,
            "area": 10.0,
            "name": "r1",
        },
    },
    {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [10.0, 0.0],
                [20.0, 0.0],
                [20.0, 10.0],
                [10.0, 10.0],
                [10.0, 0.0],
            ]],
        },
        "properties": {
            "id": 2,
            "landuse": "residential",
            "district": "A",
            "population": 200,
            "area": 15.0,
            "name": "r2",
        },
    },
    {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [100.0, 100.0],
                [110.0, 100.0],
                [110.0, 110.0],
                [100.0, 110.0],
                [100.0, 100.0],
            ]],
        },
        "properties": {
            "id": 3,
            "landuse": "commercial",
            "district": "B",
            "population": 50,
            "area": 8.0,
            "name": "c1",
        },
    },
    {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "id": 4,
            "landuse": "unknown",
            "district": None,
            "population": None,
            "area": None,
            "name": None,
        },
    },
]


def _find_group(result, group_value):
    for feature in result.features:
        props = feature["properties"]
        if props["_group_key"] == group_value:
            return feature
    raise AssertionError(f"group not found: {group_value!r}")


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "dissolve_aggregator"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Dissolve Aggregator"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("python") == "python"
    assert _validate_engine("shapely") == "shapely"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_configured_precision_defaults() -> None:
    assert _configured_precision({}) == 6
    assert _configured_precision({"coordinate_precision": None}) is None


def test_validate_precision() -> None:
    assert _validate_precision(None) is None
    assert _validate_precision(3) == 3

    with pytest.raises(ValueError):
        _validate_precision(-1)


def test_is_number() -> None:
    assert _is_number(1) is True
    assert _is_number(1.5) is True
    assert _is_number(True) is False
    assert _is_number("1") is False


def test_get_path() -> None:
    props = {"a": {"b": 10}, "items": [{"x": 1}]}

    assert _get_path(props, "a.b") == 10
    assert _get_path(props, "items.0.x") == 1
    assert _get_path(props, "missing") is MISSING


def test_normalize_group_by() -> None:
    assert _normalize_group_by(None) == []
    assert _normalize_group_by("landuse") == ["landuse"]
    assert _normalize_group_by(["landuse", "district", "landuse"]) == ["landuse", "district"]

    with pytest.raises(ValueError):
        _normalize_group_by(123)


def test_normalize_aggregate_fields_dict() -> None:
    result = _normalize_aggregate_fields(
        {
            "population": ["sum", "mean"],
            "name": "first",
        }
    )

    assert result == {
        "population": ["sum", "mean"],
        "name": ["first"],
    }


def test_normalize_aggregate_fields_list() -> None:
    result = _normalize_aggregate_fields(
        [
            {"field": "population", "ops": ["sum", "mean"]},
            {"field": "name", "ops": "first"},
        ]
    )

    assert result == {
        "population": ["sum", "mean"],
        "name": ["first"],
    }


def test_normalize_aggregate_fields_rejects_bad_op() -> None:
    with pytest.raises(ValueError):
        _normalize_aggregate_fields({"population": ["bad"]})


def test_geometry_bbox_polygon() -> None:
    bbox = _geometry_bbox(FEATURES[0]["geometry"])
    assert bbox == [0.0, 0.0, 10.0, 10.0]


def test_merge_bbox_arrays() -> None:
    merged = _merge_bbox_arrays(
        [
            [0.0, 0.0, 10.0, 10.0],
            [10.0, 0.0, 20.0, 10.0],
        ]
    )

    assert merged == {
        "minx": 0.0,
        "miny": 0.0,
        "maxx": 20.0,
        "maxy": 10.0,
    }


def test_bbox_to_geometry_polygon() -> None:
    geometry = _bbox_to_geometry(
        {"minx": 0.0, "miny": 0.0, "maxx": 20.0, "maxy": 10.0},
        precision=2,
    )

    assert geometry["type"] == "Polygon"
    assert geometry["coordinates"][0][0] == [0.0, 0.0]
    assert geometry["coordinates"][0][2] == [20.0, 10.0]


def test_bbox_to_geometry_point() -> None:
    geometry = _bbox_to_geometry(
        {"minx": 5.0, "miny": 5.0, "maxx": 5.0, "maxy": 5.0},
        precision=2,
    )

    assert geometry["type"] == "Point"
    assert geometry["coordinates"] == [5.0, 5.0]


def test_python_dissolve_geometries() -> None:
    geometry, engine_used = _python_dissolve_geometries(
        [FEATURES[0]["geometry"], FEATURES[1]["geometry"]],
        precision=2,
    )

    assert engine_used == "python"
    assert geometry["type"] == "Polygon"
    assert geometry["coordinates"][0][2] == [20.0, 10.0]


def test_dissolve_geometries_python() -> None:
    geometry, engine_used = _dissolve_geometries(
        [FEATURES[0]["geometry"], FEATURES[1]["geometry"]],
        engine="python",
        precision=2,
    )

    assert engine_used == "python"
    assert geometry["type"] == "Polygon"


def test_group_key_for_feature() -> None:
    props = FEATURES[0]["properties"]

    assert _group_key_for_feature(props, []) == ("__all__",)
    assert _group_key_for_feature(props, ["landuse"]) == ("residential",)
    assert _group_key_for_feature(props, ["landuse", "district"]) == ("residential", "A")


def test_aggregate_values_numeric() -> None:
    result = _aggregate_values(
        [100, 200, None],
        ["count", "non_null_count", "sum", "mean", "min", "max"],
        values_max_items=10,
    )

    assert result["count"] == 3
    assert result["non_null_count"] == 2
    assert result["sum"] == 300.0
    assert result["mean"] == 150.0
    assert result["min"] == 100.0
    assert result["max"] == 200.0


def test_aggregate_values_categorical() -> None:
    result = _aggregate_values(
        ["a", "b", "a", None],
        ["first", "last", "unique_count", "values"],
        values_max_items=2,
    )

    assert result["first"] == "a"
    assert result["last"] == "a"
    assert result["unique_count"] == 2
    assert result["values"] == ["a", "b"]


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


def test_is_geographic_crs() -> None:
    assert _is_geographic_crs("EPSG:4326") is True
    assert _is_geographic_crs("CRS:84") is True
    assert _is_geographic_crs("EPSG:3857") is False


def test_dissolve_features_all_python() -> None:
    result = dissolve_features(
        features=FEATURES[:3],
        group_by=None,
        aggregate_fields={
            "population": ["sum", "mean", "count"],
            "name": ["first", "last", "unique_count"],
        },
        engine="python",
        precision=2,
    )

    assert len(result.features) == 1

    feature = result.features[0]
    props = feature["properties"]

    assert feature["geometry"]["type"] == "Polygon"
    assert props["_group_key"] == "__all__"
    assert props["_group_by"] == []
    assert props["_feature_count"] == 3
    assert props["_dissolve_status"] == "success"
    assert props["_dissolve_engine"] == "python"

    assert props["population_sum"] == 350.0
    assert props["population_mean"] == pytest.approx(116.67)
    assert props["population_count"] == 3
    assert props["name_first"] == "r1"
    assert props["name_last"] == "c1"
    assert props["name_unique_count"] == 3

    md = result.metadata
    assert md["source"] == "dissolve_aggregator"
    assert md["operation"] == "dissolve_aggregate"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["group_by"] == []
    assert md["group_count"] == 1
    assert md["input_feature_count"] == 3
    assert md["output_feature_count"] == 1


def test_dissolve_features_group_by_landuse_python() -> None:
    result = dissolve_features(
        features=FEATURES,
        group_by="landuse",
        aggregate_fields={
            "population": ["sum", "mean", "non_null_count"],
            "area": ["sum"],
        },
        engine="python",
        precision=2,
    )

    assert len(result.features) == 3

    residential = _find_group(result, "residential")
    commercial = _find_group(result, "commercial")
    unknown = _find_group(result, "unknown")

    res_props = residential["properties"]
    com_props = commercial["properties"]
    unk_props = unknown["properties"]

    assert res_props["landuse"] == "residential"
    assert res_props["_feature_count"] == 2
    assert res_props["population_sum"] == 300.0
    assert res_props["population_mean"] == 150.0
    assert res_props["population_non_null_count"] == 2
    assert res_props["area_sum"] == 25.0

    assert commercial["geometry"]["type"] == "Polygon"
    assert com_props["_feature_count"] == 1
    assert com_props["population_sum"] == 50.0

    assert unknown["geometry"] is None
    assert unk_props["_feature_count"] == 1
    assert unk_props["population_sum"] is None

    assert result.metadata["group_by"] == ["landuse"]
    assert result.metadata["group_count"] == 3


def test_dissolve_features_group_by_multiple_fields() -> None:
    result = dissolve_features(
        features=FEATURES,
        group_by=["landuse", "district"],
        aggregate_fields={"population": ["sum"]},
        engine="python",
    )

    assert len(result.features) == 3
    group_keys = [feature["properties"]["_group_key"] for feature in result.features]

    assert ["residential", "A"] in group_keys
    assert ["commercial", "B"] in group_keys
    assert ["unknown", None] in group_keys


def test_dissolve_features_values_aggregation_limit() -> None:
    result = dissolve_features(
        features=FEATURES[:3],
        group_by=None,
        aggregate_fields={"name": ["values"]},
        engine="python",
    )

    props = result.features[0]["properties"]

    assert props["name_values"] == ["r1", "r2", "c1"]


def test_dissolve_features_without_aggregations() -> None:
    result = dissolve_features(
        features=FEATURES[:2],
        group_by="landuse",
        aggregate_fields={},
        engine="python",
    )

    assert len(result.features) == 1
    props = result.features[0]["properties"]

    assert props["_group_key"] == "residential"
    assert props["_feature_count"] == 2
    assert "population_sum" not in props


def test_dissolve_features_warns_for_geographic_crs(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "dissolve_aggregator.yaml").write_text(
        """
default_engine: python
default_group_by: landuse
coordinate_precision: 6
drop_failed: false
preserve_group_fields: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
default_aggregate_fields:
  population:
    - sum
aggregation:
  output_separator: "_"
  values_max_items: 1000
fields:
  group_key_field: _group_key
  group_by_field: _group_by
  feature_count_field: _feature_count
  dissolved_count_field: _dissolved_count
  status_field: _dissolve_status
  engine_field: _dissolve_engine
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = dissolve_features(
        features=FEATURES[:2],
    )

    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_dissolve_features_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "dissolve_aggregator.yaml").write_text(
        """
default_engine: python
default_group_by: landuse
coordinate_precision: 3
drop_failed: false
preserve_group_fields: true
source_crs: EPSG:3857
warn_if_geographic_crs: false
default_aggregate_fields:
  population:
    - sum
    - mean
  name:
    - first
aggregation:
  output_separator: "__"
  values_max_items: 1000
fields:
  group_key_field: group_key
  group_by_field: group_by
  feature_count_field: feature_count
  dissolved_count_field: dissolved_count
  status_field: status
  engine_field: engine_used
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = dissolve_features(
        features=FEATURES[:2],
    )

    assert len(result.features) == 1

    props = result.features[0]["properties"]

    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["group_by"] == ["landuse"]
    assert result.metadata["coordinate_precision"] == 3

    assert props["group_key"] == "residential"
    assert props["group_by"] == ["landuse"]
    assert props["feature_count"] == 2
    assert props["status"] == "success"
    assert props["engine_used"] == "python"
    assert props["population__sum"] == 300.0
    assert props["population__mean"] == 150.0
    assert props["name__first"] == "r1"


def test_dissolve_features_metadata_merge() -> None:
    result = dissolve_features(
        features=FEATURES[:2],
        group_by="landuse",
        engine="python",
        metadata={"analysis_id": "dissolve-1"},
    )

    assert result.metadata["analysis_id"] == "dissolve-1"


def test_dissolve_features_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        dissolve_features(
            features=FEATURES[:2],
            group_by="landuse",
            engine="python",
            metadata="bad",
        )


def test_dissolve_features_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        dissolve_features(
            features={"type": "Point", "coordinates": [1, 2]},
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = dissolve_features(
        features=FEATURES[:2],
        group_by="landuse",
        aggregate_fields={"population": ["sum"]},
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_dissolve_aggregator")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_dissolve_aggregator"
    assert artifact.payload["source"] == "dissolve_aggregator"
    assert artifact.payload["operation"] == "dissolve_aggregate"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "dissolve_features" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "dissolve_features")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "dissolve_features"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "group_by" in descriptor.optional_inputs
    assert "aggregate_fields" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "dissolve_aggregate"
    assert descriptor.metadata["requires_shapely_for_exact_union"] is True
    assert descriptor.metadata["aggregation_supported"] is True


def test_shapely_engine_if_installed() -> None:
    pytest.importorskip("shapely", reason="shapely not installed")

    result = dissolve_features(
        features=FEATURES[:2],
        group_by="landuse",
        aggregate_fields={"population": ["sum"]},
        engine="shapely",
        precision=4,
    )

    assert len(result.features) == 1
    assert result.metadata["engines_used"] == ["shapely"]
    assert result.features[0]["properties"]["_dissolve_engine"] == "shapely"
    assert result.features[0]["properties"]["population_sum"] == 300.0
    assert result.features[0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}
