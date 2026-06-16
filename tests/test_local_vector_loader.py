"""
Tests for local_vector_loader plugin.

Run:
    pytest tests/test_local_vector_loader.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.local_vector_loader import (  # noqa: E402
    ALLOWED_VECTOR_EXTENSIONS,
    GEOPANDAS_REQUIRED_EXTENSIONS,
    PLUGIN,
    PLUGIN_ID,
    _geometry_bbox,
    _merge_bboxes,
    _validate_path,
    load_local_vector,
)


@pytest.fixture
def sample_geojson(tmp_path: Path) -> str:
    """
    Create a valid temporary GeoJSON FeatureCollection.
    """
    path = tmp_path / "sample.geojson"

    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.4, 35.7],
                },
                "properties": {
                    "id": 1,
                    "name": "A",
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [51.0, 35.0],
                            [52.0, 35.0],
                            [52.0, 36.0],
                            [51.0, 36.0],
                            [51.0, 35.0],
                        ]
                    ],
                },
                "properties": {
                    "id": 2,
                    "name": "B",
                },
            },
        ],
    }

    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture
def single_feature_geojson(tmp_path: Path) -> str:
    """
    Create a valid single GeoJSON Feature.
    """
    path = tmp_path / "single.geojson"

    data = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [10.0, 20.0],
        },
        "properties": {
            "name": "single",
        },
    }

    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture
def empty_geojson(tmp_path: Path) -> str:
    """
    Create an empty FeatureCollection.
    """
    path = tmp_path / "empty.geojson"

    data = {
        "type": "FeatureCollection",
        "features": [],
    }

    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_plugin_manifest_basic_fields() -> None:
    """
    PLUGIN must expose a valid manifest.
    """
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "local_vector_loader"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Local Vector Loader"
    assert "filesystem" in PLUGIN.manifest.permissions


def test_allowed_vector_extensions() -> None:
    """
    Important vector extensions must be supported.
    """
    assert ".geojson" in ALLOWED_VECTOR_EXTENSIONS
    assert ".json" in ALLOWED_VECTOR_EXTENSIONS
    assert ".shp" in ALLOWED_VECTOR_EXTENSIONS
    assert ".gpkg" in ALLOWED_VECTOR_EXTENSIONS
    assert ".kml" in ALLOWED_VECTOR_EXTENSIONS
    assert ".fgb" in ALLOWED_VECTOR_EXTENSIONS


def test_geopandas_required_extensions() -> None:
    """
    Non-GeoJSON vector formats should be marked as geopandas-dependent.
    """
    assert ".shp" in GEOPANDAS_REQUIRED_EXTENSIONS
    assert ".gpkg" in GEOPANDAS_REQUIRED_EXTENSIONS
    assert ".kml" in GEOPANDAS_REQUIRED_EXTENSIONS
    assert ".fgb" in GEOPANDAS_REQUIRED_EXTENSIONS


def test_validate_path_success(sample_geojson: str) -> None:
    """
    _validate_path should return resolved Path for valid vector path.
    """
    resolved = _validate_path(sample_geojson)
    assert isinstance(resolved, Path)
    assert resolved.exists()
    assert resolved.is_file()
    assert resolved.suffix.lower() == ".geojson"


def test_validate_path_rejects_empty_path() -> None:
    """
    Empty path must raise ValueError.
    """
    with pytest.raises(ValueError, match="non-empty string"):
        _validate_path("")


def test_validate_path_rejects_none() -> None:
    """
    None path must raise ValueError.
    """
    with pytest.raises(ValueError, match="non-empty string"):
        _validate_path(None)  # type: ignore[arg-type]


def test_validate_path_rejects_missing_file() -> None:
    """
    Missing vector file must raise FileNotFoundError.
    """
    with pytest.raises(FileNotFoundError):
        _validate_path("/tmp/this_vector_file_does_not_exist_12345.geojson")


def test_validate_path_rejects_directory(tmp_path: Path) -> None:
    """
    Directory path must not be accepted as vector file.
    """
    vector_dir = tmp_path / "folder.geojson"
    vector_dir.mkdir()

    with pytest.raises(ValueError, match="not a file"):
        _validate_path(str(vector_dir))


def test_validate_path_rejects_invalid_extension(tmp_path: Path) -> None:
    """
    Invalid extension must raise ValueError when strict_extensions=True.
    """
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("not a vector")

    with pytest.raises(ValueError, match="Unsupported vector extension"):
        _validate_path(str(txt_file), strict_extensions=True)


def test_validate_path_allows_invalid_extension_when_not_strict(tmp_path: Path) -> None:
    """
    Invalid extension can pass path validation if strict_extensions=False.

    This only validates path. It does not mean the loader can parse it.
    """
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("not a vector")

    resolved = _validate_path(str(txt_file), strict_extensions=False)
    assert resolved.exists()
    assert resolved.suffix == ".txt"


def test_geometry_bbox_point() -> None:
    """
    Point geometry bbox should be calculated correctly.
    """
    geometry = {
        "type": "Point",
        "coordinates": [51.4, 35.7],
    }

    assert _geometry_bbox(geometry) == [51.4, 35.7, 51.4, 35.7]


def test_geometry_bbox_polygon() -> None:
    """
    Polygon geometry bbox should be calculated correctly.
    """
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [51.0, 35.0],
                [52.0, 35.0],
                [52.0, 36.0],
                [51.0, 36.0],
                [51.0, 35.0],
            ]
        ],
    }

    assert _geometry_bbox(geometry) == [51.0, 35.0, 52.0, 36.0]


def test_merge_bboxes() -> None:
    """
    Multiple bboxes should be merged into one bbox dict.
    """
    merged = _merge_bboxes([
        [51.4, 35.7, 51.4, 35.7],
        [51.0, 35.0, 52.0, 36.0],
    ])

    assert merged == {
        "minx": 51.0,
        "miny": 35.0,
        "maxx": 52.0,
        "maxy": 36.0,
    }


def test_load_local_vector_success(sample_geojson: str) -> None:
    """
    load_local_vector should return VectorOut for valid GeoJSON.
    """
    result = load_local_vector(sample_geojson)

    assert result is not None
    assert isinstance(result.features, list)
    assert len(result.features) == 2
    assert isinstance(result.metadata, dict)


def test_load_local_vector_metadata_core_fields(sample_geojson: str) -> None:
    """
    Metadata should contain core vector information.
    """
    result = load_local_vector(sample_geojson)
    md = result.metadata

    assert md["source"] == "local_file"
    assert md["loader"] == "local_vector_loader"
    assert md["format"] == "geojson"
    assert md["geojson_type"] == "FeatureCollection"
    assert md["filename"] == "sample.geojson"
    assert md["extension"] == ".geojson"
    assert md["feature_count"] == 2
    assert md["crs"] == "EPSG:4326"
    assert md["file_size_bytes"] > 0


def test_load_local_vector_geometry_types(sample_geojson: str) -> None:
    """
    Geometry type counts should be extracted correctly.
    """
    result = load_local_vector(sample_geojson)
    geometry_types = result.metadata["geometry_types"]

    assert geometry_types["Point"] == 1
    assert geometry_types["Polygon"] == 1


def test_load_local_vector_bounds(sample_geojson: str) -> None:
    """
    Bounds should be extracted correctly from features.
    """
    result = load_local_vector(sample_geojson)
    bounds = result.metadata["bounds"]

    assert bounds["minx"] == pytest.approx(51.0)
    assert bounds["miny"] == pytest.approx(35.0)
    assert bounds["maxx"] == pytest.approx(52.0)
    assert bounds["maxy"] == pytest.approx(36.0)


def test_load_single_feature_geojson(single_feature_geojson: str) -> None:
    """
    Single GeoJSON Feature should be accepted.
    """
    result = load_local_vector(single_feature_geojson)

    assert len(result.features) == 1
    assert result.metadata["geojson_type"] == "Feature"
    assert result.metadata["feature_count"] == 1
    assert result.features[0]["properties"]["name"] == "single"


def test_load_empty_feature_collection(empty_geojson: str) -> None:
    """
    Empty FeatureCollection should be valid.
    """
    result = load_local_vector(empty_geojson)

    assert result.features == []
    assert result.metadata["feature_count"] == 0
    assert result.metadata["bounds"] is None
    assert result.metadata["geometry_types"] == {}


def test_load_local_vector_with_max_features(sample_geojson: str) -> None:
    """
    max_features should limit returned features.
    """
    result = load_local_vector(sample_geojson, max_features=1)

    assert len(result.features) == 1
    assert result.metadata["feature_count"] == 1


def test_load_local_vector_rejects_negative_max_features(sample_geojson: str) -> None:
    """
    Negative max_features must raise ValueError.
    """
    with pytest.raises(ValueError, match="max_features"):
        load_local_vector(sample_geojson, max_features=-1)


def test_load_local_vector_rejects_invalid_json(tmp_path: Path) -> None:
    """
    Invalid JSON content should raise ValueError.
    """
    path = tmp_path / "bad.geojson"
    path.write_text("{ invalid json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        load_local_vector(str(path))


def test_load_local_vector_rejects_unsupported_geojson_type(tmp_path: Path) -> None:
    """
    Unsupported GeoJSON root type should raise ValueError.
    """
    path = tmp_path / "geometry.geojson"
    data = {
        "type": "Point",
        "coordinates": [1, 2],
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported GeoJSON type"):
        load_local_vector(str(path))


def test_load_local_vector_rejects_invalid_extension(tmp_path: Path) -> None:
    """
    load_local_vector should reject unsupported extensions by default.
    """
    file_path = tmp_path / "sample.txt"
    file_path.write_text("not vector")

    with pytest.raises(ValueError, match="Unsupported vector extension"):
        load_local_vector(str(file_path))


def test_load_local_vector_uppercase_extension(tmp_path: Path) -> None:
    """
    Uppercase vector extension should be accepted.
    """
    path = tmp_path / "UPPER.GEOJSON"

    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [1.0, 2.0],
                },
                "properties": {
                    "id": 1,
                },
            }
        ],
    }

    path.write_text(json.dumps(data), encoding="utf-8")
    result = load_local_vector(str(path))

    assert len(result.features) == 1
    assert result.metadata["extension"] == ".geojson"


def test_vectorout_to_artifact(sample_geojson: str) -> None:
    """
    VectorOut must be convertible to SDK/Kernel ExecutionArtifact.
    """
    result = load_local_vector(sample_geojson)
    artifact = result.to_artifact(produced_by="test_local_vector_loader")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_local_vector_loader"
    assert "features" in artifact.payload
    assert len(artifact.payload["features"]) == 2
    assert artifact.payload["source"] == "local_file"
    assert artifact.payload["loader"] == "local_vector_loader"
    assert artifact.payload["feature_count"] == 2


def test_capability_registered_inside_plugin() -> None:
    """
    auto_collect should collect decorated capabilities into SDKPlugin.
    """
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "load_local_vector" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    """
    Capability descriptor generated by SDK registration should contain expected fields.
    """
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "load_local_vector")

    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "load_local_vector"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.kind == "capability"
    assert descriptor.output_kind == "vector"
    assert "path" in descriptor.required_inputs
    assert "strict_extensions" in descriptor.optional_inputs
    assert "layer" in descriptor.optional_inputs
    assert "max_features" in descriptor.optional_inputs
    assert "filesystem" in descriptor.requires_permissions
    assert descriptor.metadata["routable"] is True
    assert descriptor.metadata["category"] == "data_io"
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["access_scope"] == "read_vector"


def test_load_local_vector_uses_config_allowed_extension_and_default_max_features(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """
    local_vector_loader should read allowed_extensions, allowed_roots and
    default_max_features from config.
    """
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    data_dir = tmp_path / "vectors"
    data_dir.mkdir()

    vector_path = data_dir / "sample.txt"

    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 2]},
                "properties": {"id": 1},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [3, 4]},
                "properties": {"id": 2},
            },
        ],
    }

    vector_path.write_text(json.dumps(data), encoding="utf-8")

    config_file = config_dir / "local_vector_loader.yaml"
    config_file.write_text(
        f"""
default_strict_extensions: true
default_max_features: 1
allowed_extensions:
  - .txt
allowed_roots:
  - {str(data_dir)}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = load_local_vector(str(vector_path))

    assert result is not None
    assert len(result.features) == 1
    assert result.metadata["filename"] == "sample.txt"
    assert result.metadata["extension"] == ".txt"
    assert result.metadata["feature_count"] == 1


def test_load_local_vector_rejects_path_outside_config_allowed_roots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """
    local_vector_loader should reject files outside configured allowed_roots.
    """
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    allowed_dir = tmp_path / "allowed"
    outside_dir = tmp_path / "outside"
    allowed_dir.mkdir()
    outside_dir.mkdir()

    vector_path = outside_dir / "sample.geojson"

    data = {
        "type": "FeatureCollection",
        "features": [],
    }

    vector_path.write_text(json.dumps(data), encoding="utf-8")

    config_file = config_dir / "local_vector_loader.yaml"
    config_file.write_text(
        f"""
default_strict_extensions: true
allowed_extensions:
  - .geojson
allowed_roots:
  - {str(allowed_dir)}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="allowed root"):
        load_local_vector(str(vector_path))
