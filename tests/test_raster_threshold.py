"""
Tests for raster_threshold plugin.

Run:
    pytest tests/test_raster_threshold.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.raster_threshold import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _band_value,
    _compare_value,
    _is_nodata,
    _is_number,
    _threshold_value,
    _validate_band_index,
    _validate_engine,
    _validate_operator,
    _validate_precision,
    _validate_threshold_params,
    threshold_raster,
)


RASTER_NDVI = {
    "data": [
        [0.10, 0.25, 0.60],
        [0.00, 0.40, None],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_WITH_NODATA_AND_INVALID = {
    "data": [
        [1, 2, -9999],
        [4, "bad", None],
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
            [1, 2],
            [3, 4],
        ],
        [
            [10, 20],
            [30, 40],
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
    assert PLUGIN.manifest.id == "raster_threshold"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Raster Threshold"


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


def test_validate_operator() -> None:
    assert _validate_operator(">") == "gt"
    assert _validate_operator(">=") == "gte"
    assert _validate_operator("<") == "lt"
    assert _validate_operator("<=") == "lte"
    assert _validate_operator("==") == "eq"
    assert _validate_operator("!=") == "neq"
    assert _validate_operator("between") == "between"
    assert _validate_operator("outside") == "outside"

    with pytest.raises(ValueError):
        _validate_operator("bad")


def test_validate_threshold_params_single_threshold() -> None:
    threshold, min_value, max_value = _validate_threshold_params(
        operator="gt",
        threshold=0.3,
        min_value=None,
        max_value=None,
    )

    assert threshold == 0.3
    assert min_value is None
    assert max_value is None


def test_validate_threshold_params_range() -> None:
    threshold, min_value, max_value = _validate_threshold_params(
        operator="between",
        threshold=None,
        min_value=0.2,
        max_value=0.5,
    )

    assert threshold is None
    assert min_value == 0.2
    assert max_value == 0.5


def test_validate_threshold_params_rejects_missing_threshold() -> None:
    with pytest.raises(ValueError, match="threshold"):
        _validate_threshold_params(
            operator="gt",
            threshold=None,
            min_value=None,
            max_value=None,
        )


def test_validate_threshold_params_rejects_missing_range() -> None:
    with pytest.raises(ValueError, match="min_value"):
        _validate_threshold_params(
            operator="between",
            threshold=None,
            min_value=None,
            max_value=1,
        )


def test_band_value_2d() -> None:
    assert _band_value(RASTER_NDVI["data"], band_index=1, row=0, col=2) == 0.60


def test_band_value_3d() -> None:
    assert _band_value(RASTER_2BAND["data"], band_index=1, row=1, col=1) == 4
    assert _band_value(RASTER_2BAND["data"], band_index=2, row=1, col=1) == 40


def test_compare_value_basic_operators() -> None:
    assert _compare_value(
        5,
        operator="gt",
        threshold=3,
        min_value=None,
        max_value=None,
        inclusive_min=True,
        inclusive_max=True,
    ) is True

    assert _compare_value(
        5,
        operator="lt",
        threshold=3,
        min_value=None,
        max_value=None,
        inclusive_min=True,
        inclusive_max=True,
    ) is False

    assert _compare_value(
        5,
        operator="eq",
        threshold=5,
        min_value=None,
        max_value=None,
        inclusive_min=True,
        inclusive_max=True,
    ) is True


def test_compare_value_between() -> None:
    assert _compare_value(
        0.3,
        operator="between",
        threshold=None,
        min_value=0.2,
        max_value=0.5,
        inclusive_min=True,
        inclusive_max=True,
    ) is True

    assert _compare_value(
        0.2,
        operator="between",
        threshold=None,
        min_value=0.2,
        max_value=0.5,
        inclusive_min=False,
        inclusive_max=True,
    ) is False


def test_compare_value_outside() -> None:
    assert _compare_value(
        0.1,
        operator="outside",
        threshold=None,
        min_value=0.2,
        max_value=0.5,
        inclusive_min=True,
        inclusive_max=True,
    ) is True

    assert _compare_value(
        0.3,
        operator="outside",
        threshold=None,
        min_value=0.2,
        max_value=0.5,
        inclusive_min=True,
        inclusive_max=True,
    ) is False


def test_threshold_value_true_false() -> None:
    value, status = _threshold_value(
        0.6,
        operator="gt",
        threshold=0.3,
        min_value=None,
        max_value=None,
        inclusive_min=True,
        inclusive_max=True,
        nodata=-9999,
        output_nodata=-9999,
        true_value=1,
        false_value=0,
        precision=3,
    )

    assert value == 1
    assert status == "true"

    value, status = _threshold_value(
        0.1,
        operator="gt",
        threshold=0.3,
        min_value=None,
        max_value=None,
        inclusive_min=True,
        inclusive_max=True,
        nodata=-9999,
        output_nodata=-9999,
        true_value=1,
        false_value=0,
        precision=3,
    )

    assert value == 0
    assert status == "false"


def test_threshold_value_nodata() -> None:
    value, status = _threshold_value(
        None,
        operator="gt",
        threshold=0.3,
        min_value=None,
        max_value=None,
        inclusive_min=True,
        inclusive_max=True,
        nodata=-9999,
        output_nodata=-9999,
        true_value=1,
        false_value=0,
        precision=3,
    )

    assert value == -9999
    assert status == "input_nodata"


def test_threshold_raster_gt_ndvi() -> None:
    result = threshold_raster(
        raster=RASTER_NDVI,
        operator="gt",
        threshold=0.3,
        true_value=1,
        false_value=0,
        output_nodata=-9999,
        engine="python",
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0, 0, 1],
        [0, 1, -9999],
    ]

    md = result.metadata
    assert md["source"] == "raster_threshold"
    assert md["operation"] == "raster_threshold"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["operator"] == "gt"
    assert md["threshold"] == 0.3
    assert md["true_value"] == 1
    assert md["false_value"] == 0
    assert md["input_band_count"] == 1
    assert md["selected_band_index"] == 1
    assert md["output_band_count"] == 1
    assert md["width"] == 3
    assert md["height"] == 2
    assert md["true_pixel_count"] == 2
    assert md["false_pixel_count"] == 3
    assert md["input_nodata_pixel_count"] == 1
    assert md["nodata_pixel_count"] == 1


def test_threshold_raster_gte() -> None:
    result = threshold_raster(
        raster=RASTER_NDVI,
        operator=">=",
        threshold=0.4,
        output_nodata=-9999,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0, 0, 1],
        [0, 1, -9999],
    ]


def test_threshold_raster_lt() -> None:
    result = threshold_raster(
        raster=RASTER_NDVI,
        operator="<",
        threshold=0.2,
        output_nodata=-9999,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [1, 0, 0],
        [1, 0, -9999],
    ]


def test_threshold_raster_between() -> None:
    result = threshold_raster(
        raster=RASTER_NDVI,
        operator="between",
        min_value=0.2,
        max_value=0.5,
        true_value=10,
        false_value=0,
        output_nodata=-9999,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0, 10, 0],
        [0, 10, -9999],
    ]

    assert result.metadata["operator"] == "between"
    assert result.metadata["min_value"] == 0.2
    assert result.metadata["max_value"] == 0.5
    assert result.metadata["true_pixel_count"] == 2


def test_threshold_raster_between_exclusive() -> None:
    result = threshold_raster(
        raster={
            "data": [[0.2, 0.3, 0.5]],
            "metadata": {"nodata": -9999},
        },
        operator="between",
        min_value=0.2,
        max_value=0.5,
        inclusive_min=False,
        inclusive_max=False,
        output_nodata=-9999,
    )

    data = _get_data(result)

    assert data == [[0, 1, 0]]


def test_threshold_raster_outside() -> None:
    result = threshold_raster(
        raster=RASTER_NDVI,
        operator="outside",
        min_value=0.2,
        max_value=0.5,
        output_nodata=-9999,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [1, 0, 1],
        [1, 0, -9999],
    ]

    assert result.metadata["operator"] == "outside"
    assert result.metadata["true_pixel_count"] == 3


def test_threshold_raster_eq_neq() -> None:
    raster = {
        "data": [[1, 2, 2, 3]],
        "metadata": {"nodata": -9999},
    }

    eq_result = threshold_raster(
        raster=raster,
        operator="eq",
        threshold=2,
        output_nodata=-9999,
    )

    neq_result = threshold_raster(
        raster=raster,
        operator="neq",
        threshold=2,
        output_nodata=-9999,
    )

    assert _get_data(eq_result) == [[0, 1, 1, 0]]
    assert _get_data(neq_result) == [[1, 0, 0, 1]]


def test_threshold_raster_invalid_values_to_nodata() -> None:
    result = threshold_raster(
        raster=RASTER_WITH_NODATA_AND_INVALID,
        operator="gt",
        threshold=2,
        output_nodata=-9999,
    )

    data = _get_data(result)

    assert data == [
        [0, 0, -9999],
        [1, -9999, -9999],
    ]

    assert result.metadata["true_pixel_count"] == 1
    assert result.metadata["false_pixel_count"] == 2
    assert result.metadata["input_nodata_pixel_count"] == 2
    assert result.metadata["invalid_input_pixel_count"] == 1
    assert result.metadata["nodata_pixel_count"] == 3


def test_threshold_raster_3d_selected_band() -> None:
    result = threshold_raster(
        raster=RASTER_2BAND,
        band_index=2,
        operator="gt",
        threshold=20,
        output_nodata=-9999,
    )

    data = _get_data(result)

    assert data == [
        [0, 0],
        [1, 1],
    ]

    assert result.metadata["input_band_count"] == 2
    assert result.metadata["selected_band_index"] == 2
    assert result.metadata["true_pixel_count"] == 2


def test_threshold_raster_custom_values() -> None:
    result = threshold_raster(
        raster=RASTER_NDVI,
        operator="gt",
        threshold=0.3,
        true_value="yes",
        false_value="no",
        output_nodata="nodata",
    )

    data = _get_data(result)

    assert data == [
        ["no", "no", "yes"],
        ["no", "yes", "nodata"],
    ]

    assert result.metadata["true_value"] == "yes"
    assert result.metadata["false_value"] == "no"


def test_threshold_raster_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "raster_threshold.yaml").write_text(
        """
