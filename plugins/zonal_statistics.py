"""
zonal_statistics.py

GeoChat SDK Plugin
==================

Plugin ID:
    zonal_statistics

Purpose:
    Calculate raster statistics inside vector zone geometries.

Capability:
    - calculate_zonal_statistics

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Supported zone forms:
    - VectorOut-like object with .features
    - FeatureCollection
    - Feature
    - list[Feature]

Engine:
    - python

No external dependency is required.
"""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs
from plugins.raster_clip_mask import (
    _array_shape,
    _bboxes_intersect,
    _extract_raster,
    _geometry_bbox,
    _get_transform_from_metadata,
    _is_geographic_crs,
    _pixel_bbox,
    _pixel_center,
    _point_matches_geometry,
)


PLUGIN_ID = "zonal_statistics"

VALID_ENGINES = {"python", "auto"}

VALID_STATS = {
    "count",
    "valid_count",
    "nodata_count",
    "numeric_count",
    "non_numeric_count",
    "min",
    "max",
    "sum",
    "mean",
    "median",
    "sample_stdev",
    "population_stdev",
    "unique_count",
    "majority",
    "minority",
}

DEFAULT_STATS = [
    "count",
    "valid_count",
    "nodata_count",
    "numeric_count",
    "min",
    "max",
    "sum",
    "mean",
    "median",
    "sample_stdev",
    "population_stdev",
    "unique_count",
    "majority",
    "minority",
]


def _load_zonal_config() -> dict[str, Any]:
    """
    Load config/plugins/zonal_statistics.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    """
    Return current UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def _validate_engine(engine: str) -> str:
    """
    Validate zonal statistics engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _validate_precision(value: Any) -> int | None:
    """
    Validate numeric precision.
    """
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
    """
    Return configured precision.
    """
    value = config.get("coordinate_precision", 6)
    return _validate_precision(value)


def _round_value(value: Any, precision: int | None) -> Any:
    """
    Round float values.
    """
    if value is None:
        return None

    if not isinstance(value, float):
        return value

    if precision is None:
        return value

    return round(value, precision)


def _is_number(value: Any) -> bool:
    """
    True if finite number excluding bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_nan(value: Any) -> bool:
    """
    True if value is NaN.
    """
    try:
        return isinstance(value, float) and math.isnan(value)
    except Exception:
        return False


def _is_nodata(value: Any, nodata: Any) -> bool:
    """
    Check if raster value should be treated as nodata.
    """
    if value is None:
        return True

    if _is_nan(value):
        return True

    if nodata is None:
        return False

    if _is_nan(nodata):
        return _is_nan(value)

    try:
        return value == nodata
    except Exception:
        return False


def _validate_band_index(value: Any, band_count: int) -> int:
    """
    Validate 1-based band index.
    """
    if isinstance(value, bool):
        raise ValueError("band_index must be a positive integer.")

    try:
        band_index = int(value)
    except Exception as exc:
        raise ValueError("band_index must be a positive integer.") from exc

    if band_index <= 0:
        raise ValueError("band_index must be >= 1.")

    if band_index > band_count:
        raise ValueError(f"band_index {band_index} is out of range. Raster has {band_count} band(s).")

    return band_index


def _normalize_stats(stats: Any) -> list[str]:
    """
    Normalize requested statistics.
    """
    if stats is None:
        return list(DEFAULT_STATS)

    if isinstance(stats, str):
        values = [stats]
    elif isinstance(stats, (list, tuple, set)):
        values = list(stats)
    else:
        raise ValueError("stats must be string, list, tuple, set, or None.")

    result: list[str] = []

    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("stats items must be non-empty strings.")

        text = item.strip().lower()

        if text not in VALID_STATS:
            raise ValueError(f"Unsupported statistic '{text}'. Valid stats: {sorted(VALID_STATS)}")

        if text not in result:
            result.append(text)

    return result


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    """
    Return configured output field names.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in zonal_statistics config must be a dict.")

    return {
        "zone_index_field": str(fields.get("zone_index_field", "zone_index")),
        "zone_id_field": str(fields.get("zone_id_field", "zone_id")),
        "status_field": str(fields.get("status_field", "status")),
        "engine_field": str(fields.get("engine_field", "engine")),
        "band_index_field": str(fields.get("band_index_field", "band_index")),
    }


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize GeoJSON Feature.
    """
    if not isinstance(feature, dict):
        raise ValueError(f"Zone feature at index {index} must be a dict/object.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Zone item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if properties is None:
        properties = {}

    if not isinstance(properties, dict):
        raise ValueError(f"Zone feature properties at index {index} must be dict/object or null.")

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": dict(properties),
    }


