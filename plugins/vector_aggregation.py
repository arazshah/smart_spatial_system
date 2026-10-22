"""
vector_aggregation.py

GeoChat SDK Plugin
==================

Plugin ID:
    vector_aggregation

Capabilities:
    - aggregate_points_to_polygons:
        For each polygon, count the points inside it and optionally summarise a
        numeric point attribute (sum/mean/min/max) and compute point density.
    - aggregate_table:
        Group features by one or more attributes and summarise numeric fields
        (count/sum/mean/median/min/max/std/first/last/unique_count), with sort
        and limit. The output is attribute-only (null geometry): a table.

Config-aware behavior:
    Reads config/plugins/vector_aggregation.yaml (size limits).

Notes:
    A point on the shared boundary of two polygons, or inside overlapping
    polygons, is counted for each of them. Non-point geometries passed as
    ``points`` are represented by their representative point.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    get_shapely,
    load_config,
    make_vector_out,
    to_int,
    validate_choice,
)

PLUGIN_ID = "vector_aggregation"

DEFAULT_MAX_FEATURES = 500_000
POINT_STATS = {"sum", "mean", "min", "max"}
TABLE_OPS = {
    "count", "non_null_count", "sum", "mean", "median", "min", "max", "std", "first", "last",
    "unique_count",
}


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _summarise(values: list[float], stat: str) -> float | None:
    if not values:
        return None
    return {
        "sum": sum,
        "mean": statistics.fmean,
        "min": min,
        "max": max,
    }[stat](values)


def _as_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ValueError(f"{name} must be a string or a list of strings.")


@capability(
    name="aggregate_points_to_polygons",
    keywords=[
        "count points in polygons", "points per polygon", "aggregate points", "point density",
        "count per district", "points in zones", "summarize points by area",
        "شمارش نقاط در پلی گون", "تعداد نقطه در محدوده", "تجمیع نقاط", "تراکم نقاط",
        "تعداد در هر منطقه", "جمع نقاط به محدوده",
    ],
    description=(
        "Count the points inside each polygon and optionally summarise a numeric point "
        "attribute (sum/mean/min/max) and point density per polygon."
    ),
    required_inputs=["points", "polygons"],
    optional_inputs=["value_field", "stats", "density", "count_field", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "aggregate_points_to_polygons",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def aggregate_points_to_polygons(
    points: Any,
    polygons: Any,
    value_field: str | None = None,
    stats: list[str] | str | None = None,
    density: bool = False,
    count_field: str = "point_count",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Aggregate points into polygons.

    Args:
        points: Point features (other geometries use their representative point).
        polygons: Polygon features receiving the aggregates.
        value_field: Numeric point attribute to summarise (non-numeric values are skipped).
        stats: Any of sum, mean, min, max (default ["sum", "mean"] when value_field is set).
        density: Also add ``<count_field>_density`` = count / polygon area (planar units).
        count_field: Name of the count property.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the polygons plus ``count_field`` and the requested statistics.
    """
    config = load_config(PLUGIN_ID)
    limit = to_int(config.get("max_features", DEFAULT_MAX_FEATURES), "max_features")
    if not isinstance(count_field, str) or not count_field.strip():
        raise ValueError("count_field must be a non-empty string.")

    point_features, source_info = extract_features(points)
    polygon_features, _ = extract_features(polygons)
    check_limit(len(point_features), limit, "point count")
    check_limit(len(polygon_features), limit, "polygon count")

    wanted = [validate_choice(s, POINT_STATS, "stats") for s in _as_list(stats, "stats")]
    if value_field and not wanted:
        wanted = ["sum", "mean"]
    if wanted and not value_field:
        raise ValueError("stats requires value_field.")

    shape, _, _ = get_shapely(PLUGIN_ID)
    from shapely import STRtree

    point_geoms = []
    point_values: list[float | None] = []
    for feature in point_features:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shape(geometry)
        if geom.is_empty:
            continue
        point_geoms.append(geom if geom.geom_type == "Point" else geom.representative_point())
        point_values.append(_numeric((feature.get("properties") or {}).get(value_field))
                            if value_field else None)
    tree = STRtree(point_geoms)

    output: list[dict[str, Any]] = []
    for feature in polygon_features:
        geometry = feature.get("geometry")
        properties = dict(feature.get("properties") or {})
        count = 0
        values: list[float] = []
        area = None
        if geometry:
            polygon = shape(geometry)
            if polygon.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError("polygons must contain only Polygon/MultiPolygon geometries.")
            area = polygon.area
            hits = tree.query(polygon, predicate="intersects")
            count = len(hits)
            if value_field:
                values = [v for i in hits if (v := point_values[int(i)]) is not None]
        properties[count_field] = count
        for stat in wanted:
            properties[f"{value_field}_{stat}"] = _summarise(values, stat)
        if density:
            properties[f"{count_field}_density"] = (count / area) if area else None
        output.append({"type": "Feature", "geometry": geometry, "properties": properties})

    return make_vector_out(
        output,
        plugin_id=PLUGIN_ID,
        operation="aggregate_points_to_polygons",
        source_info=source_info,
        extra={
            "point_count": len(point_geoms),
            "polygon_count": len(polygon_features),
            "value_field": value_field,
            "stats": wanted,
        },
        user_metadata=metadata,
    )