default_engine: python
default_band_index: 1
default_operator: gt
default_threshold: 0.3
default_min_value: null
default_max_value: null
inclusive_min: true
inclusive_max: true
true_value: 1
false_value: 0
default_nodata: -9999
default_output_nodata: -9999
coordinate_precision: 3
preserve_metadata: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": RASTER_NDVI["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 2],
        },
    }

    result = threshold_raster(
        raster=raster,
    )

    data = _get_data(result)

    assert data == [
        [0, 0, 1],
        [0, 1, -9999],
    ]

    assert result.metadata["operator"] == "gt"
    assert result.metadata["threshold"] == 0.3
    assert result.metadata["coordinate_precision"] == 3
    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_threshold_raster_metadata_merge() -> None:
    result = threshold_raster(
        raster=RASTER_NDVI,
        operator="gt",
        threshold=0.3,
        metadata={"analysis_id": "threshold-1"},
    )

    assert result.metadata["analysis_id"] == "threshold-1"


def test_threshold_raster_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        threshold_raster(
            raster=RASTER_NDVI,
            operator="gt",
            threshold=0.3,
            metadata="bad",
        )


def test_threshold_raster_rejects_missing_threshold() -> None:
    with pytest.raises(ValueError, match="threshold"):
        threshold_raster(
            raster=RASTER_NDVI,
            operator="gt",
            threshold=None,
        )


