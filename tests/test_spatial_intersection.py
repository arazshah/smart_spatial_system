"""
Tests for spatial_intersection plugin.

Run:
    pytest tests/test_spatial_intersection.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.spatial_intersection import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _bbox_area,
    _bbox_intersection,
    _bbox_to_geometry,
    _bboxes_intersect,
    _calculate_intersection,
    _configured_precision,
    _extract_features,
    _geometry_bbox,
    _is_geographic_crs,
    _is_position,
    _python_intersection_geometry,
    _validate_engine,
    _validate_mode,
    intersect_features,
)


SOURCE_POLYGON = {
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
    "properties": {"id": "source_poly"},
}

TARGET_POLYGON_OVERLAP = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [5.0, 5.0],
            [15.0, 5.0],
            [15.0, 15.0],
            [5.0, 15.0],
            [5.0, 5.0],
        ]],
    },
    "properties": {"id": "target_overlap"},
}

TARGET_POLYGON_FAR = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [20.0, 20.0],
            [30.0, 20.0],
            [30.0, 30.0],
            [20.0, 30.0],
            [20.0, 20.0],
        ]],
    },
    "properties": {"id": "target_far"},
}

SOURCE_POINT_INSIDE = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [6.0, 6.0]},
    "properties": {"id": "source_point_inside"},
}

SOURCE_POINT_OUTSIDE = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [50.0, 50.0]},
    "properties": {"id": "source_point_outside"},
}

NULL_GEOMETRY_FEATURE = {
    "type": "Feature",
    "geometry": None,
    "properties": {"id": "null"},
}


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "spatial_intersection"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Spatial Intersection"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("python") == "python"
    assert _validate_engine("shapely") == "shapely"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_mode() -> None:
    assert _validate_mode("filter") == "filter"
    assert _validate_mode("pairwise") == "pairwise"

    with pytest.raises(ValueError):
        _validate_mode("bad")


def test_configured_precision_defaults() -> None:
    assert _configured_precision({}) == 6


def test_is_position() -> None:
    assert _is_position([1, 2]) is True
    assert _is_position([1, 2, 3]) is True
    assert _is_position([1]) is False
    assert _is_position(["x", "y"]) is False


def test_geometry_bbox_polygon() -> None:
    bbox = _geometry_bbox(SOURCE_POLYGON["geometry"])
    assert bbox == [0.0, 0.0, 10.0, 10.0]


def test_bboxes_intersect() -> None:
    assert _bboxes_intersect([0, 0, 10, 10], [5, 5, 15, 15]) is True
    assert _bboxes_intersect([0, 0, 10, 10], [20, 20, 30, 30]) is False


def test_bbox_intersection() -> None:
    bbox = _bbox_intersection([0, 0, 10, 10], [5, 5, 15, 15])
    assert bbox == [5, 5, 10, 10]


def test_bbox_area() -> None:
    assert _bbox_area([5, 5, 10, 10]) == 25.0


def test_bbox_to_geometry_polygon() -> None:
    geometry = _bbox_to_geometry([5, 5, 10, 10], precision=2)

    assert geometry["type"] == "Polygon"
    assert geometry["coordinates"][0][0] == [5.0, 5.0]
    assert geometry["coordinates"][0][2] == [10.0, 10.0]


def test_bbox_to_geometry_point() -> None:
    geometry = _bbox_to_geometry([5, 5, 5, 5], precision=2)

    assert geometry["type"] == "Point"
    assert geometry["coordinates"] == [5.0, 5.0]


def test_python_intersection_geometry_overlap() -> None:
    intersects, geometry, area = _python_intersection_geometry(
        SOURCE_POLYGON["geometry"],
        TARGET_POLYGON_OVERLAP["geometry"],
        precision=2,
    )

    assert intersects is True
    assert geometry["type"] == "Polygon"
    assert area == 25.0


def test_python_intersection_geometry_no_overlap() -> None:
    intersects, geometry, area = _python_intersection_geometry(
        SOURCE_POLYGON["geometry"],
        TARGET_POLYGON_FAR["geometry"],
        precision=2,
    )

    assert intersects is False
    assert geometry is None
    assert area is None


def test_calculate_intersection_python() -> None:
    intersects, geometry, area, engine_used = _calculate_intersection(
        SOURCE_POLYGON["geometry"],
        TARGET_POLYGON_OVERLAP["geometry"],
        engine="python",
        precision=2,
    )

    assert intersects is True
    assert geometry["type"] == "Polygon"
    assert area == 25.0
    assert engine_used == "python"


def test_extract_features_from_list() -> None:
    features, info = _extract_features([SOURCE_POLYGON], label="source")

    assert len(features) == 1
    assert info["source_input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [SOURCE_POLYGON],
    }

    features, info = _extract_features(collection, label="source")

    assert len(features) == 1
    assert info["source_input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(
        features=[SOURCE_POLYGON],
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector, label="source")

    assert len(features) == 1
    assert info["source_input_type"] == "VectorOut"
    assert info["source_input_metadata"]["source"] == "test"


def test_is_geographic_crs() -> None:
    assert _is_geographic_crs("EPSG:4326") is True
    assert _is_geographic_crs("CRS:84") is True
    assert _is_geographic_crs("EPSG:3857") is False


def test_intersect_features_filter_python_success() -> None:
    result = intersect_features(
        source_features=[SOURCE_POLYGON, SOURCE_POINT_OUTSIDE],
        target_features=[TARGET_POLYGON_OVERLAP],
        mode="filter",
        engine="python",
        precision=4,
    )

    assert len(result.features) == 1

    feature = result.features[0]
    props = feature["properties"]

    assert feature["geometry"]["type"] == "Polygon"
    assert props["id"] == "source_poly"
    assert props["_intersects"] is True
    assert props["_source_index"] == 0
    assert props["_target_index"] == 0
    assert props["_intersection_status"] == "success"
    assert props["_intersection_engine"] == "python"
    assert props["_intersection_mode"] == "filter"
    assert props["_intersection_area"] == 25.0
    assert props["_target_properties"]["id"] == "target_overlap"

    md = result.metadata
    assert md["source"] == "spatial_intersection"
    assert md["operation"] == "intersection"
    assert md["mode"] == "filter"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["source_feature_count"] == 2
    assert md["target_feature_count"] == 1
    assert md["pair_count"] == 2
    assert md["intersecting_pair_count"] == 1
    assert md["output_feature_count"] == 1
    assert md["success_count"] == 1


def test_intersect_features_filter_keep_non_intersecting() -> None:
    result = intersect_features(
        source_features=[SOURCE_POLYGON, SOURCE_POINT_OUTSIDE],
        target_features=[TARGET_POLYGON_OVERLAP],
        mode="filter",
        engine="python",
        drop_non_intersecting=False,
    )

    assert len(result.features) == 2

    assert result.features[0]["properties"]["_intersects"] is True
    assert result.features[1]["properties"]["_intersects"] is False
    assert result.features[1]["properties"]["_intersection_status"] == "no_intersection"


def test_intersect_features_pairwise_python_success() -> None:
    result = intersect_features(
        source_features=[SOURCE_POLYGON],
        target_features=[TARGET_POLYGON_OVERLAP, TARGET_POLYGON_FAR],
        mode="pairwise",
        engine="python",
        precision=4,
    )

    assert len(result.features) == 1

    feature = result.features[0]
    props = feature["properties"]

    assert feature["geometry"]["type"] == "Polygon"
    assert props["_intersects"] is True
    assert props["_source_index"] == 0
    assert props["_target_index"] == 0
    assert props["_intersection_area"] == 25.0
    assert props["_intersection_status"] == "success"

    md = result.metadata
    assert md["mode"] == "pairwise"
    assert md["pair_count"] == 2
    assert md["intersecting_pair_count"] == 1
    assert md["non_intersecting_pair_count"] == 1
    assert md["success_count"] == 1


def test_intersect_features_pairwise_include_non_intersecting() -> None:
    result = intersect_features(
        source_features=[SOURCE_POLYGON],
        target_features=[TARGET_POLYGON_OVERLAP, TARGET_POLYGON_FAR],
        mode="pairwise",
        engine="python",
        drop_non_intersecting=False,
    )

    assert len(result.features) == 2
    assert result.features[0]["properties"]["_intersects"] is True
    assert result.features[1]["properties"]["_intersects"] is False
    assert result.features[1]["geometry"] is None


def test_intersect_features_point_inside_bbox_python() -> None:
    result = intersect_features(
        source_features=[SOURCE_POINT_INSIDE],
        target_features=[TARGET_POLYGON_OVERLAP],
        mode="pairwise",
        engine="python",
        precision=4,
    )

    assert len(result.features) == 1
    assert result.features[0]["geometry"]["type"] == "Point"
    assert result.features[0]["geometry"]["coordinates"] == [6.0, 6.0]


def test_intersect_features_handles_null_geometry_by_default() -> None:
    result = intersect_features(
        source_features=[SOURCE_POLYGON, NULL_GEOMETRY_FEATURE],
        target_features=[TARGET_POLYGON_OVERLAP],
        mode="filter",
        engine="python",
        drop_non_intersecting=False,
    )

    assert len(result.features) == 2
    assert result.features[1]["properties"]["_intersects"] is False
    assert result.features[1]["properties"]["_intersection_status"] == "no_intersection"


def test_intersect_features_rejects_empty_targets() -> None:
    with pytest.raises(ValueError, match="target_features"):
        intersect_features(
            source_features=[SOURCE_POLYGON],
            target_features=[],
            mode="filter",
            engine="python",
        )


def test_intersect_features_warns_for_geographic_crs(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spatial_intersection.yaml").write_text(
        """
