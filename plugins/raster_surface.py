"""
raster_surface.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_surface

Capabilities:
    - calculate_hillshade:   shaded relief from a DEM (Horn's method).
    - generate_contours:     contour lines from a surface raster (marching squares).
    - calculate_focal_statistics: moving-window statistics (smoothing, local range...).
    - detect_raster_change:  change between two co-registered rasters (difference/ratio/percent).
    - raster_to_points:      pixel centres as point features carrying the pixel value.

Config-aware behavior:
    Reads config/plugins/raster_surface.yaml. ``max_cells`` bounds every raster read;
    ``max_work`` bounds window operations; ``max_points`` / ``max_contour_lines`` bound outputs.

Important:
    Only non-rotated north-up transforms are supported. For geographic rasters (EPSG:4326)
    hillshade converts degrees to metres at the raster's mid latitude; reproject first for
    exact results. Nodata becomes NaN internally and -9999 on output.
"""

from __future__ import annotations

import math
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.raster_common import (
    DEFAULT_NODATA,
    make_raster_out,
    numpy_module,
    read_raster,
)
from plugins._shared.vector_common import (
    check_limit,
    get_shapely,
    load_config,
    make_vector_out,
    to_float,
    to_int,
    utc_now_iso,
    validate_choice,
)

PLUGIN_ID = "raster_surface"

DEFAULTS = {
    "max_cells": 4_000_000,
    "max_work": 300_000_000,
    "max_points": 200_000,
    "max_contour_lines": 50_000,
    "max_contour_levels": 100,
}
STATISTICS = {"mean", "median", "min", "max", "sum", "std", "range"}
CHANGE_METHODS = {"difference", "ratio", "percent_change"}
_TOL = 1e-6
_META = {
    "category": "analysis",
    "data_type": "raster",
    "access_scope": "spatial_analysis",
    "config_aware": True,
    "routable": True,
}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _band(array: Any, band_index: Any):
    index = to_int(band_index, "band_index")
    if not 1 <= index <= array.shape[0]:
        raise ValueError(f"band_index must be between 1 and {array.shape[0]}.")
    return array[index - 1]


def _is_geographic(crs: Any) -> bool:
    return str(crs or "").strip().upper() in {"EPSG:4326", "EPSG:4269", "CRS:84", "OGC:CRS84", "4326"}


def _out_meta(base: dict[str, Any], operation: str, transform: list[float], shape, extra=None):
    return {
        **{k: v for k, v in base.items() if k in {"crs", "units"}},
        "source": PLUGIN_ID,
        "operation": operation,
        "transform": transform,
        "width": int(shape[-1]),
        "height": int(shape[-2]),
        "band_count": 1,
        "created_at": utc_now_iso(),
        **(extra or {}),
    }


