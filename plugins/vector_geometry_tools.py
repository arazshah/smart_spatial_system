"""
vector_geometry_tools.py

GeoChat SDK Plugin
==================

Plugin ID:
    vector_geometry_tools

Capabilities:
    - simplify_geometries:
        Reduce vertex counts (Douglas-Peucker) while keeping topology valid.
    - explode_multipart:
        Split Multi* geometries / geometry collections into one feature per part.
    - voronoi_thiessen:
        Thiessen (Voronoi) polygons around points, e.g. "service area of each station",
        optionally clipped to a boundary.
    - calculate_convex_hull:
        Convex hull, bounding box or minimum rotated rectangle of all features or of each group.
    - check_topology:
        Quality report of a layer: invalid, empty, duplicate, overlapping and sliver/tiny geometries.

Config-aware behavior:
    Reads config/plugins/vector_geometry_tools.yaml (size limits).

Important:
    Coordinates are planar; ``tolerance`` is in coordinate units.
"""

from __future__ import annotations

import math
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
    to_float,
    to_int,
    validate_choice,
)

PLUGIN_ID = "vector_geometry_tools"

DEFAULTS = {"max_features": 200_000, "max_output_features": 500_000, "max_points": 50_000}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _vertex_count(geom: Any) -> int:
    if geom.is_empty:
        return 0
    if hasattr(geom, "geoms"):
        return sum(_vertex_count(g) for g in geom.geoms)
    if geom.geom_type == "Polygon":
        return len(geom.exterior.coords) + sum(len(r.coords) for r in geom.interiors)
    return len(geom.coords)


_META = {
    "category": "analysis",
    "data_type": "vector",
    "returns": "VectorOut",
    "artifact_kind": "features",
    "access_scope": "spatial_analysis",
    "config_aware": True,
    "routable": True,
}


