"""
spatial_predicate.py

Spatial Predicate Filtering
===========================

Plugin ID:
    spatial_predicate

Purpose:
    Real geometric spatial filtering (not bbox-only).
    Provides true point-in-polygon containment filtering.

Capabilities:
    - filter_points_in_polygon
        Keep only point features that fall inside one or more polygons.

Notes:
    Uses self-contained ray-casting point-in-polygon with hole support.
    Planar test (lon/lat treated as planar). Good for MVP filtering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut


PLUGIN_ID = "spatial_predicate"

EPSILON = 1e-12


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ #
# Feature extraction
# ------------------------------------------------------------------ #

def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(feature, dict):
        raise ValueError(f"Feature at index {index} must be a dict.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": dict(properties),
    }


def _extract_features(input_data: Any, label: str = "features") -> list[dict[str, Any]]:
    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw = getattr(input_data, "features")
    elif isinstance(input_data, dict):
        gtype = input_data.get("type")
        if gtype == "FeatureCollection":
            raw = input_data.get("features", [])
        elif gtype == "Feature":
            raw = [input_data]
        else:
            raise ValueError(f"{label} dict must be FeatureCollection or Feature.")
    elif isinstance(input_data, list):
        raw = input_data
    else:
        raise ValueError(
            f"{label} must be VectorOut-like, FeatureCollection, Feature, or list[Feature]."
        )

    if not isinstance(raw, list):
        raise ValueError(f"{label} features must be a list.")

    return [_normalize_feature(item, idx) for idx, item in enumerate(raw)]


# ------------------------------------------------------------------ #
# Geometry helpers (self-contained ray casting)
# ------------------------------------------------------------------ #

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_position(position: Any) -> tuple[float, float]:
    if (
        isinstance(position, (list, tuple))
        and len(position) >= 2
        and _is_number(position[0])
        and _is_number(position[1])
    ):
        return float(position[0]), float(position[1])
    raise ValueError("Invalid coordinate position.")


def _ring_coords(ring: Any) -> list[tuple[float, float]]:
    if not isinstance(ring, (list, tuple)):
        return []
    points: list[tuple[float, float]] = []
    for pos in ring:
        try:
            points.append(_validate_position(pos))
        except ValueError:
            continue
    return points


def _ensure_closed_ring(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not ring:
        return ring
    if ring[0] != ring[-1]:
        return list(ring) + [ring[0]]
    return ring


def _point_on_segment(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    px, py = p
    ax, ay = a
    bx, by = b

    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > EPSILON:
        return False

    if min(ax, bx) - EPSILON <= px <= max(ax, bx) + EPSILON and \
       min(ay, by) - EPSILON <= py <= max(ay, by) + EPSILON:
        return True
    return False


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    ring = _ensure_closed_ring(ring)
    if len(ring) < 4:
        return False

    x, y = point
    inside = False

    for a, b in zip(ring[:-1], ring[1:]):
        if _point_on_segment(point, a, b):
            return True

        xi, yi = a
        xj, yj = b

        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or EPSILON) + xi
        )
        if intersects:
            inside = not inside

    return inside


def _point_in_polygon_rings(
    point: tuple[float, float],
    rings: list[list[tuple[float, float]]],
) -> bool:
    if not rings:
        return False

    outer = rings[0]
    holes = rings[1:]

    if not _point_in_ring(point, outer):
        return False

    for hole in holes:
        if _point_in_ring(point, hole):
            return False

    return True


def _collect_polygons(geometry: dict[str, Any] | None) -> list[list[list[tuple[float, float]]]]:
    """
    Return list of polygons. Each polygon is list of rings.
    Each ring is list of (x, y).
    """
    polygons: list[list[list[tuple[float, float]]]] = []

    if not isinstance(geometry, dict):
        return polygons

    gtype = geometry.get("type")

    if gtype == "Polygon":
        rings_raw = geometry.get("coordinates")
        if isinstance(rings_raw, (list, tuple)):
            rings = [_ring_coords(r) for r in rings_raw]
            rings = [r for r in rings if r]
            if rings:
                polygons.append(rings)

    elif gtype == "MultiPolygon":
        polys_raw = geometry.get("coordinates")
        if isinstance(polys_raw, (list, tuple)):
            for poly in polys_raw:
                if not isinstance(poly, (list, tuple)):
                    continue
                rings = [_ring_coords(r) for r in poly]
                rings = [r for r in rings if r]
                if rings:
                    polygons.append(rings)

    elif gtype == "GeometryCollection":
        for sub in geometry.get("geometries") or []:
            polygons.extend(_collect_polygons(sub))

    return polygons


def _feature_point(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    """
    Return a representative point for a feature geometry.
    For Point: the coordinate. For others: None (only points supported here).
    """
    if not isinstance(geometry, dict):
        return None

    if geometry.get("type") == "Point":
        try:
            return _validate_position(geometry.get("coordinates"))
        except ValueError:
            return None

    return None


def _collect_all_polygons(polygon_features: list[dict[str, Any]]) -> list[list[list[tuple[float, float]]]]:
    all_polygons: list[list[list[tuple[float, float]]]] = []
    for feature in polygon_features:
        all_polygons.extend(_collect_polygons(feature.get("geometry")))
    return all_polygons


def _walk_positions(coords: Any) -> list[list[float]]:
    positions: list[list[float]] = []
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and _is_number(coords[0])
        and _is_number(coords[1])
    ):
        positions.append([float(coords[0]), float(coords[1])])
        return positions
    if isinstance(coords, (list, tuple)):
        for item in coords:
            positions.extend(_walk_positions(item))
    return positions


def _build_vector_metadata(features: list[dict[str, Any]]) -> dict[str, Any]:
    xs: list[float] = []
    ys: list[float] = []
    geometry_types: dict[str, int] = {}

    for feature in features:
        geom = feature.get("geometry")
        if isinstance(geom, dict):
            gt = str(geom.get("type") or "Unknown")
            geometry_types[gt] = geometry_types.get(gt, 0) + 1
            for x, y in _walk_positions(geom.get("coordinates")):
                xs.append(x)
                ys.append(y)

    bounds = None
    if xs and ys:
        bounds = {
            "minx": min(xs),
            "miny": min(ys),
            "maxx": max(xs),
            "maxy": max(ys),
        }

    return {
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": bounds,
    }


# ------------------------------------------------------------------ #
# Capability: filter_points_in_polygon
# ------------------------------------------------------------------ #

@capability(
    name="filter_points_in_polygon",
    keywords=[
        "point in polygon",
        "within polygon",
        "inside polygon",
        "contained in",
        "spatial within",
        "points inside",
        "filter inside",
        "داخل پلیگان",
        "داخل محدوده",
        "درون چندضلعی",
        "نقاط داخل",
        "محدوده مجاز",
        "داخل ناحیه",
    ],
    description=(
        "Keep only point features that fall inside one or more polygon features "
        "(true ray-casting point-in-polygon, with hole support)."
    ),
    required_inputs=["points", "polygons"],
    optional_inputs=["predicate", "drop_outside", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "spatial_predicate",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": False,
        "planar_only": True,
        "routable": True,
        "module_name": "plugins.spatial_predicate",
    },
)
def filter_points_in_polygon(
    points: Any,
    polygons: Any,
    predicate: str = "within",
    drop_outside: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Filter point features by polygon containment.

    Args:
        points:
            Point features (VectorOut-like / FeatureCollection / Feature / list).
        polygons:
            Polygon features used as containment mask.
        predicate:
            "within"  -> keep points inside polygons
            "outside" -> keep points outside polygons
        drop_outside:
            If True (and predicate=within), points outside are removed.
            If False, all points are returned but tagged with __in_polygon__.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with matching point features. Each output feature gets
        an "__in_polygon__" boolean property.
    """
    if predicate not in {"within", "outside"}:
        raise ValueError("predicate must be 'within' or 'outside'.")

    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    point_features = _extract_features(points, label="points")
    polygon_features = _extract_features(polygons, label="polygons")

    all_polygons = _collect_all_polygons(polygon_features)

    output_features: list[dict[str, Any]] = []
    matched_count = 0
    skipped_non_point = 0

    for feature in point_features:
        pt = _feature_point(feature.get("geometry"))

        if pt is None:
            skipped_non_point += 1
            # Non-point geometry: cannot test containment here.
            in_polygon = False
        else:
            in_polygon = any(
                _point_in_polygon_rings(pt, rings)
                for rings in all_polygons
            )

        keep = in_polygon if predicate == "within" else (not in_polygon)

        if keep:
            matched_count += 1

        if keep or not drop_outside:
            out = dict(feature)
            props = dict(out.get("properties") or {})
            props["__in_polygon__"] = bool(in_polygon)
            out["properties"] = props
            if keep or not drop_outside:
                output_features.append(out)

    if drop_outside:
        output_features = [
            f for f in output_features
            if (
                f["properties"].get("__in_polygon__")
                if predicate == "within"
                else not f["properties"].get("__in_polygon__")
            )
        ]

    stats_metadata = _build_vector_metadata(output_features)
    user_metadata = metadata or {}

    output_metadata = {
        "source": "spatial_predicate",
        "loader": PLUGIN_ID,
        "operation": "filter_points_in_polygon",
        "predicate": predicate,
        "input_point_count": len(point_features),
        "polygon_count": len(all_polygons),
        "matched_count": matched_count,
        "output_feature_count": len(output_features),
        "skipped_non_point": skipped_non_point,
        "drop_outside": bool(drop_outside),
        "created_at": _utc_now_iso(),
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
    name="Spatial Predicate",
    description=(
        "True geometric spatial predicate filtering, including "
        "point-in-polygon containment with hole support."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
