"""
Tests for raster_clip_mask plugin.

Run:
    pytest tests/test_raster_clip_mask.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.raster_clip_mask import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _array_shape,
    _bboxes_intersect,
    _extract_raster,
    _geometry_bbox,
    _get_transform_from_metadata,
    _is_geographic_crs,
    _mask_array,
    _normalize_bbox,
    _normalize_transform,
    _pixel_center,
    _pixel_bbox,
    _point_in_polygon,
    _raster_bbox,
    _slice_array,
    _updated_transform,
    _validate_engine,
    _validate_precision,
    _window_for_bbox,
    clip_mask_raster,
)


RASTER_5X5 = {
    "data": [
        [1, 2, 3, 4, 5],
        [6, 7, 8, 9, 10],
        [11, 12, 13, 14, 15],
        [16, 17, 18, 19, 20],
        [21, 22, 23, 24, 25],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 5],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


RASTER_2BAND_3X3 = {
    "data": [
        [
            [1, 2, 3],
            [4, 5, 6],
            [7, 8, 9],
        ],
        [
            [10, 20, 30],
            [40, 50, 60],
            [70, 80, 90],
        ],
    ],
    "metadata": {
        "transform": [1, 0, 0, 0, -1, 3],
        "crs": "EPSG:3857",
    },
}


POLYGON_CENTER = {
    "type": "Polygon",
    "coordinates": [[
        [1.0, 1.0],
        [4.0, 1.0],
        [4.0, 4.0],
        [1.0, 4.0],
        [1.0, 1.0],
    ]],
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
    assert PLUGIN.manifest.id == "raster_clip_mask"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Raster Clip Mask"


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


def test_normalize_bbox_list() -> None:
    assert _normalize_bbox([0, 1, 2, 3]) == [0.0, 1.0, 2.0, 3.0]


def test_normalize_bbox_dict() -> None:
    bbox = {"minx": 0, "miny": 1, "maxx": 2, "maxy": 3}
    assert _normalize_bbox(bbox) == [0.0, 1.0, 2.0, 3.0]


def test_bboxes_intersect() -> None:
    assert _bboxes_intersect([0, 0, 10, 10], [5, 5, 15, 15]) is True
    assert _bboxes_intersect([0, 0, 10, 10], [20, 20, 30, 30]) is False


def test_normalize_transform_list() -> None:
    assert _normalize_transform([1, 0, 0, 0, -1, 5]) == [1.0, 0.0, 0.0, 0.0, -1.0, 5.0]


def test_normalize_transform_dict() -> None:
    transform = {
        "pixel_width": 1,
        "pixel_height": 1,
        "origin_x": 0,
        "origin_y": 5,
    }

    assert _normalize_transform(transform) == [1.0, 0.0, 0.0, 0.0, -1.0, 5.0]


def test_pixel_center() -> None:
    transform = [1, 0, 0, 0, -1, 5]

    assert _pixel_center(0, 0, transform) == (0.5, 4.5)
    assert _pixel_center(2, 3, transform) == (3.5, 2.5)


def test_pixel_bbox() -> None:
    transform = [1, 0, 0, 0, -1, 5]

    assert _pixel_bbox(0, 0, transform) == [0.0, 4.0, 1.0, 5.0]


def test_raster_bbox() -> None:
    transform = [1, 0, 0, 0, -1, 5]

    assert _raster_bbox(5, 5, transform) == [0.0, 0.0, 5.0, 5.0]


def test_array_shape_2d() -> None:
    assert _array_shape(RASTER_5X5["data"]) == (1, 5, 5)


def test_array_shape_3d() -> None:
    assert _array_shape(RASTER_2BAND_3X3["data"]) == (2, 3, 3)


def test_slice_array_2d() -> None:
    sliced = _slice_array(RASTER_5X5["data"], 1, 4, 1, 4)

    assert sliced == [
        [7, 8, 9],
        [12, 13, 14],
        [17, 18, 19],
    ]


def test_slice_array_3d() -> None:
    sliced = _slice_array(RASTER_2BAND_3X3["data"], 1, 3, 1, 3)

    assert sliced == [
        [
            [5, 6],
            [8, 9],
        ],
        [
            [50, 60],
            [80, 90],
        ],
    ]


def test_updated_transform() -> None:
    transform = [1, 0, 0, 0, -1, 5]

    assert _updated_transform(transform, 1, 1) == [1, 0, 1, 0, -1, 4]


def test_window_for_bbox_center_based() -> None:
    transform = [1, 0, 0, 0, -1, 5]

    window = _window_for_bbox(
        bbox=[1, 1, 4, 4],
        width=5,
        height=5,
        transform=transform,
        all_touched=False,
    )

    assert window == (1, 4, 1, 4)


def test_point_in_polygon() -> None:
    assert _point_in_polygon(2.0, 2.0, POLYGON_CENTER["coordinates"]) is True
    assert _point_in_polygon(0.5, 0.5, POLYGON_CENTER["coordinates"]) is False


def test_geometry_bbox_polygon() -> None:
    assert _geometry_bbox(POLYGON_CENTER) == [1.0, 1.0, 4.0, 4.0]


def test_get_transform_from_metadata() -> None:
    transform = _get_transform_from_metadata(RASTER_5X5["metadata"])

    assert transform == [1.0, 0.0, 0.0, 0.0, -1.0, 5.0]


def test_extract_raster_from_dict() -> None:
    data, metadata, info = _extract_raster(RASTER_5X5)

    assert data[0][0] == 1
    assert metadata["crs"] == "EPSG:3857"
    assert info["input_type"] == "dict"


def test_is_geographic_crs() -> None:
    assert _is_geographic_crs("EPSG:4326") is True
    assert _is_geographic_crs("CRS:84") is True
    assert _is_geographic_crs("EPSG:3857") is False


def test_mask_array_with_bbox() -> None:
    data, count = _mask_array(
        data=RASTER_5X5["data"],
        transform=[1, 0, 0, 0, -1, 5],
        geometry=None,
        bbox=[1, 1, 4, 4],
        nodata=-9999,
    )

    assert count == 16
    assert data[0][0] == -9999
    assert data[2][2] == 13


def test_clip_mask_raster_bbox_crop_only() -> None:
    result = clip_mask_raster(
        raster=RASTER_5X5,
        bbox=[1, 1, 4, 4],
        crop=True,
        apply_mask=False,
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [7, 8, 9],
        [12, 13, 14],
        [17, 18, 19],
    ]

    assert result.metadata["source"] == "raster_clip_mask"
    assert result.metadata["operation"] == "clip_mask"
    assert result.metadata["input_width"] == 5
    assert result.metadata["input_height"] == 5
    assert result.metadata["output_width"] == 3
    assert result.metadata["output_height"] == 3
    assert result.metadata["window"] == {
        "row_start": 1,
        "row_stop": 4,
        "col_start": 1,
        "col_stop": 4,
    }
    assert result.metadata["transform"] == [1.0, 0.0, 1.0, 0.0, -1.0, 4.0]


def test_clip_mask_raster_bbox_no_crop_mask() -> None:
    result = clip_mask_raster(
        raster=RASTER_5X5,
        bbox=[1, 1, 4, 4],
        crop=False,
        apply_mask=True,
        nodata=-9999,
        engine="python",
    )

    data = _get_data(result)

    assert len(data) == 5
    assert len(data[0]) == 5
    assert data[0][0] == -9999
    assert data[2][2] == 13
    assert result.metadata["masked_pixel_count"] == 16


def test_clip_mask_raster_polygon_crop_and_mask() -> None:
    result = clip_mask_raster(
        raster=RASTER_5X5,
        mask_geometry=POLYGON_CENTER,
        crop=True,
        apply_mask=True,
        nodata=-9999,
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [7, 8, 9],
        [12, 13, 14],
        [17, 18, 19],
    ]

    assert result.metadata["mask_geometry_applied"] is True
    assert result.metadata["geometry_bbox"] == [1.0, 1.0, 4.0, 4.0]


def test_clip_mask_raster_3d_bbox_crop() -> None:
    result = clip_mask_raster(
        raster=RASTER_2BAND_3X3,
        bbox=[1, 0, 3, 2],
        crop=True,
        apply_mask=False,
        engine="python",
    )

    data = _get_data(result)

    assert data == [
        [
            [5, 6],
            [8, 9],
        ],
        [
            [50, 60],
            [80, 90],
        ],
    ]

    assert result.metadata["output_band_count"] == 2
    assert result.metadata["output_width"] == 2
    assert result.metadata["output_height"] == 2


def test_clip_mask_raster_uses_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    (config_dir / "raster_clip_mask.yaml").write_text(
        """
