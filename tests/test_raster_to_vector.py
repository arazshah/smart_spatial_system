"""
Tests for raster_to_vector plugin.

Run:
    pytest tests/test_raster_to_vector.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.raster_to_vector import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _band_value,
    _bbox_polygon_for_cells,
    _cell_polygon,
    _connected_components,
    _extract_transform,
    _is_nodata,
    _is_number,
    _neighbors,
    _pixel_corner,
    _selected_grid,
    _validate_band_index,
    _validate_connectivity,
    _validate_engine,
    _validate_mode,
    _validate_precision,
    _value_is_selected,
    raster_to_vector,
)


MASK_2D = {
    "data": [
        [1, 0, 1],
        [1, 1, None],
    ],
    "metadata": {
        "transform": [10, 0, 100, 0, -10, 200],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


MASK_2D_NODATA = {
    "data": [
        [1, -9999, 0],
        [None, 1, 1],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


CLASSIFIED_2D = {
    "data": [
        [1, 2, 2],
        [3, 1, 0],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


MASK_DIAGONAL = {
    "data": [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_2BAND = {
    "data": [
        [
            [0, 0],
            [0, 0],
        ],
        [
            [1, 0],
            [1, 1],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 2],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


def _get_features(result):
    if isinstance(result, dict):
        return result["features"]
    if hasattr(result, "features"):
        return result.features
    raise AssertionError("Vector output has no features")


def _get_metadata(result):
    if isinstance(result, dict):
        return result["metadata"]
    if hasattr(result, "metadata"):
        return result.metadata
    raise AssertionError("Vector output has no metadata")


def test_plugin_manifest_basic_fields() -> None:
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "raster_to_vector"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Raster To Vector"


def test_validate_engine() -> None:
    assert _validate_engine("python") == "python"
    assert _validate_engine("auto") == "auto"

    with pytest.raises(ValueError):
        _validate_engine("bad")


def test_validate_mode() -> None:
    assert _validate_mode("cells") == "cells"
    assert _validate_mode("components") == "components"

    with pytest.raises(ValueError):
        _validate_mode("bad")


def test_validate_connectivity() -> None:
    assert _validate_connectivity(4) == 4
    assert _validate_connectivity("8") == 8

    with pytest.raises(ValueError):
        _validate_connectivity(6)


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


def test_band_value_2d() -> None:
    assert _band_value(MASK_2D["data"], band_index=1, row=0, col=0) == 1


def test_band_value_3d() -> None:
    assert _band_value(RASTER_2BAND["data"], band_index=1, row=0, col=0) == 0
    assert _band_value(RASTER_2BAND["data"], band_index=2, row=1, col=1) == 1


def test_extract_transform_from_metadata() -> None:
    transform, source = _extract_transform(
        {"transform": [10, 0, 100, 0, -10, 200]},
        default_origin_x=0,
        default_origin_y=0,
        default_x_resolution=1,
        default_y_resolution=1,
    )

    assert transform == [10.0, 0.0, 100.0, 0.0, -10.0, 200.0]
    assert source == "metadata_transform"


def test_extract_transform_default() -> None:
    transform, source = _extract_transform(
        {},
        default_origin_x=100,
        default_origin_y=200,
        default_x_resolution=10,
        default_y_resolution=20,
    )

    assert transform == [10.0, 0.0, 100.0, 0.0, -20.0, 200.0]
    assert source == "default_transform"


def test_pixel_corner() -> None:
    transform = [10, 0, 100, 0, -10, 200]

    assert _pixel_corner(transform=transform, row=0, col=0) == (100, 200)
    assert _pixel_corner(transform=transform, row=1, col=2) == (120, 190)


def test_cell_polygon() -> None:
    polygon = _cell_polygon(
        transform=[10, 0, 100, 0, -10, 200],
        row=0,
        col=0,
        precision=3,
    )

    assert polygon == {
        "type": "Polygon",
        "coordinates": [[
            [100.0, 200.0],
            [110.0, 200.0],
            [110.0, 190.0],
            [100.0, 190.0],
            [100.0, 200.0],
        ]],
    }


def test_bbox_polygon_for_cells() -> None:
    polygon = _bbox_polygon_for_cells(
        transform=[1, 0, 0, 0, -1, 3],
        cells=[(0, 0), (1, 1)],
        precision=3,
    )

    assert polygon == {
        "type": "Polygon",
        "coordinates": [[
            [0.0, 3.0],
            [2.0, 3.0],
            [2.0, 1.0],
            [0.0, 1.0],
            [0.0, 3.0],
        ]],
    }


def test_value_is_selected() -> None:
    assert _value_is_selected(1, include_values=[1], exclude_values=[], nodata=-9999) is True
    assert _value_is_selected(0, include_values=[1], exclude_values=[], nodata=-9999) is False
    assert _value_is_selected(2, include_values=None, exclude_values=[], nodata=-9999) is True
    assert _value_is_selected(2, include_values=None, exclude_values=[2], nodata=-9999) is False
    assert _value_is_selected(-9999, include_values=None, exclude_values=[], nodata=-9999) is False


def test_selected_grid() -> None:
    selected = _selected_grid(
        MASK_2D["data"],
        band_index=1,
        height=2,
        width=3,
        include_values=[1],
        exclude_values=[],
        nodata=-9999,
    )

    assert selected == [
        [True, False, True],
        [True, True, False],
    ]


def test_neighbors() -> None:
    assert set(_neighbors(row=1, col=1, height=3, width=3, connectivity=4)) == {
        (0, 1), (1, 0), (1, 2), (2, 1)
    }

    assert len(_neighbors(row=1, col=1, height=3, width=3, connectivity=8)) == 8


def test_connected_components_4() -> None:
    selected = [
        [True, False, False],
        [False, True, False],
        [False, False, True],
    ]

    components = _connected_components(selected, connectivity=4)

    assert len(components) == 3
    assert all(len(component) == 1 for component in components)


def test_connected_components_8() -> None:
    selected = [
        [True, False, False],
        [False, True, False],
        [False, False, True],
    ]

    components = _connected_components(selected, connectivity=8)

    assert len(components) == 1
    assert len(components[0]) == 3


def test_raster_to_vector_cells_basic() -> None:
    result = raster_to_vector(
        raster=MASK_2D,
        include_values=[1],
        mode="cells",
        engine="python",
        precision=3,
    )

    features = _get_features(result)
    md = _get_metadata(result)

    assert result["type"] == "FeatureCollection"
    assert len(features) == 4

    first = features[0]
    assert first["type"] == "Feature"
    assert first["id"] == 1
    assert first["properties"]["value"] == 1
    assert first["properties"]["row"] == 0
    assert first["properties"]["col"] == 0
    assert first["geometry"]["type"] == "Polygon"
    assert first["geometry"]["coordinates"][0] == [
        [100.0, 200.0],
        [110.0, 200.0],
        [110.0, 190.0],
        [100.0, 190.0],
        [100.0, 200.0],
    ]

    assert md["source"] == "raster_to_vector"
    assert md["operation"] == "raster_to_vector"
    assert md["engine_requested"] == "python"
    assert md["engine_used"] == "python"
    assert md["input_band_count"] == 1
    assert md["selected_band_index"] == 1
    assert md["width"] == 3
    assert md["height"] == 2
    assert md["mode"] == "cells"
    assert md["selected_pixel_count"] == 4
    assert md["feature_count"] == 4
    assert md["truncated"] is False


def test_raster_to_vector_cells_without_pixel_properties() -> None:
    result = raster_to_vector(
        raster=MASK_2D,
        include_values=[1],
        mode="cells",
        include_pixel_properties=False,
    )

    first = _get_features(result)[0]

    assert "value" in first["properties"]
    assert "row" not in first["properties"]
    assert "col" not in first["properties"]


def test_raster_to_vector_components_4() -> None:
    result = raster_to_vector(
        raster=MASK_2D,
        include_values=[1],
        mode="components",
        connectivity=4,
        precision=3,
    )

    features = _get_features(result)
    md = _get_metadata(result)

    assert len(features) == 2
    assert features[0]["properties"]["component_id"] == 1
    assert features[0]["properties"]["pixel_count"] == 3
    assert features[1]["properties"]["pixel_count"] == 1
    assert md["mode"] == "components"
    assert md["connectivity"] == 4
    assert md["feature_count"] == 2


def test_raster_to_vector_components_8_diagonal() -> None:
    result = raster_to_vector(
        raster=MASK_DIAGONAL,
        include_values=[1],
        mode="components",
        connectivity=8,
        precision=3,
    )

    features = _get_features(result)

    assert len(features) == 1
    assert features[0]["properties"]["pixel_count"] == 3


def test_raster_to_vector_components_4_diagonal() -> None:
    result = raster_to_vector(
        raster=MASK_DIAGONAL,
        include_values=[1],
        mode="components",
        connectivity=4,
        precision=3,
    )

    features = _get_features(result)

    assert len(features) == 3


def test_raster_to_vector_include_multiple_values() -> None:
    result = raster_to_vector(
        raster=CLASSIFIED_2D,
        include_values=[1, 2],
        mode="cells",
        precision=3,
    )

    features = _get_features(result)

    assert len(features) == 4
    assert [feature["properties"]["value"] for feature in features] == [1, 2, 2, 1]


def test_raster_to_vector_include_all_when_include_values_none_and_config_empty(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "raster_to_vector.yaml").write_text(
        """
