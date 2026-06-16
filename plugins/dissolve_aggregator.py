"""
dissolve_aggregator.py

GeoChat SDK Plugin
==================

Plugin ID:
    dissolve_aggregator

Purpose:
    Dissolve vector features by one or more attribute fields and calculate
    attribute aggregations per dissolved group.

Capability:
    - dissolve_features

Supported engines:
    - auto:
        Use shapely if available, otherwise pure-python bbox fallback.
    - shapely:
        Exact geometry dissolve using shapely.ops.unary_union.
    - python:
        Bbox-based dissolve fallback.

Supported aggregation ops:
    - count
    - non_null_count
    - sum
    - mean
    - min
    - max
    - first
    - last
    - unique_count
    - values

Important:
    The python engine is bbox-based and approximate. For production-grade
    geometry dissolve, install shapely:
        pip install shapely
"""

from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "dissolve_aggregator"

VALID_ENGINES = {"auto", "shapely", "python"}
VALID_AGGREGATION_OPS = {
    "count",
    "non_null_count",
    "sum",
    "mean",
    "min",
    "max",
    "first",
    "last",
    "unique_count",
    "values",
}

MISSING = object()
EPSILON = 1e-12


def _load_dissolve_config() -> dict[str, Any]:
    """
    Load config/plugins/dissolve_aggregator.yaml if available.
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
    Validate dissolve engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return configured coordinate precision.
    """
    value = config.get("coordinate_precision", 6)

    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("coordinate_precision must be an integer or null.")

    try:
        precision = int(value)
    except Exception as exc:
        raise ValueError("coordinate_precision must be an integer or null.") from exc

    if precision < 0:
        raise ValueError("coordinate_precision must be >= 0.")

    if precision > 15:
        raise ValueError("coordinate_precision is too large. Maximum allowed value is 15.")

    return precision