default_engine: python
default_crop: true
default_mask: false
default_all_touched: false
default_nodata: -9999
coordinate_precision: 3
preserve_metadata: true
warn_if_geographic_crs: true
source_crs: EPSG:4326
fields:
  clipped_by_field: clipped_by
  masked_field: masked
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    raster = {
        "data": RASTER_5X5["data"],
        "metadata": {
            "transform": [1, 0, 0, 0, -1, 5],
        },
    }

    result = clip_mask_raster(
        raster=raster,
        bbox=[1, 1, 4, 4],
    )

    data = _get_data(result)

    assert data == [
        [7, 8, 9],
        [12, 13, 14],
        [17, 18, 19],
    ]

    assert result.metadata["engine_requested"] == "python"
    assert result.metadata["apply_mask"] is False
    assert result.metadata["source_crs"] == "EPSG:4326"
    assert result.metadata["warning"] is not None
    assert "geographic CRS" in result.metadata["warning"]


def test_clip_mask_raster_metadata_merge() -> None:
    result = clip_mask_raster(
        raster=RASTER_5X5,
        bbox=[1, 1, 4, 4],
        metadata={"analysis_id": "clip-1"},
    )

    assert result.metadata["analysis_id"] == "clip-1"


def test_clip_mask_raster_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        clip_mask_raster(
            raster=RASTER_5X5,
            bbox=[1, 1, 4, 4],
            metadata="bad",
        )


