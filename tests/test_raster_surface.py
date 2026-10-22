"""raster_surface: hillshade, contours, focal statistics, change detection, raster_to_points."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("shapely")
from shapely.geometry import shape  # noqa: E402

import plugins.raster_surface as module  # noqa: E402
from plugins.raster_surface import (  # noqa: E402
    calculate_focal_statistics,
    calculate_hillshade,
    detect_raster_change,
    generate_contours,
    raster_to_points,
)


def raster(data, x0=0.0, y_top=None, size=1.0, crs="EPSG:3857", nodata=None):
    data = np.asarray(data, dtype=float)
    top = data.shape[-2] * size if y_top is None else y_top
    return {"data": data.tolist(),
            "metadata": {"crs": crs, "transform": [size, 0, x0, 0, -size, top], "nodata": nodata}}


def grid(rows, cols, fn):
    return np.array([[fn(r, c) for c in range(cols)] for r in range(rows)], dtype=float)


# ----------------------------------------------------------------- hillshade
def test_hillshade_flat_and_sloped_surfaces():
    flat = calculate_hillshade(raster(np.zeros((5, 5))), altitude=45)
    assert np.allclose(np.array(flat.data), 255 * math.sin(math.radians(45)))
    east_down = raster(grid(9, 9, lambda r, c: -c * 10.0))  # slopes down to the east
    lit_from_east = np.array(calculate_hillshade(east_down, azimuth=90).data)[4, 4]
    lit_from_west = np.array(calculate_hillshade(east_down, azimuth=270).data)[4, 4]
    assert lit_from_east > 150 > 100 > lit_from_west  # east-facing slope is bright with a sun in the east
    faces_north = raster(grid(9, 9, lambda r, c: r * 10.0))  # row grows southwards: rises to the south
    assert np.array(calculate_hillshade(faces_north, azimuth=0).data)[4, 4] > 150 > 100 > \
        np.array(calculate_hillshade(faces_north, azimuth=180).data)[4, 4]


def test_hillshade_nodata_geographic_units_and_validation():
    data = grid(5, 5, lambda r, c: c * 5.0)
    data[2, 2] = -9999
    out = calculate_hillshade(raster(data, nodata=-9999))
    assert np.array(out.data)[2, 2] == -9999 and out.metadata["nodata"] == -9999
    metric = calculate_hillshade(raster(grid(5, 5, lambda r, c: c * 50.0), size=1000.0), azimuth=90)
    degrees = calculate_hillshade(raster(grid(5, 5, lambda r, c: c * 50.0), size=0.009, crs="EPSG:4326",
                                         y_top=35.0), azimuth=90)
    assert np.array(degrees.data)[2, 2] == pytest.approx(np.array(metric.data)[2, 2], abs=5)
    for kwargs in ({"altitude": 91}, {"z_factor": 0}, {"band_index": 2}):
        with pytest.raises(ValueError):
            calculate_hillshade(raster(np.zeros((3, 3))), **kwargs)


# ------------------------------------------------------------------- contours
def test_contours_of_a_cone_are_closed_rings_at_the_right_radius():
    cone = grid(41, 41, lambda r, c: 20 - math.hypot(r - 20, c - 20))
    out = generate_contours(raster(cone), levels=[5, 10, 15])
    assert sorted({f["properties"]["level"] for f in out.features}) == [5.0, 10.0, 15.0]
    for feature in out.features:
        line = shape(feature["geometry"])
        assert line.geom_type == "LineString" and line.is_closed
        expected = 20 - feature["properties"]["level"]
        assert line.length == pytest.approx(2 * math.pi * expected, rel=0.03)
    by_interval = generate_contours(raster(cone), interval=5)
    assert {f["properties"]["level"] for f in by_interval.features} >= {5.0, 10.0, 15.0}
    assert by_interval.metadata["data_max"] == 20.0


def test_contours_slope_levels_and_nodata():
    ramp = raster(grid(4, 6, lambda r, c: c * 1.0))
    out = generate_contours(ramp, levels=[1.5, 3.5])
    assert [f["properties"]["level"] for f in out.features] == [1.5, 3.5]
    xs = {round(x, 6) for f in out.features for x, _ in shape(f["geometry"]).coords}
    assert xs == {2.0, 4.0}  # pixel centres at 0.5, 1.5, ...: level 1.5 -> x = 2.0
    holed = grid(4, 6, lambda r, c: c * 1.0)
    holed[:, 3] = np.nan
    assert generate_contours(raster(holed), levels=[2.5]).features == []  # the crossing cells are masked
    for kwargs in ({}, {"interval": 1, "levels": [1]}, {"interval": 0}, {"levels": []},
                   {"interval": 0.001}):
        with pytest.raises(ValueError):
            generate_contours(ramp, **kwargs)


# -------------------------------------------------------------------- focal
def test_focal_statistics_values_edges_and_nodata():
    data = grid(5, 5, lambda r, c: r * 5 + c + 1.0)
    mean = np.array(calculate_focal_statistics(raster(data), "mean", 1).data)
    assert mean[2, 2] == pytest.approx(data[1:4, 1:4].mean())
    assert mean[0, 0] == pytest.approx(data[0:2, 0:2].mean())  # edge windows use the cells that exist
    assert np.array(calculate_focal_statistics(raster(data), "max", 1).data)[2, 2] == data[3, 3]
    assert np.array(calculate_focal_statistics(raster(data), "range", 1).data)[2, 2] == data[3, 3] - data[1, 1]
    assert np.array(calculate_focal_statistics(raster(data), "sum", 1, "circle").data)[2, 2] == \
        data[2, 2] + data[1, 2] + data[3, 2] + data[2, 1] + data[2, 3]
    gap = data.copy()
    gap[2, 2] = np.nan
    assert np.array(calculate_focal_statistics(raster(gap), "mean", 1).data)[2, 2] == \
        pytest.approx((data[1:4, 1:4].sum() - data[2, 2]) / 8)
    for kwargs in ({"statistic": "mode"}, {"radius": 0}, {"radius": 51}, {"shape": "hex"}):
        with pytest.raises(ValueError):
            calculate_focal_statistics(raster(data), **kwargs)


def test_focal_work_limit(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_work": 100})
    with pytest.raises(ValueError, match="work"):
        calculate_focal_statistics(raster(np.zeros((10, 10))), "mean", 2)


# -------------------------------------------------------------------- change
def test_change_detection_methods_and_classes():
    before = raster([[1.0, 2.0, 0.0, 10.0]], y_top=1)
    after = raster([[2.0, 2.0, 5.0, 5.0]], y_top=1)
    diff = detect_raster_change(before, after)
    assert diff.data == [[1.0, 0.0, 5.0, -5.0]] and diff.metadata["mean_change"] == pytest.approx(0.25)
    ratio = detect_raster_change(before, after, "ratio")
    assert ratio.data[0][:2] == [2.0, 1.0] and ratio.data[0][2] == -9999.0  # divide by zero -> nodata
    pct = detect_raster_change(before, after, "percent_change")
    assert pct.data[0][0] == 100.0 and pct.data[0][3] == -50.0
    classes = detect_raster_change(before, after, threshold=0.5)
    assert classes.data == [[1.0, 0.0, 1.0, -1.0]]
    assert (classes.metadata["increase_pixels"], classes.metadata["decrease_pixels"],
            classes.metadata["stable_pixels"]) == (2, 1, 1)
    ratio_classes = detect_raster_change(before, after, "ratio", threshold=0.5)
    assert ratio_classes.data[0][1] == 0.0  # no change = ratio 1


def test_change_detection_requires_matching_grids():
    a = raster([[1.0, 2.0]], y_top=1)
    with pytest.raises(ValueError, match="same shape"):
        detect_raster_change(a, raster([[1.0, 2.0, 3.0]], y_top=1))
    with pytest.raises(ValueError, match="same shape"):
        detect_raster_change(a, raster([[1.0, 2.0]], x0=5.0, y_top=1))
    with pytest.raises(ValueError, match="CRS"):
        detect_raster_change(a, raster([[1.0, 2.0]], y_top=1, crs="EPSG:4326"))
    with pytest.raises(ValueError):
        detect_raster_change(a, a, threshold=-1)
    with pytest.raises(ValueError):
        detect_raster_change(a, a, method="xor")


# ------------------------------------------------------------------ to points
def test_raster_to_points_positions_filters_and_limits(monkeypatch):
    data = [[1.0, 2.0, -9999.0], [4.0, 5.0, 6.0]]
    out = raster_to_points(raster(data, x0=10.0, y_top=20.0, size=2.0, nodata=-9999))
    assert len(out.features) == 5  # nodata skipped
    first = out.features[0]
    assert first["geometry"]["coordinates"] == [11.0, 19.0] and first["properties"]["value"] == 1.0
    assert out.features[-1]["geometry"]["coordinates"] == [15.0, 17.0]
    filtered = raster_to_points(raster(data, nodata=-9999), min_value=2, max_value=5)
    assert sorted(f["properties"]["value"] for f in filtered.features) == [2.0, 4.0, 5.0]
    thinned = raster_to_points(raster(np.arange(16).reshape(4, 4)), step=2, value_field="v")
    assert [f["properties"]["v"] for f in thinned.features] == [0.0, 2.0, 8.0, 10.0]
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_points": 3})
    with pytest.raises(ValueError, match="point count"):
        raster_to_points(raster(data, nodata=-9999))
    for kwargs in ({"step": 0}, {"value_field": ""}, {"band_index": 3}):
        with pytest.raises(ValueError):
            raster_to_points(raster(data), **kwargs)
