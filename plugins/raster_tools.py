"""
raster_tools.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_tools

Capabilities:
    - sample_raster_at_points:
        Read the raster value under each point and store it as a property.
    - mosaic_rasters:
        Merge several rasters of the same grid (same pixel size/CRS) into one.
    - reproject_raster:
        Reproject and/or resample a raster (needs rasterio).
    - weighted_overlay:
        Multi-criteria suitability: normalise several rasters, weight them
        (benefit or cost direction) and sum.

Config-aware behavior:
    Reads config/plugins/raster_tools.yaml. ``max_cells`` bounds the size of any
    raster read or produced; ``max_rasters`` bounds the number of inputs.

Important:
    Only non-rotated north-up transforms are supported. Pixel values are read
    as float64; nodata becomes NaN internally and is written back as the output
    nodata value (-9999 by default) so downstream JSON stays valid.
"""

from __future__ import annotations

import math
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
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
    extract_features,
    get_shapely,
    load_config,
    make_vector_out,
    to_float,
    to_int,
    utc_now_iso,
    validate_choice,
)

PLUGIN_ID = "raster_tools"

DEFAULT_MAX_CELLS = 4_000_000
DEFAULT_MAX_RASTERS = 50
MOSAIC_METHODS = {"first", "last", "mean", "min", "max"}
RESAMPLING = {"nearest", "bilinear", "cubic", "average", "max", "min", "mode"}
DIRECTIONS = {"benefit", "cost"}
_TOL = 1e-6


def _limits(config: dict[str, Any]) -> tuple[int, int]:
    return (
        to_int(config.get("max_cells", DEFAULT_MAX_CELLS), "max_cells"),
        to_int(config.get("max_rasters", DEFAULT_MAX_RASTERS), "max_rasters"),
    )


def _band(array: Any, band_index: Any):
    index = to_int(band_index, "band_index")
    if not 1 <= index <= array.shape[0]:
        raise ValueError(f"band_index must be between 1 and {array.shape[0]}.")
    return array[index - 1]


def _same_crs(a: Any, b: Any) -> bool:
    return a is None or b is None or str(a).strip().upper() == str(b).strip().upper()


def _output_metadata(base: dict[str, Any], operation: str, transform: list[float], array_shape,
                     extra: dict[str, Any] | None = None) -> dict[str, Any]:
    keep = {k: v for k, v in base.items() if k in {"crs", "units"}}
    return {
        **keep,
        "source": PLUGIN_ID,
        "operation": operation,
        "transform": transform,
        "width": int(array_shape[-1]),
        "height": int(array_shape[-2]),
        "created_at": utc_now_iso(),
        **(extra or {}),
    }


