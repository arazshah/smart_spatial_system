"""
Tests for area_perimeter_calc plugin.

Run:
    pytest tests/test_area_perimeter_calc.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geochat_sdk.types.vector import VectorOut  # noqa: E402

from plugins.area_perimeter_calc import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _configured_precision,
    _distance,
    _extract_features,
    _geometry_bbox,
    _is_geographic_crs,
    _is_position,
    _python_metrics_geometry,
    _ring_area_abs,
    _ring_length,
    _validate_engine,
    calculate_area_perimeter,
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
        "coordinates": [[0.0, 0.0], [3.0, 4.0]],
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

POLYGON_WITH_HOLE_FEATURE = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [
                [0.0, 0.0],
                [10.0, 0.0],
                [10.0, 10.0],
                [0.0, 10.0],
                [0.0, 0.0],
            ],
            [
                [2.0, 2.0],
                [8.0, 2.0],
                [8.0, 8.0],
                [2.0, 8.0],
                [2.0, 2.0],
            ],
        ],
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
    assert PLUGIN.manifest.id == "area_perimeter_calc"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Area & Perimeter Calculator"


def test_validate_engine() -> None:
    assert _validate_engine("auto") == "auto"
    assert _validate_engine("python") == "python"
    assert _validate_engine("shapely") == "shapely"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_configured_precision_defaults() -> None:
    assert _configured_precision({}) == 6


def test_is_position() -> None:
    assert _is_position([1, 2]) is True
    assert _is_position([1, 2, 3]) is True
    assert _is_position([1]) is False


def test_distance() -> None:
    assert _distance((0, 0), (3, 4)) == 5.0


def test_ring_length() -> None:
    ring = [[0, 0], [3, 4]]
    assert _ring_length(ring) == 5.0


def test_ring_area_abs_square() -> None:
    ring = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    assert _ring_area_abs(ring) == 100.0


def test_python_metrics_point() -> None:
    metrics = _python_metrics_geometry(POINT_FEATURE["geometry"])
    assert metrics["area"] == 0.0
    assert metrics["perimeter"] == 0.0
    assert metrics["length"] == 0.0


def test_python_metrics_line() -> None:
    metrics = _python_metrics_geometry(LINE_FEATURE["geometry"])
    assert metrics["area"] == 0.0
    assert metrics["perimeter"] == 0.0
    assert metrics["length"] == 5.0


def test_python_metrics_polygon() -> None:
    metrics = _python_metrics_geometry(POLYGON_FEATURE["geometry"])
    assert metrics["area"] == 100.0
    assert metrics["perimeter"] == 40.0
    assert metrics["length"] == 40.0


def test_python_metrics_polygon_with_hole() -> None:
    metrics = _python_metrics_geometry(POLYGON_WITH_HOLE_FEATURE["geometry"])
    assert metrics["area"] == 64.0
    assert metrics["perimeter"] == 64.0
    assert metrics["length"] == 64.0


def test_python_metrics_null_geometry() -> None:
    metrics = _python_metrics_geometry(None)
    assert metrics["area"] is None
    assert metrics["perimeter"] is None
    assert metrics["length"] is None


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
    vector = VectorOut(features=[POINT_FEATURE], metadata={"source": "test"})
    features, info = _extract_features(vector)

    assert len(features) == 1
    assert info["input_type"] == "VectorOut"
    assert info["input_metadata"]["source"] == "test"


def test_geometry_bbox_polygon() -> None:
    bbox = _geometry_bbox(POLYGON_FEATURE["geometry"])
    assert bbox == [0.0, 0.0, 10.0, 10.0]


def test_is_geographic_crs() -> None:
    assert _is_geographic_crs("EPSG:4326") is True
    assert _is_geographic_crs("CRS:84") is True
    assert _is_geographic_crs("EPSG:3857") is False


def test_calculate_area_perimeter_python_success() -> None:
    result = calculate_area_perimeter(
        features=[POINT_FEATURE, LINE_FEATURE, POLYGON_FEATURE],
        engine="python",
        precision=4,
    )

    assert len(result.features) == 3

    point_props = result.features[0]["properties"]
    line_props = result.features[1]["properties"]
    poly_props = result.features[2]["properties"]

    assert point_props["_area"] == 0.0
    assert point_props["_perimeter"] == 0.0
    assert point_props["_length"] == 0.0

    assert line_props["_length"] == 5.0
    assert line_props["_area"] == 0.0

    assert poly_props["_area"] == 100.0
    assert poly_props["_perimeter"] == 40.0
    assert poly_props["_length"] == 40.0
    assert poly_props["_metric_status"] == "success"
    assert poly_props["_metric_engine"] == "python"
    assert poly_props["_source_geometry_type"] == "Polygon"

    md = result.metadata
    assert md["source"] == "area_perimeter_calc"
    assert md["operation"] == "metrics"
    assert md["engine_requested"] == "python"
    assert md["engines_used"] == ["python"]
    assert md["coordinate_precision"] == 4
    assert md["input_feature_count"] == 3
    assert md["output_feature_count"] == 3
    assert md["success_count"] == 3
    assert md["failed_count"] == 0
    assert md["geometry_types"]["Point"] == 1
    assert md["geometry_types"]["LineString"] == 1
    assert md["geometry_types"]["Polygon"] == 1


def test_calculate_area_perimeter_warns_for_geographic_crs() -> None:
    result = calculate_area_perimeter(
        features=[POLYGON_FEATURE],
        engine="python",
        source_crs="EPSG:4326",
    )

    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_calculate_area_perimeter_handles_null_geometry() -> None:
    result = calculate_area_perimeter(
        features=[POINT_FEATURE, NULL_GEOMETRY_FEATURE],
        engine="python",
    )

    assert len(result.features) == 2
    assert result.metadata["success_count"] == 2
    assert result.metadata["failed_count"] == 0

    null_props = result.features[1]["properties"]
    assert null_props["_area"] is None
    assert null_props["_perimeter"] is None
    assert null_props["_length"] is None
    assert null_props["_metric_status"] == "success"


def test_calculate_area_perimeter_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "area_perimeter_calc.yaml").write_text(
        """
