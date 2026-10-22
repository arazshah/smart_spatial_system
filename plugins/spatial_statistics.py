"""
spatial_statistics.py

GeoChat SDK Plugin
==================

Plugin ID:
    spatial_statistics

Capabilities:
    - calculate_kernel_density:
        Kernel density surface (heat map) of points as a raster, optionally
        weighted by a numeric attribute.
    - cluster_features:
        Group features into spatial clusters with DBSCAN (density based, finds
        the number of clusters itself and marks noise) or k-means.

Config-aware behavior:
    Reads config/plugins/spatial_statistics.yaml. All sizes are bounded: a
    request that would exceed the configured grid-cell, point or work limits
    fails fast with a message saying how to shrink it.

Important:
    Distances (bandwidth, eps, cell_size) are in coordinate units; use a
    projected CRS for metric results. Non-point geometries are represented by
    their representative point. Results are deterministic (k-means uses a seed).
"""

from __future__ import annotations

import math
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.raster_common import make_raster_out
from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    get_shapely,
    load_config,
    make_vector_out,
    to_float,
    to_int,
    utc_now_iso,
    validate_choice,
)

PLUGIN_ID = "spatial_statistics"

KERNELS = {"gaussian", "quartic", "epanechnikov"}
METHODS = {"dbscan", "kmeans"}
DEFAULTS = {
    "max_cells": 1_000_000,
    "max_points": 200_000,
    "max_work": 200_000_000,  # points x kernel-window cells
    "max_dbscan_points": 20_000,
    "max_clusters": 1_000,
}


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - numpy is a base dependency
        raise SDKDependencyError("spatial_statistics requires numpy: pip install numpy") from exc
    return np


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _points_of(features: list[dict[str, Any]], weight_field: str | None):
    """Return (xs, ys, weights, feature_indices) using representative points."""
    np = _numpy()
    shape, _, _ = get_shapely(PLUGIN_ID)
    xs: list[float] = []
    ys: list[float] = []
    weights: list[float] = []
    indices: list[int] = []
    for index, feature in enumerate(features):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shape(geometry)
        if geom.is_empty:
            continue
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        weight = 1.0
        if weight_field:
            raw = (feature.get("properties") or {}).get(weight_field)
            try:
                weight = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(weight) or weight < 0:
                continue
        xs.append(point.x)
        ys.append(point.y)
        weights.append(weight)
        indices.append(index)
    return np.array(xs), np.array(ys), np.array(weights), indices


def _kernel_values(kernel: str, d2, h: float):
    np = _numpy()
    if kernel == "gaussian":
        return np.exp(-d2 / (2.0 * h * h)) / (2.0 * math.pi * h * h)
    u2 = np.clip(d2 / (h * h), 0.0, 1.0)
    if kernel == "quartic":
        return np.where(d2 < h * h, 3.0 / (math.pi * h * h) * (1.0 - u2) ** 2, 0.0)
    return np.where(d2 < h * h, 2.0 / (math.pi * h * h) * (1.0 - u2), 0.0)


