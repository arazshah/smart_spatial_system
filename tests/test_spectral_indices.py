"""
Tests for spectral_indices plugin.

Run:
    pytest tests/test_spectral_indices.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.spectral_indices import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    SUPPORTED_INDICES,
    _band_value,
    _calculate_index_value,
    _clip_value,
    _ensure_required_bands,
    _is_nodata,
    _is_number,
    _normalize_band_map,
    _required_bands,
    _safe_ratio,
    _validate_engine,
    _validate_index_name,
    _validate_precision,
    calculate_spectral_index,
)


RASTER_6BAND = {
    "data": [
        # blue
        [
            [5, 10],
            [15, 20],
        ],
        # green
        [
            [10, 20],
            [30, 40],
        ],
        # red
        [
            [20, 20],
            [40, 40],
        ],
        # nir
        [
            [60, 20],
            [20, 40],
        ],
        # swir1
        [
            [30, 40],
            [50, 60],
        ],
        # swir2
        [
            [15, 20],
            [25, 30],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_WITH_NODATA_ZERO = {
    "data": [
        # red
        [
            [20, -9999],
            [-10, 40],
        ],
        # nir
        [
            [60, 20],
            [10, -40],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


BAND_MAP_6 = {
    "blue": 1,
    "green": 2,
    "red": 3,
    "nir": 4,
    "swir1": 5,
    "swir2": 6,
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
    assert PLUGIN.manifest.id == "spectral_indices"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Spectral Indices"


def test_supported_indices_content() -> None:
    assert "ndvi" in SUPPORTED_INDICES
    assert "ndwi" in SUPPORTED_INDICES
    assert "ndbi" in SUPPORTED_INDICES
    assert "savi" in SUPPORTED_INDICES
    assert "evi" in SUPPORTED_INDICES


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


def test_validate_index_name() -> None:
    assert _validate_index_name("NDVI") == "ndvi"
    assert _validate_index_name("ndwi") == "ndwi"

    with pytest.raises(ValueError):
        _validate_index_name("bad")


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


def test_safe_ratio() -> None:
    value, status = _safe_ratio(10, 2, division_by_zero_value=None, output_nodata=-9999)
    assert value == 5.0
    assert status == "success"

    value, status = _safe_ratio(10, 0, division_by_zero_value=None, output_nodata=-9999)
    assert value == -9999
    assert status == "division_by_zero"

    value, status = _safe_ratio(10, 0, division_by_zero_value=0, output_nodata=-9999)
    assert value == 0
    assert status == "division_by_zero"


def test_required_bands() -> None:
    assert _required_bands("ndvi") == ["nir", "red"]
    assert _required_bands("evi") == ["nir", "red", "blue"]


def test_normalize_band_map() -> None:
    config = {
        "default_band_map": {
            "red": 1,
            "nir": 2,
        }
    }

    result = _normalize_band_map(
        band_map={"green": 1},
        config=config,
        band_count=2,
    )

    assert result["red"] == 1
    assert result["nir"] == 2
    assert result["green"] == 1


def test_ensure_required_bands() -> None:
    _ensure_required_bands("ndvi", {"red": 1, "nir": 2})

    with pytest.raises(ValueError):
        _ensure_required_bands("ndvi", {"red": 1})


def test_band_value_3d() -> None:
    assert _band_value(RASTER_6BAND["data"], band_index=1, row=0, col=0) == 5
    assert _band_value(RASTER_6BAND["data"], band_index=4, row=1, col=0) == 20


def test_calculate_index_value_ndvi() -> None:
    value, status = _calculate_index_value(
        index_name="ndvi",
        values={"red": 20, "nir": 60},
        nodata=-9999,
        output_nodata=-9999,
        division_by_zero_value=None,
        clip_output=True,
        output_min=-1,
        output_max=1,
        precision=3,
        params={},
        config_params={},
    )

    assert value == 0.5
    assert status == "success"


def test_calculate_index_value_savi() -> None:
    value, status = _calculate_index_value(
        index_name="savi",
        values={"red": 20, "nir": 60},
        nodata=-9999,
        output_nodata=-9999,
        division_by_zero_value=None,
        clip_output=True,
        output_min=-1,
        output_max=1,
        precision=3,
        params={"savi_l": 0.5},
        config_params={},
    )

    assert value == pytest.approx(0.745, rel=1e-3)
    assert status == "success"


def test_calculate_spectral_index_ndvi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="ndvi",
        band_map=BAND_MAP_6,
        engine="python",
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.5, 0.0],
        [-0.333, 0.0],
    ]

    md = result.metadata
    assert md["source"] == "spectral_indices"
    assert md["operation"] == "spectral_index"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["index_name"] == "ndvi"
    assert md["index_display_name"] == "NDVI"
    assert md["formula"] == "(NIR - RED) / (NIR + RED)"
    assert md["required_bands"] == ["nir", "red"]
    assert md["input_band_count"] == 6
    assert md["output_band_count"] == 1
    assert md["width"] == 2
    assert md["height"] == 2
    assert md["success_pixel_count"] == 4
    assert md["valid_pixel_count"] == 4
    assert md["nodata_pixel_count"] == 0
    assert md["output_min_value"] == -0.333
    assert md["output_max_value"] == 0.5


def test_calculate_spectral_index_ndwi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="ndwi",
        band_map=BAND_MAP_6,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [-0.714, 0.0],
        [0.2, 0.0],
    ]


def test_calculate_spectral_index_ndbi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="ndbi",
        band_map=BAND_MAP_6,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [-0.333, 0.333],
        [0.429, 0.2],
    ]


def test_calculate_spectral_index_gndvi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="gndvi",
        band_map=BAND_MAP_6,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.714, 0.0],
        [-0.2, 0.0],
    ]


def test_calculate_spectral_index_ndmi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="ndmi",
        band_map=BAND_MAP_6,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.333, -0.333],
        [-0.429, -0.2],
    ]


def test_calculate_spectral_index_mndwi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="mndwi",
        band_map=BAND_MAP_6,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [-0.5, -0.333],
        [-0.25, -0.2],
    ]


def test_calculate_spectral_index_nbr() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="nbr",
        band_map=BAND_MAP_6,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.6, 0.0],
        [-0.111, 0.143],
    ]


def test_calculate_spectral_index_savi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="savi",
        band_map=BAND_MAP_6,
        params={"savi_l": 0.5},
        precision=3,
    )

    data = _get_data(result)

    assert data[0][0] == pytest.approx(0.745, rel=1e-3)
    assert data[0][1] == 0.0
    assert data[1][0] == pytest.approx(-0.496, rel=1e-3)
    assert data[1][1] == 0.0


def test_calculate_spectral_index_evi() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="evi",
        band_map=BAND_MAP_6,
        precision=3,
    )

    data = _get_data(result)

    assert data[0][0] == pytest.approx(0.697, rel=1e-3)
    assert data[0][1] == 0.0


def test_calculate_spectral_index_with_nodata_and_zero_division_to_nodata() -> None:
    result = calculate_spectral_index(
        raster=RASTER_WITH_NODATA_ZERO,
        index_name="ndvi",
        band_map={"red": 1, "nir": 2},
        output_nodata=-9999,
        division_by_zero_value=None,
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


def test_calculate_spectral_index_with_zero_division_default_zero() -> None:
    result = calculate_spectral_index(
        raster=RASTER_WITH_NODATA_ZERO,
        index_name="ndvi",
        band_map={"red": 1, "nir": 2},
        output_nodata=-9999,
        division_by_zero_value=0,
        precision=3,
    )

    data = _get_data(result)

    assert data == [
        [0.5, -9999],
        [0.0, 0.0],
    ]

    assert result.metadata["nodata_pixel_count"] == 1


def test_calculate_spectral_index_no_clip_output() -> None:
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

    result = calculate_spectral_index(
        raster=raster,
        index_name="ndvi",
        band_map={"red": 1, "nir": 2},
        clip_output=False,
        precision=3,
    )

    data = _get_data(result)

    assert data == [[3.0]]
    assert result.metadata["clip_output"] is False


def test_calculate_spectral_index_rejects_invalid_output_range() -> None:
    with pytest.raises(ValueError, match="output_min"):
        calculate_spectral_index(
            raster=RASTER_6BAND,
            index_name="ndvi",
            band_map=BAND_MAP_6,
            output_min=1,
            output_max=-1,
        )


def test_calculate_spectral_index_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spectral_indices.yaml").write_text(
        """
