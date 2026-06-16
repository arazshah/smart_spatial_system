"""
spatial_query_filter.py

GeoChat SDK Plugin
==================

Plugin ID:
    spatial_query_filter

Purpose:
    Filter GeoJSON-like vector features using attribute predicates, geometry type,
    bbox constraints, sorting, offset, and limit.

Capability:
    - filter_features

Supported attribute operators:
    - eq
    - ne
    - gt
    - gte
    - lt
    - lte
    - in
    - not_in
    - contains
    - startswith
    - endswith
    - regex
    - exists
    - is_null
    - between

Supported logical operators:
    - and
    - or
    - not

Spatial filter:
    - bbox intersects
    - bbox within

No external dependency is required.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "spatial_query_filter"

VALID_BBOX_MODES = {"intersects", "within"}
VALID_SORT_ORDERS = {"asc", "desc"}
VALID_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "startswith",
    "endswith",
    "regex",
    "exists",
    "is_null",
    "between",
}

MISSING = object()
EPSILON = 1e-12


def _load_filter_config() -> dict[str, Any]:
    """
    Load config/plugins/spatial_query_filter.yaml if available.
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


def _validate_bbox_mode(mode: str) -> str:
    """
    Validate bbox filter mode.
    """
    if not isinstance(mode, str) or not mode.strip():
        raise ValueError("bbox_mode must be a non-empty string.")

    mode = mode.strip().lower()

    if mode not in VALID_BBOX_MODES:
        raise ValueError(f"Unsupported bbox_mode '{mode}'. Valid modes: {sorted(VALID_BBOX_MODES)}")

    return mode


def _validate_sort_order(order: str) -> str:
    """
    Validate sort order.
    """
    if not isinstance(order, str) or not order.strip():
        raise ValueError("sort_order must be a non-empty string.")

    order = order.strip().lower()

    if order not in VALID_SORT_ORDERS:
        raise ValueError(f"Unsupported sort_order '{order}'. Valid orders: {sorted(VALID_SORT_ORDERS)}")

    return order


