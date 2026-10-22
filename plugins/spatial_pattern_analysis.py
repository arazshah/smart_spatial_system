"""
spatial_pattern_analysis.py

GeoChat SDK Plugin
==================

Plugin ID:
    spatial_pattern_analysis

Capabilities:
    - calculate_hotspots:       Getis-Ord Gi* hot/cold spot analysis of a numeric attribute.
    - calculate_spatial_autocorrelation: global Moran's I (is the attribute clustered, dispersed
                                or random?).
    - interpolate_idw:          inverse-distance-weighted surface from sample points (raster).
    - aggregate_to_h3:          points -> H3 hexagon counts/statistics (needs the ``h3`` package).

Spatial weights (hotspots, Moran): ``knn`` (the k nearest features, default 8) or
``distance_band`` (all features within a distance, in coordinate units). Non-point geometries are
represented by their representative point.

Statistics notes:
    Gi* uses binary weights including the feature itself and the usual closed-form z-score;
    p-values are two-sided from the normal distribution WITHOUT multiple-testing (FDR) correction,
    so with many features treat 90 % bins as exploratory. Moran's I uses the normality-assumption
    variance.

Config-aware behavior:
    Reads config/plugins/spatial_pattern_analysis.yaml; feature counts, neighbour links and
    interpolation work are bounded.
"""

from __future__ import annotations

import math
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.raster_common import DEFAULT_NODATA, make_raster_out, numpy_module
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
from plugins.csv_xy_loader import parse_number

PLUGIN_ID = "spatial_pattern_analysis"

DEFAULTS = {
    "max_features": 20_000,
    "max_links": 5_000_000,
    "max_work": 200_000_000,
    "max_cells": 1_000_000,
    "max_h3_points": 500_000,
}
_META = {
    "category": "analysis",
    "data_type": "vector",
    "access_scope": "spatial_analysis",
    "config_aware": True,
    "routable": True,
}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def _located_values(features: list[dict[str, Any]], field: str):
    """Return (indices, xs, ys, values) for features with a geometry and a numeric ``field``."""
    np = numpy_module()
    shape = get_shapely(PLUGIN_ID)[0]
    idx, xs, ys, vals = [], [], [], []
    for index, feature in enumerate(features):
        geometry = feature.get("geometry")
        value = parse_number((feature.get("properties") or {}).get(field))
        if not geometry or value is None:
            continue
        geom = shape(geometry)
        if geom.is_empty:
            continue
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        idx.append(index)
        xs.append(point.x)
        ys.append(point.y)
        vals.append(value)
    return idx, np.array(xs), np.array(ys), np.array(vals)


