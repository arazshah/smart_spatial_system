"""
Tests for centroid_extractor plugin.

Run:
    pytest tests/test_centroid_extractor.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.centroid_extractor import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _configured_precision,
    _distance,
    _extract_features,
    _geometry_bbox,
    _is_position,
    _iter_positions,
    _linestring_centroid,
    _mean_point,
    _polygon_centroid,
    _polygon_ring_centroid,
    _python_centroid_geometry,
    _validate_engine,
    extract_centroids,
)


POINT_FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [51.0, 35.0]},
    "properties": {"id": 1},
}

LINE_FEATURE = {
    "type": "Feature",
    "geometry": {
        "type": "LineString",
        "coordinates": [[0.0, 0.0], [10.0, 0.0]],
    },
    "properties": {"id": 2},
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
    "properties": {"id": 3},
}

MULTIPOINT_FEATURE = {
    "type": "Feature",
    "geometry": {
        "type": "MultiPoint",
        "coordinates": [[0.0, 0.0], [10.0, 10.0]],
    },
    "properties": {"id": 4},
}

NULL_GEOMETRY_FEATURE = {
    "type": "Feature",
    "geometry": None,
    "properties": {"id": 5},
}


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "centroid_extractor"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Centroid Extractor"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("python") == "python"
    assert _validate_engine("shapely") == "shapely"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_configured_precision_defaults() -> None:
    assert _configured_precision({}) == 8


def test_configured_precision_null() -> None:
    assert _configured_precision({"coordinate_precision": None}) is None


def test_is_position() -> None:
    assert _is_position([1, 2]) is True
    assert _is_position([1, 2, 3]) is True
    assert _is_position([1]) is False
    assert _is_position(["x", "y"]) is False


def test_iter_positions() -> None:
    coords = [[[0, 0], [1, 1]], [[2, 2]]]
    assert _iter_positions(coords) == [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]


def test_mean_point() -> None:
    assert _mean_point([(0, 0), (10, 10)]) == (5.0, 5.0)
    assert _mean_point([]) is None


def test_distance() -> None:
    assert _distance((0, 0), (3, 4)) == 5.0


def test_linestring_centroid() -> None:
    centroid = _linestring_centroid([[0, 0], [10, 0]])
    assert centroid == (5.0, 0.0)


def test_polygon_ring_centroid_square() -> None:
    ring = [
        [0.0, 0.0],
        [10.0, 0.0],
        [10.0, 10.0],
        [0.0, 10.0],
        [0.0, 0.0],
    ]

    item = _polygon_ring_centroid(ring)

    assert item is not None
    cx, cy, area = item

    assert cx == pytest.approx(5.0)
    assert cy == pytest.approx(5.0)
    assert abs(area) == pytest.approx(100.0)


def test_polygon_centroid_square() -> None:
    item = _polygon_centroid(POLYGON_FEATURE["geometry"]["coordinates"])

    assert item is not None
    cx, cy, area = item

    assert cx == pytest.approx(5.0)
    assert cy == pytest.approx(5.0)
    assert area == pytest.approx(100.0)


def test_python_centroid_point() -> None:
    centroid = _python_centroid_geometry(POINT_FEATURE["geometry"])
    assert centroid == (51.0, 35.0)


def test_python_centroid_multipoint() -> None:
    centroid = _python_centroid_geometry(MULTIPOINT_FEATURE["geometry"])
    assert centroid == (5.0, 5.0)


def test_python_centroid_linestring() -> None:
    centroid = _python_centroid_geometry(LINE_FEATURE["geometry"])
    assert centroid == (5.0, 0.0)


def test_python_centroid_polygon() -> None:
    centroid = _python_centroid_geometry(POLYGON_FEATURE["geometry"])
    assert centroid == pytest.approx((5.0, 5.0))


def test_python_centroid_geometry_collection() -> None:
    geometry = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Point", "coordinates": [0, 0]},
            {"type": "Point", "coordinates": [10, 10]},
        ],
    }

    centroid = _python_centroid_geometry(geometry)
    assert centroid == (5.0, 5.0)


def test_extract_features_from_list() -> None:
    features, info = _extract_features([POINT_FEATURE, POLYGON_FEATURE])

    assert len(features) == 2
    assert info["input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [POINT_FEATURE, POLYGON_FEATURE],
    }

    features, info = _extract_features(collection)

    assert len(features) == 2
    assert info["input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(
        features=[POINT_FEATURE],
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector)

    assert len(features) == 1
    assert info["input_type"] == "VectorOut"
    assert info["input_metadata"]["source"] == "test"


def test_geometry_bbox_point() -> None:
    geometry = {"type": "Point", "coordinates": [5, 6]}
    assert _geometry_bbox(geometry) == [5.0, 6.0, 5.0, 6.0]


def test_extract_centroids_python_success() -> None:
    result = extract_centroids(
        features=[POINT_FEATURE, LINE_FEATURE, POLYGON_FEATURE],
        engine="python",
        precision=4,
    )

    assert len(result.features) == 3

    assert result.features[0]["geometry"]["coordinates"] == [51.0, 35.0]
    assert result.features[1]["geometry"]["coordinates"] == [5.0, 0.0]
    assert result.features[2]["geometry"]["coordinates"] == [5.0, 5.0]

    assert result.features[2]["properties"]["id"] == 3
    assert result.features[2]["properties"]["_source_geometry_type"] == "Polygon"
    assert result.features[2]["properties"]["_centroid_status"] == "success"
    assert result.features[2]["properties"]["_centroid_engine"] == "python"

    md = result.metadata
    assert md["source"] == "centroid_extractor"
    assert md["operation"] == "centroid"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["coordinate_precision"] == 4
    assert md["input_feature_count"] == 3
    assert md["output_feature_count"] == 3
    assert md["success_count"] == 3
    assert md["failed_count"] == 0
    assert md["geometry_types"]["Point"] == 3


def test_extract_centroids_flags_null_geometry_by_default() -> None:
    result = extract_centroids(
        features=[POINT_FEATURE, NULL_GEOMETRY_FEATURE],
        engine="python",
    )

    assert len(result.features) == 2
    assert result.metadata["success_count"] == 1
    assert result.metadata["failed_count"] == 1
    assert result.metadata["dropped_count"] == 0

    failed = result.features[1]
    assert failed["geometry"] is None
    assert failed["properties"]["_centroid_status"] == "failed"


def test_extract_centroids_drop_failed() -> None:
    result = extract_centroids(
        features=[POINT_FEATURE, NULL_GEOMETRY_FEATURE],
        engine="python",
        drop_failed=True,
    )

    assert len(result.features) == 1
    assert result.metadata["success_count"] == 1
    assert result.metadata["failed_count"] == 1
    assert result.metadata["dropped_count"] == 1


def test_extract_centroids_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "centroid_extractor.yaml").write_text(
        """
