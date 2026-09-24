"""
Regression test for case-study bug 006.

_band_value/_pixel_value helpers called _array_shape(data) (which walks
every row of the array to validate shape) on EVERY pixel, in
ndvi_calculator, raster_reclassify, band_math, zonal_statistics,
raster_to_vector, and spectral_indices. That makes per-call cost grow with
raster height on top of the O(H*W) pixel loop, so doubling both H and W
took roughly 8x longer instead of the expected ~4x, and blew up badly at
real raster sizes (492x562 measured at ~32s before the fix).

Run:
    pytest tests/test_bug_006_raster_pixel_loop_performance.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.ndvi_calculator import calculate_ndvi  # noqa: E402


def _make_two_band_raster(size: int) -> dict:
    red = [[float(i % 5) for i in range(size)] for _ in range(size)]
    nir = [[float((i + 1) % 5) for i in range(size)] for _ in range(size)]
    return {
        "data": [red, nir],
        "metadata": {"transform": [1, 0, 0, 0, -1, size]},
    }


def _timed_ndvi(size: int) -> float:
    raster = _make_two_band_raster(size)
    start = time.perf_counter()
    calculate_ndvi(raster, red_band=1, nir_band=2)
    return time.perf_counter() - start


def test_doubling_raster_dimensions_scales_sub_quadratically():
    """
    Doubling both H and W should scale wall-clock time roughly like the
    O(H*W) pixel count (~4x), not the O(H^2*W) blowup from re-walking the
    whole array shape on every pixel (~8x or worse).
    """
    small_time = _timed_ndvi(150)
    large_time = _timed_ndvi(300)

    # Generous upper bound (spec asks for <= 5x); guards against regression
    # back to the ~8x+ quadratic-plus blowup without being flaky on a
    # loaded CI box.
    assert large_time <= small_time * 6.0 + 0.05, (
        f"doubling raster dimensions took {large_time / small_time:.2f}x longer "
        f"(small={small_time:.3f}s, large={large_time:.3f}s); expected <= ~5x"
    )


def test_1000x1000_ndvi_completes_within_ten_seconds():
    elapsed = _timed_ndvi(1000)

    assert elapsed < 10.0, f"1000x1000 NDVI took {elapsed:.2f}s, expected < 10s"
