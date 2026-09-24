"""
Regression test for case-study bug 001.

local_raster_loader.load_local_raster() returns an SDK RasterOut that only
has .path and .metadata (no .data). raster_clip_mask._extract_raster (shared
by every raster analysis plugin, including ndvi_calculator) required an
object with .data, so any real-file RasterOut immediately raised:

    ValueError: raster must be RasterOut-like object or dict with data/array

Run:
    pytest tests/test_bug_001_raster_loader_ndvi_pipeline.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

rasterio = pytest.importorskip("rasterio")
import numpy as np  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from plugins.local_raster_loader import load_local_raster  # noqa: E402
from plugins.ndvi_calculator import calculate_ndvi  # noqa: E402


@pytest.fixture()
def two_band_tif(tmp_path: Path) -> Path:
    """
    Write a small two-band GeoTIFF (band 1 = RED, band 2 = NIR).
    """
    path = tmp_path / "two_band.tif"

    red = np.full((3, 3), 0.2, dtype="float32")
    nir = np.full((3, 3), 0.8, dtype="float32")

    transform = from_origin(0, 3, 1, 1)

    with rasterio.open(
        str(path),
        "w",
        driver="GTiff",
        height=3,
        width=3,
        count=2,
        dtype="float32",
        crs="EPSG:3857",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(red, 1)
        dst.write(nir, 2)

    return path


def test_local_raster_loader_output_is_consumable_by_ndvi_calculator(two_band_tif: Path):
    """
    load_local_raster() -> calculate_ndvi() must work end-to-end for a real
    GeoTIFF file, without the caller having to pre-read pixel data.
    """
    raster = load_local_raster(str(two_band_tif))

    result = calculate_ndvi(raster, red_band=1, nir_band=2)

    # NDVI = (0.8 - 0.2) / (0.8 + 0.2) = 0.6 for every pixel.
    for row in result.data:
        for value in row:
            assert value == pytest.approx(0.6, abs=1e-6)

    assert result.metadata["width"] == 3
    assert result.metadata["height"] == 3
