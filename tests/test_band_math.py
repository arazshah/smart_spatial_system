"""
Tests for band_math plugin.

Run:
    pytest tests/test_band_math.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.band_math import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _band_value,
    _cast_output_value,
    _clip,
    _compile_expression,
    _evaluate_expression,
    _is_nodata,
    _is_number,
    _resolve_expression,
    _safe_div,
    _validate_engine,
    _validate_output_dtype,
    _validate_precision,
    _where,
    calculate_band_math,
)


RASTER_2D = {
    "data": [
        [1, 2],
        [3, 4],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_2BAND = {
    "data": [
        [
            [10, 20],
            [30, 40],
        ],
        [
            [2, 4],
            [5, 8],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_2BAND_WITH_NODATA = {
    "data": [
        [
            [10, -9999],
            [30, 40],
        ],
        [
            [2, 4],
            [0, 8],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
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
    assert PLUGIN.manifest.id == "band_math"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Band Math"


def test_validate_engine() -> None:
    assert _validate_engine("python") == "python"
    assert _validate_engine("auto") == "auto"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_output_dtype() -> None:
    assert _validate_output_dtype("float") == "float"
    assert _validate_output_dtype("int") == "int"
    assert _validate_output_dtype("bool") == "bool"
    assert _validate_output_dtype("preserve") == "preserve"

    with pytest.raises(ValueError):
        _validate_output_dtype("bad")


def test_validate_precision() -> None:
    assert _validate_precision(None) is None
    assert _validate_precision(3) == 3

    with pytest.raises(ValueError):
        _validate_precision(-1)


def test_is_number() -> None:
    assert _is_number(1) is True
    assert _is_number(1.5) is True
    assert _is_number(True) is False
    assert _is_number("1") is False


def test_is_nodata() -> None:
    assert _is_nodata(None, -9999) is True
    assert _is_nodata(-9999, -9999) is True
    assert _is_nodata(10, -9999) is False


def test_band_value_2d() -> None:
    assert _band_value(RASTER_2D["data"], band_index=1, row=1, col=1) == 4


def test_band_value_3d() -> None:
    assert _band_value(RASTER_2BAND["data"], band_index=1, row=0, col=1) == 20
    assert _band_value(RASTER_2BAND["data"], band_index=2, row=1, col=0) == 5


def test_safe_div() -> None:
    assert _safe_div(10, 2, 0) == 5
    assert _safe_div(10, 0, 99) == 99


def test_clip() -> None:
    assert _clip(10, 0, 5) == 5
    assert _clip(-1, 0, 5) == 0
    assert _clip(3, 0, 5) == 3


def test_where() -> None:
    assert _where(True, 1, 0) == 1
    assert _where(False, 1, 0) == 0


def test_resolve_expression_direct() -> None:
    config = {"presets": {"ndvi": "safe_div(b2 - b1, b2 + b1, 0)"}}
    assert _resolve_expression("b1 + b2", None, config) == "b1 + b2"


def test_resolve_expression_preset() -> None:
    config = {"presets": {"ndvi": "safe_div(b2 - b1, b2 + b1, 0)"}}
    assert _resolve_expression(None, "ndvi", config) == "safe_div(b2 - b1, b2 + b1, 0)"


def test_compile_and_evaluate_expression() -> None:
    compiled = _compile_expression("safe_div(b1 - b2, b1 + b2, 0)")
    result = _evaluate_expression(compiled, {"b1": 10, "b2": 2})

    assert result == pytest.approx(8 / 12)


def test_cast_output_value() -> None:
    assert _cast_output_value(1.23456, "float", 2) == 1.23
    assert _cast_output_value(1.7, "int", 2) == 2
    assert _cast_output_value(0, "bool", None) is False
    assert _cast_output_value(5, "preserve", 2) == 5.0


def test_calculate_band_math_simple_expression_2d() -> None:
    result = calculate_band_math(
        raster=RASTER_2D,
        expression="b1 * 2",
        output_dtype="float",
        engine="python",
        precision=2,
    )

    data = _get_data(result)

    assert data == [
        [2.0, 4.0],
        [6.0, 8.0],
    ]

    md = result.metadata
    assert md["source"] == "band_math"
    assert md["operation"] == "band_math"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["expression"] == "b1 * 2"
    assert md["input_band_count"] == 1
    assert md["output_band_count"] == 1
    assert md["valid_pixel_count"] == 4
    assert md["nodata_pixel_count"] == 0
    assert md["error_pixel_count"] == 0


def test_calculate_band_math_two_band_ratio() -> None:
    result = calculate_band_math(
        raster=RASTER_2BAND,
        expression="safe_div(b1 - b2, b1 + b2, 0)",
        output_dtype="float",
        engine="python",
        precision=4,
    )

    data = _get_data(result)

    assert data[0][0] == pytest.approx((10 - 2) / (10 + 2), rel=1e-4)
    assert data[0][1] == pytest.approx((20 - 4) / (20 + 4), rel=1e-4)
    assert data[1][0] == pytest.approx((30 - 5) / (30 + 5), rel=1e-4)
    assert data[1][1] == pytest.approx((40 - 8) / (40 + 8), rel=1e-4)


def test_calculate_band_math_where_function() -> None:
    result = calculate_band_math(
        raster=RASTER_2D,
        expression="where(b1 > 2, b1, 0)",
        output_dtype="int",
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [0, 0],
        [3, 4],
    ]


def test_calculate_band_math_clip_function() -> None:
    result = calculate_band_math(
        raster=RASTER_2D,
        expression="clip(b1 * 2, 0, 5)",
        output_dtype="float",
        engine="python",
        precision=2,
    )

    data = _get_data(result)

    assert data == [
        [2.0, 4.0],
        [5.0, 5.0],
    ]


def test_calculate_band_math_with_nodata() -> None:
    result = calculate_band_math(
        raster=RASTER_2BAND_WITH_NODATA,
        expression="safe_div(b1, b2, 0)",
        output_dtype="float",
        engine="python",
        precision=2,
    )

    data = _get_data(result)

    assert data[0][0] == 5.0
    assert data[0][1] == -9999
    assert data[1][0] == 0.0
    assert data[1][1] == 5.0

    assert result.metadata["valid_pixel_count"] == 3
    assert result.metadata["nodata_pixel_count"] == 1
    assert result.metadata["error_pixel_count"] == 0


def test_calculate_band_math_using_preset(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "band_math.yaml").write_text(
        """
