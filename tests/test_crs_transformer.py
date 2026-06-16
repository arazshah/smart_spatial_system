"""
Tests for crs_transformer plugin.

Run:
    pytest tests/test_crs_transformer.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.exceptions import SDKDependencyError  # noqa: E402
from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.crs_transformer import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _configured_allowed_crs,
    _configured_precision,
    _extract_features,
    _geometry_bbox,
    _is_position,
    _lonlat_to_webmercator,
    _normalize_crs,
    _python_transform_position,
    _transform_coordinates,
    _transform_geometry,
    _validate_engine,
    _webmercator_to_lonlat,
    transform_vector_crs,
)


@pytest.fixture
def sample_features():
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "properties": {"id": 1, "name": "origin"},
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[0.0, 0.0], [1.0, 1.0]],
            },
            "properties": {"id": 2, "name": "line"},
        },
    ]


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "crs_transformer"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "CRS Transformer"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("pyproj") == "pyproj"
    assert _validate_engine("python") == "python"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_normalize_crs() -> None:
    assert _normalize_crs(4326) == "EPSG:4326"
    assert _normalize_crs("4326") == "EPSG:4326"
    assert _normalize_crs("epsg:4326") == "EPSG:4326"
    assert _normalize_crs(" EPSG:3857 ") == "EPSG:3857"

    with pytest.raises(ValueError):
        _normalize_crs(None)


def test_configured_allowed_crs_defaults() -> None:
    assert _configured_allowed_crs({}) == set()


def test_configured_allowed_crs_values() -> None:
    allowed = _configured_allowed_crs({"allowed_crs": ["epsg:4326", 3857]})
    assert allowed == {"EPSG:4326", "EPSG:3857"}


def test_configured_precision_defaults() -> None:
    assert _configured_precision({}) == 8


def test_configured_precision_null() -> None:
    assert _configured_precision({"coordinate_precision": None}) is None


def test_is_position() -> None:
    assert _is_position([1, 2]) is True
    assert _is_position([1, 2, 3]) is True
    assert _is_position([1]) is False
    assert _is_position(["x", "y"]) is False


def test_lonlat_to_webmercator_origin() -> None:
    x, y = _lonlat_to_webmercator(0, 0)
    assert x == pytest.approx(0.0, abs=1e-8)
    assert y == pytest.approx(0.0, abs=1e-8)


def test_lonlat_webmercator_round_trip() -> None:
    x, y = _lonlat_to_webmercator(51.4, 35.7)
    lon, lat = _webmercator_to_lonlat(x, y)

    assert lon == pytest.approx(51.4, rel=1e-8)
    assert lat == pytest.approx(35.7, rel=1e-8)


def test_python_transform_position_4326_to_3857() -> None:
    result = _python_transform_position(
        position=[0.0, 0.0],
        source_crs="EPSG:4326",
        target_crs="EPSG:3857",
        precision=8,
    )

    assert result == [0.0, 0.0]


def test_python_transform_position_preserves_extra_dimensions() -> None:
    result = _python_transform_position(
        position=[0.0, 0.0, 100.0],
        source_crs="EPSG:4326",
        target_crs="EPSG:3857",
        precision=8,
    )

    assert result[0] == 0.0
    assert result[1] == 0.0
    assert result[2] == 100.0


def test_python_transform_position_unsupported_pair_raises() -> None:
    with pytest.raises(SDKDependencyError):
        _python_transform_position(
            position=[0.0, 0.0],
            source_crs="EPSG:4326",
            target_crs="EPSG:32639",
            precision=8,
        )


def test_transform_coordinates_point() -> None:
    counter = {"count": 0}

    result = _transform_coordinates(
        [0.0, 0.0],
        lambda pos: [pos[0] + 1, pos[1] + 2],
        counter,
    )

    assert result == [1.0, 2.0]
    assert counter["count"] == 1


def test_transform_coordinates_nested_linestring() -> None:
    counter = {"count": 0}

    result = _transform_coordinates(
        [[0.0, 0.0], [1.0, 1.0]],
        lambda pos: [pos[0] + 1, pos[1] + 2],
        counter,
    )

    assert result == [[1.0, 2.0], [2.0, 3.0]]
    assert counter["count"] == 2


def test_transform_geometry_point() -> None:
    geometry = {"type": "Point", "coordinates": [0.0, 0.0]}
    counter = {"count": 0}

    result = _transform_geometry(
        geometry,
        lambda pos: [pos[0] + 1, pos[1] + 2],
        counter,
    )

    assert result["type"] == "Point"
    assert result["coordinates"] == [1.0, 2.0]
    assert counter["count"] == 1


def test_transform_geometry_collection() -> None:
    geometry = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Point", "coordinates": [0.0, 0.0]},
            {"type": "Point", "coordinates": [1.0, 1.0]},
        ],
    }
    counter = {"count": 0}

    result = _transform_geometry(
        geometry,
        lambda pos: [pos[0] + 1, pos[1] + 2],
        counter,
    )

    assert result["type"] == "GeometryCollection"
    assert result["geometries"][0]["coordinates"] == [1.0, 2.0]
    assert result["geometries"][1]["coordinates"] == [2.0, 3.0]
    assert counter["count"] == 2


def test_geometry_bbox_point() -> None:
    geometry = {"type": "Point", "coordinates": [1, 2]}
    assert _geometry_bbox(geometry) == [1.0, 2.0, 1.0, 2.0]


def test_extract_features_from_list(sample_features) -> None:
    features, info = _extract_features(sample_features)

    assert len(features) == 2
    assert info["input_geojson_type"] == "FeatureList"


def test_extract_features_from_feature_collection(sample_features) -> None:
    collection = {
        "type": "FeatureCollection",
        "features": sample_features,
    }

    features, info = _extract_features(collection)

    assert len(features) == 2
    assert info["input_geojson_type"] == "FeatureCollection"


def test_extract_features_from_single_feature(sample_features) -> None:
    features, info = _extract_features(sample_features[0])

    assert len(features) == 1
    assert info["input_geojson_type"] == "Feature"


def test_extract_features_from_vectorout(sample_features) -> None:
    vector = VectorOut(
        features=sample_features,
        metadata={"source": "test"},
    )

    features, info = _extract_features(vector)

    assert len(features) == 2
    assert info["input_type"] == "VectorOut"
    assert info["input_metadata"]["source"] == "test"


def test_transform_vector_crs_python_success(sample_features) -> None:
    result = transform_vector_crs(
        features=sample_features,
        source_crs="EPSG:4326",
        target_crs="EPSG:3857",
        engine="python",
        precision=4,
    )

    assert len(result.features) == 2

    point = result.features[0]
    assert point["geometry"]["type"] == "Point"
    assert point["geometry"]["coordinates"] == [0.0, 0.0]
    assert point["properties"]["id"] == 1

    md = result.metadata
    assert md["source"] == "crs_transformer"
    assert md["operation"] == "crs_transform"
    assert md["source_crs"] == "EPSG:4326"
    assert md["target_crs"] == "EPSG:3857"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["coordinate_precision"] == 4
    assert md["input_feature_count"] == 2
    assert md["output_feature_count"] == 2
    assert md["coordinate_transform_count"] == 3


def test_transform_vector_crs_identity_allowed(sample_features) -> None:
    result = transform_vector_crs(
        features=sample_features,
        source_crs="EPSG:4326",
        target_crs="EPSG:4326",
        engine="python",
    )

    assert len(result.features) == 2
    assert result.features[0]["geometry"]["coordinates"] == [0.0, 0.0]
    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["target_crs"] == "EPSG:4326"


def test_transform_vector_crs_uses_config_defaults(
    monkeypatch,
    tmp_path: Path,
    sample_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "crs_transformer.yaml").write_text(
        """
