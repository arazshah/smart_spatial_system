"""
Regression test for a PR #58 review finding on bug 001's fix.

raster_clip_mask._extract_raster lazily reads pixel data from a path-backed
RasterOut (e.g. from local_raster_loader or wms_wfs_fetcher) that has no
in-memory .data. The fix unconditionally installed whatever transform
rasterio reported for the file. For an unreferenced image (no worldfile/CRS
embedded -- e.g. a WMS PNG/JPEG response saved without georeferencing),
rasterio/GDAL fills in the identity transform [1,0,0,0,1,0], which was then
silently treated as real map coordinates by every downstream raster plugin.

This asserts that:
- an unreferenced file with a 'bbox' + width/height in its metadata (as
  wms_wfs_fetcher attaches) gets a transform *derived* from that bbox,
  not the meaningless identity transform.
- an unreferenced file with no bbox metadata to fall back on raises a clear
  error instead of silently propagating the identity transform.

Run:
    pytest tests/test_review_001_unreferenced_raster_transform.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

rasterio = pytest.importorskip("rasterio")
import numpy as np  # noqa: E402
from affine import Affine  # noqa: E402

from plugins.raster_clip_mask import _extract_raster  # noqa: E402


def _write_unreferenced_tif(path: Path) -> None:
    data = np.full((4, 4), 0.5, dtype="float32")
    with rasterio.open(
        str(path),
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="float32",
        crs=None,
        transform=Affine.identity(),
    ) as dst:
        dst.write(data, 1)


def test_unreferenced_raster_with_bbox_metadata_derives_transform(tmp_path: Path) -> None:
    path = tmp_path / "unreferenced.tif"
    _write_unreferenced_tif(path)

    raster = SimpleNamespace(
        path=str(path),
        metadata={"bbox": {"minx": 10.0, "miny": 20.0, "maxx": 18.0, "maxy": 28.0}},
    )

    _data, metadata, _source_info = _extract_raster(raster)

    transform = metadata["transform"]
    assert transform != [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

    pixel_width = transform[0]
    pixel_height = transform[4]
    origin_x = transform[2]
    origin_y = transform[5]

    assert pixel_width == pytest.approx((18.0 - 10.0) / 4)
    assert pixel_height == pytest.approx(-(28.0 - 20.0) / 4)
    assert origin_x == pytest.approx(10.0)
    assert origin_y == pytest.approx(28.0)


def test_unreferenced_raster_without_bbox_metadata_raises(tmp_path: Path) -> None:
    path = tmp_path / "unreferenced_no_bbox.tif"
    _write_unreferenced_tif(path)

    raster = SimpleNamespace(path=str(path), metadata={})

    with pytest.raises(ValueError, match="no embedded georeferencing"):
        _extract_raster(raster)