@capability(
    name="simplify_geometries",
    keywords=[
        "simplify", "generalize", "reduce vertices", "douglas peucker", "lighten geometry",
        "ساده سازی", "ساده‌سازی هندسه", "کاهش رأس", "تعمیم", "سبک کردن لایه",
    ],
    description=(
        "Simplify line and polygon geometries (Douglas-Peucker) to cut vertex counts, "
        "keeping topology valid by default."
    ),
    required_inputs=["features", "tolerance"],
    optional_inputs=["preserve_topology", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "simplify"},
)
def simplify_geometries(
    features: Any,
    tolerance: float,
    preserve_topology: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Simplify geometries.

    Args:
        features: Input features.
        tolerance: Maximum allowed deviation, in coordinate units (> 0).
        preserve_topology: Avoid invalid or collapsed results (slower, safer). When false,
            tiny polygons can disappear; they are dropped and counted.
        metadata: Extra output metadata.

    Returns:
        VectorOut with simplified geometries; metadata has ``vertices_before`` / ``vertices_after``.
    """
    config = load_config(PLUGIN_ID)
    tol = to_float(tolerance, "tolerance")
    if tol <= 0 or not math.isfinite(tol):
        raise ValueError("tolerance must be a positive number.")
    shape, mapping, _ = get_shapely(PLUGIN_ID)
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")

    before = after = dropped = 0
    output: list[dict[str, Any]] = []
    for feature in input_features:
        geometry = feature.get("geometry")
        if not geometry:
            output.append(feature)
            continue
        geom = shape(geometry)
        before += _vertex_count(geom)
        result = geom.simplify(tol, preserve_topology=bool(preserve_topology))
        if result.is_empty:
            dropped += 1
            continue
        after += _vertex_count(result)
        output.append({"type": "Feature", "geometry": dict(mapping(result)),
                       "properties": dict(feature.get("properties") or {})})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="simplify", source_info=source_info,
        extra={"tolerance": tol, "vertices_before": before, "vertices_after": after,
               "dropped_empty": dropped},
        user_metadata=metadata,
    )


@capability(
    name="explode_multipart",
    keywords=[
        "explode", "multipart to singlepart", "split multipart", "break apart", "single parts",
        "تبدیل چندبخشی به تک بخشی", "شکستن چندبخشی", "جداسازی بخش ها", "explode multipart",
    ],
    description=(
        "Split MultiPoint/MultiLineString/MultiPolygon features and geometry collections into one "
        "feature per part; attributes are copied and part_index records the order."
    ),
    required_inputs=["features"],
    optional_inputs=["metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "explode_multipart"},
)
def explode_multipart(features: Any, metadata: dict[str, Any] | None = None) -> VectorOut:
    """
    Explode multipart geometries.

    Args:
        features: Input features.
        metadata: Extra output metadata.

    Returns:
        VectorOut with one feature per geometry part (``source_feature_index``, ``part_index``).
    """
    config = load_config(PLUGIN_ID)
    shape, mapping, _ = get_shapely(PLUGIN_ID)
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")
    max_output = _limit(config, "max_output_features")

    def parts(geom: Any):
        if hasattr(geom, "geoms"):
            for sub in geom.geoms:
                yield from parts(sub)
        elif not geom.is_empty:
            yield geom

    output: list[dict[str, Any]] = []
    for index, feature in enumerate(input_features):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        for part_index, part in enumerate(parts(shape(geometry))):
            properties = dict(feature.get("properties") or {})
            properties["source_feature_index"] = index
            properties["part_index"] = part_index
            output.append({"type": "Feature", "geometry": dict(mapping(part)),
                           "properties": properties})
            check_limit(len(output), max_output, "output feature count")
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="explode_multipart", source_info=source_info,
        extra={"input_feature_count": len(input_features)}, user_metadata=metadata,
    )


@capability(
    name="voronoi_thiessen",
    keywords=[
        "voronoi", "thiessen", "thiessen polygons", "nearest area", "catchment of each point",
        "service zones of points",
        "ورونوی", "تیسن", "چندضلعی تیسن", "حوزه نفوذ", "محدوده هر ایستگاه", "نزدیک ترین ناحیه",
    ],
    description=(
        "Thiessen / Voronoi polygons: the area closer to each input point than to any other, "
        "optionally clipped to a boundary layer or bbox; point attributes are copied to its cell."
    ),
    required_inputs=["points"],
    optional_inputs=["clip_to", "bbox", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "voronoi"},
)
def voronoi_thiessen(
    points: Any,
    clip_to: Any = None,
    bbox: list[float] | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Build Voronoi cells.

    Args:
        points: Point features (other geometries use their representative point). Duplicate
            locations keep the first feature.
        clip_to: Optional polygon layer the cells are clipped to (e.g. a city boundary).
        bbox: Optional [minx, miny, maxx, maxy] clip. Default: the points' extent plus 20 %.
        metadata: Extra output metadata.

    Returns:
        VectorOut of Polygon cells carrying the properties of their point and ``cell_area``.
    """
    config = load_config(PLUGIN_ID)
    shape, mapping, unary_union = get_shapely(PLUGIN_ID)
    from shapely import STRtree, box
    from shapely.geometry import MultiPoint
    from shapely.ops import voronoi_diagram

    input_features, source_info = extract_features(points)
    check_limit(len(input_features), _limit(config, "max_points"), "point count")
    located: list[tuple[Any, dict[str, Any]]] = []
    seen: set[tuple[float, float]] = set()
    duplicates = 0
    for feature in input_features:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shape(geometry)
        if geom.is_empty:
            continue
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        key = (point.x, point.y)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        located.append((point, dict(feature.get("properties") or {})))
    if len(located) < 2:
        raise ValueError("Voronoi cells need at least two distinct points.")

    xs = [p.x for p, _ in located]
    ys = [p.y for p, _ in located]
    if clip_to is not None:
        clip_features, _ = extract_features(clip_to)
        clip_geom = unary_union([shape(f["geometry"]) for f in clip_features if f.get("geometry")])
    elif bbox is not None:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("bbox must be [minx, miny, maxx, maxy].")
        clip_geom = box(*(to_float(v, "bbox") for v in bbox))
    else:
        pad_x = max((max(xs) - min(xs)) * 0.2, 1e-9)
        pad_y = max((max(ys) - min(ys)) * 0.2, 1e-9)
        clip_geom = box(min(xs) - pad_x, min(ys) - pad_y, max(xs) + pad_x, max(ys) + pad_y)
    if clip_geom.is_empty:
        raise ValueError("The clip geometry is empty.")

    envelope = clip_geom.envelope.union(MultiPoint([p for p, _ in located]).envelope).buffer(
        max(clip_geom.bounds[2] - clip_geom.bounds[0], clip_geom.bounds[3] - clip_geom.bounds[1]))
    cells = voronoi_diagram(MultiPoint([p for p, _ in located]), envelope=envelope)
    tree = STRtree([p for p, _ in located])
    output: list[dict[str, Any]] = []
    for cell in cells.geoms:
        clipped = cell.intersection(clip_geom)
        if clipped.is_empty or clipped.area == 0:
            continue
        owner = int(tree.nearest(cell.representative_point()))
        # the cell of a point contains that point; nearest() picks it unless several are equidistant
        hits = tree.query(cell, predicate="contains")
        if len(hits):
            owner = int(hits[0])
        properties = dict(located[owner][1])
        properties["cell_area"] = clipped.area
        output.append({"type": "Feature", "geometry": dict(mapping(clipped)), "properties": properties})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="voronoi", source_info=source_info,
        extra={"point_count": len(located), "duplicate_points_skipped": duplicates,
               "clipped": clip_to is not None or bbox is not None},
        user_metadata=metadata,
    )


