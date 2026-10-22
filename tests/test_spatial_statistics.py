"""Tests for the spatial_statistics plugin (kernel density, clustering)."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("shapely")

from plugins.spatial_statistics import (  # noqa: E402
    calculate_kernel_density,
    cluster_features,
)


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def blob(cx, cy, n, spread=0.5):
    rng = np.random.default_rng(1)
    return [pt(cx + rng.normal(0, spread), cy + rng.normal(0, spread)) for _ in range(n)]


# ------------------------------------------------------------------------- KDE
def test_density_conserves_mass_and_peaks_at_the_point():
    out = calculate_kernel_density([pt(50, 50)], cell_size=1, bandwidth=5)
    grid = np.array(out.data)
    assert grid.sum() * 1 * 1 == pytest.approx(1.0, rel=0.03)  # integral ~ total weight
    row, col = np.unravel_index(grid.argmax(), grid.shape)
    transform = out.metadata["transform"]
    x = transform[2] + (col + 0.5) * transform[0]
    y = transform[5] + (row + 0.5) * transform[4]
    assert abs(x - 50) <= 1 and abs(y - 50) <= 1
    assert out.metadata["kernel"] == "gaussian" and out.metadata["point_count"] == 1


@pytest.mark.parametrize("kernel", ["gaussian", "quartic", "epanechnikov"])
def test_all_kernels_integrate_to_total_weight(kernel):
    points = [pt(10, 10, w=2), pt(14, 12, w=3)]
    out = calculate_kernel_density(points, 0.5, 4, weight_field="w", kernel=kernel)
    assert np.array(out.data).sum() * 0.25 == pytest.approx(5.0, rel=0.05)


def test_density_weights_bbox_and_validation():
    out = calculate_kernel_density([pt(5, 5, w=1), pt(6, 6, w="x")], 1, 2, weight_field="w",
                                   bbox=[0, 0, 10, 10])
    assert out.metadata["point_count"] == 1  # non-numeric weight skipped
    assert (out.metadata["width"], out.metadata["height"]) == (10, 10)
    for kwargs in ({"cell_size": 0, "bandwidth": 1}, {"cell_size": 1, "bandwidth": -1},
                   {"cell_size": 1, "bandwidth": 1, "kernel": "box"},
                   {"cell_size": 1, "bandwidth": 1, "bbox": [0, 0, 0, 5]}):
        with pytest.raises(ValueError):
            calculate_kernel_density([pt(1, 1)], **kwargs)
    with pytest.raises(ValueError):
        calculate_kernel_density([], 1, 1)


def test_density_is_bounded():
    with pytest.raises(ValueError, match="cell count"):
        calculate_kernel_density([pt(0, 0), pt(100000, 100000)], 1, 1)
    with pytest.raises(ValueError, match="work"):
        calculate_kernel_density([pt(i, 0) for i in range(2000)], 1, 5000, bbox=[0, 0, 100, 100])


# -------------------------------------------------------------------- clusters
def test_dbscan_finds_two_clusters_and_noise():
    features = blob(0, 0, 40) + blob(100, 100, 40) + [pt(500, 500)]
    out = cluster_features(features, "dbscan", eps=3, min_samples=5)
    ids = [f["properties"]["cluster_id"] for f in out.features]
    assert out.metadata["cluster_count"] == 2
    assert ids[-1] == -1 and out.metadata["noise_count"] >= 1
    assert len(set(ids[:40])) == 1 and len(set(ids[40:80])) == 1 and ids[0] != ids[40]
    assert out.features[0]["properties"]["cluster_size"] == ids.count(ids[0])


def test_kmeans_is_deterministic_and_separates_blobs():
    features = blob(0, 0, 30) + blob(50, 50, 30)
    first = cluster_features(features, "kmeans", k=2, seed=3)
    second = cluster_features(features, "kmeans", k=2, seed=3)
    assert [f["properties"] for f in first.features] == [f["properties"] for f in second.features]
    ids = [f["properties"]["cluster_id"] for f in first.features]
    assert len(set(ids[:30])) == 1 and len(set(ids[30:])) == 1 and ids[0] != ids[30]
    assert "distance_to_center" in first.features[0]["properties"]


def test_cluster_keeps_features_without_geometry_and_validates():
    features = [pt(0, 0), {"type": "Feature", "properties": {"n": 1}, "geometry": None}, pt(0.1, 0)]
    out = cluster_features(features, "dbscan", eps=1, min_samples=2)
    assert out.features[1]["properties"]["cluster_id"] is None
    for kwargs in ({"method": "dbscan"}, {"method": "dbscan", "eps": -1},
                   {"method": "kmeans"}, {"method": "kmeans", "k": 99}, {"method": "spectral"},
                   {"method": "dbscan", "eps": 1, "min_samples": 0}):
        with pytest.raises(ValueError):
            cluster_features([pt(0, 0), pt(1, 1)], **kwargs)


def test_dbscan_point_limit(monkeypatch):
    import plugins.spatial_statistics as module

    monkeypatch.setattr(module, "load_config", lambda _id: {"max_dbscan_points": 2})
    with pytest.raises(ValueError, match="DBSCAN"):
        cluster_features([pt(0, 0), pt(1, 1), pt(2, 2)], "dbscan", eps=1)