def _extract_zones(input_data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract zone features from VectorOut, FeatureCollection, Feature or list[Feature].
    """
    source_info: dict[str, Any] = {}

    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw_features = getattr(input_data, "features")
        source_info["zones_input_type"] = type(input_data).__name__

        source_metadata = getattr(input_data, "metadata", None)
        if isinstance(source_metadata, dict):
            source_info["zones_input_metadata"] = source_metadata

    elif isinstance(input_data, dict):
        geojson_type = input_data.get("type")
        source_info["zones_input_geojson_type"] = geojson_type

        if geojson_type == "FeatureCollection":
            raw_features = input_data.get("features", [])
            if not isinstance(raw_features, list):
                raise ValueError("FeatureCollection.features must be a list.")
        elif geojson_type == "Feature":
            raw_features = [input_data]
        else:
            raise ValueError("zones dict must be GeoJSON FeatureCollection or Feature.")

    elif isinstance(input_data, list):
        raw_features = input_data
        source_info["zones_input_geojson_type"] = "FeatureList"

    else:
        raise ValueError("zones must be VectorOut, list, FeatureCollection dict or Feature dict.")

    if not isinstance(raw_features, list):
        raise ValueError("Extracted zones must be a list.")

    zones = [_normalize_feature(item, idx) for idx, item in enumerate(raw_features)]
    return zones, source_info


def _get_property_path(obj: dict[str, Any], path: str, default: Any = None) -> Any:
    """
    Read dot-separated property path.
    """
    if not isinstance(path, str) or not path.strip():
        return default

    current: Any = obj

    for part in path.strip().split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except Exception:
                return default
        else:
            return default

    return current


def _pixel_value(data: Any, *, row: int, col: int, band_index: int) -> Any:
    """
    Read raster pixel value using 1-based band_index.
    """
    bands, _height, _width = _array_shape(data)

    if bands == 1:
        # 2D raster
        if data and isinstance(data[0], list) and (not data[0] or not isinstance(data[0][0], list)):
            return data[row][col]

        # 3D single-band raster
        return data[0][row][col]

    return data[band_index - 1][row][col]


def _point_matches_zone(x: float, y: float, geometry: dict[str, Any] | None) -> bool:
    """
    Test point against zone geometry.

    Unlike raster_clip_mask helper, None zone geometry should not match the whole raster.
    """
    if geometry is None:
        return False

    return bool(_point_matches_geometry(x, y, geometry))


def _pixel_matches_zone(
    *,
    row: int,
    col: int,
    transform: list[float],
    geometry: dict[str, Any] | None,
    geometry_bbox: list[float] | None,
    all_touched: bool,
) -> bool:
    """
    Decide whether a pixel belongs to a zone.
    """
    if geometry is None:
        return False

    if all_touched:
        if geometry_bbox is None:
            return False
        return _bboxes_intersect(_pixel_bbox(row, col, transform), geometry_bbox)

    x, y = _pixel_center(row, col, transform)
    return _point_matches_zone(x, y, geometry)


def _safe_sort_key(value: Any) -> tuple[str, str]:
    """
    Stable sort key for mixed values.
    """
    return (type(value).__name__, repr(value))


def _majority_minority(values: list[Any]) -> tuple[Any, Any]:
    """
    Calculate majority and minority values.

    Ties are resolved by stable representation ordering.
    """
    if not values:
        return None, None

    counter = Counter(values)
    rows = [{"value": value, "count": count} for value, count in counter.items()]
    rows.sort(key=lambda item: (-item["count"], _safe_sort_key(item["value"])))

    majority = rows[0]["value"]

    rows_min = list(rows)
    rows_min.sort(key=lambda item: (item["count"], _safe_sort_key(item["value"])))
    minority = rows_min[0]["value"]

    return majority, minority


def _calculate_zone_stats(
    values: list[Any],
    *,
    nodata: Any,
    precision: int | None,
) -> dict[str, Any]:
    """
    Calculate all supported statistics for one zone.
    """
    count = len(values)

    non_nodata_values = [
        value for value in values
        if not _is_nodata(value, nodata)
    ]

    numeric_values = [
        float(value)
        for value in non_nodata_values
        if _is_number(value)
    ]

    non_numeric_count = len(non_nodata_values) - len(numeric_values)

    sorted_numeric = sorted(numeric_values)
    numeric_count = len(sorted_numeric)

    if numeric_count:
        total = float(sum(sorted_numeric))
        mean = total / numeric_count

        if numeric_count % 2 == 1:
            median = sorted_numeric[numeric_count // 2]
        else:
            median = (sorted_numeric[(numeric_count // 2) - 1] + sorted_numeric[numeric_count // 2]) / 2.0

        if numeric_count > 1:
            sample_var = sum((item - mean) ** 2 for item in sorted_numeric) / (numeric_count - 1)
            sample_stdev = math.sqrt(sample_var)
        else:
            sample_stdev = None

        population_var = sum((item - mean) ** 2 for item in sorted_numeric) / numeric_count
        population_stdev = math.sqrt(population_var)

        min_value = float(sorted_numeric[0])
        max_value = float(sorted_numeric[-1])
    else:
        total = None
        mean = None
        median = None
        sample_stdev = None
        population_stdev = None
        min_value = None
        max_value = None

    majority, minority = _majority_minority(non_nodata_values)

    output = {
        "count": count,
        "valid_count": len(non_nodata_values),
        "nodata_count": count - len(non_nodata_values),
        "numeric_count": numeric_count,
        "non_numeric_count": non_numeric_count,
        "min": _round_value(min_value, precision),
        "max": _round_value(max_value, precision),
        "sum": _round_value(total, precision),
        "mean": _round_value(mean, precision),
        "median": _round_value(median, precision),
        "sample_stdev": _round_value(sample_stdev, precision),
        "population_stdev": _round_value(population_stdev, precision),
        "unique_count": len(set(repr(value) for value in non_nodata_values)),
        "majority": majority,
        "minority": minority,
    }

    return output


def _collect_zone_values(
    *,
    data: Any,
    transform: list[float],
    zone_geometry: dict[str, Any] | None,
    band_index: int,
    all_touched: bool,
) -> list[Any]:
    """
    Collect raster values inside one zone.
    """
    _bands, height, width = _array_shape(data)
    geometry_bbox = _geometry_bbox(zone_geometry)

    if zone_geometry is None or geometry_bbox is None:
        return []

    values: list[Any] = []

    for row in range(height):
        for col in range(width):
            if _pixel_matches_zone(
                row=row,
                col=col,
                transform=transform,
                geometry=zone_geometry,
                geometry_bbox=geometry_bbox,
                all_touched=all_touched,
            ):
                values.append(
                    _pixel_value(
                        data,
                        row=row,
                        col=col,
                        band_index=band_index,
                    )
                )

    return values


def _make_output_feature(
    *,
    zone: dict[str, Any],
    zone_index: int,
    zone_id: Any,
    zone_stats: dict[str, Any],
    requested_stats: list[str],
    stat_prefix: str,
    output_fields: dict[str, str],
    preserve_properties: bool,
    include_zone_geometry: bool,
    engine_used: str,
    band_index: int,
    status: str,
) -> dict[str, Any]:
    """
    Build output Feature for one zone.
    """
    properties = deepcopy(zone.get("properties") or {}) if preserve_properties else {}

    properties[output_fields["zone_index_field"]] = zone_index
    properties[output_fields["zone_id_field"]] = zone_id
    properties[output_fields["status_field"]] = status
    properties[output_fields["engine_field"]] = engine_used
    properties[output_fields["band_index_field"]] = band_index

    for stat_name in requested_stats:
        properties[f"{stat_prefix}{stat_name}"] = zone_stats.get(stat_name)

    return {
        "type": "Feature",
        "geometry": deepcopy(zone.get("geometry")) if include_zone_geometry else None,
        "properties": properties,
    }


def _merge_bbox_arrays(bboxes: list[list[float]]) -> dict[str, float] | None:
    """
    Merge bbox arrays.
    """
    valid = [bbox for bbox in bboxes if bbox and len(bbox) == 4]

    if not valid:
        return None

    return {
        "minx": min(bbox[0] for bbox in valid),
        "miny": min(bbox[1] for bbox in valid),
        "maxx": max(bbox[2] for bbox in valid),
        "maxy": max(bbox[3] for bbox in valid),
    }


def _build_vector_metadata(features: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build VectorOut metadata.
    """
    geometry_types: dict[str, int] = {}
    bboxes: list[list[float]] = []

    for feature in features:
        geometry = feature.get("geometry")

        if isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "Unknown")
            try:
                bbox = _geometry_bbox(geometry)
                if bbox is not None:
                    bboxes.append(bbox)
            except Exception:
                pass
        elif geometry is None:
            gtype = "Null"
        else:
            gtype = "Invalid"

        geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

    return {
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": _merge_bbox_arrays(bboxes),
    }