def _validate_precision(value: Any) -> int | None:
    """
    Validate runtime precision.
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


def _round_value(value: float | None, precision: int | None) -> float | None:
    """
    Round numeric value.
    """
    if value is None:
        return None

    if precision is None:
        return float(value)

    return round(float(value), precision)


def _round_position(position: list[Any] | tuple[Any, ...], precision: int | None) -> list[Any]:
    """
    Round coordinate position.
    """
    if not _is_position(position):
        raise ValueError(f"Invalid coordinate position: {position!r}")

    extra = list(position[2:])

    return [
        _round_value(float(position[0]), precision),
        _round_value(float(position[1]), precision),
        *extra,
    ]


def _round_coordinates(obj: Any, precision: int | None) -> Any:
    """
    Recursively round coordinates.
    """
    if obj is None:
        return None

    if _is_position(obj):
        return _round_position(obj, precision)

    if isinstance(obj, list):
        return [_round_coordinates(item, precision) for item in obj]

    if isinstance(obj, tuple):
        return [_round_coordinates(item, precision) for item in obj]

    return obj


def _round_geometry(geometry: dict[str, Any] | None, precision: int | None) -> dict[str, Any] | None:
    """
    Round GeoJSON geometry coordinates.
    """
    if geometry is None:
        return None

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    gtype = geometry.get("type")

    if gtype == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [
                _round_geometry(sub, precision)
                for sub in geometry.get("geometries", [])
                if isinstance(sub, dict)
            ],
        }

    if "coordinates" not in geometry:
        return dict(geometry)

    return {
        "type": gtype,
        "coordinates": _round_coordinates(geometry.get("coordinates"), precision),
    }


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    """
    Return configured output property names.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in dissolve_aggregator config must be a dict.")

    return {
        "group_key_field": str(fields.get("group_key_field", "_group_key")),
        "group_by_field": str(fields.get("group_by_field", "_group_by")),
        "feature_count_field": str(fields.get("feature_count_field", "_feature_count")),
        "dissolved_count_field": str(fields.get("dissolved_count_field", "_dissolved_count")),
        "status_field": str(fields.get("status_field", "_dissolve_status")),
        "engine_field": str(fields.get("engine_field", "_dissolve_engine")),
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
    Extract features from:
        - VectorOut-like object with .features
        - list[Feature]
        - FeatureCollection dict
        - single Feature dict
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
    Return True if numeric and finite, excluding bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_position(value: Any) -> bool:
    """
    Return True if value is coordinate position [x, y, ...].
    """
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _iter_positions(coords: Any) -> list[tuple[float, float]]:
    """
    Recursively collect coordinate positions.
    """
    if _is_position(coords):
        return [(float(coords[0]), float(coords[1]))]

    if isinstance(coords, (list, tuple)):
        result: list[tuple[float, float]] = []
        for item in coords:
            result.extend(_iter_positions(item))
        return result

    return []


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox from GeoJSON geometry.

    Output:
        [minx, miny, maxx, maxy]
    """
    if geometry is None:
        return None

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    if geometry.get("type") == "GeometryCollection":
        bboxes: list[list[float]] = []
        geometries = geometry.get("geometries") or []

        if isinstance(geometries, list):
            for sub in geometries:
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


def _merge_bbox_arrays(bboxes: list[list[float]]) -> dict[str, float] | None:
    """
    Merge bbox arrays.
    """
    valid = [b for b in bboxes if b and len(b) == 4]

    if not valid:
        return None

    return {
        "minx": min(b[0] for b in valid),
        "miny": min(b[1] for b in valid),
        "maxx": max(b[2] for b in valid),
        "maxy": max(b[3] for b in valid),
    }


def _bbox_to_geometry(bbox: dict[str, float] | None, precision: int | None = None) -> dict[str, Any] | None:
    """
    Convert merged bbox to GeoJSON geometry.
    """
    if bbox is None:
        return None

    minx = float(bbox["minx"])
    miny = float(bbox["miny"])
    maxx = float(bbox["maxx"])
    maxy = float(bbox["maxy"])

    if abs(maxx - minx) <= EPSILON and abs(maxy - miny) <= EPSILON:
        return {
            "type": "Point",
            "coordinates": [
                _round_value(minx, precision),
                _round_value(miny, precision),
            ],
        }

    if abs(maxx - minx) <= EPSILON or abs(maxy - miny) <= EPSILON:
        return {
            "type": "LineString",
            "coordinates": [
                [_round_value(minx, precision), _round_value(miny, precision)],
                [_round_value(maxx, precision), _round_value(maxy, precision)],
            ],
        }

    return {
        "type": "Polygon",
        "coordinates": [[
            [_round_value(minx, precision), _round_value(miny, precision)],
            [_round_value(maxx, precision), _round_value(miny, precision)],
            [_round_value(maxx, precision), _round_value(maxy, precision)],
            [_round_value(minx, precision), _round_value(maxy, precision)],
            [_round_value(minx, precision), _round_value(miny, precision)],
        ]],
    }


def _python_dissolve_geometries(
    geometries: list[dict[str, Any] | None],
    *,
    precision: int | None,
) -> tuple[dict[str, Any] | None, str]:
    """
    Bbox-based dissolve fallback.
    """
    bboxes: list[list[float]] = []

    for geometry in geometries:
        bbox = _geometry_bbox(geometry)
        if bbox is not None:
            bboxes.append(bbox)

    merged = _merge_bbox_arrays(bboxes)
    return _bbox_to_geometry(merged, precision=precision), "python"


def _get_shapely_tools():
    """
    Lazy import shapely tools.
    """
    try:
        from shapely.geometry import mapping, shape
        from shapely.ops import unary_union
    except ImportError as exc:
        raise SDKDependencyError(
            "dissolve_aggregator requires 'shapely' for this engine. "
            "Install it with: pip install shapely"
        ) from exc

    return shape, mapping, unary_union


def _shapely_dissolve_geometries(
    geometries: list[dict[str, Any] | None],
    *,
    precision: int | None,
) -> tuple[dict[str, Any] | None, str]:
    """
    Exact dissolve using shapely unary_union.
    """
    shape, mapping, unary_union = _get_shapely_tools()

    shapely_geometries = []

    for geometry in geometries:
        if geometry is None:
            continue

        if not isinstance(geometry, dict):
            raise ValueError("geometry must be a dict/object or null.")

        try:
            geom = shape(geometry)
        except Exception as exc:
            raise ValueError(f"cannot build shapely geometry: {exc}") from exc

        if not geom.is_empty:
            shapely_geometries.append(geom)

    if not shapely_geometries:
        return None, "shapely"

    unioned = unary_union(shapely_geometries)

    if unioned.is_empty:
        return None, "shapely"

    geometry = dict(mapping(unioned))
    geometry = _round_geometry(geometry, precision)

    return geometry, "shapely"


def _dissolve_geometries(
    geometries: list[dict[str, Any] | None],
    *,
    engine: str,
    precision: int | None,
) -> tuple[dict[str, Any] | None, str]:
    """
    Dissolve geometries.

    Returns:
        (geometry, engine_used)
    """
    engine = _validate_engine(engine)

    if engine == "python":
        return _python_dissolve_geometries(geometries, precision=precision)

    if engine == "shapely":
        return _shapely_dissolve_geometries(geometries, precision=precision)

    try:
        return _shapely_dissolve_geometries(geometries, precision=precision)
    except SDKDependencyError:
        return _python_dissolve_geometries(geometries, precision=precision)


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


def _normalize_aggregate_fields(aggregate_fields: Any) -> dict[str, list[str]]:
    """
    Normalize aggregate_fields.

    Supported forms:
        None
        {}
        {"population": ["sum", "mean"], "name": "first"}
        [{"field": "population", "ops": ["sum", "mean"]}]
    """
    if aggregate_fields is None:
        return {}

    result: dict[str, list[str]] = {}

    if isinstance(aggregate_fields, dict):
        items = aggregate_fields.items()

        for field, ops in items:
            if not isinstance(field, str) or not field.strip():
                raise ValueError("aggregate_fields keys must be non-empty strings.")

            field_name = field.strip()

            if isinstance(ops, str):
                op_values = [ops]
            elif isinstance(ops, (list, tuple, set)):
                op_values = list(ops)
            else:
                raise ValueError("aggregate_fields values must be string/list/tuple/set.")

            result[field_name] = _normalize_aggregation_ops(op_values)

        return result

    if isinstance(aggregate_fields, list):
        for item in aggregate_fields:
            if not isinstance(item, dict):
                raise ValueError("aggregate_fields list items must be dict/object.")

            field = item.get("field")
            ops = item.get("ops", [])

            if not isinstance(field, str) or not field.strip():
                raise ValueError("aggregate_fields item requires non-empty 'field'.")

            field_name = field.strip()

            if isinstance(ops, str):
                op_values = [ops]
            elif isinstance(ops, (list, tuple, set)):
                op_values = list(ops)
            else:
                raise ValueError("aggregate_fields item 'ops' must be string/list/tuple/set.")

            result[field_name] = _normalize_aggregation_ops(op_values)

        return result

    raise ValueError("aggregate_fields must be dict, list, or None.")


def _normalize_aggregation_ops(ops: list[Any]) -> list[str]:
    """
    Normalize aggregation ops.
    """
    result: list[str] = []

    for op in ops:
        if not isinstance(op, str) or not op.strip():
            raise ValueError("aggregation ops must be non-empty strings.")

        text = op.strip().lower()

        if text not in VALID_AGGREGATION_OPS:
            raise ValueError(
                f"Unsupported aggregation op '{text}'. "
                f"Valid ops: {sorted(VALID_AGGREGATION_OPS)}"
            )

        if text not in result:
            result.append(text)

    return result


def _group_key_for_feature(properties: dict[str, Any], group_by: list[str]) -> tuple[Any, ...]:
    """
    Build group key for a feature.
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


def _group_key_to_output(group_key: tuple[Any, ...], group_by: list[str]) -> Any:
    """
    Convert group key to output value.
    """
    if not group_by:
        return "__all__"

    if len(group_by) == 1:
        return group_key[0]

    return list(group_key)


def _is_geographic_crs(value: Any) -> bool:
    """
    Check if CRS is known geographic CRS.
    """
    if not isinstance(value, str):
        return False

    text = value.strip().upper()

    return text in {
        "EPSG:4326",
        "CRS:84",
        "OGC:CRS84",
    }


def _safe_sort_repr(value: Any) -> str:
    """
    Stable repr for unique values.
    """
    return repr(value)


def _aggregate_values(values: list[Any], ops: list[str], *, values_max_items: int) -> dict[str, Any]:
    """
    Calculate aggregation values for one field.
    """
    output: dict[str, Any] = {}

    non_null_values = [value for value in values if value is not None and value is not MISSING]
    numeric_values = [float(value) for value in non_null_values if _is_number(value)]

    for op in ops:
        if op == "count":
            output[op] = len(values)

        elif op == "non_null_count":
            output[op] = len(non_null_values)

        elif op == "sum":
            output[op] = float(sum(numeric_values)) if numeric_values else None

        elif op == "mean":
            output[op] = float(sum(numeric_values) / len(numeric_values)) if numeric_values else None

        elif op == "min":
            if numeric_values:
                output[op] = float(min(numeric_values))
            elif non_null_values:
                try:
                    output[op] = min(non_null_values)
                except TypeError:
                    output[op] = min(non_null_values, key=_safe_sort_repr)
            else:
                output[op] = None

        elif op == "max":
            if numeric_values:
                output[op] = float(max(numeric_values))
            elif non_null_values:
                try:
                    output[op] = max(non_null_values)
                except TypeError:
                    output[op] = max(non_null_values, key=_safe_sort_repr)
            else:
                output[op] = None

        elif op == "first":
            output[op] = non_null_values[0] if non_null_values else None

        elif op == "last":
            output[op] = non_null_values[-1] if non_null_values else None

        elif op == "unique_count":
            output[op] = len(set(repr(value) for value in non_null_values))

        elif op == "values":
            output[op] = non_null_values[:values_max_items]

        else:
            raise ValueError(f"Unsupported aggregation op: {op}")

    return output


def _round_aggregation_value(value: Any, precision: int | None) -> Any:
    """
    Round float aggregation output.
    """
    if isinstance(value, float):
        if precision is None:
            return value
        return round(value, precision)

    if isinstance(value, list):
        return [_round_aggregation_value(item, precision) for item in value]

    return value


def _make_aggregation_properties(
    *,
    group_features: list[dict[str, Any]],
    aggregate_fields: dict[str, list[str]],
    output_separator: str,
    values_max_items: int,
    precision: int | None,
) -> dict[str, Any]:
    """
    Build aggregation properties for a group.
    """
    result: dict[str, Any] = {}

    for field, ops in aggregate_fields.items():
        values: list[Any] = []

        for feature in group_features:
            props = feature.get("properties") or {}
            value = _get_path(props, field, default=None)
            if value is MISSING:
                value = None
            values.append(value)

        aggregated = _aggregate_values(values, ops, values_max_items=values_max_items)

        safe_field = field.replace(".", output_separator)

        for op, value in aggregated.items():
            out_name = f"{safe_field}{output_separator}{op}"
            result[out_name] = _round_aggregation_value(value, precision)

    return result


def _make_group_base_properties(
    *,
    group_key: tuple[Any, ...],
    group_by: list[str],
    group_features: list[dict[str, Any]],
    fields: dict[str, str],
    preserve_group_fields: bool,
    engine_used: str,
    status: str,
) -> dict[str, Any]:
    """
    Build base output properties for dissolved group.
    """
    properties: dict[str, Any] = {}

    if preserve_group_fields and group_by:
        for idx, group_field in enumerate(group_by):
            properties[group_field] = group_key[idx]

    properties[fields["group_key_field"]] = _group_key_to_output(group_key, group_by)
    properties[fields["group_by_field"]] = list(group_by)
    properties[fields["feature_count_field"]] = len(group_features)
    properties[fields["dissolved_count_field"]] = len(group_features)
    properties[fields["status_field"]] = status
    properties[fields["engine_field"]] = engine_used

    return properties


def _build_dissolved_feature(
    *,
    group_key: tuple[Any, ...],
    group_by: list[str],
    group_features: list[dict[str, Any]],
    aggregate_fields: dict[str, list[str]],
    engine: str,
    precision: int | None,
    fields: dict[str, str],
    preserve_group_fields: bool,
    output_separator: str,
    values_max_items: int,
    drop_failed: bool,
) -> tuple[dict[str, Any] | None, str, bool]:
    """
    Build one dissolved output feature.

    Returns:
        (feature_or_none, engine_used, failed)
    """
    geometries = [feature.get("geometry") for feature in group_features]

    try:
        geometry, engine_used = _dissolve_geometries(
            geometries,
            engine=engine,
            precision=precision,
        )

        properties = _make_group_base_properties(
            group_key=group_key,
            group_by=group_by,
            group_features=group_features,
            fields=fields,
            preserve_group_fields=preserve_group_fields,
            engine_used=engine_used,
            status="success",
        )

        properties.update(
            _make_aggregation_properties(
                group_features=group_features,
                aggregate_fields=aggregate_fields,
                output_separator=output_separator,
                values_max_items=values_max_items,
                precision=precision,
            )
        )

        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": properties,
        }, engine_used, False

    except Exception as exc:
        if drop_failed:
            return None, engine, True

        properties = _make_group_base_properties(
            group_key=group_key,
            group_by=group_by,
            group_features=group_features,
            fields=fields,
            preserve_group_fields=preserve_group_fields,
            engine_used=engine,
            status="failed",
        )
        properties["_dissolve_reason"] = str(exc)

        properties.update(
            _make_aggregation_properties(
                group_features=group_features,
                aggregate_fields=aggregate_fields,
                output_separator=output_separator,
                values_max_items=values_max_items,
                precision=precision,
            )
        )

        return {
            "type": "Feature",
            "geometry": None,
            "properties": properties,
        }, engine, True


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
    name="dissolve_features",
    keywords=[
        "dissolve",
        "dissolve features",
        "aggregate by field",
        "group dissolve",
        "union by attribute",
        "merge polygons by attribute",
        "summarize polygons",
        "تجمیع عوارض",
        "ادغام عوارض",
        "دیزالو",
        "گروه بندی هندسه",
        "ادغام بر اساس ویژگی",
        "تجمیع بر اساس فیلد",
    ],
    description="Dissolve vector features by attribute fields and calculate aggregations per group.",
    required_inputs=["features"],
    optional_inputs=[
        "group_by",
        "aggregate_fields",
        "engine",
        "precision",
        "drop_failed",
        "source_crs",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "dissolve_aggregate",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_exact_union": True,
        "python_engine": "bbox_fallback",
        "aggregation_supported": True,
        "routable": True,
    },
)
def dissolve_features(
    features: Any,
    group_by: str | list[str] | None = None,
    aggregate_fields: dict[str, Any] | list[dict[str, Any]] | None = None,
    engine: str | None = None,
    precision: int | None = None,
    drop_failed: bool | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Dissolve vector features by group fields and calculate aggregations.

    Args:
        features:
            VectorOut, FeatureCollection, Feature, or list[Feature].
        group_by:
            Field name or list of property paths. If None, all features are dissolved together.
        aggregate_fields:
            Aggregation specification.

            Example:
                {
                    "population": ["sum", "mean"],
                    "name": ["first", "unique_count"],
                    "area": "sum"
                }

            Or:
                [
                    {"field": "population", "ops": ["sum", "mean"]},
                    {"field": "name", "ops": ["first"]}
                ]

        engine:
            auto | shapely | python.
        precision:
            Rounding precision for output geometry and float aggregations.
        drop_failed:
            If True, failed dissolved groups are dropped.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with one dissolved feature per group.
    """
    config = _load_dissolve_config()

    input_features, source_info = _extract_features(features)

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_group_by = _normalize_group_by(
        pick_first(group_by, config.get("default_group_by"), default=None)
    )

    default_aggregate_fields = config.get("default_aggregate_fields", {})
    final_aggregate_fields = _normalize_aggregate_fields(
        pick_first(aggregate_fields, default_aggregate_fields, default={})
    )

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    final_drop_failed = bool(
        pick_first(drop_failed, config.get("drop_failed"), default=False)
    )

    preserve_group_fields = bool(config.get("preserve_group_fields", True))

    final_source_crs = pick_first(source_crs, config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    aggregation_config = config.get("aggregation") or {}
    if not isinstance(aggregation_config, dict):
        raise ValueError("aggregation in dissolve_aggregator config must be a dict.")

    output_separator = str(aggregation_config.get("output_separator", "_"))
    values_max_items = int(aggregation_config.get("values_max_items", 1000))

    if values_max_items < 0:
        raise ValueError("aggregation.values_max_items must be >= 0.")

    if values_max_items > 100000:
        raise ValueError("aggregation.values_max_items is too large. Maximum allowed value is 100000.")

    fields = _configured_fields(config)

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for feature in input_features:
        props = feature.get("properties") or {}
        group_key = _group_key_for_feature(props, final_group_by)
        grouped[group_key].append(feature)

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    failed_group_count = 0
    dropped_failed_count = 0

    for group_key in sorted(grouped.keys(), key=lambda item: repr(item)):
        group_features = grouped[group_key]

        feature, engine_used, failed = _build_dissolved_feature(
            group_key=group_key,
            group_by=final_group_by,
            group_features=group_features,
            aggregate_fields=final_aggregate_fields,
            engine=final_engine,
            precision=final_precision,
            fields=fields,
            preserve_group_fields=preserve_group_fields,
            output_separator=output_separator,
            values_max_items=values_max_items,
            drop_failed=final_drop_failed,
        )

        engines_used.add(engine_used)

        if failed:
            failed_group_count += 1
            if final_drop_failed:
                dropped_failed_count += 1

        if feature is not None:
            output_features.append(feature)

    stats = _build_vector_metadata(output_features)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Dissolve is being evaluated on a geographic CRS. "
            "For metric geometry workflows, consider reprojecting first."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "dissolve_aggregator",
        "loader": PLUGIN_ID,
        "operation": "dissolve_aggregate",
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "group_by": final_group_by,
        "group_count": len(grouped),
        "aggregate_fields": final_aggregate_fields,
        "aggregation_field_count": len(final_aggregate_fields),
        "coordinate_precision": final_precision,
        "drop_failed": final_drop_failed,
        "failed_group_count": failed_group_count,
        "dropped_failed_count": dropped_failed_count,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "input_feature_count": len(input_features),
        "output_feature_count": len(output_features),
        "created_at": _utc_now_iso(),
        **source_info,
        **stats,
        **user_metadata,
    }

    return VectorOut(
        features=output_features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Dissolve Aggregator",
    description=(
        "Dissolves vector features by attribute groups and computes aggregations. "
        "Uses shapely for exact geometry union and bbox fallback otherwise."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