default_engine: python
default_band_index: 1
default_mode: cells
default_connectivity: 4
default_include_values: null
default_exclude_values:
  - 0
default_nodata: -9999
default_origin_x: 0.0
default_origin_y: 0.0
default_x_resolution: 1.0
default_y_resolution: 1.0
coordinate_precision: 3
preserve_metadata: true
source_crs: null
warn_if_geographic_crs: false
include_pixel_properties: true
include_component_cells: false
max_features: null
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = raster_to_vector(
        raster=CLASSIFIED_2D,
    )

    features = _get_features(result)

    assert len(features) == 5
    assert [feature["properties"]["value"] for feature in features] == [1, 2, 2, 3, 1]


def test_raster_to_vector_exclude_values() -> None:
    result = raster_to_vector(
        raster=CLASSIFIED_2D,
        include_values=None,
        exclude_values=[0, 3],
        mode="cells",
        precision=3,
    )

    features = _get_features(result)

    # Because local real config default_include_values is [1], runtime include_values=None
    # uses config default. So only value 1 remains.
    assert len(features) == 2
    assert [feature["properties"]["value"] for feature in features] == [1, 1]


def test_raster_to_vector_3d_selected_band() -> None:
    result = raster_to_vector(
        raster=RASTER_2BAND,
        band_index=2,
        include_values=[1],
        mode="cells",
        precision=3,
    )

    features = _get_features(result)
    md = _get_metadata(result)

    assert len(features) == 3
    assert md["input_band_count"] == 2
    assert md["selected_band_index"] == 2


