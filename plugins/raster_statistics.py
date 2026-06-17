"""
raster_statistics.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_statistics

Purpose:
    Calculate descriptive statistics for raster-like in-memory data.

Capability:
    - calculate_raster_statistics

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Output:
    Plain dict:
        {
            "statistics": [...],
            "summary": {...},
            "metadata": {...}
        }

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

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs
from plugins.raster_clip_mask import (
    _array_shape,
    _extract_raster,
    _is_geographic_crs,
)


PLUGIN_ID = "raster_statistics"

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
    "p25",
    "p75",
    "unique_count",
    "majority",
    "minority",
    "histogram",
}

DEFAULT_STATS = [
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
    "p25",
    "p75",
    "unique_count",
    "majority",
    "minority",
]


def _load_raster_statistics_config() -> dict[str, Any]:
    """
    Load config/plugins/raster_statistics.yaml if available.
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
    Validate execution engine.
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
    Return configured coordinate/numeric precision.
    """
    return _validate_precision(config.get("coordinate_precision", 6))


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
    Return True for finite numbers excluding bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_nan(value: Any) -> bool:
    """
    Return True if value is NaN.
    """
    try:
        return isinstance(value, float) and math.isnan(value)
    except Exception:
        return False


def _is_nodata(value: Any, nodata: Any) -> bool:
    """
    Check nodata logic.
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


def _validate_band_index(value: Any, band_count: int) -> int:
    """
    Validate 1-based band index.
    """
    if isinstance(value, bool):
        raise ValueError("band index must be a positive integer.")

    try:
        band_index = int(value)
    except Exception as exc:
        raise ValueError("band index must be a positive integer.") from exc

    if band_index <= 0:
        raise ValueError("band index must be >= 1.")

    if band_index > band_count:
        raise ValueError(f"band index {band_index} is out of range. Raster has {band_count} band(s).")

    return band_index


def _normalize_bands(bands: Any, band_count: int) -> list[int]:
    """
    Normalize selected bands.

    Band indices are 1-based.
    None means all bands.
    """
    if bands is None:
        return list(range(1, band_count + 1))

    if isinstance(bands, int) and not isinstance(bands, bool):
        values = [bands]
    elif isinstance(bands, str):
        values = [bands]
    elif isinstance(bands, (list, tuple, set)):
        values = list(bands)
    else:
        raise ValueError("bands must be int, string, list, tuple, set, or None.")

    result: list[int] = []

    for item in values:
        band_index = _validate_band_index(item, band_count)
        if band_index not in result:
            result.append(band_index)

    return result


def _band_values(data: Any, band_index: int) -> list[Any]:
    """
    Flatten values for selected 1-based band.
    """
    bands, height, width = _array_shape(data)

    values: list[Any] = []

    if bands == 1:
        # 2D raster
        if data and isinstance(data[0], list) and (not data[0] or not isinstance(data[0][0], list)):
            for row in data:
                values.extend(row)
            return values

        # 3D single-band raster
        for row in data[0]:
            values.extend(row)
        return values

    band = data[band_index - 1]
    for row in band:
        values.extend(row)

    return values


def _safe_sort_key(value: Any) -> tuple[str, str]:
    """
    Stable sorting key for mixed values.
    """
    return (type(value).__name__, repr(value))


def _majority_minority(values: list[Any]) -> tuple[Any, Any]:
    """
    Calculate majority/minority using repr-based counting.

    This avoids hashability problems for uncommon scalar-like values.
    """
    if not values:
        return None, None

    repr_to_value: dict[str, Any] = {}
    counter: Counter[str] = Counter()

    for value in values:
        key = repr(value)
        repr_to_value.setdefault(key, value)
        counter[key] += 1

    rows = [
        {
            "key": key,
            "value": repr_to_value[key],
            "count": count,
        }
        for key, count in counter.items()
    ]

    rows.sort(key=lambda item: (-item["count"], _safe_sort_key(item["value"])))
    majority = rows[0]["value"]

    rows.sort(key=lambda item: (item["count"], _safe_sort_key(item["value"])))
    minority = rows[0]["value"]

    return majority, minority


def _percentile(sorted_values: list[float], percentile: float) -> float | None:
    """
    Linear interpolation percentile.

    percentile:
        0..100
    """
    if not sorted_values:
        return None

    if percentile <= 0:
        return float(sorted_values[0])

    if percentile >= 100:
        return float(sorted_values[-1])

    n = len(sorted_values)
    position = (n - 1) * (percentile / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))

    if lower == upper:
        return float(sorted_values[lower])

    fraction = position - lower

    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction)