def _neighbours(xs, ys, mode: str, k: int, distance: float | None, max_links: int,
                include_self: bool) -> list[Any]:
    """Neighbour index arrays per feature (chunked pairwise distances, bounded links)."""
    np = numpy_module()
    n = len(xs)
    result: list[Any] = []
    links = 0
    chunk = max(1, 2_000_000 // max(n, 1))
    for start in range(0, n, chunk):
        stop = min(n, start + chunk)
        d2 = (xs[start:stop, None] - xs[None, :]) ** 2 + (ys[start:stop, None] - ys[None, :]) ** 2
        rows = np.arange(start, stop)
        d2[np.arange(stop - start), rows] = np.inf  # exclude self first
        for local in range(stop - start):
            row = d2[local]
            if mode == "knn":
                kk = min(k, n - 1)
                picked = np.argpartition(row, kk - 1)[:kk] if kk > 0 else np.array([], dtype=int)
            else:
                picked = np.nonzero(row <= distance * distance)[0]
            if include_self:
                picked = np.append(picked, start + local)
            links += len(picked)
            if links > max_links:
                raise ValueError(
                    f"The spatial weights would exceed {max_links} neighbour links; "
                    "use a smaller distance band or knn."
                )
            result.append(picked.astype(int))
    return result


def _weights_args(weights: str, k: int, distance: float | None):
    mode = validate_choice(weights, {"knn", "distance_band"}, "weights")
    kk = to_int(k, "k")
    if mode == "knn" and not 1 <= kk <= 200:
        raise ValueError("k must be between 1 and 200.")
    dist = None
    if mode == "distance_band":
        dist = to_float(distance, "distance") if distance is not None else None
        if dist is None or dist <= 0 or not math.isfinite(dist):
            raise ValueError("distance (> 0, coordinate units) is required for weights='distance_band'.")
    return mode, kk, dist


@capability(
    name="calculate_hotspots",
    keywords=[
        "hotspot", "hot spot", "cold spot", "getis ord", "gi*", "hot spot analysis", "spatial cluster of values",
        "crime hotspots", "significant clusters",
        "نقاط داغ", "هات اسپات", "لکه های داغ", "گتیس اورد", "خوشه های معنادار", "نقاط سرد",
    ],
    description=(
        "Getis-Ord Gi* hot/cold spot analysis: finds where high (or low) values of a numeric "
        "attribute cluster significantly; adds z-score, p-value and a -3..+3 confidence bin."
    ),
    required_inputs=["features", "field"],
    optional_inputs=["weights", "k", "distance", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "hotspots", "returns": "VectorOut", "artifact_kind": "features"},
)
def calculate_hotspots(
    features: Any,
    field: str,
    weights: str = "knn",
    k: int = 8,
    distance: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Gi* hot spot analysis.

    Args:
        features: Input features (at least 8 with a numeric ``field``).
        field: Numeric attribute (Persian digits understood).
        weights: ``knn`` or ``distance_band``.
        k: Neighbours for knn (the feature itself is always included, "star").
        distance: Band radius for distance_band, coordinate units.
        metadata: Extra output metadata.

    Returns:
        VectorOut with ``gi_z``, ``gi_p``, ``gi_bin`` (+3/+2/+1 hot at 99/95/90 %, 0 not significant,
        -1/-2/-3 cold) and ``gi_label``. Features without a usable value get nulls.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    if not isinstance(field, str) or not field.strip():
        raise ValueError("field must be a property name.")
    mode, kk, dist = _weights_args(weights, k, distance)
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")
    idx, xs, ys, values = _located_values(input_features, field)
    n = len(values)
    if n < 8:
        raise ValueError(f"Hot spot analysis needs at least 8 features with a numeric '{field}' (got {n}).")
    mean = float(values.mean())
    s = float(math.sqrt((values**2).mean() - mean * mean))
    if s == 0:
        raise ValueError(f"'{field}' is constant; there are no hot or cold spots.")

    links = _neighbours(xs, ys, mode, kk, dist, _limit(config, "max_links"), include_self=True)
    z_scores = np.empty(n)
    for i, neighbour in enumerate(links):
        wsum = len(neighbour)  # binary weights
        numerator = float(values[neighbour].sum()) - mean * wsum
        denominator = s * math.sqrt((n * wsum - wsum * wsum) / (n - 1)) if n > 1 else 0.0
        z_scores[i] = numerator / denominator if denominator > 0 else 0.0

    labels = {3: "Hot spot (99%)", 2: "Hot spot (95%)", 1: "Hot spot (90%)", 0: "Not significant",
              -1: "Cold spot (90%)", -2: "Cold spot (95%)", -3: "Cold spot (99%)"}

    def bin_of(z: float) -> int:
        a = abs(z)
        level = 3 if a >= 2.576 else 2 if a >= 1.960 else 1 if a >= 1.645 else 0
        return level if z > 0 else -level

    by_index = {index: pos for pos, index in enumerate(idx)}
    output = []
    for index, feature in enumerate(input_features):
        props = dict(feature.get("properties") or {})
        pos = by_index.get(index)
        if pos is None:
            props.update(gi_z=None, gi_p=None, gi_bin=None, gi_label=None)
        else:
            z = float(z_scores[pos])
            klass = bin_of(z)
            props.update(gi_z=z, gi_p=_normal_two_sided_p(z), gi_bin=klass, gi_label=labels[klass])
        output.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    counts = {labels[b]: int(sum(1 for z in z_scores if bin_of(float(z)) == b)) for b in labels}
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="hotspots", source_info=source_info,
        extra={"field": field, "weights": mode, "k": kk if mode == "knn" else None, "distance": dist,
               "analysed_features": n, "bin_counts": counts, "multiple_testing_correction": "none"},
        user_metadata=metadata,
    )


@capability(
    name="calculate_spatial_autocorrelation",
    keywords=[
        "moran", "morans i", "spatial autocorrelation", "is it clustered", "clustered or random",
        "spatial dependence",
        "موران", "خودهمبستگی فضایی", "الگوی فضایی", "خوشه ای یا تصادفی", "وابستگی مکانی",
    ],
    description=(
        "Global Moran's I: tests whether a numeric attribute is spatially clustered, dispersed or "
        "random, with z-score and p-value."
    ),
    required_inputs=["features", "field"],
    optional_inputs=["weights", "k", "distance", "row_standardize", "metadata"],
    output_kind="json",
    permissions=[],
    metadata={**_META, "operation": "morans_i", "returns": "dict", "artifact_kind": "json"},
)
def calculate_spatial_autocorrelation(
    features: Any,
    field: str,
    weights: str = "knn",
    k: int = 8,
    distance: float | None = None,
    row_standardize: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Global Moran's I.

    Args:
        features: Input features (at least 8 with a numeric ``field``).
        field: Numeric attribute.
        weights: ``knn`` or ``distance_band``.
        k: Neighbours for knn.
        distance: Band radius for distance_band, coordinate units.
        row_standardize: Scale each feature's weights to sum to 1 (recommended).
        metadata: Extra output metadata.

    Returns:
        dict ``{"type": "spatial_autocorrelation", "moran_i", "expected_i", "variance", "z_score",
        "p_value", "interpretation", ...}``.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    if not isinstance(field, str) or not field.strip():
        raise ValueError("field must be a property name.")
    mode, kk, dist = _weights_args(weights, k, distance)
    input_features, _ = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")
    _, xs, ys, values = _located_values(input_features, field)
    n = len(values)
    if n < 8:
        raise ValueError(f"Moran's I needs at least 8 features with a numeric '{field}' (got {n}).")
    deviation = values - values.mean()
    denominator = float((deviation**2).sum())
    if denominator == 0:
        raise ValueError(f"'{field}' is constant; Moran's I is undefined.")

    links = _neighbours(xs, ys, mode, kk, dist, _limit(config, "max_links"), include_self=False)
    row_sum = np.zeros(n)
    col_sum = np.zeros(n)
    s0 = s1 = 0.0
    cross = 0.0
    weight_of: list[Any] = []
    for i, neighbour in enumerate(links):
        w = (1.0 / len(neighbour) if row_standardize and len(neighbour) else 1.0)
        weight_of.append(w)
        row_sum[i] = w * len(neighbour)
        col_sum[neighbour] += w
        cross += w * float((deviation[i] * deviation[neighbour]).sum())
        s0 += w * len(neighbour)
    if s0 == 0:
        raise ValueError("No feature has a neighbour; increase k or the distance band.")
    # S1 = 1/2 sum_ij (w_ij + w_ji)^2 ; needs reciprocal weights
    reverse: dict[tuple[int, int], float] = {}
    for i, neighbour in enumerate(links):
        for j in neighbour.tolist():
            reverse[(i, j)] = weight_of[i]
    pairs = set(reverse) | {(j, i) for i, j in reverse}
    s1 = 0.5 * sum((reverse.get((i, j), 0.0) + reverse.get((j, i), 0.0)) ** 2 for i, j in pairs)
    s2 = float(((row_sum + col_sum) ** 2).sum())

    moran = (n / s0) * cross / denominator
    expected = -1.0 / (n - 1)
    variance = (n * n * s1 - n * s2 + 3 * s0 * s0) / ((n * n - 1) * s0 * s0) - expected**2
    z = (moran - expected) / math.sqrt(variance) if variance > 0 else 0.0
    p = _normal_two_sided_p(z)
    if p < 0.05 and moran > expected:
        reading = "clustered (similar values are near each other)"
    elif p < 0.05:
        reading = "dispersed (neighbours tend to have different values)"
    else:
        reading = "not significantly different from a random pattern"
    return {
        "type": "spatial_autocorrelation",
        "field": field,
        "moran_i": moran,
        "expected_i": expected,
        "variance": variance,
        "z_score": z,
        "p_value": p,
        "interpretation": reading,
        "n": n,
        "weights": mode,
        "k": kk if mode == "knn" else None,
        "distance": dist,
        "row_standardized": bool(row_standardize),
        "metadata": {"source": PLUGIN_ID, "operation": "morans_i", "created_at": utc_now_iso(),
                     **(metadata or {})},
    }


@capability(
    name="interpolate_idw",
    keywords=[
        "idw", "inverse distance weighting", "interpolation", "interpolate surface", "spatial interpolation",
        "rainfall surface", "pollution surface",
        "درونیابی", "وزن دهی معکوس فاصله", "ایدی دابلیو", "سطح درونیابی", "نقشه پیوسته از نقاط",
    ],
    description=(
        "Inverse-distance-weighted interpolation: a continuous raster surface from sample points "
        "with a numeric value (e.g. station measurements)."
    ),
    required_inputs=["points", "value_field", "cell_size"],
    optional_inputs=["power", "neighbors", "search_radius", "bbox", "crs", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={**_META, "operation": "idw", "returns": "RasterOut", "artifact_kind": "raster"},
)
def interpolate_idw(
    points: Any,
    value_field: str,
    cell_size: float,
    power: float = 2.0,
    neighbors: int | None = 12,
    search_radius: float | None = None,
    bbox: list[float] | None = None,
    crs: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    IDW interpolation.

    Args:
        points: Sample points with a numeric ``value_field``.
        value_field: Numeric attribute to interpolate.
        cell_size: Output pixel size (coordinate units).
        power: Distance exponent (> 0; 2 is the classic choice).
        neighbors: Use only the nearest N samples per cell (None = all samples).
        search_radius: Ignore samples farther than this; cells with no sample are nodata.
        bbox: [minx, miny, maxx, maxy]; default is the sample extent.
        crs: CRS label stored in the raster metadata.
        metadata: Extra output metadata.

    Returns:
        RasterOut (single band, north-up). Values never leave the range of the samples.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    size = to_float(cell_size, "cell_size")
    exponent = to_float(power, "power")
    if size <= 0 or exponent <= 0 or not math.isfinite(size) or not math.isfinite(exponent):
        raise ValueError("cell_size and power must be positive numbers.")
    nn = None if neighbors is None else to_int(neighbors, "neighbors")
    if nn is not None and nn < 1:
        raise ValueError("neighbors must be >= 1.")
    radius = None if search_radius is None else to_float(search_radius, "search_radius")
    if radius is not None and radius <= 0:
        raise ValueError("search_radius must be positive.")
    input_features, source_info = extract_features(points)
    check_limit(len(input_features), _limit(config, "max_features"), "sample count")
    _, xs, ys, values = _located_values(input_features, value_field)
    if len(values) < 2:
        raise ValueError(f"IDW needs at least 2 sample points with a numeric '{value_field}'.")

    if bbox is not None:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("bbox must be [minx, miny, maxx, maxy].")
        minx, miny, maxx, maxy = (to_float(v, "bbox") for v in bbox)
        if not (minx < maxx and miny < maxy):
            raise ValueError("bbox must satisfy minx < maxx and miny < maxy.")
    else:
        minx, miny, maxx, maxy = float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())
        if minx == maxx or miny == maxy:
            raise ValueError("The samples are collinear; pass an explicit bbox.")
    width = max(1, math.ceil((maxx - minx) / size))
    height = max(1, math.ceil((maxy - miny) / size))
    check_limit(width * height, _limit(config, "max_cells"), "output cell count (raise cell_size)")
    check_limit(width * height * len(values), _limit(config, "max_work"),
                "estimated work (cells x samples; raise cell_size or thin the samples)")

    cx = minx + (np.arange(width) + 0.5) * size
    cy = maxy - (np.arange(height) + 0.5) * size
    grid = np.full((height, width), np.nan)
    rows_per_chunk = max(1, 2_000_000 // max(width * len(values), 1))
    for start in range(0, height, rows_per_chunk):
        stop = min(height, start + rows_per_chunk)
        gx, gy = np.meshgrid(cx, cy[start:stop])
        d2 = (gx[..., None] - xs) ** 2 + (gy[..., None] - ys) ** 2  # rows x cols x samples
        if nn is not None and nn < len(values):
            cutoff = np.partition(d2, nn - 1, axis=-1)[..., nn - 1:nn]
            d2 = np.where(d2 <= cutoff, d2, np.inf)
        if radius is not None:
            d2 = np.where(d2 <= radius * radius, d2, np.inf)
        with np.errstate(divide="ignore", invalid="ignore"):
            w = 1.0 / np.power(d2, exponent / 2.0)
        exact = d2 == 0
        w = np.where(np.isinf(d2), 0.0, w)
        total = w.sum(axis=-1)
        estimate = np.where(total > 0, (w * values).sum(axis=-1) / np.where(total > 0, total, 1.0), np.nan)
        hit = exact.any(axis=-1)
        if hit.any():
            estimate = np.where(hit, (np.where(exact, values, 0.0).sum(axis=-1) / np.maximum(exact.sum(axis=-1), 1)),
                                estimate)
        grid[start:stop] = estimate
    meta = {
        "source": PLUGIN_ID, "operation": "idw", "transform": [size, 0.0, minx, 0.0, -size, maxy],
        "width": width, "height": height, "band_count": 1, "crs": crs, "power": exponent,
        "neighbors": nn, "search_radius": radius, "sample_count": len(values),
        "value_field": value_field, "created_at": utc_now_iso(), **source_info, **(metadata or {}),
    }
    return make_raster_out(grid, meta, nodata=DEFAULT_NODATA)


@capability(
    name="aggregate_to_h3",
    keywords=[
        "h3", "hexagon", "hex bin", "hexbin", "uber h3", "hexagonal aggregation", "count per hexagon",
        "شش ضلعی", "اچ تری", "تجمیع شش ضلعی", "هگزاگون", "تعداد در هر شش ضلعی",
    ],
    description=(
        "Aggregate points into H3 hexagonal cells (resolution 0-12): count and optional "
        "sum/mean/min/max of a numeric attribute per cell. Coordinates must be WGS84 lon/lat."
    ),
    required_inputs=["points", "resolution"],
    optional_inputs=["value_field", "stats", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "aggregate_to_h3", "returns": "VectorOut", "artifact_kind": "features"},
)
def aggregate_to_h3(
    points: Any,
    resolution: int,
    value_field: str | None = None,
    stats: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    H3 binning.

    Args:
        points: Point features in WGS84 lon/lat.
        resolution: H3 resolution 0 (huge) - 12 (about 10 m); 8 is roughly a city block cluster
            (~0.7 km2), 9 about 0.1 km2.
        value_field: Optional numeric attribute to summarise per cell.
        stats: Any of sum, mean, min, max (default sum and mean when value_field is given).
        metadata: Extra output metadata.

    Returns:
        VectorOut of hexagon polygons with ``h3``, ``count`` and the statistics.
    """
    try:
        import h3
    except ImportError as exc:
        raise SDKDependencyError(
            "aggregate_to_h3 requires the h3 package: pip install h3 (extra: smart_spatial_system[h3])"
        ) from exc
    config = load_config(PLUGIN_ID)
    level = to_int(resolution, "resolution")
    if not 0 <= level <= 12:
        raise ValueError("resolution must be between 0 and 12.")
    wanted = list(stats or (["sum", "mean"] if value_field else []))
    for name in wanted:
        validate_choice(name, {"sum", "mean", "min", "max"}, "stats")
    if wanted and not value_field:
        raise ValueError("stats requires value_field.")
    input_features, source_info = extract_features(points)
    check_limit(len(input_features), _limit(config, "max_h3_points"), "point count")
    shape = get_shapely(PLUGIN_ID)[0]

    cells: dict[str, list[float | None]] = {}
    skipped = 0
    for feature in input_features:
        geometry = feature.get("geometry")
        if not geometry:
            skipped += 1
            continue
        geom = shape(geometry)
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        if not (-180 <= point.x <= 180 and -90 <= point.y <= 90):
            raise ValueError("H3 needs WGS84 lon/lat coordinates; reproject the points first.")
        value = parse_number((feature.get("properties") or {}).get(value_field)) if value_field else None
        cells.setdefault(h3.latlng_to_cell(point.y, point.x, level), []).append(value)

    features = []
    for cell, vals in cells.items():
        numbers = [v for v in vals if v is not None]
        props: dict[str, Any] = {"h3": cell, "count": len(vals), "resolution": level}
        for name in wanted:
            props[f"{value_field}_{name}"] = (
                None if not numbers else
                sum(numbers) if name == "sum" else
                sum(numbers) / len(numbers) if name == "mean" else
                min(numbers) if name == "min" else max(numbers)
            )
        ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(cell)]
        ring.append(ring[0])
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                         "properties": props})
    return make_vector_out(
        features, plugin_id=PLUGIN_ID, operation="aggregate_to_h3", source_info=source_info,
        extra={"resolution": level, "crs": "EPSG:4326", "point_count": len(input_features) - skipped,
               "skipped_without_geometry": skipped, "value_field": value_field, "stats": wanted},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Spatial Pattern Analysis",
    description="Hot spots (Gi*), Moran's I, IDW interpolation and H3 hexagon aggregation.",
    author="GeoChat Platform Team",
    permissions=[],
)
