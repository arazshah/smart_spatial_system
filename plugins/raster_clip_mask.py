"""
raster_clip_mask.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_clip_mask

Purpose:
    Clip and/or mask raster-like in-memory data by bbox or GeoJSON geometry.

Capability:
    - clip_mask_raster

Supported input raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported data layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Supported transform:
    - affine list/tuple [a, b, c, d, e, f]
      x = a * col + b * row + c
      y = d * col + e * row + f

Python engine:
    - bbox clipping
    - polygon/point/multipolygon geometry masking
    - bbox fallback for unsupported geometry types

No external dependency is required for tests.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.raster import RasterOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "raster_clip_mask"

VALID_ENGINES = {"python", "auto"}
EPSILON = 1e-12


def _load_clip_config() -> dict[str, Any]:
    """
    Load config/plugins/raster_clip_mask.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_engine(engine: str) -> str:
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _validate_precision(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("precision must be an integer or None.")

    try:
        precision = int(value)
    except Exception as exc:
        raise ValueError("precision must be an integer or None.") from exc

    if precision < 0 or precision > 15:
        raise ValueError("precision must be between 0 and 15.")

    return precision


def _configured_precision(config: dict[str, Any]) -> int | None:
    value = config.get("coordinate_precision", 6)
    return _validate_precision(value)


def _round_value(value: float, precision: int | None) -> float:
    if precision is None:
        return float(value)
    return round(float(value), precision)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _normalize_bbox(bbox: Any) -> list[float] | None:
    """
    Normalize bbox input.

    Supports:
        [minx, miny, maxx, maxy]
        {"minx": ..., "miny": ..., "maxx": ..., "maxy": ...}
    """
    if bbox is None:
        return None

    if isinstance(bbox, dict):
        try:
            values = [
                float(bbox["minx"]),
                float(bbox["miny"]),
                float(bbox["maxx"]),
                float(bbox["maxy"]),
            ]
        except Exception as exc:
            raise ValueError("bbox dict must contain numeric minx, miny, maxx, maxy.") from exc

    elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            values = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
        except Exception as exc:
            raise ValueError("bbox list must contain four numeric values.") from exc

    else:
        raise ValueError("bbox must be [minx, miny, maxx, maxy], dict, or None.")

    minx, miny, maxx, maxy = values

    if minx > maxx:
        raise ValueError("bbox minx must be <= maxx.")

    if miny > maxy:
        raise ValueError("bbox miny must be <= maxy.")

    return values


def _bboxes_intersect(a: list[float] | None, b: list[float] | None) -> bool:
    if not a or not b:
        return False

    return not (
        a[2] < b[0] - EPSILON
        or a[0] > b[2] + EPSILON
        or a[3] < b[1] - EPSILON
        or a[1] > b[3] + EPSILON
    )


def _merge_bbox_arrays(bboxes: list[list[float]]) -> dict[str, float] | None:
    valid = [b for b in bboxes if b and len(b) == 4]

    if not valid:
        return None

    return {
        "minx": min(b[0] for b in valid),
        "miny": min(b[1] for b in valid),
        "maxx": max(b[2] for b in valid),
        "maxy": max(b[3] for b in valid),
    }


def _is_position(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _iter_positions(coords: Any) -> list[tuple[float, float]]:
    if _is_position(coords):
        return [(float(coords[0]), float(coords[1]))]

    if isinstance(coords, (list, tuple)):
        result: list[tuple[float, float]] = []
        for item in coords:
            result.extend(_iter_positions(item))
        return result

    return []


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    if geometry is None:
        return None

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    gtype = geometry.get("type")

    if gtype == "Feature":
        return _geometry_bbox(geometry.get("geometry"))

    if gtype == "FeatureCollection":
        bboxes = []
        for feature in geometry.get("features", []):
            if isinstance(feature, dict):
                bbox = _geometry_bbox(feature)
                if bbox:
                    bboxes.append(bbox)
        merged = _merge_bbox_arrays(bboxes)
        if not merged:
            return None
        return [merged["minx"], merged["miny"], merged["maxx"], merged["maxy"]]

    if gtype == "GeometryCollection":
        bboxes = []
        for sub in geometry.get("geometries", []):
            if isinstance(sub, dict):
                bbox = _geometry_bbox(sub)
                if bbox:
                    bboxes.append(bbox)
        merged = _merge_bbox_arrays(bboxes)
        if not merged:
            return None
        return [merged["minx"], merged["miny"], merged["maxx"], merged["maxy"]]

    coords = geometry.get("coordinates")
    if coords is None:
        return None

    points = _iter_positions(coords)
    if not points:
        return None

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    return [min(xs), min(ys), max(xs), max(ys)]


def _normalize_transform(transform: Any) -> list[float]:
    """
    Normalize affine transform [a, b, c, d, e, f].
    """
    if isinstance(transform, dict):
        try:
            return [
                float(transform.get("a", transform.get("pixel_width"))),
                float(transform.get("b", 0.0)),
                float(transform.get("c", transform.get("origin_x"))),
                float(transform.get("d", 0.0)),
                float(transform.get("e", -abs(float(transform.get("pixel_height"))))),
                float(transform.get("f", transform.get("origin_y"))),
            ]
        except Exception as exc:
            raise ValueError("Invalid transform dict.") from exc

    if isinstance(transform, (list, tuple)) and len(transform) == 6:
        try:
            return [float(item) for item in transform]
        except Exception as exc:
            raise ValueError("transform list must contain six numeric values.") from exc

    raise ValueError("transform must be affine [a, b, c, d, e, f] or dict.")


def _is_supported_north_up_transform(transform: list[float]) -> bool:
    a, b, _c, d, e, _f = transform
    return abs(b) <= EPSILON and abs(d) <= EPSILON and abs(a) > EPSILON and abs(e) > EPSILON


def _pixel_center(row: int, col: int, transform: list[float]) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    x = a * (col + 0.5) + b * (row + 0.5) + c
    y = d * (col + 0.5) + e * (row + 0.5) + f
    return float(x), float(y)


def _pixel_bbox(row: int, col: int, transform: list[float]) -> list[float]:
    a, b, c, d, e, f = transform

    corners = [
        (a * col + b * row + c, d * col + e * row + f),
        (a * (col + 1) + b * row + c, d * (col + 1) + e * row + f),
        (a * col + b * (row + 1) + c, d * col + e * (row + 1) + f),
        (a * (col + 1) + b * (row + 1) + c, d * (col + 1) + e * (row + 1) + f),
    ]

    xs = [float(item[0]) for item in corners]
    ys = [float(item[1]) for item in corners]

    return [min(xs), min(ys), max(xs), max(ys)]


def _raster_bbox(width: int, height: int, transform: list[float]) -> list[float]:
    corners = [
        _pixel_bbox(0, 0, transform),
        _pixel_bbox(0, width - 1, transform),
        _pixel_bbox(height - 1, 0, transform),
        _pixel_bbox(height - 1, width - 1, transform),
    ]

    return [
        min(b[0] for b in corners),
        min(b[1] for b in corners),
        max(b[2] for b in corners),
        max(b[3] for b in corners),
    ]


def _is_2d_array(data: Any) -> bool:
    return isinstance(data, list) and (not data or all(isinstance(row, list) for row in data))


def _is_3d_array(data: Any) -> bool:
    return (
        isinstance(data, list)
        and bool(data)
        and all(isinstance(band, list) for band in data)
        and all(_is_2d_array(band) for band in data)
    )


def _array_shape(data: Any) -> tuple[int, int, int]:
    """
    Return (bands, height, width).
    """
    if _is_3d_array(data) and data and data[0] and isinstance(data[0][0], list):
        bands = len(data)
        height = len(data[0])
        width = len(data[0][0]) if height else 0

        for band in data:
            if len(band) != height:
                raise ValueError("All raster bands must have the same height.")
            for row in band:
                if len(row) != width:
                    raise ValueError("All raster rows must have the same width.")

        return bands, height, width

    if _is_2d_array(data):
        height = len(data)
        width = len(data[0]) if height else 0

        for row in data:
            if len(row) != width:
                raise ValueError("All raster rows must have the same width.")

        return 1, height, width

    raise ValueError("Raster data must be 2D list or 3D band-first list.")


def _slice_array(data: Any, row_start: int, row_stop: int, col_start: int, col_stop: int) -> Any:
    bands, _height, _width = _array_shape(data)

    if bands == 1 and _is_2d_array(data) and not (_is_3d_array(data) and data and data[0] and isinstance(data[0][0], list)):
        return [list(row[col_start:col_stop]) for row in data[row_start:row_stop]]

    return [
        [list(row[col_start:col_stop]) for row in band[row_start:row_stop]]
        for band in data
    ]


def _set_pixel(data: Any, row: int, col: int, value: Any) -> None:
    bands, _height, _width = _array_shape(data)

    if bands == 1 and _is_2d_array(data) and not (_is_3d_array(data) and data and data[0] and isinstance(data[0][0], list)):
        data[row][col] = value
        return

    for band in data:
        band[row][col] = value


def _extract_raster(input_data: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """
    Extract raster data and metadata.
    """
    source_info: dict[str, Any] = {}

    if hasattr(input_data, "data") and not isinstance(input_data, dict):
        data = getattr(input_data, "data")
        metadata = getattr(input_data, "metadata", {}) or {}
        source_info["input_type"] = type(input_data).__name__

    elif isinstance(input_data, dict):
        if "data" in input_data:
            data = input_data["data"]
        elif "array" in input_data:
            data = input_data["array"]
        else:
            raise ValueError("raster dict must contain 'data' or 'array'.")

        metadata = input_data.get("metadata", {}) or {}
        source_info["input_type"] = "dict"

    else:
        raise ValueError("raster must be RasterOut-like object or dict with data/array.")

    if not isinstance(metadata, dict):
        raise ValueError("raster metadata must be a dict.")

    _array_shape(data)

    return deepcopy(data), dict(metadata), source_info


def _get_transform_from_metadata(metadata: dict[str, Any], transform: Any = None) -> list[float]:
    candidate = pick_first(
        transform,
        metadata.get("transform"),
        metadata.get("affine_transform"),
        default=None,
    )

    if candidate is None:
        raise ValueError("transform is required either as input or in raster metadata.")

    normalized = _normalize_transform(candidate)

    if not _is_supported_north_up_transform(normalized):
        raise ValueError("Only non-rotated north-up/south-up affine transforms are supported.")

    return normalized


def _updated_transform(transform: list[float], row_start: int, col_start: int) -> list[float]:
    a, b, c, d, e, f = transform
    new_c = a * col_start + b * row_start + c
    new_f = d * col_start + e * row_start + f
    return [a, b, new_c, d, e, new_f]


def _window_for_bbox(
    *,
    bbox: list[float],
    width: int,
    height: int,
    transform: list[float],
    all_touched: bool,
) -> tuple[int, int, int, int]:
    """
    Find row/col window whose pixels intersect bbox or centers fall inside bbox.
    """
    selected_rows: list[int] = []
    selected_cols: list[int] = []

    for row in range(height):
        row_hit = False

        for col in range(width):
            if all_touched:
                hit = _bboxes_intersect(_pixel_bbox(row, col, transform), bbox)
            else:
                x, y = _pixel_center(row, col, transform)
                hit = bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]

            if hit:
                selected_rows.append(row)
                selected_cols.append(col)
                row_hit = True

        if row_hit:
            continue

    if not selected_rows or not selected_cols:
        raise ValueError("clip bbox does not overlap raster.")

    return min(selected_rows), max(selected_rows) + 1, min(selected_cols), max(selected_cols) + 1


def _point_in_ring(x: float, y: float, ring: list[Any]) -> bool:
    """
    Ray casting point-in-polygon for one ring.
    """
    if not ring or len(ring) < 3:
        return False

    inside = False
    j = len(ring) - 1

    for i in range(len(ring)):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])

        intersects = ((yi > y) != (yj > y)) and (
            x < ((xj - xi) * (y - yi) / ((yj - yi) or EPSILON) + xi)
        )

        if intersects:
            inside = not inside

        j = i

    return inside


