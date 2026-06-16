"""
attribute_statistics.py

GeoChat SDK Plugin
==================

Plugin ID:
    attribute_statistics

Purpose:
    Compute descriptive statistics over GeoJSON-like vector feature properties.

Capability:
    - calculate_attribute_statistics

Supported:
    - field inference
    - explicit field selection
    - grouping by one or more property paths
    - count, null_count, non_null_count
    - unique_count
    - numeric_count
    - min, max, sum, mean, median
    - sample and population standard deviation
    - top values for categorical/text fields

No external dependency is required.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "attribute_statistics"

MISSING = object()


def _load_statistics_config() -> dict[str, Any]:
    """
    Load config/plugins/attribute_statistics.yaml if available.
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


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    """
    Return configured output property field names.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in attribute_statistics config must be a dict.")

    return {
        "stat_field_field": str(fields.get("stat_field_field", "_stat_field")),
        "group_field_field": str(fields.get("group_field_field", "_group_field")),
        "group_value_field": str(fields.get("group_value_field", "_group_value")),
        "count_field": str(fields.get("count_field", "_count")),
        "non_null_count_field": str(fields.get("non_null_count_field", "_non_null_count")),
        "null_count_field": str(fields.get("null_count_field", "_null_count")),
        "unique_count_field": str(fields.get("unique_count_field", "_unique_count")),
        "numeric_count_field": str(fields.get("numeric_count_field", "_numeric_count")),
        "min_field": str(fields.get("min_field", "_min")),
        "max_field": str(fields.get("max_field", "_max")),
        "sum_field": str(fields.get("sum_field", "_sum")),
        "mean_field": str(fields.get("mean_field", "_mean")),
        "median_field": str(fields.get("median_field", "_median")),
        "sample_stdev_field": str(fields.get("sample_stdev_field", "_sample_stdev")),
        "population_stdev_field": str(fields.get("population_stdev_field", "_population_stdev")),
        "top_values_field": str(fields.get("top_values_field", "_top_values")),
        "status_field": str(fields.get("status_field", "_statistics_status")),
    }


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize GeoJSON Feature.
    """
    if not isinstance(feature, dict):
        raise ValueError(f"Feature at index {index} must be a dict/object.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if properties is None:
        properties = {}

    if not isinstance(properties, dict):
        raise ValueError(f"Feature properties at index {index} must be dict/object or null.")

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": dict(properties),
    }