@capability(
    name="calculate_zonal_statistics",
    keywords=[
        "zonal statistics",
        "raster zonal statistics",
        "statistics by zone",
        "raster stats by polygon",
        "mean raster in polygon",
        "ndvi by district",
        "dem statistics",
        "آمار ناحیه‌ای",
        "آمار زونال",
        "آمار رستر در پلیگون",
        "میانگین رستر",
        "آمار ناحیه",
        "آمار منطقه‌ای",
    ],
    description="Calculate raster statistics inside vector zone geometries.",
    required_inputs=["raster", "zones"],
    optional_inputs=[
        "stats",
        "band_index",
        "zone_id_field",
        "transform",
        "nodata",
        "all_touched",
        "include_zone_geometry",
        "stat_prefix",
        "engine",
        "precision",
        "source_crs",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster_vector",
        "operation": "zonal_statistics",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "raster_vector_analysis",
        "config_aware": True,
        "raster_vector_fusion": True,
        "routable": True,
    },
)
def calculate_zonal_statistics(
    raster: Any,
    zones: Any,
    stats: str | list[str] | None = None,
    band_index: int | None = None,
    zone_id_field: str | None = None,
    transform: list[float] | dict[str, Any] | None = None,
    nodata: Any = None,
    all_touched: bool | None = None,
    include_zone_geometry: bool | None = None,
    stat_prefix: str | None = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Calculate zonal statistics for raster values inside vector zones.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        zones:
            VectorOut-like object, FeatureCollection, Feature, or list[Feature].
        stats:
            Requested statistics.
        band_index:
            1-based band index.
        zone_id_field:
            Optional property path used as zone ID.
        transform:
            Optional affine transform. If omitted, raster metadata transform is used.
        nodata:
            Nodata value. If omitted, raster metadata/config nodata is used.
        all_touched:
            If True, pixel bbox intersection with zone bbox is used.
            If False, pixel center must be inside geometry.
        include_zone_geometry:
            If True, output features preserve zone geometry.
        stat_prefix:
            Prefix for statistic property names.
        engine:
            python | auto.
        precision:
            Rounding precision for floating-point statistics.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with one feature per zone and zonal statistic properties.
    """
    config = _load_zonal_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    data, raster_metadata, raster_info = _extract_raster(raster)
    source_transform = _get_transform_from_metadata(raster_metadata, transform=transform)

    band_count, raster_height, raster_width = _array_shape(data)

    final_band_index = _validate_band_index(
        pick_first(band_index, config.get("default_band_index"), default=1),
        band_count=band_count,
    )

    final_stats = _normalize_stats(
        pick_first(stats, config.get("default_stats"), default=DEFAULT_STATS)
    )

    final_all_touched = bool(
        pick_first(all_touched, config.get("default_all_touched"), default=False)
    )

    final_include_zone_geometry = bool(
        pick_first(include_zone_geometry, config.get("default_include_zone_geometry"), default=True)
    )

    preserve_properties = bool(config.get("preserve_properties", True))

    final_stat_prefix = str(
        pick_first(stat_prefix, config.get("stat_prefix"), default="zonal_")
    )

    final_nodata = pick_first(nodata, raster_metadata.get("nodata"), config.get("default_nodata"), default=None)

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    final_zone_id_field = zone_id_field

    final_source_crs = pick_first(source_crs, raster_metadata.get("crs"), config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    output_fields = _configured_fields(config)

    zone_features, zones_info = _extract_zones(zones)

    output_features: list[dict[str, Any]] = []

    total_selected_pixel_count = 0
    total_valid_pixel_count = 0
    empty_zone_count = 0

    for zone_index, zone in enumerate(zone_features):
        zone_props = zone.get("properties") or {}

        if final_zone_id_field:
            zone_id = _get_property_path(zone_props, final_zone_id_field, default=zone_index)
        else:
            zone_id = zone_props.get("id", zone_index)

        values = _collect_zone_values(
            data=data,
            transform=source_transform,
            zone_geometry=zone.get("geometry"),
            band_index=final_band_index,
            all_touched=final_all_touched,
        )

        zone_stats = _calculate_zone_stats(
            values,
            nodata=final_nodata,
            precision=final_precision,
        )

        total_selected_pixel_count += zone_stats["count"]
        total_valid_pixel_count += zone_stats["valid_count"]

        status = "success"
        if zone_stats["count"] == 0:
            status = "empty"
            empty_zone_count += 1

        output_features.append(
            _make_output_feature(
                zone=zone,
                zone_index=zone_index,
                zone_id=zone_id,
                zone_stats=zone_stats,
                requested_stats=final_stats,
                stat_prefix=final_stat_prefix,
                output_fields=output_fields,
                preserve_properties=preserve_properties,
                include_zone_geometry=final_include_zone_geometry,
                engine_used="python",
                band_index=final_band_index,
                status=status,
            )
        )

    vector_metadata = _build_vector_metadata(output_features)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Zonal statistics are being evaluated on a geographic CRS. "
            "For area-weighted or metric workflows, consider reprojecting first."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "zonal_statistics",
        "loader": PLUGIN_ID,
        "operation": "zonal_statistics",
        "engine_requested": final_engine,
        "engine_used": "python",
        "raster_width": raster_width,
        "raster_height": raster_height,
        "raster_band_count": band_count,
        "band_index": final_band_index,
        "zone_count": len(zone_features),
        "empty_zone_count": empty_zone_count,
        "total_selected_pixel_count": total_selected_pixel_count,
        "total_valid_pixel_count": total_valid_pixel_count,
        "stats": final_stats,
        "stat_prefix": final_stat_prefix,
        "zone_id_field": final_zone_id_field,
        "nodata": final_nodata,
        "all_touched": final_all_touched,
        "include_zone_geometry": final_include_zone_geometry,
        "coordinate_precision": final_precision,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "created_at": _utc_now_iso(),
        **raster_info,
        **zones_info,
        **vector_metadata,
        **user_metadata,
    }

    return VectorOut(
        features=output_features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Zonal Statistics",
    description=(
        "Calculates raster statistics inside vector zone geometries. "
        "Provides pure-python raster-vector fusion for zonal analysis."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