@capability(
    name="calculate_kernel_density",
    keywords=[
        "kernel density", "heatmap", "heat map", "density surface", "hotspot map", "point density",
        "kde", "density of points",
        "تراکم کرنل", "نقشه حرارتی", "نقشه تراکم", "هیت مپ", "تراکم نقاط", "سطح تراکم",
    ],
    description=(
        "Kernel density estimation: turn points (optionally weighted by an attribute) into a "
        "density raster / heat map using a gaussian, quartic or epanechnikov kernel."
    ),
    required_inputs=["points", "cell_size", "bandwidth"],
    optional_inputs=["weight_field", "kernel", "bbox", "crs", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "kernel_density",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def calculate_kernel_density(
    points: Any,
    cell_size: float,
    bandwidth: float,
    weight_field: str | None = None,
    kernel: str = "gaussian",
    bbox: list[float] | None = None,
    crs: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Compute a kernel density raster.

    Args:
        points: Point features.
        cell_size: Output pixel size (coordinate units).
        bandwidth: Gaussian sigma, or the radius of the quartic/epanechnikov kernel.
            The gaussian is truncated at 3 x bandwidth.
        weight_field: Numeric attribute used as weight (non-numeric/negative are skipped).
        kernel: gaussian | quartic | epanechnikov.
        bbox: [minx, miny, maxx, maxy] output extent; default is the point extent
            padded by the kernel radius.
        crs: CRS label stored in the raster metadata.
        metadata: Extra output metadata.

    Returns:
        RasterOut (single band, north-up) with value = weighted points per unit area.
    """
    np = _numpy()
    config = load_config(PLUGIN_ID)
    size = to_float(cell_size, "cell_size")
    h = to_float(bandwidth, "bandwidth")
    if size <= 0 or h <= 0 or not math.isfinite(size) or not math.isfinite(h):
        raise ValueError("cell_size and bandwidth must be positive numbers.")
    kind = validate_choice(kernel, KERNELS, "kernel")
    radius = 3.0 * h if kind == "gaussian" else h

    features, source_info = extract_features(points)
    check_limit(len(features), _limit(config, "max_points"), "point count")
    xs, ys, ws, _ = _points_of(features, weight_field)
    if xs.size == 0:
        raise ValueError("No usable point geometries (or weights) found.")

    if bbox is not None:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("bbox must be [minx, miny, maxx, maxy].")
        minx, miny, maxx, maxy = (to_float(v, "bbox") for v in bbox)
        if not (minx < maxx and miny < maxy):
            raise ValueError("bbox must satisfy minx < maxx and miny < maxy.")
    else:
        minx, miny = float(xs.min()) - radius, float(ys.min()) - radius
        maxx, maxy = float(xs.max()) + radius, float(ys.max()) + radius

    width = max(1, math.ceil((maxx - minx) / size))
    height = max(1, math.ceil((maxy - miny) / size))
    check_limit(width * height, _limit(config, "max_cells"), "output cell count (raise cell_size)")
    half = math.ceil(radius / size)
    check_limit(
        int(xs.size) * (2 * half + 1) ** 2,
        _limit(config, "max_work"),
        "estimated work (points x kernel window; raise cell_size or lower bandwidth)",
    )

    grid = np.zeros((height, width), dtype=np.float64)
    top = maxy
    for x, y, w in zip(xs, ys, ws, strict=True):
        col = int((x - minx) // size)
        row = int((top - y) // size)
        c0, c1 = max(0, col - half), min(width, col + half + 1)
        r0, r1 = max(0, row - half), min(height, row + half + 1)
        if c0 >= c1 or r0 >= r1:
            continue
        cx = minx + (np.arange(c0, c1) + 0.5) * size
        cy = top - (np.arange(r0, r1) + 0.5) * size
        d2 = (cx[None, :] - x) ** 2 + (cy[:, None] - y) ** 2
        grid[r0:r1, c0:c1] += w * np.where(d2 <= radius * radius, _kernel_values(kind, d2, h), 0.0)

    raster_metadata = {
        "source": PLUGIN_ID,
        "operation": "kernel_density",
        "transform": [size, 0.0, minx, 0.0, -size, top],
        "width": width,
        "height": height,
        "band_count": 1,
        "nodata": None,
        "crs": crs,
        "kernel": kind,
        "bandwidth": h,
        "cell_size": size,
        "point_count": int(xs.size),
        "total_weight": float(ws.sum()),
        "max_density": float(grid.max()),
        "weight_field": weight_field,
        "created_at": utc_now_iso(),
        **source_info,
        **(metadata or {}),
    }
    return make_raster_out(grid.tolist(), raster_metadata)


def _dbscan(xs, ys, eps: float, min_samples: int):
    np = _numpy()
    n = xs.size
    cells: dict[tuple[int, int], list[int]] = {}
    keys = np.floor(np.column_stack((xs, ys)) / eps).astype(np.int64)
    for i in range(n):
        cells.setdefault((int(keys[i, 0]), int(keys[i, 1])), []).append(i)

    def neighbours(i: int):
        kx, ky = int(keys[i, 0]), int(keys[i, 1])
        candidates: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(cells.get((kx + dx, ky + dy), ()))
        cand = np.array(candidates)
        d2 = (xs[cand] - xs[i]) ** 2 + (ys[cand] - ys[i]) ** 2
        return cand[d2 <= eps * eps]

    labels = np.full(n, -1, dtype=np.int64)
    visited = np.zeros(n, dtype=bool)
    cluster = 0
    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        near = neighbours(i)
        if near.size < min_samples:
            continue  # noise for now; may become a border point later
        labels[i] = cluster
        queue = list(near)
        while queue:
            j = queue.pop()
            if labels[j] == -1:
                labels[j] = cluster
            if visited[j]:
                continue
            visited[j] = True
            near_j = neighbours(j)
            if near_j.size >= min_samples:
                queue.extend(int(q) for q in near_j if not visited[q] or labels[q] == -1)
        cluster += 1
    return labels, cluster


def _kmeans(xs, ys, k: int, max_iterations: int, seed: int):
    np = _numpy()
    rng = np.random.default_rng(seed)
    data = np.column_stack((xs, ys))
    n = len(data)
    centers = np.empty((k, 2))
    centers[0] = data[rng.integers(n)]
    closest = ((data - centers[0]) ** 2).sum(axis=1)
    for c in range(1, k):  # k-means++ seeding
        total = closest.sum()
        index = rng.integers(n) if total == 0 else rng.choice(n, p=closest / total)
        centers[c] = data[index]
        closest = np.minimum(closest, ((data - centers[c]) ** 2).sum(axis=1))
    labels = np.zeros(n, dtype=np.int64)
    for _ in range(max_iterations):
        d2 = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        new_labels = d2.argmin(axis=1)
        for c in range(k):
            members = data[new_labels == c]
            if len(members):
                centers[c] = members.mean(axis=0)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
    d2 = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    labels = d2.argmin(axis=1)
    return labels, centers, np.sqrt(d2[np.arange(n), labels])


@capability(
    name="cluster_features",
    keywords=[
        "cluster", "clustering", "dbscan", "kmeans", "k-means", "hotspot groups", "group nearby",
        "خوشه بندی", "خوشه", "دسته بندی مکانی", "کلاستر", "نقاط نزدیک به هم", "دی بی اسکن",
    ],
    description=(
        "Spatial clustering of features: DBSCAN (density based, marks noise as -1) or k-means. "
        "Adds cluster_id and cluster_size to every feature."
    ),
    required_inputs=["features"],
    optional_inputs=["method", "eps", "min_samples", "k", "max_iterations", "seed", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "cluster",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def cluster_features(
    features: Any,
    method: str = "dbscan",
    eps: float | None = None,
    min_samples: int = 5,
    k: int | None = None,
    max_iterations: int = 100,
    seed: int = 0,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Cluster features by location.

    Args:
        features: Input features (non-points use their representative point).
        method: ``dbscan`` or ``kmeans``.
        eps: DBSCAN neighbourhood radius (coordinate units); required for dbscan.
        min_samples: DBSCAN minimum neighbours (including the point itself) for a core point.
        k: Number of clusters for kmeans; required for kmeans.
        max_iterations: kmeans iteration cap.
        seed: kmeans seed; the same input and seed always give the same result.
        metadata: Extra output metadata.

    Returns:
        VectorOut with ``cluster_id`` (DBSCAN noise = -1) and ``cluster_size``;
        kmeans also adds ``distance_to_center``. Features without geometry get
        ``cluster_id`` null.
    """
    np = _numpy()
    config = load_config(PLUGIN_ID)
    chosen = validate_choice(method, METHODS, "method")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_points"), "feature count")
    xs, ys, _, indices = _points_of(input_features, None)
    if xs.size == 0:
        raise ValueError("No usable geometries to cluster.")

    extra: dict[str, Any] = {"method": chosen}
    distances = None
    if chosen == "dbscan":
        check_limit(
            int(xs.size), _limit(config, "max_dbscan_points"),
            "DBSCAN point count (use kmeans or filter first)",
        )
        radius = to_float(eps, "eps") if eps is not None else None
        if radius is None or radius <= 0 or not math.isfinite(radius):
            raise ValueError("eps must be a positive number for dbscan.")
        samples = to_int(min_samples, "min_samples")
        if samples < 1:
            raise ValueError("min_samples must be >= 1.")
        labels, cluster_count = _dbscan(xs, ys, radius, samples)
        extra.update(eps=radius, min_samples=samples, cluster_count=cluster_count,
                     noise_count=int((labels == -1).sum()))
    else:
        clusters = to_int(k, "k") if k is not None else None
        if clusters is None or clusters < 1:
            raise ValueError("k must be a positive integer for kmeans.")
        check_limit(clusters, _limit(config, "max_clusters"), "k")
        if clusters > xs.size:
            raise ValueError(f"k ({clusters}) cannot exceed the number of usable features ({xs.size}).")
        iterations = to_int(max_iterations, "max_iterations")
        if not 1 <= iterations <= 1000:
            raise ValueError("max_iterations must be between 1 and 1000.")
        labels, centers, distances = _kmeans(xs, ys, clusters, iterations, to_int(seed, "seed"))
        extra.update(k=clusters, seed=to_int(seed, "seed"), cluster_count=clusters,
                     centers=[[float(c[0]), float(c[1])] for c in centers])

    sizes = {int(c): int((labels == c).sum()) for c in np.unique(labels)}
    by_feature = {index: pos for pos, index in enumerate(indices)}
    output: list[dict[str, Any]] = []
    for index, feature in enumerate(input_features):
        properties = dict(feature.get("properties") or {})
        pos = by_feature.get(index)
        if pos is None:
            properties.update(cluster_id=None, cluster_size=None)
        else:
            label = int(labels[pos])
            properties.update(cluster_id=label, cluster_size=sizes[label])
            if distances is not None:
                properties["distance_to_center"] = float(distances[pos])
        output.append({"type": "Feature", "geometry": feature.get("geometry"),
                       "properties": properties})

    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="cluster", source_info=source_info,
        extra=extra, user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Spatial Statistics",
    description="Kernel density surfaces and spatial clustering (DBSCAN, k-means).",
    author="GeoChat Platform Team",
    permissions=[],
)
