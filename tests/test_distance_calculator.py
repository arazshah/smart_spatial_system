"""
Tests for distance_calculator plugin.

Run:
    pytest tests/test_distance_calculator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.distance_calculator import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _calculate_distance,
    _collect_geometry,
    _configured_precision,
    _distance_points,
    _extract_features,
    _geometry_bbox,
    _is_geographic_crs,
    _is_position,
    _point_in_ring,
    _point_segment_distance,
    _python_distance_geometry,
    _segment_segment_distance,
    _segments_intersect,
    _validate_engine,
    _validate_mode,
    calculate_distances,
)


SOURCE_POINT = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
    "properties": {"id": "s1"},
}

TARGET_POINT_A = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [3.0, 4.0]},
    "properties": {"id": "t1"},
}

TARGET_POINT_B = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [1.0, 0.0]},
    "properties": {"id": "t2"},
}

LINE_FEATURE = {
    "type": "Feature",
    "geometry": {
        "type": "LineString",
        "coordinates": [[0.0, 2.0], [10.0, 2.0]],
    },
    "properties": {"id": "line"},
}

POLYGON_FEATURE = {
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
    "properties": {"id": "poly"},
}

POINT_INSIDE_POLYGON = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [5.0, 5.0]},
    "properties": {"id": "inside"},
}

NULL_GEOMETRY_FEATURE = {
    "type": "Feature",
    "geometry": None,
    "properties": {"id": "null"},
}


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "distance_calculator"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Distance Calculator"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("python") == "python"
    assert _validate_engine("shapely") == "shapely"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_mode() -> None:
    assert _validate_mode("nearest") == "nearest"
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


def test_distance_points() -> None:
    assert _distance_points((0, 0), (3, 4)) == 5.0


def test_point_segment_distance() -> None:
    distance = _point_segment_distance(
        point=(0.0, 0.0),
        a=(0.0, 2.0),
        b=(10.0, 2.0),
    )

    assert distance == pytest.approx(2.0)


def test_segments_intersect() -> None:
    assert _segments_intersect(
        (0, 0), (10, 10),
        (0, 10), (10, 0),
    ) is True

    assert _segments_intersect(
        (0, 0), (1, 0),
        (0, 2), (1, 2),
    ) is False


def test_segment_segment_distance_intersecting() -> None:
    distance = _segment_segment_distance(
        (0, 0), (10, 10),
        (0, 10), (10, 0),
    )

    assert distance == 0.0


def test_segment_segment_distance_parallel() -> None:
    distance = _segment_segment_distance(
        (0, 0), (10, 0),
        (0, 2), (10, 2),
    )

    assert distance == pytest.approx(2.0)


def test_point_in_ring() -> None:
    ring = [
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 10.0),
        (0.0, 10.0),
        (0.0, 0.0),
    ]

    assert _point_in_ring((5.0, 5.0), ring) is True
    assert _point_in_ring((20.0, 20.0), ring) is False


def test_collect_geometry_point() -> None:
    collected = _collect_geometry(SOURCE_POINT["geometry"])

    assert collected["points"] == [(0.0, 0.0)]
    assert collected["segments"] == []
    assert collected["polygons"] == []


def test_collect_geometry_line() -> None:
    collected = _collect_geometry(LINE_FEATURE["geometry"])

    assert len(collected["points"]) == 2
    assert len(collected["segments"]) == 1


def test_collect_geometry_polygon() -> None:
    collected = _collect_geometry(POLYGON_FEATURE["geometry"])

    assert len(collected["points"]) >= 5
    assert len(collected["segments"]) == 4
    assert len(collected["polygons"]) == 1


def test_python_distance_point_to_point() -> None:
    distance = _python_distance_geometry(
        SOURCE_POINT["geometry"],
        TARGET_POINT_A["geometry"],
    )

    assert distance == 5.0


def test_python_distance_point_to_line() -> None:
    distance = _python_distance_geometry(
        SOURCE_POINT["geometry"],
        LINE_FEATURE["geometry"],
    )

    assert distance == pytest.approx(2.0)


def test_python_distance_point_inside_polygon_is_zero() -> None:
    distance = _python_distance_geometry(
        POINT_INSIDE_POLYGON["geometry"],
        POLYGON_FEATURE["geometry"],
    )

    assert distance == 0.0


def test_python_distance_null_geometry() -> None:
    distance = _python_distance_geometry(
        SOURCE_POINT["geometry"],
        None,
    )

    assert distance is None


def test_calculate_distance_python() -> None:
    distance, engine_used = _calculate_distance(
        SOURCE_POINT["geometry"],
        TARGET_POINT_A["geometry"],
        engine="python",
    )

    assert distance == 5.0
    assert engine_used == "python"


def test_extract_features_from_list() -> None:
    features, info = _extract_features([SOURCE_POINT, TARGET_POINT_A], label="source")

    assert len(features) == 2
    assert info["source_input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [SOURCE_POINT, TARGET_POINT_A],
    }

    features, info = _extract_features(collection, label="source")

    assert len(features) == 2
    assert info["source_input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(
        features=[SOURCE_POINT],
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector, label="source")

    assert len(features) == 1
    assert info["source_input_type"] == "VectorOut"
    assert info["source_input_metadata"]["source"] == "test"


def test_geometry_bbox_polygon() -> None:
    bbox = _geometry_bbox(POLYGON_FEATURE["geometry"])
    assert bbox == [0.0, 0.0, 10.0, 10.0]


def test_is_geographic_crs() -> None:
    assert _is_geographic_crs("EPSG:4326") is True
    assert _is_geographic_crs("CRS:84") is True
    assert _is_geographic_crs("EPSG:3857") is False


def test_calculate_distances_nearest_python_success() -> None:
    result = calculate_distances(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A, TARGET_POINT_B],
        mode="nearest",
        engine="python",
        precision=4,
    )

    assert len(result.features) == 1

    props = result.features[0]["properties"]
    assert props["id"] == "s1"
    assert props["_distance"] == 1.0
    assert props["_source_index"] == 0
    assert props["_target_index"] == 1
    assert props["_distance_status"] == "success"
    assert props["_distance_engine"] == "python"
    assert props["_distance_mode"] == "nearest"
    assert props["_target_properties"]["id"] == "t2"

    md = result.metadata
    assert md["source"] == "distance_calculator"
    assert md["operation"] == "distance"
    assert md["mode"] == "nearest"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["source_feature_count"] == 1
    assert md["target_feature_count"] == 2
    assert md["pair_count"] == 2
    assert md["success_count"] == 1
    assert md["failed_count"] == 0


def test_calculate_distances_pairwise_python_success() -> None:
    result = calculate_distances(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A, TARGET_POINT_B],
        mode="pairwise",
        engine="python",
        precision=4,
    )

    assert len(result.features) == 2

    distances = [feature["properties"]["_distance"] for feature in result.features]
    target_indexes = [feature["properties"]["_target_index"] for feature in result.features]

    assert distances == [5.0, 1.0]
    assert target_indexes == [0, 1]

    assert result.metadata["mode"] == "pairwise"
    assert result.metadata["pair_count"] == 2
    assert result.metadata["success_count"] == 2


def test_calculate_distances_handles_null_geometry_by_default() -> None:
    result = calculate_distances(
        source_features=[SOURCE_POINT, NULL_GEOMETRY_FEATURE],
        target_features=[TARGET_POINT_B],
        mode="nearest",
        engine="python",
    )

    assert len(result.features) == 2
    assert result.metadata["success_count"] == 1
    assert result.metadata["failed_count"] == 1
    assert result.metadata["dropped_count"] == 0

    failed = result.features[1]["properties"]
    assert failed["_distance"] is None
    assert failed["_distance_status"] == "failed"


def test_calculate_distances_drop_failed() -> None:
    result = calculate_distances(
        source_features=[SOURCE_POINT, NULL_GEOMETRY_FEATURE],
        target_features=[TARGET_POINT_B],
        mode="nearest",
        engine="python",
        drop_failed=True,
    )

    assert len(result.features) == 1
    assert result.metadata["success_count"] == 1
    assert result.metadata["failed_count"] == 1
    assert result.metadata["dropped_count"] == 1


def test_calculate_distances_warns_for_geographic_crs() -> None:
    result = calculate_distances(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
        mode="nearest",
        engine="python",
        source_crs="EPSG:4326",
    )

    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_calculate_distances_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "distance_calculator.yaml").write_text(
        """