# --------------------------------------------------------------- sample at points
@capability(
    name="sample_raster_at_points",
    keywords=[
        "sample raster", "extract raster values", "raster value at point", "extract values to points",
        "نمونه برداری رستر", "مقدار رستر در نقطه", "استخراج مقدار رستر", "ارتفاع نقاط",
    ],
    description=(
        "Read the raster value (one band) under each point feature and store it as a property; "
        "null where the point is outside the raster or on nodata."
    ),
    required_inputs=["raster", "points"],
    optional_inputs=["band_index", "field_name", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster",
        "operation": "sample_raster_at_points",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def sample_raster_at_points(
    raster: Any,
    points: Any,
    band_index: int = 1,
    field_name: str = "raster_value",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Sample a raster at point locations.

    Args:
        raster: Raster (dict or RasterOut with data and metadata.transform).
        points: Point features, in the raster's CRS (non-points use a representative point).
        band_index: 1-based band to sample.
        field_name: Property that receives the value.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the input features plus ``field_name``.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    max_cells, _ = _limits(config)
    if not isinstance(field_name, str) or not field_name.strip():
        raise ValueError("field_name must be a non-empty string.")
    array, raster_meta, transform = read_raster(raster, max_cells=max_cells)
    band = _band(array, band_index)
    features, source_info = extract_features(points)
    shape, _, _ = get_shapely(PLUGIN_ID)

    a, _, c, _, e, f = transform
    rows, cols = band.shape
    output: list[dict[str, Any]] = []
    outside = 0
    for feature in features:
        properties = dict(feature.get("properties") or {})
        value = None
        geometry = feature.get("geometry")
        if geometry:
            geom = shape(geometry)
            if not geom.is_empty:
                point = geom if geom.geom_type == "Point" else geom.representative_point()
                col = math.floor((point.x - c) / a)
                row = math.floor((point.y - f) / e)
                if 0 <= row < rows and 0 <= col < cols:
                    cell = band[row, col]
                    value = None if np.isnan(cell) else float(cell)
                else:
                    outside += 1
        properties[field_name] = value
        output.append({"type": "Feature", "geometry": geometry, "properties": properties})

    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="sample_raster_at_points", source_info=source_info,
        extra={"band_index": band_index, "field_name": field_name, "outside_count": outside,
               "raster_crs": raster_meta.get("crs")},
        user_metadata=metadata,
    )


# ----------------------------------------------------------------------- mosaic
@capability(
    name="mosaic_rasters",
    keywords=[
        "mosaic", "merge rasters", "stitch rasters", "combine tiles", "raster merge",
        "موزاییک", "ادغام رستر", "ترکیب تصاویر", "چسباندن تایل ها",
    ],
    description=(
        "Merge several rasters that share the same CRS and pixel size into one mosaic; "
        "overlaps are resolved by first/last/mean/min/max."
    ),
    required_inputs=["rasters"],
    optional_inputs=["method", "band_index", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster",
        "operation": "mosaic",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def mosaic_rasters(
    rasters: list[Any],
    method: str = "first",
    band_index: int | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Mosaic rasters on a common grid.

    Args:
        rasters: Two or more rasters with identical pixel size and CRS, positioned on
            the same pixel lattice (reproject/resample first otherwise).
        method: How to combine overlapping pixels: first, last, mean, min, max.
        band_index: Mosaic only this 1-based band; default is all bands.
        metadata: Extra output metadata.

    Returns:
        RasterOut covering the union of the inputs; uncovered pixels are nodata (-9999).
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    max_cells, max_rasters = _limits(config)
    chosen = validate_choice(method, MOSAIC_METHODS, "method")
    if not isinstance(rasters, (list, tuple)) or len(rasters) < 2:
        raise ValueError("rasters must be a list of at least two rasters.")
    check_limit(len(rasters), max_rasters, "raster count")

    tiles = []
    for item in rasters:
        array, meta, transform = read_raster(item, max_cells=max_cells)
        if band_index is not None:
            array = _band(array, band_index)[None, :, :]
        tiles.append((array, meta, transform))

    first_array, first_meta, first_t = tiles[0]
    a, _, _, _, e, _ = first_t
    for array, meta, transform in tiles[1:]:
        if array.shape[0] != first_array.shape[0]:
            raise ValueError("All rasters must have the same number of bands.")
        if not (math.isclose(transform[0], a, rel_tol=_TOL) and math.isclose(transform[4], e, rel_tol=_TOL)):
            raise ValueError("Rasters must share the same pixel size; resample them first.")
        if not _same_crs(first_meta.get("crs"), meta.get("crs")):
            raise ValueError("Rasters must share the same CRS; reproject them first.")

    minx = min(t[2] for _, _, t in tiles)
    maxy = max(t[5] for _, _, t in tiles)
    maxx = max(t[2] + arr.shape[2] * a for arr, _, t in tiles)
    miny = min(t[5] + arr.shape[1] * e for arr, _, t in tiles)
    width = round((maxx - minx) / a)
    height = round((maxy - miny) / -e)
    check_limit(first_array.shape[0] * width * height, max_cells, "mosaic cell count")

    bands = first_array.shape[0]
    total = np.zeros((bands, height, width))
    count = np.zeros((bands, height, width))
    best = np.full((bands, height, width), np.nan)
    for array, _, transform in tiles:
        col_f = (transform[2] - minx) / a
        row_f = (maxy - transform[5]) / -e
        col, row = round(col_f), round(row_f)
        if abs(col - col_f) > 1e-3 or abs(row - row_f) > 1e-3:
            raise ValueError("Rasters are not aligned to the same pixel grid; resample them first.")
        h, w = array.shape[1:]
        window = (slice(None), slice(row, row + h), slice(col, col + w))
        valid = ~np.isnan(array)
        if chosen == "first":
            target = best[window]
            best[window] = np.where(np.isnan(target) & valid, array, target)
        elif chosen == "last":
            best[window] = np.where(valid, array, best[window])
        elif chosen == "mean":
            total[window] += np.where(valid, array, 0.0)
            count[window] += valid
        elif chosen == "min":
            best[window] = np.fmin(best[window], array)
        else:
            best[window] = np.fmax(best[window], array)
    if chosen == "mean":
        with np.errstate(invalid="ignore", divide="ignore"):
            best = np.where(count > 0, total / np.where(count > 0, count, 1), np.nan)

    transform_out = [a, 0.0, minx, 0.0, e, maxy]
    out_meta = _output_metadata(
        first_meta, "mosaic", transform_out, best.shape,
        {"method": chosen, "input_count": len(tiles), "band_count": bands, **(metadata or {})},
    )
    result = best[0] if bands == 1 else best
    return make_raster_out(result, out_meta, nodata=DEFAULT_NODATA)


# ------------------------------------------------------------------- reproject
@capability(
    name="reproject_raster",
    keywords=[
        "reproject raster", "resample raster", "change raster crs", "raster resolution", "warp raster",
        "تغییر سیستم مختصات رستر", "بازنمونه برداری رستر", "تغییر رزولوشن", "ری پروجکت رستر",
    ],
    description=(
        "Reproject a raster to another CRS and/or resample it to a new pixel size "
        "(nearest, bilinear, cubic, average, ...). Requires rasterio."
    ),
    required_inputs=["raster"],
    optional_inputs=["target_crs", "resolution", "resampling", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster",
        "operation": "reproject",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def reproject_raster(
    raster: Any,
    target_crs: str | int | None = None,
    resolution: float | None = None,
    resampling: str = "nearest",
    metadata: dict[str, Any] | None = None,
):
    """
    Reproject and/or resample a raster.

    Args:
        raster: Raster whose metadata has ``crs`` and a north-up ``transform``.
        target_crs: Target CRS ("EPSG:3857", 3857, WKT...). Omit to keep the CRS.
        resolution: Output pixel size in target units. Omit to let rasterio choose.
        resampling: nearest | bilinear | cubic | average | max | min | mode.
        metadata: Extra output metadata.

    Returns:
        RasterOut on the new grid; pixels with no source data are nodata (-9999).
    """
    try:
        from rasterio.crs import CRS
        from rasterio.enums import Resampling
        from rasterio.transform import Affine
        from rasterio.warp import calculate_default_transform
        from rasterio.warp import reproject as rio_reproject
    except ImportError as exc:
        raise SDKDependencyError(
            "reproject_raster requires rasterio: pip install rasterio"
        ) from exc
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    max_cells, _ = _limits(config)
    method = Resampling[validate_choice(resampling, RESAMPLING, "resampling")]

    array, meta, transform = read_raster(raster, max_cells=max_cells)
    if not meta.get("crs"):
        raise ValueError("raster metadata needs a 'crs' to reproject.")
    try:
        src_crs = CRS.from_user_input(meta["crs"])
        dst_crs = CRS.from_user_input(target_crs) if target_crs is not None else src_crs
    except Exception as exc:
        raise ValueError(f"Invalid CRS: {exc}") from exc
    if target_crs is None and resolution is None:
        raise ValueError("Provide target_crs and/or resolution.")
    res = None
    if resolution is not None:
        res = to_float(resolution, "resolution")
        if res <= 0 or not math.isfinite(res):
            raise ValueError("resolution must be a positive number.")

    bands, rows, cols = array.shape
    a, _, c, _, e, f = transform
    left, top, right, bottom = c, f, c + a * cols, f + e * rows
    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs, cols, rows, left=left, bottom=bottom, right=right, top=top,
        resolution=res,
    )
    check_limit(bands * dst_w * dst_h, max_cells, "reprojected cell count")

    destination = np.full((bands, dst_h, dst_w), np.nan)
    rio_reproject(
        source=array, destination=destination,
        src_transform=Affine(a, 0.0, c, 0.0, e, f), src_crs=src_crs,
        dst_transform=dst_transform, dst_crs=dst_crs,
        src_nodata=np.nan, dst_nodata=np.nan, resampling=method,
    )
    out_meta = _output_metadata(
        {"crs": dst_crs.to_string() or str(dst_crs)}, "reproject",
        [dst_transform.a, dst_transform.b, dst_transform.c,
         dst_transform.d, dst_transform.e, dst_transform.f],
        destination.shape,
        {"resampling": method.name, "source_crs": str(meta["crs"]), "band_count": bands,
         **(metadata or {})},
    )
    return make_raster_out(destination[0] if bands == 1 else destination, out_meta,
                           nodata=DEFAULT_NODATA)


# ------------------------------------------------------------- weighted overlay
@capability(
    name="weighted_overlay",
    keywords=[
        "weighted overlay", "suitability", "multi criteria", "mcda", "site selection raster",
        "overlay analysis", "ahp",
        "هم پوشانی وزنی", "تناسب زمین", "چند معیاره", "مکان یابی", "تحلیل تناسب", "روی هم گذاری وزن دار",
    ],
    description=(
        "Multi-criteria suitability: normalise each raster to 0-1 (benefit or cost direction), "
        "weight and sum them into one suitability raster."
    ),
    required_inputs=["rasters", "weights"],
    optional_inputs=["directions", "band_index", "scale_max", "missing", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster",
        "operation": "weighted_overlay",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def weighted_overlay(
    rasters: list[Any],
    weights: list[float],
    directions: list[str] | None = None,
    band_index: int = 1,
    scale_max: float = 100.0,
    missing: str = "mask",
    metadata: dict[str, Any] | None = None,
):
    """
    Weighted overlay (suitability) of rasters on the same grid.

    Args:
        rasters: Criteria rasters with identical shape and transform.
        weights: One non-negative weight per raster (relative; they are normalised).
        directions: Per raster ``benefit`` (higher is better) or ``cost`` (lower is
            better); default all benefit.
        band_index: 1-based band of each raster to use.
        scale_max: Output range is 0..scale_max (default 100).
        missing: ``mask`` makes a pixel nodata if any criterion is nodata; ``ignore``
            re-normalises the weights over the available criteria.
        metadata: Extra output metadata.

    Returns:
        RasterOut with suitability in 0..scale_max (nodata -9999). A constant raster
        normalises to 0.5 (it carries no information).
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    max_cells, max_rasters = _limits(config)
    if not isinstance(rasters, (list, tuple)) or len(rasters) < 2:
        raise ValueError("rasters must be a list of at least two rasters.")
    check_limit(len(rasters), max_rasters, "raster count")
    if not isinstance(weights, (list, tuple)) or len(weights) != len(rasters):
        raise ValueError("weights must have one number per raster.")
    values = [to_float(w, "weights") for w in weights]
    if any(w < 0 or not math.isfinite(w) for w in values) or sum(values) <= 0:
        raise ValueError("weights must be non-negative with a positive sum.")
    chosen_dirs = ["benefit"] * len(rasters) if directions is None else list(directions)
    if len(chosen_dirs) != len(rasters):
        raise ValueError("directions must have one entry per raster.")
    chosen_dirs = [validate_choice(d, DIRECTIONS, "directions") for d in chosen_dirs]
    top = to_float(scale_max, "scale_max")
    if top <= 0:
        raise ValueError("scale_max must be positive.")
    policy = validate_choice(missing, {"mask", "ignore"}, "missing")

    layers = []
    base_meta: dict[str, Any] = {}
    base_t: list[float] = []
    for index, item in enumerate(rasters):
        array, meta, transform = read_raster(item, max_cells=max_cells)
        layer = _band(array, band_index)
        if index == 0:
            base_meta, base_t, shape = meta, transform, layer.shape
        else:
            if layer.shape != shape or not all(
                math.isclose(x, y, rel_tol=_TOL, abs_tol=1e-9) for x, y in zip(transform, base_t, strict=True)
            ):
                raise ValueError("All rasters must have the same shape and transform; align them first.")
            if not _same_crs(base_meta.get("crs"), meta.get("crs")):
                raise ValueError("All rasters must share the same CRS.")
        lo, hi = np.nanmin(layer), np.nanmax(layer)
        if np.isnan(lo):
            raise ValueError(f"raster {index} has no valid pixels.")
        scaled = np.where(np.isnan(layer), np.nan, 0.5) if hi == lo else (layer - lo) / (hi - lo)
        layers.append(1.0 - scaled if chosen_dirs[index] == "cost" else scaled)

    stack = np.stack(layers)
    w = np.array(values)[:, None, None]
    valid = ~np.isnan(stack)
    weighted = np.where(valid, stack, 0.0) * w
    weight_sum = (valid * w).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(weight_sum > 0, weighted.sum(axis=0) / np.where(weight_sum > 0, weight_sum, 1), np.nan)
    if policy == "mask":
        result = np.where(valid.all(axis=0), result, np.nan)
    result = result * top

    out_meta = _output_metadata(
        base_meta, "weighted_overlay", base_t, result.shape,
        {"weights": values, "directions": chosen_dirs, "scale_max": top, "missing": policy,
         "input_count": len(layers), "band_count": 1, **(metadata or {})},
    )
    return make_raster_out(result, out_meta, nodata=DEFAULT_NODATA)


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Raster Tools",
    description="Sample, mosaic, reproject/resample and weighted-overlay (suitability) rasters.",
    author="GeoChat Platform Team",
    permissions=[],
)