default_source_crs: EPSG:4326
default_target_crs: EPSG:3857
default_engine: python
coordinate_precision: 3
allow_identity: true
preserve_properties: true
allowed_crs:
  - EPSG:4326
  - EPSG:3857
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = transform_vector_crs(features=sample_features)

    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["target_crs"] == "EPSG:3857"
    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["coordinate_precision"] == 3


def test_transform_vector_crs_rejects_disallowed_crs(
    monkeypatch,
    tmp_path: Path,
    sample_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "crs_transformer.yaml").write_text(
        """
default_engine: python
allowed_crs:
  - EPSG:4326
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="not allowed"):
        transform_vector_crs(
            features=sample_features,
            source_crs="EPSG:4326",
            target_crs="EPSG:3857",
            engine="python",
        )


def test_transform_vector_crs_rejects_identity_when_disabled(
    monkeypatch,
    tmp_path: Path,
    sample_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "crs_transformer.yaml").write_text(
        """
default_engine: python
allow_identity: false
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="Identity CRS transform"):
        transform_vector_crs(
            features=sample_features,
            source_crs="EPSG:4326",
            target_crs="EPSG:4326",
            engine="python",
        )


def test_transform_vector_crs_rejects_invalid_metadata(sample_features) -> None:
    with pytest.raises(ValueError, match="metadata"):
        transform_vector_crs(
            features=sample_features,
            source_crs="EPSG:4326",
            target_crs="EPSG:3857",
            engine="python",
            metadata="bad",
        )


def test_transform_vector_crs_metadata_merge(sample_features) -> None:
    result = transform_vector_crs(
        features=sample_features,
        source_crs="EPSG:4326",
        target_crs="EPSG:3857",
        engine="python",
        metadata={"analysis_id": "crs-1"},
    )

    assert result.metadata["analysis_id"] == "crs-1"


def test_vectorout_to_artifact(sample_features) -> None:
    result = transform_vector_crs(
        features=sample_features,
        source_crs="EPSG:4326",
        target_crs="EPSG:3857",
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_crs_transformer")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_crs_transformer"
    assert artifact.payload["source"] == "crs_transformer"
    assert artifact.payload["operation"] == "crs_transform"
    assert len(artifact.payload["features"]) == 2


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "transform_vector_crs" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "transform_vector_crs")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "transform_vector_crs"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "source_crs" in descriptor.required_inputs
    assert "target_crs" in descriptor.required_inputs
    assert "engine" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "crs_transform"


def test_pyproj_engine_if_installed(sample_features) -> None:
    pytest.importorskip("pyproj", reason="pyproj not installed")

    result = transform_vector_crs(
        features=sample_features,
        source_crs="EPSG:4326",
        target_crs="EPSG:3857",
        engine="pyproj",
        precision=4,
    )

    assert result.metadata["engines_used"] == ["pyproj"]
    assert result.features[0]["geometry"]["coordinates"] == [0.0, 0.0]


def test_auto_engine_unsupported_pair_requires_pyproj_if_not_available(sample_features) -> None:
    """
    This test is deterministic only for python engine.
    It confirms that unsupported CRS pairs fail when pure-python engine is forced.
    """
    with pytest.raises(SDKDependencyError):
        transform_vector_crs(
            features=sample_features,
            source_crs="EPSG:4326",
            target_crs="EPSG:32639",
            engine="python",
        )
