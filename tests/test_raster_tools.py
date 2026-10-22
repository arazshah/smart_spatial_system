"""Tests for the raster_tools plugin."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("shapely")

from plugins.raster_tools import (  # noqa: E402
    mosaic_rasters,
    reproject_raster,
    sample_raster_at_points,
    weighted_overlay,
)


def raster(data, x0=0.0, y0=None, size=1.0, crs="EPSG:3857", nodata=None):
    data = np.asarray(data, dtype=float)
    rows = data.shape[-2]
    y0 = rows * size if y0 is None else y0
    return {
        "data": data.tolist(),
        "metadata": {"crs": crs, "transform": [size, 0, x0, 0, -size, y0], "nodata": nodata},
    }


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


# ---------------------------------------------------------------------- sample
def test_sample_values_nodata_and_outside():
    r = raster([[1, 2, 3], [4, 5, -9999]], nodata=-9999)  # 3 cols x 2 rows, origin top-left (0, 2)
    out = sample_raster_at_points(r, [pt(0.5, 1.5), pt(2.5, 0.5), pt(1.5, 0.5), pt(99, 99), pt(2.5, 1.5)])
    values = [f["properties"]["raster_value"] for f in out.features]
    assert values == [1.0, None, 5.0, None, 3.0]
    assert out.metadata["outside_count"] == 1


def test_sample_band_and_validation():
    r = raster([[[1, 2]], [[10, 20]]])  # 2 bands, 1 row, 2 cols
    out = sample_raster_at_points(r, [pt(1.5, 0.5)], band_index=2, field_name="b2")
    assert out.features[0]["properties"]["b2"] == 20.0
    with pytest.raises(ValueError):
        sample_raster_at_points(r, [pt(0, 0)], band_index=3)
    with pytest.raises(ValueError):
        sample_raster_at_points({"data": [[1]], "metadata": {}}, [pt(0, 0)])  # no transform
    with pytest.raises(ValueError):
        sample_raster_at_points({"data": [[1]], "metadata": {"transform": [1, 0.5, 0, 0, -1, 1]}}, [pt(0, 0)])


# ---------------------------------------------------------------------- mosaic
def test_mosaic_side_by_side_and_overlap_methods():
    left = raster([[1, 2], [3, 4]], x0=0, y0=2)
    right = raster([[10, 20], [30, 40]], x0=1, y0=2)  # overlaps the right column of `left`
    first = mosaic_rasters([left, right], method="first")
    assert np.array(first.data).tolist() == [[1, 2, 20], [3, 4, 40]]
    last = mosaic_rasters([left, right], method="last")
    assert np.array(last.data).tolist() == [[1, 10, 20], [3, 30, 40]]
    mean = mosaic_rasters([left, right], method="mean")
    assert np.array(mean.data).tolist() == [[1, 6, 20], [3, 17, 40]]
    assert first.metadata["transform"] == [1.0, 0.0, 0.0, 0.0, -1.0, 2.0]


def test_mosaic_gaps_become_nodata_and_mismatches_are_refused():
    a = raster([[1]], x0=0, y0=1)
    b = raster([[2]], x0=2, y0=1)  # one empty pixel between
    out = mosaic_rasters([a, b])
    assert out.data == [[1.0, -9999.0, 2.0]] and out.metadata["nodata"] == -9999.0
    with pytest.raises(ValueError, match="pixel size"):
        mosaic_rasters([a, raster([[1]], size=2.0)])
    with pytest.raises(ValueError, match="CRS"):
        mosaic_rasters([a, raster([[1]], x0=1, y0=1, crs="EPSG:4326")])
    with pytest.raises(ValueError, match="aligned"):
        mosaic_rasters([a, raster([[1]], x0=1.5, y0=1)])
    with pytest.raises(ValueError):
        mosaic_rasters([a])


# ------------------------------------------------------------------- reproject
def test_reproject_changes_crs_and_resample_changes_resolution():
    pytest.importorskip("rasterio")
    r = raster(np.arange(16).reshape(4, 4), x0=5_000_000, y0=4_000_000, size=1000.0, crs="EPSG:3857")
    out = reproject_raster(r, target_crs="EPSG:4326")
    assert out.metadata["crs"] == "EPSG:4326"
    assert out.metadata["transform"][0] < 0.1  # degrees now
    finer = reproject_raster(r, resolution=500.0)
    assert (finer.metadata["width"], finer.metadata["height"]) == (8, 8)
    assert finer.metadata["crs"] == "EPSG:3857"
    arr = np.array(finer.data)
    assert set(np.unique(arr)) <= set(range(16))  # nearest keeps original values


def test_reproject_validation_and_limits(monkeypatch):
    pytest.importorskip("rasterio")
    r = raster([[1, 2], [3, 4]], crs="EPSG:3857")
    with pytest.raises(ValueError):
        reproject_raster(r)  # nothing to do
    with pytest.raises(ValueError, match="CRS"):
        reproject_raster(r, target_crs="not-a-crs")
    with pytest.raises(ValueError):
        reproject_raster(r, resolution=-1)
    with pytest.raises(ValueError, match="crs"):
        reproject_raster({"data": [[1]], "metadata": {"transform": [1, 0, 0, 0, -1, 1]}}, target_crs="EPSG:4326")
    with pytest.raises(ValueError, match="cell count"):
        reproject_raster(r, resolution=1e-6)


# ------------------------------------------------------------ weighted overlay
def test_weighted_overlay_benefit_cost_and_scale():
    slope = raster([[0, 10]], y0=1)  # cost: flat is better
    near_road = raster([[100, 0]], y0=1)  # cost too
    green = raster([[0, 4]], y0=1)  # benefit
    out = weighted_overlay([slope, near_road, green], [2, 1, 1], ["cost", "cost", "benefit"])
    # pixel0: slope cost 1, road cost 0, green 0 -> (2*1 + 0 + 0)/4 = 0.5 -> 50
    # pixel1: slope cost 0, road cost 1, green 1 -> (0 + 1 + 1)/4 = 0.5 -> 50
    assert np.array(out.data).round(6).tolist() == [[50.0, 50.0]]
    unit = weighted_overlay([green, green], [1, 3], scale_max=1)
    assert np.array(unit.data).tolist() == [[0.0, 1.0]]


def test_weighted_overlay_missing_policy_and_validation():
    a = raster([[1, 2]], y0=1, nodata=-9999)
    b = raster([[1, -9999]], y0=1, nodata=-9999)
    masked = weighted_overlay([a, b], [1, 1])
    assert masked.data[0][1] == -9999.0 and masked.metadata["nodata"] == -9999.0
    ignored = weighted_overlay([a, b], [1, 1], missing="ignore")
    assert ignored.data[0][1] == 100.0
    with pytest.raises(ValueError, match="weights"):
        weighted_overlay([a, b], [1])
    with pytest.raises(ValueError, match="weights"):
        weighted_overlay([a, b], [0, 0])
    with pytest.raises(ValueError, match="directions"):
        weighted_overlay([a, b], [1, 1], ["up", "down"])
    with pytest.raises(ValueError, match="shape"):
        weighted_overlay([a, raster([[1, 2, 3]], y0=1)], [1, 1])
    flat = raster([[5, 5]], y0=1)
    assert weighted_overlay([flat, flat], [1, 1]).data == [[50.0, 50.0]]
