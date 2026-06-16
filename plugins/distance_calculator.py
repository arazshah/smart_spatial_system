"""
distance_calculator.py

GeoChat SDK Plugin
==================

Plugin ID:
    distance_calculator

Purpose:
    Calculate planar distances between GeoJSON-like vector geometries.

Capability:
    - calculate_distances:
        Calculates nearest or pairwise distances between source and target features.

Engines:
    - auto:
        Use shapely if available, otherwise pure-python fallback.
    - shapely:
        Robust geometry distance through shapely.
    - python:
        Pure-python planar fallback supporting common GeoJSON geometry types.

Modes:
    - nearest:
        For each source feature, find nearest target feature.
    - pairwise:
        Calculate distance for every source-target pair.

Important:
    Calculations are planar, not geodesic. Reproject geographic data first
    using crs_transformer for reliable meter-based distances.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "distance_calculator"

VALID_ENGINES = {"auto", "shapely", "python"}
VALID_MODES = {"nearest", "pairwise"}

EPSILON = 1e-12


def _load_distance_config() -> dict[str, Any]:
    """
    Load config/plugins/distance_calculator.yaml if available.
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
    Validate distance calculation engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _validate_mode(mode: str) -> str:
    """
    Validate distance calculation mode.
    """
    if not isinstance(mode, str) or not mode.strip():
        raise ValueError("mode must be a non-empty string.")

    mode = mode.strip().lower()

    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode '{mode}'. Valid modes: {sorted(VALID_MODES)}")

    return mode


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return output precision.
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


def _round_value(value: float | None, precision: int | None) -> float | None:
    """
    Round distance value.
    """
    if value is None:
        return None

    if precision is None:
        return float(value)

    return round(float(value), precision)


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    """
    Return output field names.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in distance_calculator config must be a dict.")

    return {
        "distance_field": str(fields.get("distance_field", "_distance")),
        "source_index_field": str(fields.get("source_index_field", "_source_index")),
        "target_index_field": str(fields.get("target_index_field", "_target_index")),
        "status_field": str(fields.get("status_field", "_distance_status")),
        "engine_field": str(fields.get("engine_field", "_distance_engine")),
        "mode_field": str(fields.get("mode_field", "_distance_mode")),
        "target_properties_field": str(fields.get("target_properties_field", "_target_properties")),
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


def _extract_features(input_data: Any, label: str = "features") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract features from VectorOut, FeatureCollection, Feature, or list[Feature].
    """
    source_info: dict[str, Any] = {}

    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw_features = getattr(input_data, "features")
        source_info[f"{label}_input_type"] = type(input_data).__name__

        source_metadata = getattr(input_data, "metadata", None)
        if isinstance(source_metadata, dict):
            source_info[f"{label}_input_metadata"] = source_metadata

    elif isinstance(input_data, dict):
        geojson_type = input_data.get("type")
        source_info[f"{label}_input_geojson_type"] = geojson_type

        if geojson_type == "FeatureCollection":
            raw_features = input_data.get("features", [])
            if not isinstance(raw_features, list):
                raise ValueError(f"{label}.features must be a list.")
        elif geojson_type == "Feature":
            raw_features = [input_data]
        else:
            raise ValueError(f"{label} dict must be GeoJSON FeatureCollection or Feature.")

    elif isinstance(input_data, list):
        raw_features = input_data
        source_info[f"{label}_input_geojson_type"] = "FeatureList"

    else:
        raise ValueError(f"{label} must be VectorOut, list, FeatureCollection dict or Feature dict.")

    if not isinstance(raw_features, list):
        raise ValueError(f"Extracted {label} must be a list.")

    features = [_normalize_feature(item, idx) for idx, item in enumerate(raw_features)]

    return features, source_info


