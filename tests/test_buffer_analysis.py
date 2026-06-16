"""
Tests for buffer_analysis plugin.

Run:
    pytest tests/test_buffer_analysis.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.exceptions import SDKDependencyError  # noqa: E402
from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.buffer_analysis import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _build_vector_metadata,
    _configured_allowed_geometry_types,
    _extract_features,
    _geometry_bbox,
    _merge_bboxes,
    _python_buffer_point_geometry,
    _validate_distance,
    _validate_engine,
    _validate_quad_segs,
    buffer_vector_features,
)


@pytest.fixture
def sample_point_features():
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "properties": {"id": 1, "name": "A"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [10.0, 10.0]},
            "properties": {"id": 2, "name": "B"},
        },
    ]


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "buffer_analysis"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Buffer Analysis"


def test_validate_distance_accepts_positive() -> None:
    assert _validate_distance(10) == 10.0
    assert _validate_distance("2.5") == 2.5


def test_validate_distance_rejects_zero() -> None:
    with pytest.raises(ValueError, match="must not be zero"):
        _validate_distance(0)


def test_validate_distance_rejects_negative_by_default() -> None:
    with pytest.raises(ValueError, match="negative distance"):
        _validate_distance(-10)


def test_validate_distance_allows_negative_when_enabled() -> None:
    assert _validate_distance(-10, allow_negative=True) == -10.0


def test_validate_quad_segs() -> None:
    assert _validate_quad_segs(8) == 8
    assert _validate_quad_segs("4") == 4

    with pytest.raises(ValueError):
        _validate_quad_segs(0)


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("shapely") == "shapely"
    assert _validate_engine("python") == "python"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_configured_allowed_geometry_types_defaults() -> None:
    allowed = _configured_allowed_geometry_types({})
    assert "Point" in allowed
    assert "Polygon" in allowed


def test_extract_features_from_list(sample_point_features) -> None:
    features, info = _extract_features(sample_point_features)

    assert len(features) == 2
    assert info["input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection(sample_point_features) -> None:
    collection = {
        "type": "FeatureCollection",
        "features": sample_point_features,
    }

    features, info = _extract_features(collection)

    assert len(features) == 2
    assert info["input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_single_feature(sample_point_features) -> None:
    features, info = _extract_features(sample_point_features[0])

    assert len(features) == 1
    assert info["input_geojson_type"] == "Feature"


def test_extract_features_from_vectorout(sample_point_features) -> None:
    vector = VectorOut(
        features=sample_point_features,
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector)

    assert len(features) == 2
    assert info["input_type"] == "VectorOut"
    assert info["input_metadata"]["source"] == "test"


def test_extract_features_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        _extract_features({"type": "Point", "coordinates": [1, 2]})


def test_python_buffer_point_geometry() -> None:
    geometry = {"type": "Point", "coordinates": [0.0, 0.0]}

    buffered = _python_buffer_point_geometry(
        geometry=geometry,
        distance=10.0,
        quad_segs=4,
    )

    assert buffered["type"] == "Polygon"
    ring = buffered["coordinates"][0]

    assert len(ring) == 17
    assert ring[0] == ring[-1]
    assert ring[0][0] == pytest.approx(10.0)
    assert ring[0][1] == pytest.approx(0.0)


def test_python_buffer_rejects_non_point() -> None:
    geometry = {
        "type": "LineString",
        "coordinates": [[0, 0], [1, 1]],
    }

    with pytest.raises(SDKDependencyError):
        _python_buffer_point_geometry(
            geometry=geometry,
            distance=10.0,
            quad_segs=4,
        )


def test_geometry_bbox_polygon() -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [0, 0],
            [10, 0],
            [10, 5],
            [0, 5],
            [0, 0],
        ]],
    }

    assert _geometry_bbox(geometry) == [0.0, 0.0, 10.0, 5.0]


def test_merge_bboxes() -> None:
    merged = _merge_bboxes([
        [0, 0, 1, 1],
        [-1, -2, 10, 5],
    ])

    assert merged == {
        "minx": -1,
        "miny": -2,
        "maxx": 10,
        "maxy": 5,
    }


def test_build_vector_metadata(sample_point_features) -> None:
    metadata = _build_vector_metadata(sample_point_features)

    assert metadata["feature_count"] == 2
    assert metadata["geometry_types"]["Point"] == 2
    assert metadata["bounds"]["minx"] == 0.0
    assert metadata["bounds"]["maxx"] == 10.0


def test_buffer_vector_features_python_engine_success(sample_point_features) -> None:
    result = buffer_vector_features(
        features=sample_point_features,
        distance=10,
        units="meters",
        quad_segs=4,
        engine="python",
    )

    assert result is not None
    assert len(result.features) == 2

    first = result.features[0]
    assert first["type"] == "Feature"
    assert first["geometry"]["type"] == "Polygon"
    assert first["properties"]["id"] == 1
    assert first["properties"]["_buffer_source_index"] == 0

    md = result.metadata
    assert md["source"] == "buffer_analysis"
    assert md["operation"] == "buffer"
    assert md["distance"] == 10.0
    assert md["units"] == "meters"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["input_feature_count"] == 2
    assert md["output_feature_count"] == 2
    assert md["feature_count"] == 2
    assert md["geometry_types"]["Polygon"] == 2


def test_buffer_vector_features_uses_config_defaults(
    monkeypatch,
    tmp_path: Path,
    sample_point_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    config_file = config_dir / "buffer_analysis.yaml"
    config_file.write_text(
        """
