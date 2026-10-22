"""spatial_pattern_analysis: Gi* hot spots, Moran's I, IDW, H3 aggregation."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("shapely")

import plugins.spatial_pattern_analysis as module  # noqa: E402
from plugins.spatial_pattern_analysis import (  # noqa: E402
    aggregate_to_h3,
    calculate_hotspots,
    calculate_spatial_autocorrelation,
    interpolate_idw,
)


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def lattice(n, fn):
    return [pt(c, r, v=fn(r, c)) for r in range(n) for c in range(n)]


def hot_corner(n=10):
    return lattice(n, lambda r, c: 100.0 if r < 3 and c < 3 else 1.0 + 0.1 * ((r * 7 + c * 3) % 5))


# --------------------------------------------------------------------- hotspots
def test_hotspot_found_where_high_values_cluster():
    out = calculate_hotspots(hot_corner(), "v", "knn", k=8)
    by_pos = {(f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]): f["properties"]
              for f in out.features}
    assert by_pos[(1, 1)]["gi_bin"] >= 2 and by_pos[(1, 1)]["gi_z"] > 1.96
    assert by_pos[(1, 1)]["gi_p"] < 0.05 and "Hot spot" in by_pos[(1, 1)]["gi_label"]
    assert by_pos[(8, 8)]["gi_bin"] <= 0 and by_pos[(8, 8)]["gi_bin"] > -3
    assert out.metadata["analysed_features"] == 100 and out.metadata["multiple_testing_correction"] == "none"
    assert sum(out.metadata["bin_counts"].values()) == 100


def test_cold_spot_distance_band_and_missing_values():
    cold = lattice(10, lambda r, c: 0.0 if r > 6 and c > 6 else 50.0 + (r + c) % 4)
    out = calculate_hotspots(cold, "v", "distance_band", distance=1.5)
    assert min(f["properties"]["gi_bin"] for f in out.features) <= -2
    data = hot_corner() + [pt(99, 99, v=None), pt(98, 98, v="n/a")]
    result = calculate_hotspots(data, "v")
    assert result.features[-1]["properties"]["gi_bin"] is None
    assert result.metadata["analysed_features"] == 100


def test_hotspot_validation():
    with pytest.raises(ValueError, match="at least 8"):
        calculate_hotspots([pt(i, i, v=i) for i in range(5)], "v")
    with pytest.raises(ValueError, match="constant"):
        calculate_hotspots(lattice(4, lambda r, c: 7.0), "v")
    with pytest.raises(ValueError, match="distance"):
        calculate_hotspots(hot_corner(), "v", "distance_band")
    for kwargs in ({"weights": "queen"}, {"k": 0}, {"k": 500}, {"field": ""}):
        with pytest.raises(ValueError):
            calculate_hotspots(hot_corner(), **{"field": "v", **kwargs})


def test_weight_links_are_bounded(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_links": 50})
    with pytest.raises(ValueError, match="neighbour links"):
        calculate_hotspots(hot_corner(), "v", "knn", k=8)
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_features": 20})
    with pytest.raises(ValueError, match="feature count"):
        calculate_hotspots(hot_corner(), "v")


# ----------------------------------------------------------------------- Moran
def brute_force_moran(points, k):
    xy = np.array([[p["geometry"]["coordinates"][0], p["geometry"]["coordinates"][1]] for p in points])
    v = np.array([p["properties"]["v"] for p in points], dtype=float)
    n = len(v)
    d = np.linalg.norm(xy[:, None] - xy[None], axis=-1)
    np.fill_diagonal(d, np.inf)
    w = np.zeros((n, n))
    for i in range(n):
        w[i, np.argsort(d[i])[:k]] = 1.0 / k
    z = v - v.mean()
    return (n / w.sum()) * (z @ w @ z) / (z @ z)


def test_morans_i_matches_a_brute_force_computation():
    rng = np.random.default_rng(3)
    data = [pt(float(x), float(y), v=float(v)) for x, y, v in
            zip(rng.uniform(0, 10, 40), rng.uniform(0, 10, 40), rng.normal(size=40), strict=True)]
    out = calculate_spatial_autocorrelation(data, "v", "knn", k=5)
    assert out["moran_i"] == pytest.approx(brute_force_moran(data, 5), abs=1e-9)
    assert out["expected_i"] == pytest.approx(-1 / 39)


def test_morans_i_detects_clustering_dispersion_and_randomness():
    gradient = calculate_spatial_autocorrelation(lattice(10, lambda r, c: r + c), "v", k=4)
    assert gradient["moran_i"] > 0.7 and gradient["p_value"] < 0.001 and "clustered" in gradient["interpretation"]
    checker = calculate_spatial_autocorrelation(lattice(10, lambda r, c: (r + c) % 2), "v", k=4)
    assert checker["moran_i"] < -0.5 and "dispersed" in checker["interpretation"]
    rng = np.random.default_rng(11)
    noise = calculate_spatial_autocorrelation(lattice(12, lambda r, c: float(rng.normal())), "v", k=8)
    assert abs(noise["z_score"]) < 2.5 and abs(noise["moran_i"]) < 0.25
    for key in ("moran_i", "expected_i", "variance", "z_score", "p_value"):
        assert math.isfinite(gradient[key])
    assert gradient["type"] == "spatial_autocorrelation" and gradient["row_standardized"] is True


def test_morans_validation():
    with pytest.raises(ValueError, match="constant"):
        calculate_spatial_autocorrelation(lattice(4, lambda r, c: 1.0), "v")
    with pytest.raises(ValueError, match="at least 8"):
        calculate_spatial_autocorrelation([pt(0, 0, v=1)], "v")
    isolated = [pt(i * 100, 0, v=float(i)) for i in range(10)]
    with pytest.raises(ValueError, match="(?i)no feature has a neighbour"):
        calculate_spatial_autocorrelation(isolated, "v", "distance_band", distance=1)


# ------------------------------------------------------------------------- IDW
def test_idw_exact_at_samples_bounded_and_symmetric():
    samples = [pt(0, 0, t=10.0), pt(10, 0, t=30.0), pt(5, 8, t=20.0)]
    out = interpolate_idw(samples, "t", 0.5, bbox=[0, 0, 10, 10])
    grid = np.array(out.data)
    assert grid.min() >= 10 - 1e-9 and grid.max() <= 30 + 1e-9
    assert out.metadata["transform"] == [0.5, 0.0, 0.0, 0.0, -0.5, 10.0]
    # on-sample pixels: the pixel centre nearest (0, 0) is (0.25, 9.75) -> row 19, col 0
    assert grid[19, 0] == pytest.approx(10.0, abs=0.5)
    two = interpolate_idw([pt(0, 0, t=0.0), pt(10, 0, t=100.0)], "t", 1.0, bbox=[0, -1, 10, 1])
    middle = np.array(two.data)[:, 4:6]  # x = 4.5 and 5.5 are symmetric around the midpoint
    assert middle.mean() == pytest.approx(50.0, abs=1e-6)


def test_idw_power_neighbors_radius_and_validation():
    samples = [pt(0, 0, t=0.0), pt(10, 0, t=100.0)]
    soft = np.array(interpolate_idw(samples, "t", 1.0, power=1, bbox=[0, -1, 10, 1]).data)[0, 2]
    sharp = np.array(interpolate_idw(samples, "t", 1.0, power=4, bbox=[0, -1, 10, 1]).data)[0, 2]
    assert 0 < sharp < soft < 50  # a higher power trusts the near sample more
    near = np.array(interpolate_idw(samples, "t", 1.0, neighbors=1, bbox=[0, -1, 10, 1]).data)
    assert set(np.unique(near)) == {0.0, 100.0}
    ranged = interpolate_idw(samples, "t", 1.0, search_radius=2.0, bbox=[0, -1, 10, 1])
    assert -9999.0 in np.array(ranged.data) and ranged.metadata["nodata"] == -9999.0
    for kwargs in ({"cell_size": 0}, {"power": 0}, {"neighbors": 0}, {"search_radius": -1},
                   {"bbox": [0, 0, 0, 5]}, {"bbox": [1, 2, 3]}):
        with pytest.raises(ValueError):
            interpolate_idw(samples, "t", **{"cell_size": 1.0, "bbox": [0, -1, 10, 1], **kwargs})
    with pytest.raises(ValueError, match="at least 2"):
        interpolate_idw([pt(0, 0, t=1.0)], "t", 1.0, bbox=[0, 0, 5, 5])


def test_idw_is_bounded(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_cells": 50})
    with pytest.raises(ValueError, match="cell count"):
        interpolate_idw([pt(0, 0, t=1.0), pt(10, 10, t=2.0)], "t", 1.0)
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_work": 100})
    with pytest.raises(ValueError, match="work"):
        interpolate_idw([pt(0, 0, t=1.0), pt(10, 10, t=2.0)], "t", 1.0)


# -------------------------------------------------------------------------- H3
def test_h3_aggregation_counts_and_stats():
    pytest.importorskip("h3")
    import h3

    data = [pt(51.389, 35.689, w=2.0), pt(51.3891, 35.6891, w=4.0), pt(51.60, 35.80, w=10.0),
            {"type": "Feature", "properties": {"w": 1}, "geometry": None}]
    out = aggregate_to_h3(data, 9, value_field="w", stats=["sum", "mean", "max"])
    assert out.metadata["skipped_without_geometry"] == 1 and out.metadata["point_count"] == 3
    assert sum(f["properties"]["count"] for f in out.features) == 3
    big = max(out.features, key=lambda f: f["properties"]["count"])
    assert big["properties"]["count"] == 2 and big["properties"]["w_sum"] == 6.0
    assert big["properties"]["w_mean"] == 3.0 and big["properties"]["w_max"] == 4.0
    assert h3.is_valid_cell(big["properties"]["h3"])
    ring = big["geometry"]["coordinates"][0]
    assert len(ring) == 7 and ring[0] == ring[-1]
    lon, lat = h3.cell_to_latlng(big["properties"]["h3"])[1], h3.cell_to_latlng(big["properties"]["h3"])[0]
    assert abs(lon - 51.389) < 0.01 and abs(lat - 35.689) < 0.01


def test_h3_validation():
    pytest.importorskip("h3")
    data = [pt(51.4, 35.7, w=1.0)]
    for kwargs in ({"resolution": 13}, {"resolution": -1}, {"resolution": 8, "stats": ["sum"]},
                   {"resolution": 8, "value_field": "w", "stats": ["median"]}):
        with pytest.raises(ValueError):
            aggregate_to_h3(data, **kwargs)
    with pytest.raises(ValueError, match="lon/lat"):
        aggregate_to_h3([pt(500000, 3950000)], 8)
