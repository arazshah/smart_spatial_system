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


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Vector Geometry Tools",
    description="Simplify geometries, explode multipart features and build Voronoi/Thiessen cells.",
    author="GeoChat Platform Team",
    permissions=[],
)