default_engine: python
coordinate_precision: 3
preserve_properties: true
drop_failed: false
fields:
  add_source_geometry_type: true
  add_source_feature_index: true
  add_centroid_status: true
  add_engine_used: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = extract_centroids(features=[POLYGON_FEATURE])

    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["coordinate_precision"] == 3
    assert result.features[0]["geometry"]["coordinates"] == [5.0, 5.0]


def test_extract_centroids_metadata_merge() -> None:
    result = extract_centroids(
        features=[POINT_FEATURE],
        engine="python",
        metadata={"analysis_id": "centroid-1"},
    )

    assert result.metadata["analysis_id"] == "centroid-1"


def test_extract_centroids_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        extract_centroids(
            features=[POINT_FEATURE],
            engine="python",
            metadata="bad",
        )


def test_extract_centroids_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        extract_centroids(
            features={"type": "Point", "coordinates": [1, 2]},
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = extract_centroids(
        features=[POINT_FEATURE, POLYGON_FEATURE],
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_centroid_extractor")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_centroid_extractor"
    assert artifact.payload["source"] == "centroid_extractor"
    assert artifact.payload["operation"] == "centroid"
    assert len(artifact.payload["features"]) == 2


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "extract_centroids" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "extract_centroids")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "extract_centroids"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "engine" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "centroid"


def test_shapely_engine_if_installed() -> None:
    pytest.importorskip("shapely", reason="shapely not installed")

    result = extract_centroids(
        features=[POLYGON_FEATURE],
        engine="shapely",
        precision=4,
    )

    assert result.metadata["engines_used"] == ["shapely"]
    assert result.features[0]["geometry"]["coordinates"] == [5.0, 5.0]