def _extract_features(input_data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract features from VectorOut, FeatureCollection, Feature, or list[Feature].
    """
    source_info: dict[str, Any] = {}

    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw_features = getattr(input_data, "features")
        source_info["input_type"] = type(input_data).__name__

        source_metadata = getattr(input_data, "metadata", None)
        if isinstance(source_metadata, dict):
            source_info["input_metadata"] = source_metadata

    elif isinstance(input_data, dict):
        geojson_type = input_data.get("type")
        source_info["input_geojson_type"] = geojson_type

        if geojson_type == "FeatureCollection":
            raw_features = input_data.get("features", [])
            if not isinstance(raw_features, list):
                raise ValueError("FeatureCollection.features must be a list.")
        elif geojson_type == "Feature":
            raw_features = [input_data]
        else:
            raise ValueError("Input dict must be GeoJSON FeatureCollection or Feature.")

    elif isinstance(input_data, list):
        raw_features = input_data
        source_info["input_geojson_type"] = "FeatureList"

    else:
        raise ValueError("features must be VectorOut, list, FeatureCollection dict or Feature dict.")

    if not isinstance(raw_features, list):
        raise ValueError("Extracted features must be a list.")

    features = [_normalize_feature(item, idx) for idx, item in enumerate(raw_features)]
    return features, source_info


def _get_path(obj: Any, path: str, default: Any = MISSING) -> Any:
    """
    Read dot-separated path from dict/list structures.
    """
    if not isinstance(path, str) or not path.strip():
        return default

    current = obj

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


def _is_number(value: Any) -> bool:
    """
    Return True if value is numeric and not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _safe_sort_key(value: Any) -> tuple[str, str]:
    """
    Stable sort key for mixed values.
    """
    return (type(value).__name__, repr(value))


def _normalize_fields(fields: Any) -> list[str] | None:
    """
    Normalize fields input.
    """
    if fields is None:
        return None

    if isinstance(fields, str):
        values = [fields]
    elif isinstance(fields, (list, tuple, set)):
        values = list(fields)
    else:
        raise ValueError("fields must be string, list, tuple, set, or None.")

    result: list[str] = []

    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("fields items must be non-empty strings.")
        text = item.strip()
        if text not in result:
            result.append(text)

    return result


def _normalize_group_by(group_by: Any) -> list[str]:
    """
    Normalize group_by input.
    """
    if group_by is None:
        return []

    if isinstance(group_by, str):
        values = [group_by]
    elif isinstance(group_by, (list, tuple, set)):
        values = list(group_by)
    else:
        raise ValueError("group_by must be string, list, tuple, set, or None.")

    result: list[str] = []

    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("group_by items must be non-empty strings.")
        text = item.strip()
        if text not in result:
            result.append(text)

    return result


def _collect_property_paths(obj: Any, prefix: str = "") -> list[str]:
    """
    Collect dot-separated property paths from nested dict properties.
    """
    paths: list[str] = []

    if not isinstance(obj, dict):
        return paths

    for key, value in obj.items():
        key_text = str(key)
        path = f"{prefix}.{key_text}" if prefix else key_text

        if isinstance(value, dict):
            nested = _collect_property_paths(value, prefix=path)
            if nested:
                paths.extend(nested)
            else:
                paths.append(path)
        else:
            paths.append(path)

    return paths


def _infer_fields(features: list[dict[str, Any]], *, preserve_field_order: bool) -> list[str]:
    """
    Infer fields from all feature properties.
    """
    result: list[str] = []

    for feature in features:
        props = feature.get("properties") or {}
        for path in _collect_property_paths(props):
            if path not in result:
                result.append(path)

    if preserve_field_order:
        return result

    return sorted(result)


def _group_key_for_feature(properties: dict[str, Any], group_by: list[str]) -> tuple[Any, ...]:
    """
    Build group key.
    """
    if not group_by:
        return ("__all__",)

    values: list[Any] = []

    for field in group_by:
        value = _get_path(properties, field, default=None)
        if value is MISSING:
            value = None
        values.append(value)

    return tuple(values)


def _group_value_to_output(group_key: tuple[Any, ...], group_by: list[str]) -> Any:
    """
    Convert group key to output value.
    """
    if not group_by:
        return "__all__"

    if len(group_by) == 1:
        return group_key[0]

    return list(group_key)


def _calculate_numeric_stats(values: list[float]) -> dict[str, float | None]:
    """
    Calculate numeric statistics.
    """
    if not values:
        return {
            "min": None,
            "max": None,
            "sum": None,
            "mean": None,
            "median": None,
            "sample_stdev": None,
            "population_stdev": None,
        }

    sorted_values = sorted(values)
    n = len(sorted_values)
    total = float(sum(sorted_values))
    mean = total / n

    if n % 2 == 1:
        median = sorted_values[n // 2]
    else:
        median = (sorted_values[(n // 2) - 1] + sorted_values[n // 2]) / 2.0

    if n > 1:
        sample_var = sum((x - mean) ** 2 for x in sorted_values) / (n - 1)
        sample_stdev = math.sqrt(sample_var)
    else:
        sample_stdev = None

    population_var = sum((x - mean) ** 2 for x in sorted_values) / n
    population_stdev = math.sqrt(population_var)

    return {
        "min": float(sorted_values[0]),
        "max": float(sorted_values[-1]),
        "sum": total,
        "mean": float(mean),
        "median": float(median),
        "sample_stdev": sample_stdev,
        "population_stdev": float(population_stdev),
    }


def _top_values(values: list[Any], max_top_values: int) -> list[dict[str, Any]]:
    """
    Return top values with counts.
    """
    counter = Counter(values)

    rows = [
        {"value": value, "count": count}
        for value, count in counter.items()
    ]

    rows.sort(key=lambda item: (-item["count"], _safe_sort_key(item["value"])))

    return rows[:max_top_values]


def _calculate_field_stats(
    *,
    values: list[Any],
    include_nulls: bool,
    max_top_values: int,
) -> dict[str, Any]:
    """
    Calculate statistics for one field in one group.
    """
    total_count = len(values)

    null_values = [value for value in values if value is None or value is MISSING]
    non_null_values = [value for value in values if value is not None and value is not MISSING]
    numeric_values = [float(value) for value in non_null_values if _is_number(value)]

    unique_source = values if include_nulls else non_null_values

    numeric_stats = _calculate_numeric_stats(numeric_values)

    return {
        "count": total_count,
        "non_null_count": len(non_null_values),
        "null_count": len(null_values),
        "unique_count": len(set(repr(item) for item in unique_source)),
        "numeric_count": len(numeric_values),
        "top_values": _top_values(non_null_values if not include_nulls else values, max_top_values),
        **numeric_stats,
    }


def _round_float(value: Any, precision: int | None) -> Any:
    """
    Round float-like values in output.
    """
    if value is None:
        return None

    if not isinstance(value, float):
        return value

    if precision is None:
        return value

    return round(value, precision)


def _validate_precision(value: Any) -> int | None:
    """
    Validate precision.
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


def _validate_max_top_values(value: Any) -> int:
    """
    Validate max_top_values.
    """
    if isinstance(value, bool):
        raise ValueError("max_top_values must be an integer.")

    try:
        result = int(value)
    except Exception as exc:
        raise ValueError("max_top_values must be an integer.") from exc

    if result < 0:
        raise ValueError("max_top_values must be >= 0.")

    if result > 1000:
        raise ValueError("max_top_values is too large. Maximum allowed value is 1000.")

    return result


def _make_statistics_feature(
    *,
    stat_field: str,
    group_by: list[str],
    group_key: tuple[Any, ...],
    stats: dict[str, Any],
    output_fields: dict[str, str],
    precision: int | None,
) -> dict[str, Any]:
    """
    Build one output Feature containing statistics.
    """
    properties = {
        output_fields["stat_field_field"]: stat_field,
        output_fields["group_field_field"]: group_by[0] if len(group_by) == 1 else list(group_by),
        output_fields["group_value_field"]: _group_value_to_output(group_key, group_by),
        output_fields["count_field"]: stats["count"],
        output_fields["non_null_count_field"]: stats["non_null_count"],
        output_fields["null_count_field"]: stats["null_count"],
        output_fields["unique_count_field"]: stats["unique_count"],
        output_fields["numeric_count_field"]: stats["numeric_count"],
        output_fields["min_field"]: _round_float(stats["min"], precision),
        output_fields["max_field"]: _round_float(stats["max"], precision),
        output_fields["sum_field"]: _round_float(stats["sum"], precision),
        output_fields["mean_field"]: _round_float(stats["mean"], precision),
        output_fields["median_field"]: _round_float(stats["median"], precision),
        output_fields["sample_stdev_field"]: _round_float(stats["sample_stdev"], precision),
        output_fields["population_stdev_field"]: _round_float(stats["population_stdev"], precision),
        output_fields["top_values_field"]: stats["top_values"],
        output_fields["status_field"]: "success",
    }

    return {
        "type": "Feature",
        "geometry": None,
        "properties": properties,
    }


def _build_vector_metadata(features: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build simple VectorOut metadata.
    """
    geometry_types: dict[str, int] = {}

    for feature in features:
        geometry = feature.get("geometry")
        if geometry is None:
            gtype = "Null"
        elif isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "Unknown")
        else:
            gtype = "Invalid"

        geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

    return {
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": None,
    }


@capability(
    name="calculate_attribute_statistics",
    keywords=[
        "attribute statistics",
        "statistics",
        "summary statistics",
        "descriptive statistics",
        "group by",
        "mean",
        "median",
        "count",
        "unique count",
        "top values",
        "آمار توصیفی",
        "آمار ویژگی‌ها",
        "میانگین",
        "میانه",
        "شمارش",
        "گروه بندی",
        "گروه‌بندی",
        "تعداد یکتا",
    ],
    description="Calculate descriptive statistics over vector feature attributes.",
    required_inputs=["features"],
    optional_inputs=[
        "fields",
        "group_by",
        "include_nulls",
        "numeric_only",
        "max_top_values",
        "precision",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "attribute_statistics",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": True,
        "group_by_supported": True,
        "routable": True,
    },
)
def calculate_attribute_statistics(
    features: Any,
    fields: str | list[str] | None = None,
    group_by: str | list[str] | None = None,
    include_nulls: bool | None = None,
    numeric_only: bool | None = None,
    max_top_values: int | None = None,
    precision: int | None = 6,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Calculate descriptive attribute statistics.

    Args:
        features:
            VectorOut, FeatureCollection, Feature, or list[Feature].
        fields:
            Field name or list of property paths. If None, fields are inferred.
        group_by:
            Optional group field or list of group fields.
        include_nulls:
            If True, null values are included in count and top_values.
        numeric_only:
            If True, output only fields containing at least one numeric value.
        max_top_values:
            Maximum number of top value entries.
        precision:
            Rounding precision for floating-point statistics.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with one null-geometry feature per field/group statistic.
    """
    config = _load_statistics_config()

    input_features, source_info = _extract_features(features)

    preserve_field_order = bool(config.get("preserve_field_order", False))

    requested_fields = _normalize_fields(
        pick_first(fields, config.get("default_fields"), default=None)
    )

    final_fields = requested_fields
    if final_fields is None:
        final_fields = _infer_fields(input_features, preserve_field_order=preserve_field_order)

    final_group_by = _normalize_group_by(
        pick_first(group_by, config.get("default_group_by"), default=None)
    )

    final_include_nulls = bool(
        pick_first(include_nulls, config.get("include_nulls"), default=True)
    )

    final_numeric_only = bool(
        pick_first(numeric_only, config.get("numeric_only"), default=False)
    )

    final_max_top_values = _validate_max_top_values(
        pick_first(max_top_values, config.get("max_top_values"), default=10)
    )

    final_precision = _validate_precision(precision)

    output_fields = _configured_fields(config)

    grouped_values: dict[tuple[Any, ...], dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))

    for feature in input_features:
        props = feature.get("properties") or {}
        group_key = _group_key_for_feature(props, final_group_by)

        for field in final_fields:
            value = _get_path(props, field, default=MISSING)
            if value is MISSING:
                value = None
            grouped_values[group_key][field].append(value)

    output_features: list[dict[str, Any]] = []

    for group_key in sorted(grouped_values.keys(), key=lambda item: repr(item)):
        field_values = grouped_values[group_key]

        for field in final_fields:
            values = field_values.get(field, [])
            stats = _calculate_field_stats(
                values=values,
                include_nulls=final_include_nulls,
                max_top_values=final_max_top_values,
            )

            if final_numeric_only and stats["numeric_count"] == 0:
                continue

            output_features.append(
                _make_statistics_feature(
                    stat_field=field,
                    group_by=final_group_by,
                    group_key=group_key,
                    stats=stats,
                    output_fields=output_fields,
                    precision=final_precision,
                )
            )

    stats_metadata = _build_vector_metadata(output_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "attribute_statistics",
        "loader": PLUGIN_ID,
        "operation": "attribute_statistics",
        "input_feature_count": len(input_features),
        "output_feature_count": len(output_features),
        "fields": list(final_fields),
        "field_count": len(final_fields),
        "group_by": final_group_by,
        "group_count": len(grouped_values),
        "include_nulls": final_include_nulls,
        "numeric_only": final_numeric_only,
        "max_top_values": final_max_top_values,
        "precision": final_precision,
        "created_at": _utc_now_iso(),
        **source_info,
        **stats_metadata,
        **user_metadata,
    }

    return VectorOut(
        features=output_features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Attribute Statistics",
    description=(
        "Calculates descriptive statistics over vector feature properties, "
        "with optional grouping and field inference."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
