"""
Tests for geometry_validator plugin.

Run:
    pytest tests/test_geometry_validator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.geometry_validator import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _all_positions_numeric,
    _configured_allowed_geometry_types,
    _configured_min_coordinates,
    _count_positions,
    _extract_features,
    _is_position,
    _python_check_geometry,
    _ring_is_closed,
    _validate_engine,
    repair_geometries,
    validate_geometries,
)


VALID_POINT = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
    "properties": {"id": 1},
}

VALID_POLYGON = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [0, 0],
            [10, 0],
            [10, 10],
            [0, 10],
            [0, 0],
        ]],
    },
    "properties": {"id": 2},
}

INVALID_POLYGON_OPEN_RING = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [0, 0],
            [10, 0],
            [10, 10],
            [0, 10],
        ]],
    },
    "properties": {"id": 3},
}

INVALID_LINESTRING_TOO_FEW = {
    "type": "Feature",
    "geometry": {
        "type": "LineString",
        "coordinates": [[0, 0]],
    },
    "properties": {"id": 4},
}

INVALID_NON_NUMERIC = {
    "type": "Feature",
    "geometry": {
        "type": "Point",
        "coordinates": ["a", "b"],
    },
    "properties": {"id": 5},
}

NULL_GEOMETRY = {
    "type": "Feature",
    "geometry": None,
    "properties": {"id": 6},
}


def _default_config():
    return {
        "default_engine": "python",
        "min_coordinates": dict(_configured_min_coordinates({})),
    }


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "geometry_validator"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Geometry Validator"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("python") == "python"
    assert _validate_engine("shapely") == "shapely"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_is_position() -> None:
    assert _is_position([1, 2]) is True
    assert _is_position([1, 2, 3]) is True
    assert _is_position([1]) is False
    assert _is_position(["a", "b"]) is False
    assert _is_position("xy") is False


def test_count_positions() -> None:
    assert _count_positions([1, 2]) == 1
    assert _count_positions([[0, 0], [1, 1], [2, 2]]) == 3
    assert _count_positions([[[0, 0], [1, 1]]]) == 2


def test_all_positions_numeric() -> None:
    assert _all_positions_numeric([[0, 0], [1, 1]]) is True
    assert _all_positions_numeric([["a", "b"]]) is False


def test_ring_is_closed() -> None:
    closed = [[0, 0], [1, 0], [1, 1], [0, 0]]
    open_ring = [[0, 0], [1, 0], [1, 1], [0, 1]]

    assert _ring_is_closed(closed) is True
    assert _ring_is_closed(open_ring) is False
    assert _ring_is_closed([[0, 0]]) is False


def test_configured_allowed_geometry_types_defaults() -> None:
    allowed = _configured_allowed_geometry_types({})
    assert "Point" in allowed
    assert "Polygon" in allowed
    assert "GeometryCollection" in allowed


def test_python_check_valid_point() -> None:
    ok, reason = _python_check_geometry(
        VALID_POINT["geometry"],
        _configured_allowed_geometry_types({}),
        _configured_min_coordinates({}),
    )
    assert ok is True


def test_python_check_valid_polygon() -> None:
    ok, reason = _python_check_geometry(
        VALID_POLYGON["geometry"],
        _configured_allowed_geometry_types({}),
        _configured_min_coordinates({}),
    )
    assert ok is True


def test_python_check_open_ring_polygon() -> None:
    ok, reason = _python_check_geometry(
        INVALID_POLYGON_OPEN_RING["geometry"],
        _configured_allowed_geometry_types({}),
        _configured_min_coordinates({}),
    )
    assert ok is False
    assert "not closed" in reason


def test_python_check_too_few_linestring() -> None:
    ok, reason = _python_check_geometry(
        INVALID_LINESTRING_TOO_FEW["geometry"],
        _configured_allowed_geometry_types({}),
        _configured_min_coordinates({}),
    )
    assert ok is False
    assert "at least" in reason


def test_python_check_non_numeric() -> None:
    ok, reason = _python_check_geometry(
        INVALID_NON_NUMERIC["geometry"],
        _configured_allowed_geometry_types({}),
        _configured_min_coordinates({}),
    )
    assert ok is False


def test_python_check_null_geometry() -> None:
    ok, reason = _python_check_geometry(
        None,
        _configured_allowed_geometry_types({}),
        _configured_min_coordinates({}),
    )
    assert ok is False
    assert "null" in reason


def test_extract_features_from_vectorout() -> None:
    vector = VectorOut(features=[VALID_POINT], metadata={"source": "test"})
    features, info = _extract_features(vector)

    assert len(features) == 1
    assert info["input_type"] == "VectorOut"


def test_validate_geometries_flags_invalid_by_default(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "geometry_validator.yaml").write_text(
        """