def _reduce(values: list[Any], op: str) -> Any:
    non_null = [v for v in values if v is not None]
    if op == "count":
        return len(values)
    if op == "non_null_count":
        return len(non_null)
    if op == "first":
        return non_null[0] if non_null else None
    if op == "last":
        return non_null[-1] if non_null else None
    if op == "unique_count":
        return len({repr(v) for v in non_null})
    numbers = [n for v in non_null if (n := _numeric(v)) is not None]
    if not numbers:
        return None
    if op == "median":
        return statistics.median(numbers)
    if op == "std":
        return statistics.pstdev(numbers) if len(numbers) > 1 else 0.0
    return _summarise(numbers, op)


def _normalize_aggregations(aggregations: Any) -> list[tuple[str, str]]:
    if not isinstance(aggregations, dict) or not aggregations:
        raise ValueError(
            'aggregations must be a non-empty object like {"population": ["sum", "mean"]}.'
        )
    pairs: list[tuple[str, str]] = []
    for field, ops in aggregations.items():
        for op in _as_list(ops, f"aggregations['{field}']"):
            pairs.append((str(field), validate_choice(op, TABLE_OPS, "aggregation operation")))
    return pairs


@capability(
    name="aggregate_table",
    keywords=[
        "group by", "aggregate", "pivot", "summary table", "sum by", "average by", "count by",
        "group statistics", "top groups",
        "گروه بندی", "تجمیع", "جدول خلاصه", "جمع بر اساس", "میانگین بر اساس", "تعداد بر اساس",
        "آمار گروهی",
    ],
    description=(
        "Group features by one or more attributes and summarise numeric fields "
        "(count, sum, mean, median, min, max, std, first, last, unique_count); "
        "sort and limit the groups. Returns an attribute table (no geometry)."
    ),
    required_inputs=["features", "aggregations"],
    optional_inputs=["group_by", "sort_by", "descending", "limit", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "aggregate_table",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def aggregate_table(
    features: Any,
    aggregations: dict[str, Any],
    group_by: str | list[str] | None = None,
    sort_by: str | None = None,
    descending: bool = True,
    limit: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Group and summarise feature attributes.

    Args:
        features: Input features.
        aggregations: ``{field: op | [ops]}``; output columns are named ``<field>_<op>``
            (the ``count`` operation of any field is also written as ``count``).
        group_by: Attribute name(s) to group by; omit for one overall row.
        sort_by: Output column to sort by.
        descending: Sort direction (nulls always last).
        limit: Keep only the first N rows after sorting.
        metadata: Extra output metadata.

    Returns:
        VectorOut whose features have null geometry and the group and summary columns.
    """
    config = load_config(PLUGIN_ID)
    input_features, source_info = extract_features(features)
    check_limit(
        len(input_features),
        to_int(config.get("max_features", DEFAULT_MAX_FEATURES), "max_features"),
        "input feature count",
    )
    group_fields = _as_list(group_by, "group_by")
    pairs = _normalize_aggregations(aggregations)
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError("limit must be a positive integer.")

    groups: dict[tuple[str, ...], tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}
    for feature in input_features:
        properties = feature.get("properties") or {}
        key_values = tuple(properties.get(f) for f in group_fields)
        key = tuple(repr(v) for v in key_values)
        groups.setdefault(key, (key_values, []))[1].append(properties)

    rows: list[dict[str, Any]] = []
    for key_values, members in groups.values():
        row: dict[str, Any] = dict(zip(group_fields, key_values, strict=True))
        for field, op in pairs:
            value = _reduce([m.get(field) for m in members], op)
            row[f"{field}_{op}"] = value
            if op == "count":
                row["count"] = value
        rows.append(row)

    if sort_by:
        if rows and sort_by not in rows[0]:
            raise ValueError(f"sort_by '{sort_by}' is not an output column: {sorted(rows[0])}.")
        present = [r for r in rows if r.get(sort_by) is not None]
        absent = [r for r in rows if r.get(sort_by) is None]
        try:
            present.sort(key=lambda r: r[sort_by], reverse=bool(descending))
        except TypeError as exc:
            raise ValueError(f"Cannot sort mixed value types in '{sort_by}'.") from exc
        rows = present + absent
    if limit is not None:
        rows = rows[:limit]

    output = [{"type": "Feature", "geometry": None, "properties": row} for row in rows]
    return make_vector_out(
        output,
        plugin_id=PLUGIN_ID,
        operation="aggregate_table",
        source_info=source_info,
        extra={
            "group_by": group_fields,
            "group_count": len(groups),
            "input_feature_count": len(input_features),
        },
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Vector Aggregation",
    description="Aggregates points into polygons and groups/summarises feature attribute tables.",
    author="GeoChat Platform Team",
    permissions=[],
)