def test_clip_mask_raster_rejects_invalid_raster() -> None:
    with pytest.raises(ValueError):
        clip_mask_raster(
            raster={"metadata": {"transform": [1, 0, 0, 0, -1, 5]}},
            bbox=[1, 1, 4, 4],
        )


def test_clip_mask_raster_rejects_missing_transform() -> None:
    with pytest.raises(ValueError, match="transform"):
        clip_mask_raster(
            raster={"data": [[1, 2], [3, 4]], "metadata": {}},
            bbox=[0, 0, 1, 1],
        )


def test_clip_mask_raster_rejects_non_overlapping_bbox() -> None:
    with pytest.raises(ValueError, match="overlap"):
        clip_mask_raster(
            raster=RASTER_5X5,
            bbox=[100, 100, 110, 110],
            crop=True,
        )


def test_capabilities_registered_inside_plugin() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "clip_mask_raster" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "clip_mask_raster")
    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "clip_mask_raster"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.output_kind == "raster"
    assert "raster" in descriptor.required_inputs
    assert "bbox" in descriptor.optional_inputs
    assert "mask_geometry" in descriptor.optional_inputs
    assert descriptor.metadata["artifact_kind"] == "raster"
    assert descriptor.metadata["config_aware"] is True
    assert descriptor.metadata["operation"] == "clip_mask"
    assert descriptor.metadata["bbox_clip_supported"] is True
    assert descriptor.metadata["geometry_mask_supported"] is True
