"""numpy engine == python engine, for every raster plugin that has both.

The numpy engine exists for speed on real scenes (see
plugins/_shared/raster_numpy.py); it must not change any result. Each test
runs the same call with engine="python" and engine="numpy" on small random
rasters that include None, NaN, zeros and the nodata value, and compares
outputs and metadata counts.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

from plugins.band_math import calculate_band_math

META = {"transform": [20.0, 0.0, 500000.0, 0.0, -20.0, 4200000.0], "crs": "EPSG:32638"}


def _rand_raster(bands: int, h: int, w: int, seed: int, nodata=None, ints=False):
    rnd = random.Random(seed)

    def v():
        r = rnd.random()
        if r < 0.05:
            return None
        if r < 0.08:
            return float("nan")
        if r < 0.10:
            return 0.0
        if nodata is not None and r < 0.13:
            return nodata
        if ints:
            return rnd.randint(1, 4)
        return round(rnd.uniform(-1, 1), 3)

    return [[[v() for _ in range(w)] for _ in range(h)] for _ in range(bands)]


def _same(a, b, tol=1e-9):
    if isinstance(a, (list, tuple)):
        return isinstance(b, type(a)) and len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return abs(a - b) <= tol
    return a == b and type(a) is type(b)


BAND_MATH_EXPRS = [
    "b1 - b2",
    "safe_div(b1 - b2, b1 + b2, 0)",
    "where((b1 > 0) and (b2 < 0.3), 1, where(b3 >= 0.4, 2, 3))",
    "b1 / b2",
    "clip(b1 * 2, -0.5, 0.5)",
    "max(b1, b2, b3) - min(b1, b2)",
    "abs(b1) + round(b2, 1)",
    "0 < b1 < 0.5",
    "not b1 > 0",
    "b1 ** 2 % 0.3",
    "b1 or b2",
    "where(b1 > 0 and b2 < 0.3 or b3 > 0.5, 1, 0)",
]


@pytest.mark.parametrize("expression", BAND_MATH_EXPRS)
@pytest.mark.parametrize("output_dtype", ["float", "int", "bool", "preserve"])
@pytest.mark.parametrize("nodata", [None, -9.0])
def test_band_math_numpy_matches_python(expression, output_dtype, nodata):
    data = _rand_raster(3, 17, 23, seed=len(expression), nodata=nodata)
    kw = dict(expression=expression, output_dtype=output_dtype, nodata=nodata)
    py = calculate_band_math({"data": data, "metadata": META}, engine="python", **kw)
    nu = calculate_band_math({"data": data, "metadata": META}, engine="numpy", **kw)
    assert _same(py.data, nu.data)
    for key in ("valid_pixel_count", "nodata_pixel_count", "error_pixel_count"):
        assert py.metadata[key] == nu.metadata[key], key
    assert nu.metadata["engine_used"] == "numpy"


def test_band_math_ndarray_in_ndarray_out():
    arr = np.random.default_rng(0).random((3, 50, 40)).astype("float32")
    arr[0, 0, 0] = np.nan
    out = calculate_band_math({"data": arr, "metadata": META}, expression="b1 - b2", engine="auto")
    assert isinstance(out.data, np.ndarray) and out.data.shape == (50, 40)
    assert np.isnan(out.data[0, 0])
    assert out.metadata["nodata_pixel_count"] == 1
    ref = calculate_band_math({"data": arr.tolist(), "metadata": META}, expression="b1 - b2", engine="python")
    np.testing.assert_allclose(np.array(ref.data, dtype=float), out.data, rtol=0, atol=1e-6, equal_nan=True)


def test_band_math_python_engine_accepts_ndarray():
    arr = np.arange(12, dtype="float64").reshape(1, 3, 4)
    out = calculate_band_math({"data": arr, "metadata": META}, expression="b1 * 2", engine="python")
    assert out.data == [[0.0, 2.0, 4.0, 6.0], [8.0, 10.0, 12.0, 14.0], [16.0, 18.0, 20.0, 22.0]]


# ---------------------------------------------------------------- zonal
from plugins.zonal_statistics import calculate_zonal_statistics  # noqa: E402


def _zones():
    # unaligned coordinates, a hole, a multipolygon and a triangle
    x0, y0 = 500000.0, 4200000.0
    sq = [[x0 + 33.3, y0 - 27.1], [x0 + 301.7, y0 - 27.1], [x0 + 301.7, y0 - 288.9], [x0 + 33.3, y0 - 288.9],
          [x0 + 33.3, y0 - 27.1]]
    hole = [[x0 + 110.0, y0 - 100.0], [x0 + 210.0, y0 - 100.0], [x0 + 210.0, y0 - 200.0], [x0 + 110.0, y0 - 200.0],
            [x0 + 110.0, y0 - 100.0]]
    tri = [[x0 + 5.0, y0 - 300.0], [x0 + 420.0, y0 - 310.0], [x0 + 200.0, y0 - 395.0], [x0 + 5.0, y0 - 300.0]]
    a = [[x0 + 320.0, y0 - 10.0], [x0 + 440.0, y0 - 10.0], [x0 + 440.0, y0 - 90.0], [x0 + 320.0, y0 - 10.0]]
    b = [[x0 + 330.0, y0 - 150.0], [x0 + 450.0, y0 - 170.0], [x0 + 380.0, y0 - 260.0], [x0 + 330.0, y0 - 150.0]]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"zid": "holed"}, "geometry": {"type": "Polygon", "coordinates": [sq, hole]}},
        {"type": "Feature", "properties": {"zid": "tri"}, "geometry": {"type": "Polygon", "coordinates": [tri]}},
        {"type": "Feature", "properties": {"zid": "multi"},
         "geometry": {"type": "MultiPolygon", "coordinates": [[a], [b]]}},
        {"type": "Feature", "properties": {"zid": "outside"}, "geometry": {"type": "Polygon", "coordinates": [[
            [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]}},
    ]}


@pytest.mark.parametrize("all_touched", [False, True])
@pytest.mark.parametrize("as_array", [False, True])
@pytest.mark.parametrize("ints", [False, True])
def test_zonal_numpy_matches_python(all_touched, as_array, ints):
    data = _rand_raster(1, 21, 24, seed=7, nodata=-9.0, ints=ints)[0]
    if as_array:
        data = np.array([[np.nan if v is None else v for v in row] for row in data],
                        dtype="float64" if not ints else "float64")
    kw = dict(zones=_zones(), stats=None, zone_id_field="zid", all_touched=all_touched, nodata=-9.0,
              include_zone_geometry=False)
    py = calculate_zonal_statistics({"data": data, "metadata": META}, engine="python", **kw)
    nu = calculate_zonal_statistics({"data": data, "metadata": META}, engine="numpy", **kw)
    for fp, fn in zip(py.features, nu.features):
        pp = {k: v for k, v in fp["properties"].items() if k != "engine"}
        pn = {k: v for k, v in fn["properties"].items() if k != "engine"}
        assert _same(list(pp.items()), list(pn.items())), (pp, pn)
    assert nu.metadata["engine_used"] == "numpy"
    assert py.metadata["total_selected_pixel_count"] == nu.metadata["total_selected_pixel_count"] > 0


def test_zonal_numpy_integer_array_keeps_int_majority():
    data = np.array([[1, 2, 2], [3, 2, 1], [1, 1, 1]], dtype="int16")
    zones = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[
        [500000.0, 4200000.0], [500060.0, 4200000.0], [500060.0, 4199940.0], [500000.0, 4199940.0],
        [500000.0, 4200000.0]]]}}
    py = calculate_zonal_statistics({"data": data.tolist(), "metadata": META}, zones=zones, engine="python")
    nu = calculate_zonal_statistics({"data": data, "metadata": META}, zones=zones, engine="numpy")
    assert py.features[0]["properties"]["zonal_majority"] == nu.features[0]["properties"]["zonal_majority"] == 1
    assert type(nu.features[0]["properties"]["zonal_majority"]) is int


# ---------------------------------------------------------------- raster_to_vector
from plugins.raster_to_vector import raster_to_vector  # noqa: E402


@pytest.mark.parametrize("mode", ["cells", "components"])
@pytest.mark.parametrize("connectivity", [4, 8])
@pytest.mark.parametrize("as_array", [False, True])
@pytest.mark.parametrize("seed", range(6))
def test_raster_to_vector_numpy_matches_python(mode, connectivity, as_array, seed):
    rnd = random.Random(seed)
    h, w = rnd.randint(4, 30), rnd.randint(4, 30)
    rows = [[rnd.choice([1, 1, 1, 2, 2, 3, None, -9]) for _ in range(w)] for _ in range(h)]
    data = np.array([[np.nan if v is None else v for v in r] for r in rows]) if as_array else rows
    kw = dict(mode=mode, connectivity=connectivity, include_values=[1, 2, -9], exclude_values=[2] if seed % 2 else [],
              nodata=-9, max_features=None if seed % 3 else 7)
    py = raster_to_vector({"data": data, "metadata": META}, engine="python", **kw)
    nu = raster_to_vector({"data": data, "metadata": META}, engine="numpy", **kw)
    assert nu["metadata"]["engine_used"] == "numpy"
    for key in ("selected_pixel_count", "feature_count", "truncated"):
        assert py["metadata"][key] == nu["metadata"][key], key
    for fp, fn in zip(py["features"], nu["features"]):
        assert fp["id"] == fn["id"]
        assert _same(fp["properties"], fn["properties"]) if not isinstance(fp["properties"], dict) else \
            _same(sorted(fp["properties"].items()), sorted(fn["properties"].items()))
        assert fp["geometry"] == fn["geometry"]


def test_raster_to_vector_numpy_large_mask_is_fast():
    import time

    rng = np.random.default_rng(3)
    arr = (rng.random((600, 600)) < 0.55).astype("uint8")
    t0 = time.time()
    out = raster_to_vector({"data": arr, "metadata": META}, mode="components", include_values=[1], engine="numpy")
    assert time.time() - t0 < 60
    assert sum(f["properties"]["pixel_count"] for f in out["features"]) == int(arr.sum())
