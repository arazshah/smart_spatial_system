"""
Tests for data_writer_exporter plugin.

Run:
    pytest tests/test_data_writer_exporter.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.data_writer_exporter import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _configured_allowed_output_roots,
    _configured_raster_extensions,
    _configured_vector_extensions,
    _extract_features,
    _geometry_bbox,
    _merge_bboxes,
    _resolve_output_path,
    _safe_filename_part,
    export_raster_copy,
    export_vector_geojson,
)


@pytest.fixture
def sample_features():
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
            "properties": {"id": 1, "name": "A"},
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
            "properties": {"id": 2, "name": "B"},
        },
    ]


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "data_writer_exporter"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Data Writer Exporter"
    assert "filesystem" in PLUGIN.manifest.permissions


def test_safe_filename_part() -> None:
    assert _safe_filename_part("roads layer.geojson") == "roads_layer.geojson"
    assert _safe_filename_part("bad/name:*?") == "bad_name"
    assert _safe_filename_part("") == "export"


def test_configured_extensions_defaults() -> None:
    assert ".geojson" in _configured_vector_extensions({})
    assert ".json" in _configured_vector_extensions({})
    assert ".tif" in _configured_raster_extensions({})
    assert ".png" in _configured_raster_extensions({})


def test_configured_allowed_output_roots_defaults() -> None:
    assert _configured_allowed_output_roots({}) == []


def test_resolve_output_path_with_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    path = _resolve_output_path(
        output_path=None,
        output_dir=str(allowed),
        filename="roads.geojson",
        default_output_dir=str(allowed),
        default_stem="vector",
        extension=".geojson",
        allowed_output_roots=[str(allowed)],
    )

    assert path == (allowed / "roads.geojson").resolve()


def test_resolve_output_path_rejects_outside_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(ValueError, match="allowed output root"):
        _resolve_output_path(
            output_path=str(outside / "roads.geojson"),
            output_dir=None,
            filename=None,
            default_output_dir=str(allowed),
            default_stem="vector",
            extension=".geojson",
            allowed_output_roots=[str(allowed)],
        )


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
    feature = sample_features[0]

    features, info = _extract_features(feature)

    assert len(features) == 1
    assert info["input_geojson_type"] == "Feature"


def test_extract_features_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        _extract_features({"type": "Point", "coordinates": [1, 2]})


def test_geometry_bbox_point() -> None:
    geometry = {"type": "Point", "coordinates": [51.4, 35.7]}
    assert _geometry_bbox(geometry) == [51.4, 35.7, 51.4, 35.7]


def test_merge_bboxes() -> None:
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


def test_export_vector_geojson_success_with_direct_output_path(
    monkeypatch,
    tmp_path: Path,
    sample_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
default_overwrite: true
pretty_json: true
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
    - .json
raster:
  allowed_extensions:
    - .tif
    - .png
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    output_path = output_dir / "roads.geojson"

    result = export_vector_geojson(
        features=sample_features,
        output_path=str(output_path),
        metadata={"layer": "roads"},
    )

    assert output_path.exists()
    assert len(result.features) == 2
    assert result.metadata["path"] == str(output_path.resolve())
    assert result.metadata["feature_count"] == 2
    assert result.metadata["geometry_types"]["Point"] == 1
    assert result.metadata["geometry_types"]["Polygon"] == 1
    assert result.metadata["layer"] == "roads"
    assert result.metadata["file_size_bytes"] > 0

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 2
    assert data["metadata"]["layer"] == "roads"


def test_export_vector_geojson_uses_default_output_dir_from_config(
    monkeypatch,
    tmp_path: Path,
    sample_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "exports"

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
default_overwrite: true
pretty_json: false
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = export_vector_geojson(
        features=sample_features,
        filename="my_roads.geojson",
    )

    out_path = Path(result.metadata["path"])
    assert out_path.exists()
    assert out_path.parent == output_dir.resolve()
    assert out_path.name == "my_roads.geojson"


def test_export_vector_geojson_rejects_outside_allowed_root(
    monkeypatch,
    tmp_path: Path,
    sample_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    allowed_dir = tmp_path / "allowed"
    outside_dir = tmp_path / "outside"
    allowed_dir.mkdir()
    outside_dir.mkdir()

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(allowed_dir)}
default_overwrite: true
allowed_output_roots:
  - {str(allowed_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="allowed output root"):
        export_vector_geojson(
            features=sample_features,
            output_path=str(outside_dir / "bad.geojson"),
        )


def test_export_vector_geojson_rejects_existing_when_overwrite_false(
    monkeypatch,
    tmp_path: Path,
    sample_features,
) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()
    output_path = output_dir / "roads.geojson"
    output_path.write_text("existing", encoding="utf-8")

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
default_overwrite: false
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(FileExistsError):
        export_vector_geojson(
            features=sample_features,
            output_path=str(output_path),
        )


def test_vectorout_to_artifact(monkeypatch, tmp_path: Path, sample_features) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)
    output_dir = tmp_path / "exports"

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = export_vector_geojson(
        features=sample_features,
        filename="artifact.geojson",
    )

    artifact = result.to_artifact(produced_by="test_data_writer_exporter")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_data_writer_exporter"
    assert len(artifact.payload["features"]) == 2
    assert artifact.payload["source"] == "data_writer_exporter"
    assert artifact.payload["feature_count"] == 2


def test_export_raster_copy_success(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()

    src = tmp_path / "source.tif"
    src.write_bytes(b"FAKE_RASTER_BYTES")

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
default_overwrite: true
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
    - .png
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = export_raster_copy(
        path=str(src),
        filename="copied.tif",
        metadata={"layer": "dem"},
    )

    out_path = Path(result.path)

    assert out_path.exists()
    assert out_path.read_bytes() == b"FAKE_RASTER_BYTES"
    assert out_path.parent == output_dir.resolve()
    assert result.metadata["source"] == "data_writer_exporter"
    assert result.metadata["input_path"] == str(src.resolve())
    assert result.metadata["layer"] == "dem"
    assert result.metadata["file_size_bytes"] == len(b"FAKE_RASTER_BYTES")


def test_export_raster_copy_rejects_missing_input(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "exports"

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(FileNotFoundError):
        export_raster_copy(
            path=str(tmp_path / "missing.tif"),
            filename="copy.tif",
        )


def test_export_raster_copy_rejects_unsupported_extension(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "exports"

    src = tmp_path / "source.bin"
    src.write_bytes(b"BYTES")

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="Unsupported raster input extension"):
        export_raster_copy(
            path=str(src),
            filename="copy.tif",
        )


def test_rasterout_to_artifact(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    output_dir = tmp_path / "exports"

    src = tmp_path / "source.tif"
    src.write_bytes(b"FAKE_RASTER_BYTES")

    config_file = config_dir / "data_writer_exporter.yaml"
    config_file.write_text(
        f"""
default_output_dir: {str(output_dir)}
allowed_output_roots:
  - {str(output_dir)}
vector:
  allowed_extensions:
    - .geojson
raster:
  allowed_extensions:
    - .tif
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = export_raster_copy(
        path=str(src),
        filename="artifact.tif",
    )

    artifact = result.to_artifact(produced_by="test_data_writer_exporter")

    assert artifact.kind == "raster_ref"
    assert artifact.produced_by == "test_data_writer_exporter"
    assert artifact.payload["path"] == str(Path(result.path).resolve())
    assert artifact.payload["source"] == "data_writer_exporter"


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "export_vector_geojson" in names
    assert "export_raster_copy" in names
    assert len(regs) >= 2


def test_vector_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "export_vector_geojson")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "export_vector_geojson"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "output_path" in descriptor.optional_inputs
    assert "filesystem" in descriptor.requires_permissions
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True


def test_raster_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "export_raster_copy")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "export_raster_copy"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "path" in descriptor.required_inputs
    assert "output_path" in descriptor.optional_inputs
    assert "filesystem" in descriptor.requires_permissions
    assert descriptor.metadata["artifact_kind"] == "raster_ref"
    assert descriptor.metadata["config_aware"] is True
