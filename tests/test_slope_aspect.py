"""
Tests for slope_aspect plugin.

Run:
    pytest tests/test_slope_aspect.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.slope_aspect import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _aspect_from_gradient,
    _band_value,
    _calculate_slope_aspect_cell,
    _extract_resolution_from_transform,
    _horn_derivatives,
    _is_nodata,
    _is_number,
    _slope_from_gradient,
    _validate_band_index,
    _validate_engine,
    _validate_output,
    _validate_precision,
    _validate_resolution,
    _validate_slope_unit,
    _window_values,
    calculate_slope_aspect,
)


DEM_EAST = {
    "data": [
        [1, 2, 3],
        [1, 2, 3],
        [1, 2, 3],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


DEM_SOUTH = {
    "data": [
        [1, 1, 1],
        [2, 2, 2],
        [3, 3, 3],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


DEM_FLAT = {
    "data": [
        [5, 5, 5],
        [5, 5, 5],
        [5, 5, 5],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


DEM_NODATA = {
    "data": [
        [1, 2, 3],
        [1, -9999, 3],
        [1, 2, 3],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


DEM_5X5_EAST = {
    "data": [
        [1, 2, 3, 4, 5],
        [1, 2, 3, 4, 5],
        [1, 2, 3, 4, 5],
        [1, 2, 3, 4, 5],
        [1, 2, 3, 4, 5],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 5],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


DEM_2BAND = {
    "data": [
        [
            [10, 10, 10],
            [10, 10, 10],
            [10, 10, 10],
        ],
        [
            [1, 2, 3],
            [1, 2, 3],
            [1, 2, 3],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


def _get_data(result):
    if hasattr(result, "data"):
        return result.data
    if hasattr(result, "array"):
        return result.array
    if hasattr(result, "payload"):
        return result.payload
    raise AssertionError("RasterOut has no data/array/payload attribute")


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "slope_aspect"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Slope Aspect"


def test_validate_engine() -> None:
    assert _validate_engine("python") == "python"
    assert _validate_engine("auto") == "auto"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_output() -> None:
    assert _validate_output("slope") == "slope"
    assert _validate_output("aspect") == "aspect"
    assert _validate_output("both") == "both"

    with pytest.raises(ValueError):
        _validate_output("bad")


def test_validate_slope_unit() -> None:
    assert _validate_slope_unit("degree") == "degree"
    assert _validate_slope_unit("radian") == "radian"
    assert _validate_slope_unit("percent") == "percent"

    with pytest.raises(ValueError):
        _validate_slope_unit("bad")


def test_validate_precision() -> None:
    assert _validate_precision(None) is None
    assert _validate_precision(3) == 3

    with pytest.raises(ValueError):
        _validate_precision(-1)


def test_validate_resolution() -> None:
    assert _validate_resolution(1, name="x_resolution") == 1.0

    with pytest.raises(ValueError):
        _validate_resolution(0, name="x_resolution")


def test_is_number() -> None:
    assert _is_number(1) is True
    assert _is_number(1.5) is True
    assert _is_number(True) is False
    assert _is_number("1") is False


def test_is_nodata() -> None:
    assert _is_nodata(None, -9999) is True
    assert _is_nodata(-9999, -9999) is True
    assert _is_nodata(10, -9999) is False


def test_validate_band_index() -> None:
    assert _validate_band_index(1, 2) == 1
    assert _validate_band_index("2", 2) == 2

    with pytest.raises(ValueError):
        _validate_band_index(0, 2)

    with pytest.raises(ValueError):
        _validate_band_index(3, 2)


def test_band_value_2d() -> None:
    assert _band_value(DEM_EAST["data"], band_index=1, row=1, col=2) == 3


def test_band_value_3d() -> None:
    assert _band_value(DEM_2BAND["data"], band_index=1, row=1, col=1) == 10
    assert _band_value(DEM_2BAND["data"], band_index=2, row=1, col=1) == 2


def test_extract_resolution_from_transform() -> None:
    x_res, y_res, source = _extract_resolution_from_transform(
        {"transform": [30, 0, 0, 0, -30, 0]},
        default_x_resolution=1,
        default_y_resolution=1,
    )

    assert x_res == 30
    assert y_res == 30
    assert source == "transform"


def test_extract_resolution_from_default() -> None:
    x_res, y_res, source = _extract_resolution_from_transform(
        {},
        default_x_resolution=2,
        default_y_resolution=3,
    )

    assert x_res == 2
    assert y_res == 3
    assert source == "default"


def test_window_values() -> None:
    window = _window_values(
        DEM_EAST["data"],
        band_index=1,
        row=1,
        col=1,
    )

    assert window == [
        [1, 2, 3],
        [1, 2, 3],
        [1, 2, 3],
    ]


def test_horn_derivatives_east() -> None:
    window = [
        [1, 2, 3],
        [1, 2, 3],
        [1, 2, 3],
    ]

    dzdx, dzdy = _horn_derivatives(
        window,
        x_resolution=1,
        y_resolution=1,
    )

    assert dzdx == pytest.approx(1.0)
    assert dzdy == pytest.approx(0.0)


def test_horn_derivatives_south() -> None:
    window = [
        [1, 1, 1],
        [2, 2, 2],
        [3, 3, 3],
    ]

    dzdx, dzdy = _horn_derivatives(
        window,
        x_resolution=1,
        y_resolution=1,
    )

    assert dzdx == pytest.approx(0.0)
    assert dzdy == pytest.approx(1.0)


def test_slope_from_gradient() -> None:
    assert _slope_from_gradient(1, 0, slope_unit="degree") == pytest.approx(45.0)
    assert _slope_from_gradient(1, 0, slope_unit="percent") == pytest.approx(100.0)
    assert _slope_from_gradient(1, 0, slope_unit="radian") == pytest.approx(0.785398, rel=1e-5)


def test_aspect_from_gradient() -> None:
    assert _aspect_from_gradient(1, 0, flat_aspect_value=-1) == pytest.approx(270.0)
    assert _aspect_from_gradient(0, 1, flat_aspect_value=-1) == pytest.approx(0.0)
    assert _aspect_from_gradient(0, 0, flat_aspect_value=-1) == -1


def test_calculate_slope_aspect_cell_success_east() -> None:
    window = [
        [1, 2, 3],
        [1, 2, 3],
        [1, 2, 3],
    ]

    slope, aspect, status = _calculate_slope_aspect_cell(
        window,
        nodata=-9999,
        output_nodata=-9999,
        x_resolution=1,
        y_resolution=1,
        slope_unit="degree",
        flat_aspect_value=-1,
        precision=3,
    )

    assert slope == 45.0
    assert aspect == 270.0
    assert status == "success"


def test_calculate_slope_aspect_cell_nodata() -> None:
    window = [
        [1, 2, 3],
        [1, -9999, 3],
        [1, 2, 3],
    ]

    slope, aspect, status = _calculate_slope_aspect_cell(
        window,
        nodata=-9999,
        output_nodata=-9999,
        x_resolution=1,
        y_resolution=1,
        slope_unit="degree",
        flat_aspect_value=-1,
        precision=3,
    )

    assert slope == -9999
    assert aspect == -9999
    assert status == "input_nodata"


def test_calculate_slope_aspect_both_east() -> None:
    result = calculate_slope_aspect(
        raster=DEM_EAST,
        output="both",
        slope_unit="degree",
        output_nodata=-9999,
        flat_aspect_value=-1,
        engine="python",
        precision=3,
    )

    slope_data = _get_data(result["slope"])
    aspect_data = _get_data(result["aspect"])

    assert slope_data == [
        [-9999, -9999, -9999],
        [-9999, 45.0, -9999],
        [-9999, -9999, -9999],
    ]

    assert aspect_data == [
        [-9999, -9999, -9999],
        [-9999, 270.0, -9999],
        [-9999, -9999, -9999],
    ]

    md = result["metadata"]
    assert md["source"] == "slope_aspect"
    assert md["operation"] == "slope_aspect"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["method"] == "Horn 3x3"
    assert md["output"] == "both"
    assert md["slope_unit"] == "degree"
    assert md["success_pixel_count"] == 1
    assert md["edge_pixel_count"] == 8
    assert md["nodata_pixel_count"] == 8
    assert md["has_slope"] is True
    assert md["has_aspect"] is True


def test_calculate_slope_aspect_both_south() -> None:
    result = calculate_slope_aspect(
        raster=DEM_SOUTH,
        output="both",
        slope_unit="degree",
        output_nodata=-9999,
        flat_aspect_value=-1,
        precision=3,
    )

    slope_data = _get_data(result["slope"])
    aspect_data = _get_data(result["aspect"])

    assert slope_data[1][1] == 45.0
    assert aspect_data[1][1] == 0.0


def test_calculate_slope_aspect_flat() -> None:
    result = calculate_slope_aspect(
        raster=DEM_FLAT,
        output="both",
        slope_unit="degree",
        output_nodata=-9999,
        flat_aspect_value=-1,
        precision=3,
    )

    slope_data = _get_data(result["slope"])
    aspect_data = _get_data(result["aspect"])

    assert slope_data[1][1] == 0.0
    assert aspect_data[1][1] == -1


def test_calculate_slope_percent() -> None:
    result = calculate_slope_aspect(
        raster=DEM_EAST,
        output="slope",
        slope_unit="percent",
        output_nodata=-9999,
        precision=3,
    )

    assert result["aspect"] is None

    slope_data = _get_data(result["slope"])

    assert slope_data[1][1] == 100.0
    assert result["metadata"]["has_slope"] is True
    assert result["metadata"]["has_aspect"] is False


def test_calculate_aspect_only() -> None:
    result = calculate_slope_aspect(
        raster=DEM_EAST,
        output="aspect",
        output_nodata=-9999,
        flat_aspect_value=-1,
        precision=3,
    )

    assert result["slope"] is None

    aspect_data = _get_data(result["aspect"])

    assert aspect_data[1][1] == 270.0
    assert result["metadata"]["has_slope"] is False
    assert result["metadata"]["has_aspect"] is True


def test_calculate_slope_aspect_with_nodata_window() -> None:
    result = calculate_slope_aspect(
        raster=DEM_NODATA,
        output="both",
        output_nodata=-9999,
        precision=3,
    )

    slope_data = _get_data(result["slope"])
    aspect_data = _get_data(result["aspect"])

    assert slope_data[1][1] == -9999
    assert aspect_data[1][1] == -9999
    assert result["metadata"]["success_pixel_count"] == 0
    assert result["metadata"]["input_nodata_pixel_count"] == 1
    assert result["metadata"]["nodata_pixel_count"] == 9


def test_calculate_slope_aspect_5x5_counts() -> None:
    result = calculate_slope_aspect(
        raster=DEM_5X5_EAST,
        output="both",
        output_nodata=-9999,
        precision=3,
    )

    slope_data = _get_data(result["slope"])

    assert slope_data[2][2] == 45.0
    assert result["metadata"]["success_pixel_count"] == 9
    assert result["metadata"]["edge_pixel_count"] == 16
    assert result["metadata"]["nodata_pixel_count"] == 16


def test_calculate_slope_aspect_selected_band() -> None:
    result = calculate_slope_aspect(
        raster=DEM_2BAND,
        band_index=2,
        output="both",
        output_nodata=-9999,
        flat_aspect_value=-1,
        precision=3,
    )

    slope_data = _get_data(result["slope"])
    aspect_data = _get_data(result["aspect"])

    assert slope_data[1][1] == 45.0
    assert aspect_data[1][1] == 270.0
    assert result["metadata"]["input_band_count"] == 2
    assert result["metadata"]["selected_band_index"] == 2


def test_calculate_slope_aspect_runtime_resolution() -> None:
    result = calculate_slope_aspect(
        raster=DEM_EAST,
        output="slope",
        slope_unit="degree",
        output_nodata=-9999,
        x_resolution=2,
        y_resolution=2,
        precision=3,
    )

    slope_data = _get_data(result["slope"])

    assert slope_data[1][1] == pytest.approx(26.565, rel=1e-3)
    assert result["metadata"]["x_resolution"] == 2.0
    assert result["metadata"]["y_resolution"] == 2.0
    assert result["metadata"]["resolution_source"] == "runtime"


def test_calculate_slope_aspect_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "slope_aspect.yaml").write_text(
        """