default_engine: python
default_output_dtype: float
default_nodata: -9999
coordinate_precision: 3
preserve_metadata: true
presets:
  ratio: "safe_div(b1, b2, 0)"
source_crs: EPSG:4326
warn_if_geographic_crs: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = calculate_band_math(
        raster=RASTER_2BAND,
        preset="ratio",
    )

    data = _get_data(result)

    assert data == [
        [5.0, 5.0],
        [6.0, 5.0],
    ]

    assert result.metadata["expression"] == "safe_div(b1, b2, 0)"
    assert result.metadata["source_crs"] == "EPSG:3857"
    assert result.metadata["warning"] is None


def test_calculate_band_math_output_bool() -> None:
    result = calculate_band_math(
        raster=RASTER_2D,
        expression="b1 > 2",
        output_dtype="bool",
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [False, False],
        [True, True],
    ]


def test_calculate_band_math_metadata_merge() -> None:
    result = calculate_band_math(
        raster=RASTER_2D,
        expression="b1 * 2",
        metadata={"analysis_id": "bandmath-1"},
    )

    assert result.metadata["analysis_id"] == "bandmath-1"


def test_calculate_band_math_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_band_math(
            raster=RASTER_2D,
            expression="b1 * 2",
            metadata="bad",
        )


def test_calculate_band_math_rejects_invalid_expression() -> None:
    with pytest.raises(ValueError):
        calculate_band_math(
            raster=RASTER_2D,
            expression="__import__('os').system('echo bad')",
        )


def test_calculate_band_math_rejects_missing_expression_and_preset() -> None:
    with pytest.raises(ValueError, match="Either expression or preset"):
        calculate_band_math(
            raster=RASTER_2D,
        )


def test_calculate_band_math_rejects_unknown_preset(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "band_math.yaml").write_text(
        """
default_engine: python
default_output_dtype: float
presets:
  ndvi: "safe_div(b2 - b1, b2 + b1, 0)"
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="Unknown preset"):
        calculate_band_math(
            raster=RASTER_2BAND,
            preset="bad",
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_band_math" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_band_math")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_band_math"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "raster" in descriptor.required_inputs
    assert "expression" in descriptor.optional_inputs
    assert "preset" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "raster"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "band_math"
    assert descriptor.metadata["spectral_index_supported"] is True
    assert descriptor.metadata["safe_expression_eval"] is True