default_engine: python
default_mode: nearest
coordinate_precision: 3
preserve_properties: true
drop_failed: false
source_crs: EPSG:3857
warn_if_geographic_crs: true
fields:
  distance_field: dist
  source_index_field: src_idx
  target_index_field: tgt_idx
  status_field: dist_status
  engine_field: dist_engine
  mode_field: dist_mode
  target_properties_field: tgt_props
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = calculate_distances(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
    )

    props = result.features[0]["properties"]

    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["mode"] == "nearest"
    assert result.metadata["coordinate_precision"] == 3
    assert props["dist"] == 1.0
    assert props["src_idx"] == 0
    assert props["tgt_idx"] == 0
    assert props["dist_status"] == "success"
    assert props["dist_engine"] == "python"
    assert props["dist_mode"] == "nearest"


def test_calculate_distances_metadata_merge() -> None:
    result = calculate_distances(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
        mode="nearest",
        engine="python",
        metadata={"analysis_id": "distance-1"},
    )

    assert result.metadata["analysis_id"] == "distance-1"


def test_calculate_distances_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_distances(
            source_features=[SOURCE_POINT],
            target_features=[TARGET_POINT_B],
            mode="nearest",
            engine="python",
            metadata="bad",
        )


def test_calculate_distances_rejects_empty_targets() -> None:
    with pytest.raises(ValueError, match="target_features"):
        calculate_distances(
            source_features=[SOURCE_POINT],
            target_features=[],
            mode="nearest",
            engine="python",
        )


def test_calculate_distances_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        calculate_distances(
            source_features={"type": "Point", "coordinates": [1, 2]},
            target_features=[TARGET_POINT_B],
            mode="nearest",
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = calculate_distances(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_B],
        mode="nearest",
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_distance_calculator")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_distance_calculator"
    assert artifact.payload["source"] == "distance_calculator"
    assert artifact.payload["operation"] == "distance"
    assert len(artifact.payload["features"]) == 1


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_distances" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_distances")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_distances"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "source_features" in descriptor.required_inputs
    assert "target_features" in descriptor.required_inputs
    assert "engine" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "distance"
    assert descriptor.metadata["planar_only"] is True


def test_shapely_engine_if_installed() -> None:
    pytest.importorskip("shapely", reason="shapely not installed")

    result = calculate_distances(
        source_features=[SOURCE_POINT],
        target_features=[TARGET_POINT_A],
        mode="nearest",
        engine="shapely",
        precision=4,
    )

    props = result.features[0]["properties"]

    assert result.metadata["engines_used"] == ["shapely"]
    assert props["_distance"] == 5.0