def _histogram(
    numeric_values: list[float],
    *,
    bins: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Calculate simple equal-width histogram.
    """
    if bins <= 0:
        raise ValueError("histogram bins must be > 0.")

    if not numeric_values:
        return []

    min_value = float(min(numeric_values))
    max_value = float(max(numeric_values))

    if min_value == max_value:
        return [
            {
                "bin": 0,
                "min": _round_value(min_value, precision),
                "max": _round_value(max_value, precision),
                "count": len(numeric_values),
            }
        ]

    width = (max_value - min_value) / bins

    rows = [
        {
            "bin": idx,
            "min": min_value + idx * width,
            "max": min_value + (idx + 1) * width,
            "count": 0,
        }
        for idx in range(bins)
    ]

    for value in numeric_values:
        if value == max_value:
            idx = bins - 1
        else:
            idx = int((value - min_value) / width)
            idx = max(0, min(idx, bins - 1))

        rows[idx]["count"] += 1

    for row in rows:
        row["min"] = _round_value(float(row["min"]), precision)
        row["max"] = _round_value(float(row["max"]), precision)

    return rows


def _calculate_stats_for_values(
    values: list[Any],
    *,
    nodata: Any,
    requested_stats: list[str],
    histogram_bins: int,
    precision: int | None,
) -> dict[str, Any]:
    """
    Calculate requested statistics for flattened raster values.
    """
    count = len(values)

    valid_values = [
        value for value in values
        if not _is_nodata(value, nodata)
    ]

    numeric_values = [
        float(value)
        for value in valid_values
        if _is_number(value)
    ]

    sorted_numeric = sorted(numeric_values)
    numeric_count = len(sorted_numeric)
    non_numeric_count = len(valid_values) - numeric_count

    if numeric_count:
        total = float(sum(sorted_numeric))
        mean = total / numeric_count
        min_value = float(sorted_numeric[0])
        max_value = float(sorted_numeric[-1])

        median = _percentile(sorted_numeric, 50)

        if numeric_count > 1:
            sample_variance = sum((value - mean) ** 2 for value in sorted_numeric) / (numeric_count - 1)
            sample_stdev = math.sqrt(sample_variance)
        else:
            sample_stdev = None

        population_variance = sum((value - mean) ** 2 for value in sorted_numeric) / numeric_count
        population_stdev = math.sqrt(population_variance)

        p25 = _percentile(sorted_numeric, 25)
        p75 = _percentile(sorted_numeric, 75)
    else:
        total = None
        mean = None
        min_value = None
        max_value = None
        median = None
        sample_stdev = None
        population_stdev = None
        p25 = None
        p75 = None

    majority, minority = _majority_minority(valid_values)

    all_stats = {
        "count": count,
        "valid_count": len(valid_values),
        "nodata_count": count - len(valid_values),
        "numeric_count": numeric_count,
        "non_numeric_count": non_numeric_count,
        "min": _round_value(min_value, precision),
        "max": _round_value(max_value, precision),
        "sum": _round_value(total, precision),
        "mean": _round_value(mean, precision),
        "median": _round_value(median, precision),
        "sample_stdev": _round_value(sample_stdev, precision),
        "population_stdev": _round_value(population_stdev, precision),
        "p25": _round_value(p25, precision),
        "p75": _round_value(p75, precision),
        "unique_count": len(set(repr(value) for value in valid_values)),
        "majority": majority,
        "minority": minority,
        "histogram": _histogram(
            sorted_numeric,
            bins=histogram_bins,
            precision=precision,
        ),
    }

    return {
        stat_name: all_stats[stat_name]
        for stat_name in requested_stats
    }


def _summarize_band_stats(band_statistics: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build summary from per-band statistics.
    """
    total_count = sum(int(item.get("count", 0) or 0) for item in band_statistics)
    total_valid_count = sum(int(item.get("valid_count", 0) or 0) for item in band_statistics)
    total_nodata_count = sum(int(item.get("nodata_count", 0) or 0) for item in band_statistics)
    total_numeric_count = sum(int(item.get("numeric_count", 0) or 0) for item in band_statistics)

    means = [
        float(item["mean"])
        for item in band_statistics
        if item.get("mean") is not None and _is_number(item.get("mean"))
    ]

    mins = [
        float(item["min"])
        for item in band_statistics
        if item.get("min") is not None and _is_number(item.get("min"))
    ]

    maxs = [
        float(item["max"])
        for item in band_statistics
        if item.get("max") is not None and _is_number(item.get("max"))
    ]

    return {
        "band_result_count": len(band_statistics),
        "total_count": total_count,
        "total_valid_count": total_valid_count,
        "total_nodata_count": total_nodata_count,
        "total_numeric_count": total_numeric_count,
        "global_min": min(mins) if mins else None,
        "global_max": max(maxs) if maxs else None,
        "mean_of_band_means": (sum(means) / len(means)) if means else None,
    }


@capability(
    name="calculate_raster_statistics",
    keywords=[
        "raster statistics",
        "raster stats",
        "image statistics",
        "ndvi statistics",
        "dem statistics",
        "min max mean raster",
        "histogram raster",
        "آمار رستر",
        "آمار تصویر",
        "میانگین رستر",
        "حداقل حداکثر رستر",
        "هیستوگرام رستر",
    ],
    description="Calculate descriptive statistics for raster bands.",
    required_inputs=["raster"],
    optional_inputs=[
        "stats",
        "bands",
        "nodata",
        "histogram_bins",
        "engine",
        "precision",
        "source_crs",
        "metadata",
    ],
    output_kind="json",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster",
        "operation": "raster_statistics",
        "returns": "dict",
        "artifact_kind": "statistics",
        "access_scope": "raster_analysis",
        "config_aware": True,
        "histogram_supported": True,
        "multi_band_supported": True,
        "routable": True,
    },
)
def calculate_raster_statistics(
    raster: Any,
    stats: str | list[str] | None = None,
    bands: int | str | list[int | str] | None = None,
    nodata: Any = None,
    histogram_bins: int | None = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Calculate raster statistics per selected band.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        stats:
            Requested statistics. If None, config/default stats are used.
        bands:
            1-based band index or list of indices. If None, all bands.
        nodata:
            Nodata value. If omitted, raster metadata/config nodata is used.
        histogram_bins:
            Number of histogram bins. If provided, histogram stat is included.
        engine:
            python | auto.
        precision:
            Rounding precision for float statistics.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        Dict containing statistics, summary, and metadata.
    """
    config = _load_raster_statistics_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_stats = _normalize_stats(
        pick_first(stats, config.get("default_stats"), default=DEFAULT_STATS)
    )

    histogram_config = config.get("histogram") or {}
    if not isinstance(histogram_config, dict):
        raise ValueError("histogram config must be a dict.")

    histogram_enabled = bool(histogram_config.get("enabled", False))

    if histogram_bins is not None:
        final_histogram_bins = int(histogram_bins)
        if "histogram" not in final_stats:
            final_stats.append("histogram")
    else:
        final_histogram_bins = int(histogram_config.get("bins", 10))
        if histogram_enabled and "histogram" not in final_stats:
            final_stats.append("histogram")

    if final_histogram_bins <= 0:
        raise ValueError("histogram_bins must be > 0.")

    final_bands = _normalize_bands(
        pick_first(bands, config.get("default_bands"), default=None),
        band_count=band_count,
    )

    final_nodata = pick_first(
        nodata,
        input_metadata.get("nodata"),
        config.get("default_nodata"),
        default=None,
    )

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    preserve_metadata = bool(config.get("preserve_metadata", True))

    final_source_crs = pick_first(
        source_crs,
        input_metadata.get("crs"),
        config.get("source_crs"),
        default=None,
    )

    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    band_statistics: list[dict[str, Any]] = []

    for band_index in final_bands:
        values = _band_values(data, band_index)

        stat_values = _calculate_stats_for_values(
            values,
            nodata=final_nodata,
            requested_stats=final_stats,
            histogram_bins=final_histogram_bins,
            precision=final_precision,
        )

        band_statistics.append(
            {
                "band_index": band_index,
                **stat_values,
            }
        )

    summary = _summarize_band_stats(band_statistics)
    summary = {
        key: _round_value(value, final_precision)
        for key, value in summary.items()
    }

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Raster statistics are being evaluated on a geographic CRS. "
            "Statistics are value-based, but metric interpretation may require reprojection."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    output_metadata = {
        **base_metadata,
        "source": "raster_statistics",
        "loader": PLUGIN_ID,
        "operation": "raster_statistics",
        "engine_requested": final_engine,
        "engine_used": "python",
        "width": width,
        "height": height,
        "input_band_count": band_count,
        "selected_bands": final_bands,
        "selected_band_count": len(final_bands),
        "stats": final_stats,
        "nodata": final_nodata,
        "histogram_bins": final_histogram_bins,
        "coordinate_precision": final_precision,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "created_at": _utc_now_iso(),
        **source_info,
        **user_metadata,
    }

    return {
        "statistics": band_statistics,
        "summary": summary,
        "metadata": output_metadata,
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Raster Statistics",
    description=(
        "Calculates descriptive statistics for raster bands, including min/max/mean, "
        "standard deviation, percentiles, majority/minority, and histograms."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
