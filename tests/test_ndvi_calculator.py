"""
Tests for ndvi_calculator plugin.

Run:
    pytest tests/test_ndvi_calculator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.ndvi_calculator import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _band_value,
    _calculate_ndvi_value,
    _clip_value,
    _is_nodata,
    _is_number,
    _validate_band_index,
    _validate_engine,
    _validate_precision,
    calculate_ndvi,
)


RASTER_RED_NIR = {
    "data": [
        [
            [10, 20],
            [30, 40],
        ],
        [
            [30, 20],
            [10, 40],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_RED_NIR_WITH_NODATA_AND_ZERO = {
    "data": [
        [
            [10, -9999],
            [-10, 40],
        ],
        [
            [30, 20],
            [10, -40],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_3BAND = {
    "data": [
        [
            [1, 2],
            [3, 4],
        ],
        [
            [10, 20],
            [30, 40],
        ],
        [
            [30, 20],
            [10, 40],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_INVALID_VALUES = {
    "data": [
        [
            [10, "bad"],
        ],
        [
            [30, 20],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 1],
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
    assert PLUGIN.manifest.id == "ndvi_calculator"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "NDVI Calculator"


def test_validate_engine() -> None:
    assert _validate_engine("python") == "python"
    assert _validate_engine("auto") == "auto"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_precision() -> None:
    assert _validate_precision(None) is None
    assert _validate_precision(3) == 3

    with pytest.raises(ValueError):
        _validate_precision(-1)


def test_validate_band_index() -> None:
    assert _validate_band_index(1, 2, name="red_band") == 1
    assert _validate_band_index("2", 2, name="nir_band") == 2

    with pytest.raises(ValueError):
        _validate_band_index(0, 2, name="red_band")

    with pytest.raises(ValueError):
        _validate_band_index(3, 2, name="nir_band")


def test_is_number() -> None:
    assert _is_number(1) is True
    assert _is_number(1.5) is True
    assert _is_number(True) is False
    assert _is_number("1") is False


def test_is_nodata() -> None:
    assert _is_nodata(None, -9999) is True
    assert _is_nodata(-9999, -9999) is True
    assert _is_nodata(10, -9999) is False


def test_clip_value() -> None:
    assert _clip_value(2.0, -1.0, 1.0) == 1.0
    assert _clip_value(-2.0, -1.0, 1.0) == -1.0
    assert _clip_value(0.5, -1.0, 1.0) == 0.5


def test_band_value_3d() -> None:
    assert _band_value(RASTER_RED_NIR["data"], band_index=1, row=0, col=1) == 20
    assert _band_value(RASTER_RED_NIR["data"], band_index=2, row=1, col=0) == 10


def test_calculate_ndvi_value_success() -> None:
    value, status = _calculate_ndvi_value(
        red=10,
        nir=30,
        nodata=-9999,
        division_by_zero_value=None,
        clip_output=True,
        output_min=-1.0,
        output_max=1.0,
        precision=3,
    )

    assert value == 0.5
    assert status == "success"


def test_calculate_ndvi_value_input_nodata() -> None:
    value, status = _calculate_ndvi_value(
        red=-9999,
        nir=30,
        nodata=-9999,
        division_by_zero_value=None,
        clip_output=True,
        output_min=-1.0,
        output_max=1.0,
        precision=3,
    )

    assert value == -9999
    assert status == "input_nodata"


def test_calculate_ndvi_value_division_by_zero_to_nodata() -> None:
    value, status = _calculate_ndvi_value(
        red=-10,
        nir=10,
        nodata=-9999,
        division_by_zero_value=None,
        clip_output=True,
        output_min=-1.0,
        output_max=1.0,
        precision=3,
    )

    assert value == -9999
    assert status == "division_by_zero"


def test_calculate_ndvi_value_division_by_zero_default_zero() -> None:
    value, status = _calculate_ndvi_value(
        red=-10,
        nir=10,
        nodata=-9999,
        division_by_zero_value=0,
        clip_output=True,
        output_min=-1.0,
        output_max=1.0,
        precision=3,
    )

    assert value == 0.0
    assert status == "division_by_zero"


def test_calculate_ndvi_basic() -> None:
    result = calculate_ndvi(
        raster=RASTER_RED_NIR,
        red_band=1,
        nir_band=2,
        engine="python",
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.5, 0.0],
        [-0.5, 0.0],
    ]

    md = result.metadata
    assert md["source"] == "ndvi_calculator"
    assert md["operation"] == "ndvi"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["red_band"] == 1
    assert md["nir_band"] == 2
    assert md["input_band_count"] == 2
    assert md["output_band_count"] == 1
    assert md["width"] == 2
    assert md["height"] == 2
    assert md["success_pixel_count"] == 4
    assert md["valid_pixel_count"] == 4
    assert md["nodata_pixel_count"] == 0
    assert md["output_min_value"] == -0.5
    assert md["output_max_value"] == 0.5
    assert md["output_mean_value"] == 0.0


def test_calculate_ndvi_with_nodata_and_zero_division_to_nodata() -> None:
    result = calculate_ndvi(
        raster=RASTER_RED_NIR_WITH_NODATA_AND_ZERO,
        red_band=1,
        nir_band=2,
        division_by_zero_value=None,
        engine="python",
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.5, -9999],
        [-9999, -9999],
    ]

    assert result.metadata["success_pixel_count"] == 1
    assert result.metadata["input_nodata_pixel_count"] == 1
    assert result.metadata["division_by_zero_pixel_count"] == 2
    assert result.metadata["nodata_pixel_count"] == 3


def test_calculate_ndvi_with_zero_division_default_zero() -> None:
    result = calculate_ndvi(
        raster=RASTER_RED_NIR_WITH_NODATA_AND_ZERO,
        red_band=1,
        nir_band=2,
        division_by_zero_value=0,
        engine="python",
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.5, -9999],
        [0.0, 0.0],
    ]

    assert result.metadata["success_pixel_count"] == 1
    assert result.metadata["input_nodata_pixel_count"] == 1
    assert result.metadata["division_by_zero_pixel_count"] == 2
    assert result.metadata["nodata_pixel_count"] == 1


def test_calculate_ndvi_uses_custom_bands() -> None:
    result = calculate_ndvi(
        raster=RASTER_3BAND,
        red_band=2,
        nir_band=3,
        engine="python",
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.5, 0.0],
        [-0.5, 0.0],
    ]

    assert result.metadata["red_band"] == 2
    assert result.metadata["nir_band"] == 3
    assert result.metadata["input_band_count"] == 3


def test_calculate_ndvi_invalid_values() -> None:
    result = calculate_ndvi(
        raster=RASTER_INVALID_VALUES,
        red_band=1,
        nir_band=2,
        engine="python",
        precision=3,
    )

    data = _get_data(result)

    assert data == [[0.5, -9999]]
    assert result.metadata["success_pixel_count"] == 1
    assert result.metadata["invalid_input_pixel_count"] == 1
    assert result.metadata["nodata_pixel_count"] == 1


def test_calculate_ndvi_no_clip_output() -> None:
    raster = {
        "data": [
            [
                [-5],
            ],
            [
                [10],
            ],
        ],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 1],
            "crs": "EPSG:3857",
            "nodata": -9999,
        },
    }

    result = calculate_ndvi(
        raster=raster,
        red_band=1,
        nir_band=2,
        clip_output=False,
        precision=3,
    )

    data = _get_data(result)

    assert data == [[3.0]]
    assert result.metadata["clip_output"] is False


def test_calculate_ndvi_rejects_invalid_output_range() -> None:
    with pytest.raises(ValueError, match="output_min"):
        calculate_ndvi(
            raster=RASTER_RED_NIR,
            red_band=1,
            nir_band=2,
            output_min=1,
            output_max=-1,
        )


def test_calculate_ndvi_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "ndvi_calculator.yaml").write_text(
        """