def _is_number(value: Any) -> bool:
    """
    Return True if numeric and not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_position(value: Any) -> bool:
    """
    Return True if value is GeoJSON coordinate position.
    """
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _validate_position(position: Any) -> tuple[float, float]:
    """
    Validate and convert coordinate position.
    """
    if not _is_position(position):
        raise ValueError(f"Invalid coordinate position: {position!r}")

    return float(position[0]), float(position[1])


def _distance_points(a: tuple[float, float], b: tuple[float, float]) -> float:
    """
    Euclidean distance between points.
    """
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _point_segment_distance(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """
    Distance from point to segment.
    """
    px, py = point
    ax, ay = a
    bx, by = b

    dx = bx - ax
    dy = by - ay

    length2 = dx * dx + dy * dy

    if length2 <= EPSILON:
        return _distance_points(point, a)

    t = ((px - ax) * dx + (py - ay) * dy) / length2
    t = max(0.0, min(1.0, t))

    projection = (ax + t * dx, ay + t * dy)

    return _distance_points(point, projection)


def _orientation(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    """
    Orientation/cross-product value.
    """
    return (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])


def _on_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    """
    Return True if b lies on segment ac.
    """
    return (
        min(a[0], c[0]) - EPSILON <= b[0] <= max(a[0], c[0]) + EPSILON
        and min(a[1], c[1]) - EPSILON <= b[1] <= max(a[1], c[1]) + EPSILON
    )


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    """
    Check if two segments intersect.
    """
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)

    if o1 * o2 < 0 and o3 * o4 < 0:
        return True

    if abs(o1) <= EPSILON and _on_segment(a1, b1, a2):
        return True

    if abs(o2) <= EPSILON and _on_segment(a1, b2, a2):
        return True

    if abs(o3) <= EPSILON and _on_segment(b1, a1, b2):
        return True

    if abs(o4) <= EPSILON and _on_segment(b1, a2, b2):
        return True

    return False


def _segment_segment_distance(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> float:
    """
    Distance between two line segments.
    """
    if _segments_intersect(a1, a2, b1, b2):
        return 0.0

    return min(
        _point_segment_distance(a1, b1, b2),
        _point_segment_distance(a2, b1, b2),
        _point_segment_distance(b1, a1, a2),
        _point_segment_distance(b2, a1, a2),
    )


def _ensure_closed_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """
    Ensure ring is closed.
    """
    if not points:
        return points

    if points[0] == points[-1]:
        return points

    return [*points, points[0]]


def _coords_to_points(coords: Any) -> list[tuple[float, float]]:
    """
    Convert coordinate list to points.
    """
    if not isinstance(coords, (list, tuple)):
        return []

    return [_validate_position(item) for item in coords if _is_position(item)]


def _coords_to_segments(coords: Any, closed: bool = False) -> tuple[list[tuple[float, float]], list[tuple[tuple[float, float], tuple[float, float]]]]:
    """
    Convert coordinate sequence to points and segments.
    """
    points = _coords_to_points(coords)

    if closed:
        points = _ensure_closed_ring(points)

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

    if len(points) >= 2:
        segments = list(zip(points[:-1], points[1:]))

    return points, segments


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    """
    Ray casting point-in-ring test.
    """
    ring = _ensure_closed_ring(ring)

    if len(ring) < 4:
        return False

    x, y = point
    inside = False

    for a, b in zip(ring[:-1], ring[1:]):
        xi, yi = a
        xj, yj = b

        if _point_segment_distance(point, a, b) <= EPSILON:
            return True

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
    """
    Check if point is inside polygon rings, respecting holes.
    """
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


def _collect_geometry(
    geometry: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Collect points, segments, and polygon rings from GeoJSON geometry.
    """
    collected = {
        "points": [],
        "segments": [],
        "polygons": [],
    }

    if geometry is None:
        return collected

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    gtype = geometry.get("type")

    if not isinstance(gtype, str) or not gtype:
        raise ValueError("geometry.type must be a non-empty string.")

    if gtype == "Point":
        point = _validate_position(geometry.get("coordinates"))
        collected["points"].append(point)
        return collected

    if gtype == "MultiPoint":
        points = _coords_to_points(geometry.get("coordinates"))
        collected["points"].extend(points)
        return collected

    if gtype == "LineString":
        points, segments = _coords_to_segments(geometry.get("coordinates"), closed=False)
        collected["points"].extend(points)
        collected["segments"].extend(segments)
        return collected

    if gtype == "MultiLineString":
        lines = geometry.get("coordinates")
        if not isinstance(lines, (list, tuple)):
            return collected

        for line in lines:
            points, segments = _coords_to_segments(line, closed=False)
            collected["points"].extend(points)
            collected["segments"].extend(segments)

        return collected

    if gtype == "Polygon":
        rings = geometry.get("coordinates")
        if not isinstance(rings, (list, tuple)):
            return collected

        polygon_rings: list[list[tuple[float, float]]] = []

        for ring in rings:
            points, segments = _coords_to_segments(ring, closed=True)
            if points:
                polygon_rings.append(points)
                collected["points"].extend(points)
                collected["segments"].extend(segments)

        if polygon_rings:
            collected["polygons"].append(polygon_rings)

        return collected

    if gtype == "MultiPolygon":
        polygons = geometry.get("coordinates")
        if not isinstance(polygons, (list, tuple)):
            return collected

        for polygon in polygons:
            sub = _collect_geometry({"type": "Polygon", "coordinates": polygon})
            collected["points"].extend(sub["points"])
            collected["segments"].extend(sub["segments"])
            collected["polygons"].extend(sub["polygons"])

        return collected

    if gtype == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            return collected

        for sub_geometry in geometries:
            sub = _collect_geometry(sub_geometry)
            collected["points"].extend(sub["points"])
            collected["segments"].extend(sub["segments"])
            collected["polygons"].extend(sub["polygons"])

        return collected

    raise ValueError(f"Unsupported geometry type for distance calculation: {gtype}")