def test_raster_to_vector_max_features() -> None:
    result = raster_to_vector(
        raster=MASK_2D,
        include_values=[1],
        mode="cells",
        max_features=2,
    )

    features = _get_features(result)
    md = _get_metadata(result)

    assert len(features) == 2
    assert md["truncated"] is True
    assert md["max_features"] == 2


def test_raster_to_vector_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "raster_to_vector.yaml").write_text(
        """
default_engine: python
default_band_index: 1
default_mode: components
default_connectivity: 8
default_include_values:
  - 1
default_exclude_values: []
default_nodata: -9999
default_origin_x: 100.0
default_origin_y: 200.0
default_x_resolution: 10.0
default_y_resolution: 10.0
coordinate_precision: 3
preserve_metadata: true
source_crs: EPSG:4326
warn_if_geographic_crs: true
include_pixel_properties: true
include_component_cells: true
max_features: null
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": MASK_DIAGONAL["data"],
        "metadata": {
            "nodata": -9999,
        },
    }

    result = raster_to_vector(
        raster=raster,
    )

    features = _get_features(result)
    md = _get_metadata(result)

    assert len(features) == 1
    assert features[0]["properties"]["pixel_count"] == 3
    assert "cells" in features[0]["properties"]
    assert md["mode"] == "components"
    assert md["connectivity"] == 8
    assert md["coordinate_precision"] == 3
    assert md["source_crs"] == "EPSG:4326"
    assert md["warning"] is not None
    assert "geographic CRS" in md["warning"]


def test_raster_to_vector_metadata_merge() -> None:
    result = raster_to_vector(
        raster=MASK_2D,
        include_values=[1],
        metadata={"analysis_id": "rtv-1"},
    )

    assert _get_metadata(result)["analysis_id"] == "rtv-1"


def test_raster_to_vector_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        raster_to_vector(
            raster=MASK_2D,
            include_values=[1],
            metadata="bad",
        )


def test_raster_to_vector_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        raster_to_vector(
            raster=MASK_2D,
            include_values=[1],
            mode="bad",
        )


def test_raster_to_vector_rejects_invalid_connectivity() -> None:
    with pytest.raises(ValueError, match="connectivity"):
        raster_to_vector(
            raster=MASK_2D,
            include_values=[1],
            mode="components",
            connectivity=6,
        )


def test_raster_to_vector_rejects_invalid_band() -> None:
    with pytest.raises(ValueError, match="band_index"):
        raster_to_vector(
            raster=RASTER_2BAND,
            band_index=3,
            include_values=[1],
        )


def test_raster_to_vector_rejects_invalid_max_features() -> None:
    with pytest.raises(ValueError, match="max_features"):
        raster_to_vector(
            raster=MASK_2D,
            include_values=[1],
            max_features=0,
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "raster_to_vector" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "raster_to_vector")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "raster_to_vector"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "vector"
    assert "raster" in descriptor.required_inputs
    assert "include_values" in descriptor.optional_inputs
    assert "mode" in descriptor.optional_inputs
    assert "connectivity" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "vector"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "raster_to_vector"
    assert descriptor.metadata["polygonize_supported"] is True
    assert descriptor.metadata["component_mode_supported"] is True