def _point_in_polygon(x: float, y: float, polygon_coords: list[Any]) -> bool:
    """
    Check point in polygon with holes.
    """
    if not polygon_coords:
        return False

    outer = polygon_coords[0]
    if not _point_in_ring(x, y, outer):
        return False

    for hole in polygon_coords[1:]:
        if _point_in_ring(x, y, hole):
            return False

    return True


def _point_matches_geometry(x: float, y: float, geometry: dict[str, Any] | None) -> bool:
    """
    Test point against GeoJSON geometry.

    For unsupported geometry types, uses geometry bbox fallback.
    """
    if geometry is None:
        return True

    if not isinstance(geometry, dict):
        return False

    gtype = geometry.get("type")

    if gtype == "Feature":
        return _point_matches_geometry(x, y, geometry.get("geometry"))

    if gtype == "FeatureCollection":
        return any(
            _point_matches_geometry(x, y, feature)
            for feature in geometry.get("features", [])
            if isinstance(feature, dict)
        )

    if gtype == "GeometryCollection":
        return any(
            _point_matches_geometry(x, y, sub)
            for sub in geometry.get("geometries", [])
            if isinstance(sub, dict)
        )

    if gtype == "Point":
        coords = geometry.get("coordinates") or []
        return len(coords) >= 2 and abs(float(coords[0]) - x) <= EPSILON and abs(float(coords[1]) - y) <= EPSILON

    if gtype == "Polygon":
        return _point_in_polygon(x, y, geometry.get("coordinates") or [])

    if gtype == "MultiPolygon":
        return any(
            _point_in_polygon(x, y, polygon)
            for polygon in geometry.get("coordinates") or []
        )

    bbox = _geometry_bbox(geometry)
    if bbox is None:
        return False

    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def _mask_array(
    *,
    data: Any,
    transform: list[float],
    geometry: dict[str, Any] | None,
    bbox: list[float] | None,
    nodata: Any,
) -> tuple[Any, int]:
    """
    Mask pixels outside geometry and/or bbox.
    """
    output = deepcopy(data)
    _bands, height, width = _array_shape(output)
    masked_count = 0

    for row in range(height):
        for col in range(width):
            x, y = _pixel_center(row, col, transform)

            keep = True

            if bbox is not None:
                keep = bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]

            if keep and geometry is not None:
                keep = _point_matches_geometry(x, y, geometry)

            if not keep:
                _set_pixel(output, row, col, nodata)
                masked_count += 1

    return output, masked_count