default_engine: python
default_band_index: 1
default_output: both
default_slope_unit: degree
default_nodata: -9999
default_output_nodata: -9999
default_x_resolution: 1.0
default_y_resolution: 1.0
flat_aspect_value: -1
coordinate_precision: 3
preserve_metadata: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": DEM_EAST["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 3],
        },
    }

    result = calculate_slope_aspect(
        raster=raster,
    )

    slope_data = _get_data(result["slope"])
    aspect_data = _get_data(result["aspect"])

    assert slope_data[1][1] == 45.0
    assert aspect_data[1][1] == 270.0
    assert result["metadata"]["coordinate_precision"] == 3
    assert result["metadata"]["source_crs"] == "EPSG:4326"
    assert result["metadata"]["warning"] is not None
    assert "geographic CRS" in result["metadata"]["warning"]


def test_calculate_slope_aspect_metadata_merge() -> None:
    result = calculate_slope_aspect(
        raster=DEM_EAST,
        output="both",
        metadata={"analysis_id": "slope-1"},
    )

    assert result["metadata"]["analysis_id"] == "slope-1"
    assert result["slope"].metadata["analysis_id"] == "slope-1"


def test_calculate_slope_aspect_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_slope_aspect(
            raster=DEM_EAST,
            metadata="bad",
        )


def test_calculate_slope_aspect_rejects_invalid_output() -> None:
    with pytest.raises(ValueError, match="output"):
        calculate_slope_aspect(
            raster=DEM_EAST,
            output="bad",
        )


def test_calculate_slope_aspect_rejects_invalid_slope_unit() -> None:
    with pytest.raises(ValueError, match="slope_unit"):
        calculate_slope_aspect(
            raster=DEM_EAST,
            slope_unit="bad",
        )


def test_calculate_slope_aspect_rejects_invalid_band() -> None:
    with pytest.raises(ValueError, match="band_index"):
        calculate_slope_aspect(
            raster=DEM_2BAND,
            band_index=3,
        )


def test_calculate_slope_aspect_rejects_invalid_resolution() -> None:
    with pytest.raises(ValueError, match="x_resolution"):
        calculate_slope_aspect(
            raster=DEM_EAST,
            x_resolution=0,
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_slope_aspect" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_slope_aspect")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_slope_aspect"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "json"
    assert "raster" in descriptor.required_inputs
    assert "band_index" in descriptor.optional_inputs
    assert "slope_unit" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "raster_collection"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "slope_aspect"
    assert descriptor.metadata["terrain_analysis"] is True
    assert descriptor.metadata["dem_required"] is True