def _configured_fields(config: dict[str, Any]) -> dict[str, Any]:
    """
    Return configured output fields.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in spatial_query_filter config must be a dict.")

    return {
        "add_source_index": bool(fields.get("add_source_index", True)),
        "add_filter_status": bool(fields.get("add_filter_status", True)),
        "source_index_field": str(fields.get("source_index_field", "_source_index")),
        "filter_status_field": str(fields.get("filter_status_field", "_filter_status")),
    }


def _validate_limit(value: Any, *, max_limit: int | None = None, allow_none: bool = True) -> int | None:
    """
    Validate limit.
    """
    if value is None:
        if allow_none:
            return None
        raise ValueError("limit cannot be None.")

    if isinstance(value, bool):
        raise ValueError("limit must be an integer or null.")

    try:
        limit = int(value)
    except Exception as exc:
        raise ValueError("limit must be an integer or null.") from exc

    if limit < 0:
        raise ValueError("limit must be >= 0.")

    if max_limit is not None and limit > max_limit:
        raise ValueError(f"limit is too large. Maximum allowed value is {max_limit}.")

    return limit


def _validate_offset(value: Any) -> int:
    """
    Validate offset.
    """
    if isinstance(value, bool):
        raise ValueError("offset must be an integer.")

    try:
        offset = int(value)
    except Exception as exc:
        raise ValueError("offset must be an integer.") from exc

    if offset < 0:
        raise ValueError("offset must be >= 0.")

    return offset


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize a GeoJSON Feature.
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

    Examples:
        name
        address.city
        tags.0
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


def _normalize_text(value: Any, case_sensitive: bool) -> Any:
    """
    Normalize string value for case-insensitive matching.
    """
    if isinstance(value, str) and not case_sensitive:
        return value.lower()
    return value


def _compare_values(left: Any, operator: str, right: Any, *, case_sensitive: bool) -> bool:
    """
    Compare two values using supported operator.
    """
    operator = operator.strip().lower()

    if operator not in VALID_OPERATORS:
        raise ValueError(f"Unsupported filter operator '{operator}'. Valid operators: {sorted(VALID_OPERATORS)}")

    if operator == "exists":
        expected = bool(right)
        exists = left is not MISSING
        return exists is expected

    if operator == "is_null":
        expected = bool(right)
        is_null = left is MISSING or left is None
        return is_null is expected

    if left is MISSING:
        return False

    left_cmp = _normalize_text(left, case_sensitive)
    right_cmp = _normalize_text(right, case_sensitive)

    if operator == "eq":
        return left_cmp == right_cmp

    if operator == "ne":
        return left_cmp != right_cmp

    if operator == "gt":
        return left_cmp > right_cmp

    if operator == "gte":
        return left_cmp >= right_cmp

    if operator == "lt":
        return left_cmp < right_cmp

    if operator == "lte":
        return left_cmp <= right_cmp

    if operator == "in":
        if not isinstance(right_cmp, (list, tuple, set)):
            raise ValueError("operator 'in' requires a list/tuple/set value.")
        values = [_normalize_text(item, case_sensitive) for item in right_cmp]
        return left_cmp in values

    if operator == "not_in":
        if not isinstance(right_cmp, (list, tuple, set)):
            raise ValueError("operator 'not_in' requires a list/tuple/set value.")
        values = [_normalize_text(item, case_sensitive) for item in right_cmp]
        return left_cmp not in values

    if operator == "contains":
        if isinstance(left_cmp, str):
            return str(right_cmp) in left_cmp
        if isinstance(left_cmp, (list, tuple, set)):
            return right_cmp in left_cmp
        return False

    if operator == "startswith":
        return isinstance(left_cmp, str) and str(left_cmp).startswith(str(right_cmp))

    if operator == "endswith":
        return isinstance(left_cmp, str) and str(left_cmp).endswith(str(right_cmp))

    if operator == "regex":
        if not isinstance(left, str):
            return False
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            return re.search(str(right), left, flags=flags) is not None
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {right!r}") from exc

    if operator == "between":
        if not isinstance(right_cmp, (list, tuple)) or len(right_cmp) != 2:
            raise ValueError("operator 'between' requires a two-item list/tuple.")
        lower = _normalize_text(right_cmp[0], case_sensitive)
        upper = _normalize_text(right_cmp[1], case_sensitive)
        return lower <= left_cmp <= upper

    raise ValueError(f"Unsupported filter operator '{operator}'.")


def _eval_field_condition(properties: dict[str, Any], condition: dict[str, Any], *, case_sensitive: bool) -> bool:
    """
    Evaluate condition in canonical form:
        {"field": "population", "op": "gt", "value": 1000}
    """
    field = condition.get("field")
    operator = condition.get("op", "eq")
    value = condition.get("value")

    if not isinstance(field, str) or not field.strip():
        raise ValueError("Field condition requires non-empty 'field'.")

    if not isinstance(operator, str) or not operator.strip():
        raise ValueError("Field condition requires non-empty 'op'.")

    left = _get_path(properties, field.strip(), default=MISSING)

    return _compare_values(left, operator, value, case_sensitive=case_sensitive)


def _eval_shortcut_condition(properties: dict[str, Any], where: dict[str, Any], *, case_sensitive: bool) -> bool:
    """
    Evaluate shortcut condition syntax.

    Examples:
        {"name": "Tehran"}
        {"population": {"gt": 1000000}}
        {"class": {"in": ["city", "town"]}}
    """
    for field, expected in where.items():
        if field in {"and", "or", "not", "field", "op", "value"}:
            continue

        left = _get_path(properties, str(field), default=MISSING)

        if isinstance(expected, dict):
            for op, value in expected.items():
                if not _compare_values(left, str(op), value, case_sensitive=case_sensitive):
                    return False
        else:
            if not _compare_values(left, "eq", expected, case_sensitive=case_sensitive):
                return False

    return True


def _eval_where(properties: dict[str, Any], where: Any, *, case_sensitive: bool) -> bool:
    """
    Evaluate full where expression.

    Supported:
        None
        {"field": "name", "op": "contains", "value": "teh"}
        {"and": [cond1, cond2]}
        {"or": [cond1, cond2]}
        {"not": cond}
        {"name": "Tehran", "population": {"gt": 1000}}
    """
    if where is None:
        return True

    if not isinstance(where, dict):
        raise ValueError("where must be a dict/object or None.")

    if "and" in where:
        items = where["and"]
        if not isinstance(items, list):
            raise ValueError("'and' condition must be a list.")
        return all(_eval_where(properties, item, case_sensitive=case_sensitive) for item in items)

    if "or" in where:
        items = where["or"]
        if not isinstance(items, list):
            raise ValueError("'or' condition must be a list.")
        return any(_eval_where(properties, item, case_sensitive=case_sensitive) for item in items)

    if "not" in where:
        return not _eval_where(properties, where["not"], case_sensitive=case_sensitive)

    if "field" in where:
        return _eval_field_condition(properties, where, case_sensitive=case_sensitive)

    return _eval_shortcut_condition(properties, where, case_sensitive=case_sensitive)


def _is_number(value: Any) -> bool:
    """
    Return True if numeric and not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_position(value: Any) -> bool:
    """
    Return True if value is a coordinate position [x, y, ...].
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
    """
    Return True if two bboxes intersect or touch.
    """
    if not a or not b:
        return False

    return not (
        a[2] < b[0] - EPSILON
        or a[0] > b[2] + EPSILON
        or a[3] < b[1] - EPSILON
        or a[1] > b[3] + EPSILON
    )


def _bbox_within(inner: list[float] | None, outer: list[float] | None) -> bool:
    """
    Return True if inner bbox is within outer bbox.
    """
    if not inner or not outer:
        return False

    return (
        inner[0] >= outer[0] - EPSILON
        and inner[1] >= outer[1] - EPSILON
        and inner[2] <= outer[2] + EPSILON
        and inner[3] <= outer[3] + EPSILON
    )


def _feature_matches_bbox(feature: dict[str, Any], bbox: list[float] | None, bbox_mode: str) -> bool:
    """
    Check feature bbox relation.
    """
    if bbox is None:
        return True

    geometry = feature.get("geometry")
    feature_bbox = _geometry_bbox(geometry)

    bbox_mode = _validate_bbox_mode(bbox_mode)

    if bbox_mode == "intersects":
        return _bboxes_intersect(feature_bbox, bbox)

    if bbox_mode == "within":
        return _bbox_within(feature_bbox, bbox)

    raise ValueError(f"Unsupported bbox_mode: {bbox_mode}")


def _normalize_geometry_types(geometry_types: Any, *, case_sensitive: bool = True) -> set[str] | None:
    """
    Normalize geometry type filter.
    """
    if geometry_types is None:
        return None

    if isinstance(geometry_types, str):
        values = [geometry_types]
    elif isinstance(geometry_types, (list, tuple, set)):
        values = list(geometry_types)
    else:
        raise ValueError("geometry_types must be string, list, set, tuple, or None.")

    result: set[str] = set()

    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("geometry_types items must be non-empty strings.")

        text = item.strip()
        result.add(text if case_sensitive else text.lower())

    return result


def _feature_matches_geometry_type(
    feature: dict[str, Any],
    geometry_types: set[str] | None,
    *,
    case_sensitive: bool = True,
) -> bool:
    """
    Check feature geometry type.
    """
    if geometry_types is None:
        return True

    geometry = feature.get("geometry")

    if geometry is None:
        gtype = "Null"
    elif isinstance(geometry, dict):
        gtype = str(geometry.get("type") or "Unknown")
    else:
        gtype = "Invalid"

    gtype = gtype if case_sensitive else gtype.lower()

    return gtype in geometry_types


def _make_output_feature(
    *,
    source_feature: dict[str, Any],
    source_index: int,
    preserve_properties: bool,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """
    Build output feature.
    """
    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}

    if fields["add_source_index"]:
        properties[fields["source_index_field"]] = source_index

    if fields["add_filter_status"]:
        properties[fields["filter_status_field"]] = "matched"

    return {
        "type": "Feature",
        "geometry": source_feature.get("geometry"),
        "properties": properties,
    }


def _sort_features(
    rows: list[tuple[int, dict[str, Any]]],
    sort_by: str | None,
    sort_order: str,
    *,
    case_sensitive: bool,
) -> list[tuple[int, dict[str, Any]]]:
    """
    Sort rows by property path.
    """
    if not sort_by:
        return rows

    if not isinstance(sort_by, str) or not sort_by.strip():
        raise ValueError("sort_by must be a non-empty string or None.")

    sort_order = _validate_sort_order(sort_order)
    reverse = sort_order == "desc"

    present: list[tuple[int, dict[str, Any]]] = []
    missing: list[tuple[int, dict[str, Any]]] = []

    for row in rows:
        _, feature = row
        value = _get_path(feature.get("properties") or {}, sort_by.strip(), default=MISSING)

        if value is MISSING or value is None:
            missing.append(row)
        else:
            present.append(row)

    def sort_key(row: tuple[int, dict[str, Any]]) -> Any:
        _, feature = row
        value = _get_path(feature.get("properties") or {}, sort_by.strip(), default=MISSING)

        if isinstance(value, str):
            return value if case_sensitive else value.lower()

        return value

    present = sorted(present, key=sort_key, reverse=reverse)

    # Keep missing values at the end for both asc and desc.
    return present + missing


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
    name="filter_features",
    keywords=[
        "filter",
        "query filter",
        "attribute filter",
        "spatial query",
        "where",
        "bbox filter",
        "geometry type filter",
        "sort features",
        "فیلتر",
        "فیلتر مکانی",
        "فیلتر توصیفی",
        "جستجوی مکانی",
        "شرط توصیفی",
        "مرتب سازی",
        "محدود کردن نتایج",
    ],
    description="Filter vector features by attribute conditions, bbox, geometry type, sorting and pagination.",
    required_inputs=["features"],
    optional_inputs=[
        "where",
        "bbox",
        "bbox_mode",
        "geometry_types",
        "case_sensitive",
        "sort_by",
        "sort_order",
        "limit",
        "offset",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "query",
        "data_type": "vector",
        "operation": "filter",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_query",
        "config_aware": True,
        "attribute_filter": True,
        "bbox_filter": True,
        "routable": True,
    },
)
def filter_features(
    features: Any,
    where: dict[str, Any] | None = None,
    bbox: list[float] | dict[str, float] | None = None,
    bbox_mode: str | None = None,
    geometry_types: str | list[str] | None = None,
    case_sensitive: bool | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    limit: int | None = None,
    offset: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Filter vector features.

    Args:
        features:
            VectorOut, FeatureCollection, Feature, or list[Feature].
        where:
            Attribute filter expression.
        bbox:
            Optional bbox [minx, miny, maxx, maxy] or dict.
        bbox_mode:
            intersects | within.
        geometry_types:
            Optional geometry type or list of geometry types.
        case_sensitive:
            Case sensitivity for string comparisons.
        sort_by:
            Optional property path to sort by.
        sort_order:
            asc | desc.
        limit:
            Maximum number of output features.
        offset:
            Number of matched features to skip.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with filtered features.
    """
    config = _load_filter_config()

    final_case_sensitive = bool(
        pick_first(case_sensitive, config.get("case_sensitive"), default=False)
    )

    final_bbox_mode = _validate_bbox_mode(
        str(pick_first(bbox_mode, config.get("default_bbox_mode"), default="intersects"))
    )

    final_sort_order = _validate_sort_order(sort_order)

    max_limit_config = config.get("max_limit", 10000)
    max_limit = None if max_limit_config is None else int(max_limit_config)

    default_limit = config.get("default_limit")
    final_limit = _validate_limit(
        pick_first(limit, default_limit, default=None),
        max_limit=max_limit,
        allow_none=True,
    )

    final_offset = _validate_offset(
        pick_first(offset, config.get("default_offset"), default=0)
    )

    final_bbox = _normalize_bbox(bbox)
    final_geometry_types = _normalize_geometry_types(
        geometry_types,
        case_sensitive=True,
    )

    preserve_properties = bool(config.get("preserve_properties", True))
    fields = _configured_fields(config)

    input_features, source_info = _extract_features(features)

    matched_rows: list[tuple[int, dict[str, Any]]] = []

    where_checked = 0
    bbox_checked = 0
    geometry_type_checked = 0

    for idx, feature in enumerate(input_features):
        properties = feature.get("properties") or {}

        if not _eval_where(properties, where, case_sensitive=final_case_sensitive):
            where_checked += 1
            continue

        where_checked += 1

        if not _feature_matches_geometry_type(
            feature,
            final_geometry_types,
            case_sensitive=True,
        ):
            geometry_type_checked += 1
            continue

        geometry_type_checked += 1

        if not _feature_matches_bbox(feature, final_bbox, final_bbox_mode):
            bbox_checked += 1
            continue

        bbox_checked += 1
        matched_rows.append((idx, feature))

    matched_before_pagination = len(matched_rows)

    matched_rows = _sort_features(
        matched_rows,
        sort_by=sort_by,
        sort_order=final_sort_order,
        case_sensitive=final_case_sensitive,
    )

    if final_offset:
        matched_rows = matched_rows[final_offset:]

    if final_limit is not None:
        matched_rows = matched_rows[:final_limit]

    output_features = [
        _make_output_feature(
            source_feature=feature,
            source_index=idx,
            preserve_properties=preserve_properties,
            fields=fields,
        )
        for idx, feature in matched_rows
    ]

    stats = _build_vector_metadata(output_features)

    # Avoid collision:
    # - metadata["geometry_types"] is the requested geometry type filter.
    # - stats["geometry_types"] is output geometry type statistics.
    if "geometry_types" in stats:
        stats["output_geometry_types"] = stats.pop("geometry_types")

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "spatial_query_filter",
        "loader": PLUGIN_ID,
        "operation": "filter",
        "input_feature_count": len(input_features),
        "matched_before_pagination": matched_before_pagination,
        "output_feature_count": len(output_features),
        "where_applied": where is not None,
        "bbox_applied": final_bbox is not None,
        "bbox_mode": final_bbox_mode,
        "geometry_types_applied": final_geometry_types is not None,
        "geometry_types": sorted(final_geometry_types) if final_geometry_types else None,
        "case_sensitive": final_case_sensitive,
        "sort_by": sort_by,
        "sort_order": final_sort_order,
        "limit": final_limit,
        "offset": final_offset,
        "where_checked": where_checked,
        "bbox_checked": bbox_checked,
        "geometry_type_checked": geometry_type_checked,
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
    name="Spatial Query Filter",
    description=(
        "Filters vector features by attribute predicates, bbox relation, geometry type, "
        "sorting, offset and limit."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