@capability(
    name="calculate_convex_hull",
    keywords=[
        "convex hull", "bounding box", "envelope", "minimum bounding rectangle", "extent polygon",
        "outline of points", "boundary of points",
        "پوش محدب", "کادر محصور", "مستطیل محیطی", "محدوده نقاط", "پوشش بیرونی",
    ],
    description=(
        "Convex hull, bounding box (envelope) or minimum rotated rectangle around all features or "
        "around each group of a field; optionally buffered."
    ),
    required_inputs=["features"],
    optional_inputs=["shape", "group_by", "buffer", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "convex_hull"},
)
def calculate_convex_hull(
    features: Any,
    shape: str = "convex_hull",
    group_by: str | None = None,
    buffer: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Bounding shapes.

    Args:
        features: Input features.
        shape: ``convex_hull`` | ``envelope`` (axis-aligned bounding box) |
            ``min_rotated_rectangle``.
        group_by: Property to group by (one shape per value); omit for one overall shape.
        buffer: Distance added around the shape (coordinate units, >= 0).
        metadata: Extra output metadata.

    Returns:
        VectorOut of Polygon features (``feature_count`` per shape, ``shape_area``, the group value).
        A group with a single point yields a Point / the geometry itself (no area to wrap).
    """
    config = load_config(PLUGIN_ID)
    kind = validate_choice(shape, {"convex_hull", "envelope", "min_rotated_rectangle"}, "shape")
    pad = to_float(buffer, "buffer")
    if pad < 0 or not math.isfinite(pad):
        raise ValueError("buffer must be a non-negative number.")
    if group_by is not None and (not isinstance(group_by, str) or not group_by.strip()):
        raise ValueError("group_by must be a property name.")
    shapely_shape, mapping, unary_union = get_shapely(PLUGIN_ID)
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")

    groups: dict[str, tuple[Any, list[Any]]] = {}
    for feature in input_features:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shapely_shape(geometry)
        if geom.is_empty:
            continue
        key_value = (feature.get("properties") or {}).get(group_by) if group_by else None
        groups.setdefault(repr(key_value), (key_value, []))[1].append(geom)
    if not groups:
        raise ValueError("No feature has a usable geometry.")

    output = []
    for key_value, geoms in groups.values():
        union = unary_union(geoms)
        result = (union.convex_hull if kind == "convex_hull"
                  else union.envelope if kind == "envelope" else union.minimum_rotated_rectangle)
        if pad:
            result = result.buffer(pad)
        props: dict[str, Any] = {"feature_count": len(geoms), "shape_area": result.area}
        if group_by:
            props[group_by] = key_value
        output.append({"type": "Feature", "geometry": dict(mapping(result)), "properties": props})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="convex_hull", source_info=source_info,
        extra={"shape": kind, "group_by": group_by, "buffer": pad}, user_metadata=metadata,
    )


@capability(
    name="check_topology",
    keywords=[
        "topology check", "validate layer", "geometry quality", "find overlaps", "find slivers",
        "data quality", "invalid geometries report", "duplicate geometries",
        "بررسی توپولوژی", "کنترل کیفیت", "همپوشانی پلی گون", "هندسه نامعتبر", "گزارش خطاهای هندسی",
        "پلی گون های تکراری",
    ],
    description=(
        "Quality report of a vector layer: invalid and empty geometries, duplicates, overlapping "
        "polygons, slivers and tiny polygons. One output feature per issue, located at the problem."
    ),
    required_inputs=["features"],
    optional_inputs=["checks", "min_area", "sliver_ratio", "overlap_tolerance", "max_issues", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "check_topology"},
)
def check_topology(
    features: Any,
    checks: list[str] | None = None,
    min_area: float = 0.0,
    sliver_ratio: float = 0.01,
    overlap_tolerance: float = 0.0,
    max_issues: int = 5000,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Check a layer.

    Args:
        features: Layer to check.
        checks: Subset of ``invalid``, ``empty``, ``duplicate``, ``overlap`` (polygons within the
            layer), ``sliver`` (polygons whose thinness ratio 4*pi*area/perimeter^2 is below
            ``sliver_ratio``), ``small`` (polygons below ``min_area``). Default: all.
        min_area: Area threshold for ``small`` (coordinate units squared; 0 disables the check).
        sliver_ratio: Thinness threshold for ``sliver`` (a circle is 1; 0.01 is a thin strip).
        overlap_tolerance: Overlaps with area <= this are ignored (to skip rounding noise).
        max_issues: Stop after this many issues (the report is then flagged ``truncated``).
        metadata: Extra output metadata.

    Returns:
        VectorOut of issue points: ``issue``, ``feature_index`` (and ``other_index`` for overlaps and
        duplicates), ``detail``, ``area``. Metadata has per-issue counts and ``clean``.
    """
    config = load_config(PLUGIN_ID)
    wanted = list(checks or ["invalid", "empty", "duplicate", "overlap", "sliver", "small"])
    for name in wanted:
        validate_choice(name, {"invalid", "empty", "duplicate", "overlap", "sliver", "small"}, "checks")
    area_floor = to_float(min_area, "min_area")
    thin = to_float(sliver_ratio, "sliver_ratio")
    tolerance = to_float(overlap_tolerance, "overlap_tolerance")
    if area_floor < 0 or thin < 0 or tolerance < 0:
        raise ValueError("min_area, sliver_ratio and overlap_tolerance must be >= 0.")
    cap = to_int(max_issues, "max_issues")
    if not 1 <= cap <= 100_000:
        raise ValueError("max_issues must be between 1 and 100000.")
    shapely_shape, mapping, _ = get_shapely(PLUGIN_ID)
    from shapely import STRtree
    from shapely.validation import explain_validity

    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")

    issues: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    truncated = False

    def report(kind: str, index: int, geom: Any, detail: str, other: int | None = None, area: float | None = None):
        nonlocal truncated
        counts[kind] = counts.get(kind, 0) + 1
        if len(issues) >= cap:
            truncated = True
            return
        point = geom.representative_point() if geom is not None and not geom.is_empty else None
        issues.append({
            "type": "Feature",
            "geometry": dict(mapping(point)) if point is not None else None,
            "properties": {"issue": kind, "feature_index": index, "other_index": other, "detail": detail,
                           "area": area},
        })

    shapes: dict[int, Any] = {}
    for index, feature in enumerate(input_features):
        geometry = feature.get("geometry")
        if not geometry:
            if "empty" in wanted:
                report("empty", index, None, "missing geometry")
            continue
        geom = shapely_shape(geometry)
        if geom.is_empty:
            if "empty" in wanted:
                report("empty", index, geom, "empty geometry")
            continue
        shapes[index] = geom
        if "invalid" in wanted and not geom.is_valid:
            report("invalid", index, geom, explain_validity(geom))

    polygons = {i: g for i, g in shapes.items() if g.geom_type in {"Polygon", "MultiPolygon"} and g.is_valid}
    if "duplicate" in wanted:
        seen: dict[str, int] = {}
        for index, geom in shapes.items():
            if not geom.is_valid:
                continue
            key = geom.normalize().wkb_hex
            if key in seen:
                report("duplicate", index, geom, f"same geometry as feature {seen[key]}", other=seen[key])
            else:
                seen[key] = index
    if "sliver" in wanted or "small" in wanted:
        for index, geom in polygons.items():
            area, perimeter = geom.area, geom.length
            if "small" in wanted and area_floor > 0 and area < area_floor:
                report("small", index, geom, f"area {area:.6g} < {area_floor:g}", area=area)
            if "sliver" in wanted and perimeter > 0 and 4 * math.pi * area / (perimeter * perimeter) < thin:
                report("sliver", index, geom, "thin polygon (thinness ratio below the threshold)", area=area)
    if "overlap" in wanted and polygons:
        ids = list(polygons)
        tree = STRtree([polygons[i] for i in ids])
        for index in ids:
            for other_position in tree.query(polygons[index], predicate="intersects"):
                other = ids[int(other_position)]
                if other <= index:
                    continue
                common = polygons[index].intersection(polygons[other])
                if common.is_empty or common.area <= tolerance or common.geom_type in {"Point", "LineString",
                                                                                     "MultiPoint", "MultiLineString"}:
                    continue
                report("overlap", index, common, f"overlaps feature {other}", other=other, area=common.area)

    return make_vector_out(
        issues, plugin_id=PLUGIN_ID, operation="check_topology", source_info=source_info,
        extra={"checks": wanted, "issue_counts": counts, "issue_total": sum(counts.values()),
               "truncated": truncated, "clean": not counts, "checked_features": len(input_features)},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Vector Geometry Tools",
    description="Simplify geometries, explode multipart features and build Voronoi/Thiessen cells.",
    author="GeoChat Platform Team",
    permissions=[],
)