default_engine: python
default_mode: filter
coordinate_precision: 6
preserve_properties: true
drop_non_intersecting: true
drop_failed: false
source_crs: EPSG:4326
warn_if_geographic_crs: true
fields:
  intersects_field: _intersects
  source_index_field: _source_index
  target_index_field: _target_index
  status_field: _intersection_status
  engine_field: _intersection_engine
  mode_field: _intersection_mode
  area_field: _intersection_area
  target_properties_field: _target_properties
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = intersect_features(
        source_features=[SOURCE_POLYGON],
        target_features=[TARGET_POLYGON_OVERLAP],
    )

    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_intersect_features_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spatial_intersection.yaml").write_text(
        """
default_engine: python
default_mode: pairwise
coordinate_precision: 3
preserve_properties: true
drop_non_intersecting: true
drop_failed: false
source_crs: EPSG:3857
warn_if_geographic_crs: false
fields:
  intersects_field: intersects
  source_index_field: src_idx
  target_index_field: tgt_idx
  status_field: status
  engine_field: engine_used
  mode_field: mode_used
  area_field: area_intersection
  target_properties_field: tgt_props
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = intersect_features(
        source_features=[SOURCE_POLYGON],
        target_features=[TARGET_POLYGON_OVERLAP],
    )

    props = result.features[0]["properties"]

    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["mode"] == "pairwise"
    assert result.metadata["coordinate_precision"] == 3
    assert props["intersects"] is True
    assert props["src_idx"] == 0
    assert props["tgt_idx"] == 0
    assert props["status"] == "success"
    assert props["engine_used"] == "python"
    assert props["mode_used"] == "pairwise"
    assert props["area_intersection"] == 25.0
    assert props["tgt_props"]["id"] == "target_overlap"


def test_intersect_features_metadata_merge() -> None:
    result = intersect_features(
        source_features=[SOURCE_POLYGON],
        target_features=[TARGET_POLYGON_OVERLAP],
        mode="filter",
        engine="python",
        metadata={"analysis_id": "intersection-1"},
    )

    assert result.metadata["analysis_id"] == "intersection-1"


def test_intersect_features_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        intersect_features(
            source_features=[SOURCE_POLYGON],
            target_features=[TARGET_POLYGON_OVERLAP],
            mode="filter",
            engine="python",
            metadata="bad",
        )


def test_intersect_features_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        intersect_features(
            source_features={"type": "Point", "coordinates": [1, 2]},
            target_features=[TARGET_POLYGON_OVERLAP],
            mode="filter",
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = intersect_features(
        source_features=[SOURCE_POLYGON],
        target_features=[TARGET_POLYGON_OVERLAP],
        mode="filter",
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_spatial_intersection")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_spatial_intersection"
    assert artifact.payload["source"] == "spatial_intersection"
    assert artifact.payload["operation"] == "intersection"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "intersect_features" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "intersect_features")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "intersect_features"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "source_features" in descriptor.required_inputs
    assert "target_features" in descriptor.required_inputs
    assert "engine" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "intersection"
    assert descriptor.metadata["requires_shapely_for_exact_geometry"] is True


def test_shapely_engine_if_installed() -> None:
    pytest.importorskip("shapely", reason="shapely not installed")

    result = intersect_features(
        source_features=[SOURCE_POLYGON],
        target_features=[TARGET_POLYGON_OVERLAP],
        mode="pairwise",
        engine="shapely",
        precision=4,
    )

    assert len(result.features) == 1
    assert result.metadata["engines_used"] == ["shapely"]
    assert result.features[0]["geometry"]["type"] == "Polygon"
    assert result.features[0]["properties"]["_intersection_area"] == 25.0