# ---------------------------------------------------------------- hillshade
@capability(
    name="calculate_hillshade",
    keywords=[
        "hillshade", "shaded relief", "relief shading", "terrain shading", "dem shading",
        "سایه روشن", "سایه‌روشن ناهمواری", "هیل شید", "نقشه سایه", "پستی بلندی",
    ],
    description=(
        "Hillshade (shaded relief, 0-255) from a DEM using Horn's method with a configurable sun "
        "azimuth/altitude and vertical exaggeration."
    ),
    required_inputs=["raster"],
    optional_inputs=["azimuth", "altitude", "z_factor", "band_index", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={**_META, "operation": "hillshade", "returns": "RasterOut", "artifact_kind": "raster"},
)
def calculate_hillshade(
    raster: Any,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    z_factor: float = 1.0,
    band_index: int = 1,
    metadata: dict[str, Any] | None = None,
):
    """
    Compute hillshade.

    Args:
        raster: DEM raster (elevation in metres).
        azimuth: Sun direction in degrees clockwise from north (default 315 = north-west).
        altitude: Sun height above the horizon in degrees (0-90).
        z_factor: Vertical exaggeration.
        band_index: 1-based band holding elevation.
        metadata: Extra output metadata.

    Returns:
        RasterOut with values 0 (dark) - 255 (bright); border pixels use replicated edges.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    az = to_float(azimuth, "azimuth")
    alt = to_float(altitude, "altitude")
    z = to_float(z_factor, "z_factor")
    if not 0 <= alt <= 90:
        raise ValueError("altitude must be between 0 and 90 degrees.")
    if z <= 0 or not math.isfinite(z):
        raise ValueError("z_factor must be positive.")
    array, meta, transform = read_raster(raster, max_cells=_limit(config, "max_cells"))
    dem = _band(array, band_index)
    a, _, _, _, e, f = transform
    xres, yres = a, -e
    if _is_geographic(meta.get("crs")):
        mid_lat = f + e * dem.shape[0] / 2.0
        xres = a * 111_320.0 * max(math.cos(math.radians(mid_lat)), 1e-6)
        yres = -e * 110_540.0

    p = np.pad(dem, 1, mode="edge")
    za, zb, zc = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    zd, zf = p[1:-1, :-2], p[1:-1, 2:]
    zg, zh, zi = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((zc + 2 * zf + zi) - (za + 2 * zd + zg)) / (8 * xres) * z
    dzdy = ((zg + 2 * zh + zi) - (za + 2 * zb + zc)) / (8 * yres) * z
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)  # north-up raster: row index grows southwards
    zenith = math.radians(90.0 - alt)
    azimuth_math = math.radians((360.0 - az + 90.0) % 360.0)
    shade = np.cos(zenith) * np.cos(slope) + np.sin(zenith) * np.sin(slope) * np.cos(azimuth_math - aspect)
    result = np.clip(255.0 * shade, 0.0, 255.0)
    result = np.where(np.isnan(dem), np.nan, result)
    out_meta = _out_meta(meta, "hillshade", transform, result.shape,
                         {"azimuth": az, "altitude": alt, "z_factor": z, **(metadata or {})})
    return make_raster_out(result, out_meta, nodata=DEFAULT_NODATA)


# ----------------------------------------------------------------- contours
def _cell_segments(np, values, level):
    """Marching squares on the pixel-centre grid: yield ((r0,c0),(r1,c1)) interpolated segments in
    fractional (row, col) coordinates."""
    v00, v01 = values[:-1, :-1], values[:-1, 1:]
    v10, v11 = values[1:, :-1], values[1:, 1:]
    stack = np.stack([v00, v01, v11, v10])  # corners clockwise from top-left
    valid = ~np.isnan(stack).any(axis=0)
    inside = stack >= level
    crossing = valid & (inside.any(axis=0)) & (~inside.all(axis=0))
    rows, cols = np.nonzero(crossing)

    def edge(r, c, k):
        """Interpolated point on edge k: 0 top, 1 right, 2 bottom, 3 left."""
        if k == 0:
            t = (level - v00[r, c]) / (v01[r, c] - v00[r, c])
            return (r, c + t)
        if k == 1:
            t = (level - v01[r, c]) / (v11[r, c] - v01[r, c])
            return (r + t, c + 1)
        if k == 2:
            t = (level - v10[r, c]) / (v11[r, c] - v10[r, c])
            return (r + 1, c + t)
        t = (level - v00[r, c]) / (v10[r, c] - v00[r, c])
        return (r + t, c)

    table = {  # case index (TL=8, TR=4, BR=2, BL=1) -> edge pairs
        1: [(3, 2)], 2: [(2, 1)], 3: [(3, 1)], 4: [(0, 1)], 6: [(0, 2)], 7: [(3, 0)],
        8: [(3, 0)], 9: [(0, 2)], 11: [(0, 1)], 12: [(3, 1)], 13: [(2, 1)], 14: [(3, 2)],
    }
    for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
        case = (8 if inside[0, r, c] else 0) | (4 if inside[1, r, c] else 0) | \
               (2 if inside[2, r, c] else 0) | (1 if inside[3, r, c] else 0)
        if case in (5, 10):  # saddle: decide by the cell mean
            centre = (v00[r, c] + v01[r, c] + v10[r, c] + v11[r, c]) / 4.0 >= level
            pairs = ([(3, 0), (2, 1)] if centre else [(3, 2), (0, 1)]) if case == 5 else \
                    ([(0, 1), (3, 2)] if centre else [(3, 0), (2, 1)])
        else:
            pairs = table.get(case, [])
        for k1, k2 in pairs:
            yield edge(r, c, k1), edge(r, c, k2)


@capability(
    name="generate_contours",
    keywords=[
        "contour", "contours", "isolines", "elevation lines", "isoline", "contour lines",
        "منحنی میزان", "خطوط تراز", "ایزولاین", "منحنی تراز", "خطوط هم ارتفاع",
    ],
    description=(
        "Generate contour lines (isolines) from a surface raster at a fixed interval or at given "
        "levels; lines carry their level."
    ),
    required_inputs=["raster"],
    optional_inputs=["interval", "levels", "base", "band_index", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "contours", "returns": "VectorOut", "artifact_kind": "features"},
)
def generate_contours(
    raster: Any,
    interval: float | None = None,
    levels: list[float] | None = None,
    base: float = 0.0,
    band_index: int = 1,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Generate contours.

    Args:
        raster: Surface raster (DEM, density, temperature...).
        interval: Spacing between contour levels (levels are base + k * interval within the
            data range). Give either interval or levels.
        levels: Explicit list of levels.
        base: Origin for interval-based levels.
        band_index: 1-based band to contour.
        metadata: Extra output metadata.

    Returns:
        VectorOut of (Multi)LineString features with property ``level``; coordinates are in the
        raster CRS.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    shape, mapping, _ = get_shapely(PLUGIN_ID)
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge

    array, meta, transform = read_raster(raster, max_cells=_limit(config, "max_cells"))
    surface = _band(array, band_index)
    if surface.shape[0] < 2 or surface.shape[1] < 2:
        raise ValueError("The raster needs at least 2 x 2 pixels for contours.")
    if (interval is None) == (levels is None):
        raise ValueError("Provide exactly one of interval or levels.")
    lo, hi = float(np.nanmin(surface)), float(np.nanmax(surface))
    if levels is not None:
        if not isinstance(levels, (list, tuple)) or not levels:
            raise ValueError("levels must be a non-empty list of numbers.")
        chosen = sorted({to_float(v, "levels") for v in levels})
    else:
        step = to_float(interval, "interval")
        if step <= 0 or not math.isfinite(step):
            raise ValueError("interval must be a positive number.")
        origin = to_float(base, "base")
        start = math.ceil((lo - origin) / step)
        end = math.floor((hi - origin) / step)
        chosen = [origin + k * step for k in range(start, end + 1)]
    check_limit(len(chosen), _limit(config, "max_contour_levels"), "number of contour levels")
    chosen = [lv for lv in chosen if lo < lv <= hi or (lv == lo == hi)]

    a, _, c, _, e, f = transform
    max_lines = _limit(config, "max_contour_lines")
    features: list[dict[str, Any]] = []
    for level in chosen:
        segments = [
            LineString([(c + a * (p0[1] + 0.5), f + e * (p0[0] + 0.5)),
                        (c + a * (p1[1] + 0.5), f + e * (p1[0] + 0.5))])
            for p0, p1 in _cell_segments(np, surface, level)
        ]
        if not segments:
            continue
        merged = linemerge(segments)
        lines = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
        for line in lines:
            features.append({"type": "Feature", "geometry": dict(mapping(line)),
                             "properties": {"level": level}})
            check_limit(len(features), max_lines, "contour line count (raise the interval)")
    _ = shape
    return make_vector_out(
        features, plugin_id=PLUGIN_ID, operation="contours",
        extra={"levels": chosen, "crs": meta.get("crs"), "data_min": lo, "data_max": hi},
        user_metadata=metadata,
    )


# ------------------------------------------------------------------- focal
@capability(
    name="calculate_focal_statistics",
    keywords=[
        "focal statistics", "moving window", "smooth raster", "neighborhood statistics",
        "local mean", "local range", "kernel filter",
        "آمار کانونی", "پنجره متحرک", "هموارسازی رستر", "آمار همسایگی", "میانگین محلی",
    ],
    description=(
        "Moving-window (focal) statistics on a raster: mean, median, min, max, sum, std or range "
        "over a square or circular neighbourhood; used to smooth or to measure local variation."
    ),
    required_inputs=["raster"],
    optional_inputs=["statistic", "radius", "shape", "band_index", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={**_META, "operation": "focal_statistics", "returns": "RasterOut", "artifact_kind": "raster"},
)
def calculate_focal_statistics(
    raster: Any,
    statistic: str = "mean",
    radius: int = 1,
    shape: str = "square",
    band_index: int = 1,
    metadata: dict[str, Any] | None = None,
):
    """
    Focal statistics.

    Args:
        raster: Input raster.
        statistic: mean | median | min | max | sum | std | range (nodata cells are ignored).
        radius: Neighbourhood radius in cells (window = 2 * radius + 1).
        shape: ``square`` or ``circle``.
        band_index: 1-based band.
        metadata: Extra output metadata.

    Returns:
        RasterOut; a pixel whose neighbourhood has no valid cell is nodata.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    stat = validate_choice(statistic, STATISTICS, "statistic")
    kind = validate_choice(shape, {"square", "circle"}, "shape")
    r = to_int(radius, "radius")
    if not 1 <= r <= 50:
        raise ValueError("radius must be between 1 and 50 cells.")
    array, meta, transform = read_raster(raster, max_cells=_limit(config, "max_cells"))
    band = _band(array, band_index)
    size = 2 * r + 1
    check_limit(band.size * size * size, _limit(config, "max_work"),
                "estimated work (cells x window; lower radius or use a smaller raster)")

    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    window_mask = (yy * yy + xx * xx <= r * r) if kind == "circle" else np.ones((size, size), bool)
    padded = np.pad(band, r, mode="constant", constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (size, size))
    result = np.full(band.shape, np.nan)
    rows_per_block = max(1, 4_000_000 // (band.shape[1] * size * size))
    reducers = {"mean": np.nanmean, "median": np.nanmedian, "min": np.nanmin, "max": np.nanmax,
                "sum": np.nansum, "std": np.nanstd}
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows are expected
        for start in range(0, band.shape[0], rows_per_block):
            block = windows[start:start + rows_per_block][..., window_mask]
            valid_any = ~np.isnan(block).all(axis=-1)
            if stat == "range":
                values = np.nanmax(block, axis=-1) - np.nanmin(block, axis=-1)
            else:
                values = reducers[stat](block, axis=-1)
            result[start:start + rows_per_block] = np.where(valid_any, values, np.nan)
    out_meta = _out_meta(meta, "focal_statistics", transform, result.shape,
                         {"statistic": stat, "radius": r, "shape": kind, **(metadata or {})})
    return make_raster_out(result, out_meta, nodata=DEFAULT_NODATA)


# ------------------------------------------------------------------ change
@capability(
    name="detect_raster_change",
    keywords=[
        "change detection", "raster difference", "before after", "compare rasters", "temporal change",
        "ndvi change", "land cover change",
        "تشخیص تغییرات", "اختلاف رستر", "قبل و بعد", "مقایسه دو تصویر", "تغییر پوشش",
    ],
    description=(
        "Change between two co-registered rasters (same grid): difference (after - before), ratio "
        "or percent change; with a threshold also a decrease/stable/increase class raster."
    ),
    required_inputs=["before", "after"],
    optional_inputs=["method", "threshold", "band_index", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={**_META, "operation": "change_detection", "returns": "RasterOut", "artifact_kind": "raster"},
)
def detect_raster_change(
    before: Any,
    after: Any,
    method: str = "difference",
    threshold: float | None = None,
    band_index: int = 1,
    metadata: dict[str, Any] | None = None,
):
    """
    Detect change.

    Args:
        before: Earlier raster.
        after: Later raster on the same grid (shape, transform, CRS).
        method: ``difference`` (after - before), ``ratio`` (after / before) or ``percent_change``
            (100 * (after - before) / |before|). Division by zero gives nodata.
        threshold: If given, the result is a class raster: -1 below -threshold, 0 within, +1 above
            (in the units of the chosen method).
        band_index: 1-based band of both rasters.
        metadata: Extra output metadata.

    Returns:
        RasterOut; metadata has ``increase_pixels`` / ``decrease_pixels`` / ``stable_pixels`` when a
        threshold is used, and ``mean_change``.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    chosen = validate_choice(method, CHANGE_METHODS, "method")
    cells = _limit(config, "max_cells")
    arr_b, meta_b, t_b = read_raster(before, max_cells=cells)
    arr_a, meta_a, t_a = read_raster(after, max_cells=cells)
    b, a = _band(arr_b, band_index), _band(arr_a, band_index)
    if b.shape != a.shape or not all(math.isclose(x, y, rel_tol=_TOL, abs_tol=1e-9)
                                     for x, y in zip(t_b, t_a, strict=True)):
        raise ValueError("before and after must have the same shape and transform; align them first.")
    if meta_b.get("crs") and meta_a.get("crs") and str(meta_b["crs"]).upper() != str(meta_a["crs"]).upper():
        raise ValueError("before and after must share the same CRS.")

    with np.errstate(divide="ignore", invalid="ignore"):
        if chosen == "difference":
            change = a - b
        elif chosen == "ratio":
            change = np.where(b != 0, a / b, np.nan)
        else:
            change = np.where(b != 0, 100.0 * (a - b) / np.abs(b), np.nan)
    extra: dict[str, Any] = {"method": chosen, "mean_change": float(np.nanmean(change))
                             if not np.isnan(change).all() else None}
    if threshold is not None:
        limit_value = to_float(threshold, "threshold")
        if limit_value < 0:
            raise ValueError("threshold must be >= 0.")
        center = 1.0 if chosen == "ratio" else 0.0
        classes = np.where(np.isnan(change), np.nan,
                           np.where(change > center + limit_value, 1.0,
                                    np.where(change < center - limit_value, -1.0, 0.0)))
        extra.update(threshold=limit_value, increase_pixels=int((classes == 1).sum()),
                     decrease_pixels=int((classes == -1).sum()), stable_pixels=int((classes == 0).sum()))
        change = classes
    out_meta = _out_meta(meta_a, "change_detection", t_a, change.shape, {**extra, **(metadata or {})})
    return make_raster_out(change, out_meta, nodata=DEFAULT_NODATA)


# ---------------------------------------------------------------- to points
@capability(
    name="raster_to_points",
    keywords=[
        "raster to points", "pixels to points", "convert raster to point layer", "cell centers",
        "رستر به نقطه", "تبدیل پیکسل به نقطه", "مرکز سلول ها", "نقاط از رستر",
    ],
    description=(
        "Convert raster pixels to point features at the pixel centres, with the pixel value as a "
        "property; optional value filter and sampling step."
    ),
    required_inputs=["raster"],
    optional_inputs=["band_index", "min_value", "max_value", "step", "value_field", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "raster_to_points", "returns": "VectorOut", "artifact_kind": "features"},
)
def raster_to_points(
    raster: Any,
    band_index: int = 1,
    min_value: float | None = None,
    max_value: float | None = None,
    step: int = 1,
    value_field: str = "value",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Raster pixels to points.

    Args:
        raster: Input raster.
        band_index: 1-based band.
        min_value: Keep pixels >= this value.
        max_value: Keep pixels <= this value.
        step: Take every N-th pixel in rows and columns (>= 1) to thin dense rasters.
        value_field: Property name for the pixel value.
        metadata: Extra output metadata.

    Returns:
        VectorOut of Points (nodata pixels are skipped); refused when more than ``max_points``.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    array, meta, transform = read_raster(raster, max_cells=_limit(config, "max_cells"))
    band = _band(array, band_index)
    stride = to_int(step, "step")
    if stride < 1:
        raise ValueError("step must be >= 1.")
    if not isinstance(value_field, str) or not value_field.strip():
        raise ValueError("value_field must be a non-empty string.")
    mask = ~np.isnan(band)
    if min_value is not None:
        mask &= band >= to_float(min_value, "min_value")
    if max_value is not None:
        mask &= band <= to_float(max_value, "max_value")
    sampling = np.zeros_like(mask)
    sampling[::stride, ::stride] = True
    rows, cols = np.nonzero(mask & sampling)
    check_limit(len(rows), _limit(config, "max_points"), "point count (raise step or filter by value)")
    a, _, c, _, e, f = transform
    features = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [c + a * (col + 0.5), f + e * (row + 0.5)]},
         "properties": {value_field: float(band[row, col]), "row": int(row), "col": int(col)}}
        for row, col in zip(rows.tolist(), cols.tolist(), strict=True)
    ]
    return make_vector_out(
        features, plugin_id=PLUGIN_ID, operation="raster_to_points",
        extra={"crs": meta.get("crs"), "step": stride, "band_index": band_index},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Raster Surface Tools",
    description="Hillshade, contours, focal statistics, change detection and raster-to-points.",
    author="GeoChat Platform Team",
    permissions=[],
)