default_distance: 5
default_units: kilometers
default_quad_segs: 2
default_engine: python
default_dissolve: false
allow_negative_distance: false
preserve_properties: true
allowed_geometry_types:
  - Point
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = buffer_vector_features(
        features=sample_point_features,
    )

    assert len(result.features) == 2
    assert result.metadata["distance"] == 5.0
    assert result.metadata["units"] == "kilometers"
    assert result.metadata["quad_segs"] == 2
    assert result.metadata["engine_requested"] == "python"


def test_buffer_vector_features_rejects_disallowed_geometry_type(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    config_file = config_dir / "buffer_analysis.yaml"
    config_file.write_text(
        """
default_distance: 5
default_engine: python
allowed_geometry_types:
  - Point
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[0, 0], [1, 1]],
            },
            "properties": {},
        }
    ]

    with pytest.raises(ValueError, match="not allowed"):
        buffer_vector_features(features=features)


def test_buffer_vector_features_python_engine_rejects_linestring() -> None:
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[0, 0], [1, 1]],
            },
            "properties": {},
        }
    ]

    with pytest.raises(SDKDependencyError):
        buffer_vector_features(
            features=features,
            distance=10,
            engine="python",
        )


def test_buffer_vector_features_rejects_negative_distance_by_config(
    monkeypatch,
    tmp_path: Path,
    sample_point_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    config_file = config_dir / "buffer_analysis.yaml"
    config_file.write_text(
        """
allow_negative_distance: false
default_engine: python
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="negative distance"):
        buffer_vector_features(
            features=sample_point_features,
            distance=-5,
        )


def test_buffer_vector_features_metadata_merge(sample_point_features) -> None:
    result = buffer_vector_features(
        features=sample_point_features,
        distance=10,
        engine="python",
        metadata={"analysis_id": "abc"},
    )

    assert result.metadata["analysis_id"] == "abc"


def test_buffer_vector_features_rejects_invalid_metadata(sample_point_features) -> None:
    with pytest.raises(ValueError, match="metadata"):
        buffer_vector_features(
            features=sample_point_features,
            distance=10,
            engine="python",
            metadata="bad",
        )


def test_dissolve_requires_shapely_when_python_engine(sample_point_features) -> None:
    with pytest.raises(SDKDependencyError, match="dissolve"):
        buffer_vector_features(
            features=sample_point_features,
            distance=10,
            engine="python",
            dissolve=True,
        )


def test_vectorout_to_artifact(sample_point_features) -> None:
    result = buffer_vector_features(
        features=sample_point_features,
        distance=10,
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_buffer_analysis")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_buffer_analysis"
    assert len(artifact.payload["features"]) == 2
    assert artifact.payload["source"] == "buffer_analysis"
    assert artifact.payload["operation"] == "buffer"


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "buffer_vector_features" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "buffer_vector_features")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "buffer_vector_features"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "distance" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "buffer"