default_engine: python
default_red_band: 2
default_nir_band: 3
default_nodata: -9999
division_by_zero_value: 0
clip_output: true
output_min: -1.0
output_max: 1.0
coordinate_precision: 3
preserve_metadata: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = calculate_ndvi(
        raster=RASTER_3BAND,
    )

    data = _get_data(result)

    assert data == [
        [0.5, 0.0],
        [-0.5, 0.0],
    ]

    assert result.metadata["red_band"] == 2
    assert result.metadata["nir_band"] == 3
    assert result.metadata["coordinate_precision"] == 3
    assert result.metadata["source_crs"] == "EPSG:3857"
    assert result.metadata["warning"] is None


def test_calculate_ndvi_config_geographic_warning(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "ndvi_calculator.yaml").write_text(
        """
default_engine: python
default_red_band: 1
default_nir_band: 2
default_nodata: -9999
division_by_zero_value: 0
clip_output: true
output_min: -1.0
output_max: 1.0
coordinate_precision: 3
preserve_metadata: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": RASTER_RED_NIR["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 2],
        },
    }

    result = calculate_ndvi(
        raster=raster,
    )

    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_calculate_ndvi_metadata_merge() -> None:
    result = calculate_ndvi(
        raster=RASTER_RED_NIR,
        red_band=1,
        nir_band=2,
        metadata={"analysis_id": "ndvi-1"},
    )

    assert result.metadata["analysis_id"] == "ndvi-1"


def test_calculate_ndvi_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_ndvi(
            raster=RASTER_RED_NIR,
            red_band=1,
            nir_band=2,
            metadata="bad",
        )


def test_calculate_ndvi_rejects_invalid_band() -> None:
    with pytest.raises(ValueError, match="nir_band"):
        calculate_ndvi(
            raster=RASTER_RED_NIR,
            red_band=1,
            nir_band=3,
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_ndvi" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_ndvi")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_ndvi"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "raster" in descriptor.required_inputs
    assert "red_band" in descriptor.optional_inputs
    assert "nir_band" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "raster"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "ndvi"
    assert descriptor.metadata["spectral_index"] == "NDVI"
    assert descriptor.metadata["formula"] == "(NIR - RED) / (NIR + RED)"