def test_threshold_raster_rejects_missing_range() -> None:
    with pytest.raises(ValueError, match="min_value"):
        threshold_raster(
            raster=RASTER_NDVI,
            operator="between",
            min_value=None,
            max_value=1,
        )


def test_threshold_raster_rejects_invalid_range() -> None:
    with pytest.raises(ValueError, match="min_value"):
        threshold_raster(
            raster=RASTER_NDVI,
            operator="between",
            min_value=1,
            max_value=0,
        )


def test_threshold_raster_rejects_invalid_operator() -> None:
    with pytest.raises(ValueError, match="operator"):
        threshold_raster(
            raster=RASTER_NDVI,
            operator="bad",
            threshold=0.3,
        )


def test_threshold_raster_rejects_invalid_band() -> None:
    with pytest.raises(ValueError, match="band_index"):
        threshold_raster(
            raster=RASTER_2BAND,
            band_index=3,
            operator="gt",
            threshold=1,
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "threshold_raster" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "threshold_raster")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "threshold_raster"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "raster" in descriptor.required_inputs
    assert "operator" in descriptor.optional_inputs
    assert "threshold" in descriptor.optional_inputs
    assert "min_value" in descriptor.optional_inputs
    assert "max_value" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "raster"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "raster_threshold"
    assert descriptor.metadata["binary_mask_supported"] is True
    assert descriptor.metadata["range_threshold_supported"] is True