def _python_distance_geometry(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
) -> float | None:
    """
    Pure-python planar distance between two GeoJSON geometries.
    """
    if source_geometry is None or target_geometry is None:
        return None

    source = _collect_geometry(source_geometry)
    target = _collect_geometry(target_geometry)

    source_points = source["points"]
    target_points = target["points"]
    source_segments = source["segments"]
    target_segments = target["segments"]
    source_polygons = source["polygons"]
    target_polygons = target["polygons"]

    if not source_points and not source_segments and not source_polygons:
        return None

    if not target_points and not target_segments and not target_polygons:
        return None

    # Point inside polygon means zero distance.
    for point in source_points:
        for polygon in target_polygons:
            if _point_in_polygon_rings(point, polygon):
                return 0.0

    for point in target_points:
        for polygon in source_polygons:
            if _point_in_polygon_rings(point, polygon):
                return 0.0

    # Segment intersection means zero distance.
    for a1, a2 in source_segments:
        for b1, b2 in target_segments:
            if _segments_intersect(a1, a2, b1, b2):
                return 0.0

    distances: list[float] = []

    # Point-point.
    for a in source_points:
        for b in target_points:
            distances.append(_distance_points(a, b))

    # Point-segment.
    for point in source_points:
        for a, b in target_segments:
            distances.append(_point_segment_distance(point, a, b))

    for point in target_points:
        for a, b in source_segments:
            distances.append(_point_segment_distance(point, a, b))

    # Segment-segment.
    for a1, a2 in source_segments:
        for b1, b2 in target_segments:
            distances.append(_segment_segment_distance(a1, a2, b1, b2))

    if not distances:
        return None

    return min(distances)


def _get_shapely_tools():
    """
    Lazy import shapely tools.
    """
    try:
        from shapely.geometry import shape
    except ImportError as exc:
        raise SDKDependencyError(
            "distance_calculator requires 'shapely' for this engine. "
            "Install it with: pip install shapely"
        ) from exc

    return shape


def _shapely_distance_geometry(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
) -> float | None:
    """
    Shapely-based planar distance.
    """
    if source_geometry is None or target_geometry is None:
        return None

    if not isinstance(source_geometry, dict) or not isinstance(target_geometry, dict):
        raise ValueError("source_geometry and target_geometry must be dict/object or null.")

    shape = _get_shapely_tools()

    try:
        source_geom = shape(source_geometry)
        target_geom = shape(target_geometry)
    except Exception as exc:
        raise ValueError(f"cannot build shapely geometry: {exc}") from exc

    if source_geom.is_empty or target_geom.is_empty:
        return None

    return float(source_geom.distance(target_geom))