default_engine: python
default_repair: false
default_drop_invalid: false
flags:
  add_validity_flag: true
  add_reason_field: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    features = [VALID_POINT, INVALID_POLYGON_OPEN_RING, NULL_GEOMETRY]

    result = validate_geometries(features=features)

    assert len(result.features) == 3

    md = result.metadata
    assert md["source"] == "geometry_validator"
    assert md["operation"] == "validate"
    assert md["input_feature_count"] == 3
    assert md["output_feature_count"] == 3
    assert md["valid_count"] == 1
    assert md["invalid_count"] == 2
    assert md["dropped_count"] == 0
    assert md["engine_requested"] == "python"

    valid_flags = [f["properties"]["_valid"] for f in result.features]
    assert valid_flags == [True, False, False]

    assert "_validity_reason" in result.features[1]["properties"]


def test_validate_geometries_drop_invalid(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "geometry_validator.yaml").write_text(
        """
default_engine: python
default_drop_invalid: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    features = [VALID_POINT, INVALID_POLYGON_OPEN_RING, NULL_GEOMETRY]

    result = validate_geometries(features=features)

    assert len(result.features) == 1
    assert result.metadata["output_feature_count"] == 1
    assert result.metadata["dropped_count"] == 2
    assert result.metadata["valid_count"] == 1
    assert result.metadata["invalid_count"] == 2


def test_validate_geometries_engine_param_override(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)
    (config_dir / "geometry_validator.yaml").write_text(
        "default_engine: auto\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = validate_geometries(
        features=[VALID_POINT],
        engine="python",
    )

    assert result.metadata["engine_requested"] == "python"
    assert "python" in result.metadata["engines_used"]


def test_validate_geometries_metadata_merge() -> None:
    result = validate_geometries(
        features=[VALID_POINT],
        engine="python",
        metadata={"analysis_id": "geom-1"},
    )

    assert result.metadata["analysis_id"] == "geom-1"


def test_validate_geometries_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        validate_geometries(
            features=[VALID_POINT],
            engine="python",
            metadata="bad",
        )


def test_validate_geometries_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        validate_geometries(
            features={"type": "Point", "coordinates": [1, 2]},
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = validate_geometries(
        features=[VALID_POINT, VALID_POLYGON],
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_geometry_validator")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_geometry_validator"
    assert artifact.payload["source"] == "geometry_validator"
    assert artifact.payload["operation"] == "validate"
    assert len(artifact.payload["features"]) == 2


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "validate_geometries" in names
    assert "repair_geometries" in names
    assert len(regs) >= 2


def test_validate_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "validate_geometries")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "validate_geometries"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "engine" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "validate"


def test_repair_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "repair_geometries")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "repair_geometries"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert descriptor.metadata["operation"] == "repair"
    assert descriptor.metadata["requires_shapely"] is True


# Shapely-dependent tests (skipped if shapely is not installed).

shapely = pytest.importorskip("shapely", reason="shapely not installed")


def test_validate_geometries_shapely_detects_self_intersection() -> None:
    bowtie = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [0, 0],
                [10, 10],
                [10, 0],
                [0, 10],
                [0, 0],
            ]],
        },
        "properties": {"id": 99},
    }

    result = validate_geometries(
        features=[bowtie],
        engine="shapely",
    )

    feature = result.features[0]
    assert feature["properties"]["_valid"] is False
    assert "shapely" in result.metadata["engines_used"]
    assert result.metadata["invalid_count"] == 1


def test_repair_geometries_fixes_self_intersection() -> None:
    bowtie = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [0, 0],
                [10, 10],
                [10, 0],
                [0, 10],
                [0, 0],
            ]],
        },
        "properties": {"id": 100},
    }

    result = repair_geometries(
        features=[bowtie],
        engine="shapely",
    )

    assert result.metadata["operation"] == "repair"
    assert result.metadata["repaired_count"] >= 1

    feature = result.features[0]
    assert feature["properties"].get("_repaired") is True