default_engine: python
coordinate_precision: 3
preserve_properties: true
drop_failed: false
always_add_fields: true
fields:
  area_field: area_calc
  perimeter_field: perimeter_calc
  length_field: length_calc
  status_field: metric_status
  engine_field: metric_engine
  geometry_type_field: geometry_type_src
source_crs: EPSG:3857
warn_if_geographic_crs: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = calculate_area_perimeter(features=[POLYGON_FEATURE])

    props = result.features[0]["properties"]
    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["coordinate_precision"] == 3
    assert props["area_calc"] == 100.0
    assert props["perimeter_calc"] == 40.0
    assert props["length_calc"] == 40.0
    assert props["metric_status"] == "success"
    assert props["metric_engine"] == "python"
    assert props["geometry_type_src"] == "Polygon"


def test_calculate_area_perimeter_metadata_merge() -> None:
    result = calculate_area_perimeter(
        features=[POLYGON_FEATURE],
        engine="python",
        metadata={"analysis_id": "metric-1"},
    )

    assert result.metadata["analysis_id"] == "metric-1"


def test_calculate_area_perimeter_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_area_perimeter(
            features=[POLYGON_FEATURE],
            engine="python",
            metadata="bad",
        )


def test_calculate_area_perimeter_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        calculate_area_perimeter(
            features={"type": "Point", "coordinates": [1, 2]},
            engine="python",
        )


def test_vectorout_to_artifact() -> None:
    result = calculate_area_perimeter(
        features=[POINT_FEATURE, POLYGON_FEATURE],
        engine="python",
    )

    artifact = result.to_artifact(produced_by="test_area_perimeter_calc")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_area_perimeter_calc"
    assert artifact.payload["source"] == "area_perimeter_calc"
    assert artifact.payload["operation"] == "metrics"
    assert len(artifact.payload["features"]) == 2


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_area_perimeter" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_area_perimeter")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_area_perimeter"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "features" in descriptor.required_inputs
    assert "engine" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "metrics"
    assert descriptor.metadata["planar_only"] is True


def test_shapely_engine_if_installed() -> None:
    pytest.importorskip("shapely", reason="shapely not installed")

    result = calculate_area_perimeter(
        features=[POLYGON_FEATURE],
        engine="shapely",
        precision=4,
    )

    props = result.features[0]["properties"]
    assert result.metadata["engines_used"] == ["shapely"]
    assert props["_area"] == 100.0
    assert props["_perimeter"] == 40.0