def _is_geographic_crs(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    text = value.strip().upper()
    return text in {"EPSG:4326", "CRS:84", "OGC:CRS84"}


def _make_raster_out(data: Any, metadata: dict[str, Any]) -> RasterOut:
    """
    Construct RasterOut while being tolerant to SDK constructor differences.

    Some SDK versions define RasterOut as:
        RasterOut(data=..., metadata=...)

    Some older/simple versions may define it as:
        RasterOut(path=..., metadata=...)

    This helper tries common constructor forms, then attaches in-memory raster
    data as .data so tests and downstream plugins can consume it consistently.
    """
    metadata = dict(metadata or {})

    last_error: Exception | None = None
    obj: Any = None

    constructor_attempts = [
        lambda: RasterOut(data=data, metadata=metadata),
        lambda: RasterOut(array=data, metadata=metadata),
        lambda: RasterOut(payload=data, metadata=metadata),
        lambda: RasterOut(values=data, metadata=metadata),
        lambda: RasterOut(path=metadata.get("path", ""), metadata=metadata),
        lambda: RasterOut(file_path=metadata.get("path", ""), metadata=metadata),
        lambda: RasterOut(uri=metadata.get("path", ""), metadata=metadata),
        lambda: RasterOut(metadata=metadata),
        lambda: RasterOut(),
    ]

    for attempt in constructor_attempts:
        try:
            obj = attempt()
            break
        except TypeError as exc:
            last_error = exc
            continue

    if obj is None:
        raise TypeError(f"Could not construct RasterOut: {last_error}") from last_error

    # Ensure in-memory raster data is accessible in a stable way.
    try:
        setattr(obj, "data", data)
    except Exception:
        try:
            object.__setattr__(obj, "data", data)
        except Exception:
            pass

    # Also attach aliases when possible for compatibility.
    for attr_name in ("array", "payload"):
        try:
            if not hasattr(obj, attr_name):
                setattr(obj, attr_name, data)
        except Exception:
            try:
                object.__setattr__(obj, attr_name, data)
            except Exception:
                pass

    # Ensure metadata is also accessible and up to date.
    try:
        setattr(obj, "metadata", metadata)
    except Exception:
        try:
            object.__setattr__(obj, "metadata", metadata)
        except Exception:
            pass

    return obj


@capability(
    name="clip_mask_raster",
    keywords=[
        "raster clip",
        "clip raster",
        "raster mask",
        "mask raster",
        "crop raster",
        "clip by bbox",
        "clip by polygon",
        "mask by geometry",
        "برش رستر",
        "ماسک رستر",
        "کلیپ رستر",
        "برش با محدوده",
        "برش با پلیگون",
    ],
    description="Clip and mask raster data by bbox or GeoJSON geometry.",
    required_inputs=["raster"],
    optional_inputs=[
        "bbox",
        "mask_geometry",
        "transform",
        "crop",
        "apply_mask",
        "all_touched",
        "nodata",
        "engine",
        "precision",
        "source_crs",
        "metadata",
    ],
    output_kind="raster",
    permissions=[],
    metadata={
        "category": "processing",
        "data_type": "raster",
        "operation": "clip_mask",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "raster_processing",
        "config_aware": True,
        "bbox_clip_supported": True,
        "geometry_mask_supported": True,
        "routable": True,
    },
)
def clip_mask_raster(
    raster: Any,
    bbox: list[float] | dict[str, float] | None = None,
    mask_geometry: dict[str, Any] | None = None,
    transform: list[float] | dict[str, Any] | None = None,
    crop: bool | None = None,
    apply_mask: bool | None = None,
    all_touched: bool | None = None,
    nodata: Any = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RasterOut:
    """
    Clip and/or mask raster data.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        bbox:
            Optional bbox [minx, miny, maxx, maxy].
        mask_geometry:
            Optional GeoJSON geometry/Feature/FeatureCollection.
        transform:
            Optional affine transform. If omitted, metadata["transform"] is used.
        crop:
            If True, output raster is cropped to bbox/mask bounds.
        apply_mask:
            If True, pixels outside bbox/geometry are set to nodata.
        all_touched:
            If True, crop window uses pixel bbox intersection instead of center.
        nodata:
            Nodata value for masked pixels.
        engine:
            python | auto.
        precision:
            Precision for output transform/bounds.
        source_crs:
            CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        RasterOut.
    """
    config = _load_clip_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    final_crop = bool(
        pick_first(crop, config.get("default_crop"), default=True)
    )

    final_apply_mask = bool(
        pick_first(apply_mask, config.get("default_mask"), default=True)
    )

    final_all_touched = bool(
        pick_first(all_touched, config.get("default_all_touched"), default=False)
    )

    final_nodata = pick_first(nodata, config.get("default_nodata"), default=None)

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    preserve_metadata = bool(config.get("preserve_metadata", True))

    data, input_metadata, source_info = _extract_raster(raster)
    source_transform = _get_transform_from_metadata(input_metadata, transform=transform)

    _bands, input_height, input_width = _array_shape(data)

    final_source_crs = pick_first(source_crs, input_metadata.get("crs"), config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    final_bbox = _normalize_bbox(bbox)
    geometry_bbox = _geometry_bbox(mask_geometry)

    clip_bbox = final_bbox
    if clip_bbox is None and mask_geometry is not None:
        clip_bbox = geometry_bbox

    raster_bounds_before = _raster_bbox(input_width, input_height, source_transform)

    row_start = 0
    row_stop = input_height
    col_start = 0
    col_stop = input_width

    if final_crop and clip_bbox is not None:
        row_start, row_stop, col_start, col_stop = _window_for_bbox(
            bbox=clip_bbox,
            width=input_width,
            height=input_height,
            transform=source_transform,
            all_touched=final_all_touched,
        )

    clipped_data = _slice_array(data, row_start, row_stop, col_start, col_stop)
    output_transform = _updated_transform(source_transform, row_start, col_start)

    masked_count = 0
    if final_apply_mask and (final_bbox is not None or mask_geometry is not None):
        clipped_data, masked_count = _mask_array(
            data=clipped_data,
            transform=output_transform,
            geometry=mask_geometry,
            bbox=final_bbox,
            nodata=final_nodata,
        )

    _out_bands, output_height, output_width = _array_shape(clipped_data)
    raster_bounds_after = _raster_bbox(output_width, output_height, output_transform) if output_width and output_height else None

    rounded_transform = [
        _round_value(item, final_precision)
        for item in output_transform
    ]

    if raster_bounds_after is not None:
        rounded_bounds = {
            "minx": _round_value(raster_bounds_after[0], final_precision),
            "miny": _round_value(raster_bounds_after[1], final_precision),
            "maxx": _round_value(raster_bounds_after[2], final_precision),
            "maxy": _round_value(raster_bounds_after[3], final_precision),
        }
    else:
        rounded_bounds = None

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Raster clip/mask is being evaluated on a geographic CRS. "
            "For metric raster workflows, consider reprojecting first."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    output_metadata = {
        **base_metadata,
        "source": "raster_clip_mask",
        "loader": PLUGIN_ID,
        "operation": "clip_mask",
        "engine_requested": final_engine,
        "engine_used": "python",
        "input_width": input_width,
        "input_height": input_height,
        "input_band_count": _bands,
        "output_width": output_width,
        "output_height": output_height,
        "output_band_count": _out_bands,
        "bbox": final_bbox,
        "mask_geometry_applied": mask_geometry is not None,
        "geometry_bbox": geometry_bbox,
        "crop": final_crop,
        "apply_mask": final_apply_mask,
        "all_touched": final_all_touched,
        "nodata": final_nodata,
        "masked_pixel_count": masked_count,
        "window": {
            "row_start": row_start,
            "row_stop": row_stop,
            "col_start": col_start,
            "col_stop": col_stop,
        },
        "transform": rounded_transform,
        "bounds": rounded_bounds,
        "input_bounds": {
            "minx": _round_value(raster_bounds_before[0], final_precision),
            "miny": _round_value(raster_bounds_before[1], final_precision),
            "maxx": _round_value(raster_bounds_before[2], final_precision),
            "maxy": _round_value(raster_bounds_before[3], final_precision),
        },
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "created_at": _utc_now_iso(),
        **source_info,
        **user_metadata,
    }

    return _make_raster_out(
        data=clipped_data,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Raster Clip Mask",
    description=(
        "Clips and masks raster-like data by bbox or GeoJSON geometry. "
        "Pure-python implementation for stable plugin execution."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