default_engine: python
default_index: ndvi
default_band_map:
  red: 3
  nir: 4
default_params:
  savi_l: 0.5
  evi_g: 2.5
  evi_c1: 6.0
  evi_c2: 7.5
  evi_l: 1.0
default_nodata: -9999
default_output_nodata: -9999
division_by_zero_value: null
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

    result = calculate_spectral_index(
        raster=RASTER_6BAND,
    )

    data = _get_data(result)

    assert data == [
        [0.5, 0.0],
        [-0.333, 0.0],
    ]

    assert result.metadata["index_name"] == "ndvi"
    assert result.metadata["band_map"]["red"] == 3
    assert result.metadata["band_map"]["nir"] == 4
    assert result.metadata["coordinate_precision"] == 3
    assert result.metadata["source_crs"] == "EPSG:3857"
    assert result.metadata["warning"] is None


def test_calculate_spectral_index_config_geographic_warning(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spectral_indices.yaml").write_text(
        """
default_engine: python
default_index: ndvi
default_band_map:
  red: 1
  nir: 2
default_nodata: -9999
default_output_nodata: -9999
division_by_zero_value: null
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
        "data": RASTER_WITH_NODATA_ZERO["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 2],
        },
    }

    result = calculate_spectral_index(
        raster=raster,
        division_by_zero_value=0,
    )

    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_calculate_spectral_index_metadata_merge() -> None:
    result = calculate_spectral_index(
        raster=RASTER_6BAND,
        index_name="ndvi",
        band_map=BAND_MAP_6,
        metadata={"analysis_id": "spectral-1"},
    )

    assert result.metadata["analysis_id"] == "spectral-1"


def test_calculate_spectral_index_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        calculate_spectral_index(
            raster=RASTER_6BAND,
            index_name="ndvi",
            band_map=BAND_MAP_6,
            metadata="bad",
        )


def test_calculate_spectral_index_rejects_invalid_params() -> None:
    with pytest.raises(ValueError, match="params"):
        calculate_spectral_index(
            raster=RASTER_6BAND,
            index_name="savi",
            band_map=BAND_MAP_6,
            params="bad",
        )


def test_calculate_spectral_index_rejects_missing_required_band(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "spectral_indices.yaml").write_text(
        """
default_engine: python
default_index: ndvi
default_band_map: {}
default_params:
  savi_l: 0.5
  evi_g: 2.5
  evi_c1: 6.0
  evi_c2: 7.5
  evi_l: 1.0
default_nodata: -9999
default_output_nodata: -9999
division_by_zero_value: null
clip_output: true
output_min: -1.0
output_max: 1.0
coordinate_precision: 3
preserve_metadata: true
source_crs: null
warn_if_geographic_crs: false
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="Missing required"):
        calculate_spectral_index(
            raster=RASTER_6BAND,
            index_name="ndvi",
            band_map={"red": 3},
        )


def test_calculate_spectral_index_rejects_invalid_band_index() -> None:
    with pytest.raises(ValueError, match="out of range"):
        calculate_spectral_index(
            raster=RASTER_6BAND,
            index_name="ndvi",
            band_map={"red": 3, "nir": 99},
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "calculate_spectral_index" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "calculate_spectral_index")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "calculate_spectral_index"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "raster" in descriptor.required_inputs
    assert "index_name" in descriptor.required_inputs
    assert "band_map" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "raster"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "spectral_index"
    assert "ndvi" in descriptor.metadata["spectral_indices_supported"]
    assert "evi" in descriptor.metadata["spectral_indices_supported"]