def _calculate_distance(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
    engine: str,
) -> tuple[float | None, str]:
    """
    Calculate distance and return (distance, engine_used).
    """
    engine = _validate_engine(engine)

    if engine == "python":
        return _python_distance_geometry(source_geometry, target_geometry), "python"

    if engine == "shapely":
        return _shapely_distance_geometry(source_geometry, target_geometry), "shapely"

    try:
        return _shapely_distance_geometry(source_geometry, target_geometry), "shapely"
    except SDKDependencyError:
        return _python_distance_geometry(source_geometry, target_geometry), "python"


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


def _make_distance_feature(
    *,
    source_feature: dict[str, Any],
    source_index: int,
    target_feature: dict[str, Any] | None,
    target_index: int | None,
    distance: float | None,
    engine_used: str,
    mode: str,
    fields: dict[str, str],
    precision: int | None,
    preserve_properties: bool,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Build output feature with distance fields.
    """
    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}

    properties[fields["distance_field"]] = _round_value(distance, precision)
    properties[fields["source_index_field"]] = source_index
    properties[fields["target_index_field"]] = target_index
    properties[fields["status_field"]] = status
    properties[fields["engine_field"]] = engine_used
    properties[fields["mode_field"]] = mode

    if target_feature is not None:
        properties[fields["target_properties_field"]] = deepcopy(target_feature.get("properties") or {})

    if reason:
        properties["_distance_reason"] = reason

    return {
        "type": "Feature",
        "geometry": source_feature.get("geometry"),
        "properties": properties,
    }


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox from GeoJSON geometry.
    """
    if not geometry:
        return None

    collected = _collect_geometry(geometry)
    points = collected["points"]

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
    name="calculate_distances",
    keywords=[
        "distance",
        "calculate distance",
        "nearest distance",
        "pairwise distance",
        "distance between geometries",
        "closest feature",
        "nearest feature",
        "فاصله",
        "محاسبه فاصله",
        "نزدیکترین",
        "نزدیک‌ترین",
        "فاصله تا عارضه",
        "فاصله بین عوارض",
    ],
    description="Calculate planar nearest or pairwise distances between vector geometries.",
    required_inputs=["source_features", "target_features"],
    optional_inputs=[
        "mode",
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
        "operation": "distance",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_robust_geometry": True,
        "planar_only": True,
        "routable": True,
    },
)
def calculate_distances(
    source_features: Any,
    target_features: Any,
    mode: str | None = None,
    engine: str | None = None,
    precision: int | None = None,
    drop_failed: bool | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Calculate planar distances between source and target vector features.

    Args:
        source_features:
            Source VectorOut, FeatureCollection, Feature, or list[Feature].
        target_features:
            Target VectorOut, FeatureCollection, Feature, or list[Feature].
        mode:
            nearest | pairwise.
        engine:
            auto | shapely | python.
        precision:
            Rounding precision for distance.
        drop_failed:
            If True, failed source/pair results are removed.
        source_crs:
            CRS hint. Used only for warning metadata.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with source geometries and distance fields.
    """
    config = _load_distance_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_mode = _validate_mode(
        str(pick_first(mode, config.get("default_mode"), default="nearest"))
    )

    final_precision = _configured_precision(config) if precision is None else precision
    if final_precision is not None:
        if isinstance(final_precision, bool):
            raise ValueError("precision must be an integer or None.")
        final_precision = int(final_precision)
        if final_precision < 0 or final_precision > 15:
            raise ValueError("precision must be between 0 and 15.")

    final_drop_failed = bool(
        pick_first(drop_failed, config.get("drop_failed"), default=False)
    )

    final_source_crs = pick_first(source_crs, config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", True))

    preserve_properties = bool(config.get("preserve_properties", True))
    fields = _configured_fields(config)

    source_items, source_info = _extract_features(source_features, label="source")
    target_items, target_info = _extract_features(target_features, label="target")

    if not target_items:
        raise ValueError("target_features must contain at least one feature.")

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    success_count = 0
    failed_count = 0
    dropped_count = 0
    pair_count = 0

    if final_mode == "pairwise":
        for source_index, source_feature in enumerate(source_items):
            for target_index, target_feature in enumerate(target_items):
                pair_count += 1

                try:
                    distance, engine_used = _calculate_distance(
                        source_geometry=source_feature.get("geometry"),
                        target_geometry=target_feature.get("geometry"),
                        engine=final_engine,
                    )
                    engines_used.add(engine_used)

                    status = "success" if distance is not None else "failed"

                    if distance is None:
                        failed_count += 1
                        if final_drop_failed:
                            dropped_count += 1
                            continue
                    else:
                        success_count += 1

                    output_features.append(
                        _make_distance_feature(
                            source_feature=source_feature,
                            source_index=source_index,
                            target_feature=target_feature,
                            target_index=target_index,
                            distance=distance,
                            engine_used=engine_used,
                            mode=final_mode,
                            fields=fields,
                            precision=final_precision,
                            preserve_properties=preserve_properties,
                            status=status,
                            reason=None if distance is not None else "distance could not be calculated",
                        )
                    )

                except Exception as exc:
                    failed_count += 1
                    engines_used.add(final_engine)

                    if final_drop_failed:
                        dropped_count += 1
                        continue

                    output_features.append(
                        _make_distance_feature(
                            source_feature=source_feature,
                            source_index=source_index,
                            target_feature=target_feature,
                            target_index=target_index,
                            distance=None,
                            engine_used=final_engine,
                            mode=final_mode,
                            fields=fields,
                            precision=final_precision,
                            preserve_properties=preserve_properties,
                            status="failed",
                            reason=str(exc),
                        )
                    )

    else:
        for source_index, source_feature in enumerate(source_items):
            best_distance: float | None = None
            best_target_index: int | None = None
            best_target_feature: dict[str, Any] | None = None
            best_engine_used: str = final_engine
            best_error: str | None = None

            for target_index, target_feature in enumerate(target_items):
                pair_count += 1

                try:
                    distance, engine_used = _calculate_distance(
                        source_geometry=source_feature.get("geometry"),
                        target_geometry=target_feature.get("geometry"),
                        engine=final_engine,
                    )
                    engines_used.add(engine_used)
                    best_engine_used = engine_used

                    if distance is None:
                        continue

                    if best_distance is None or distance < best_distance:
                        best_distance = distance
                        best_target_index = target_index
                        best_target_feature = target_feature

                except Exception as exc:
                    best_error = str(exc)
                    engines_used.add(final_engine)

            if best_distance is None:
                failed_count += 1

                if final_drop_failed:
                    dropped_count += 1
                    continue

                output_features.append(
                    _make_distance_feature(
                        source_feature=source_feature,
                        source_index=source_index,
                        target_feature=best_target_feature,
                        target_index=best_target_index,
                        distance=None,
                        engine_used=best_engine_used,
                        mode=final_mode,
                        fields=fields,
                        precision=final_precision,
                        preserve_properties=preserve_properties,
                        status="failed",
                        reason=best_error or "nearest distance could not be calculated",
                    )
                )
                continue

            success_count += 1

            output_features.append(
                _make_distance_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=best_target_feature,
                    target_index=best_target_index,
                    distance=best_distance,
                    engine_used=best_engine_used,
                    mode=final_mode,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    status="success",
                )
            )

    stats = _build_vector_metadata(output_features)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Planar distance calculation is being performed on a geographic CRS. "
            "Reproject to a projected CRS for reliable physical distance values."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "distance_calculator",
        "loader": PLUGIN_ID,
        "operation": "distance",
        "mode": final_mode,
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "coordinate_precision": final_precision,
        "drop_failed": final_drop_failed,
        "source_crs": final_source_crs,
        "planar_only": True,
        "warning": geographic_warning,
        "source_feature_count": len(source_items),
        "target_feature_count": len(target_items),
        "pair_count": pair_count,
        "output_feature_count": len(output_features),
        "success_count": success_count,
        "failed_count": failed_count,
        "dropped_count": dropped_count,
        "created_at": _utc_now_iso(),
        **source_info,
        **target_info,
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
    name="Distance Calculator",
    description=(
        "Calculates planar nearest or pairwise distances between vector geometries. "
        "Uses shapely when available and includes a pure-python fallback."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
