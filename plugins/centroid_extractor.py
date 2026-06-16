"""
centroid_extractor.py

GeoChat SDK Plugin
==================

Plugin ID:
    centroid_extractor

Purpose:
    Extract centroid / center point features from GeoJSON-like vector geometries.

Capability:
    - extract_centroids:
        Vector geometries -> centroid Point features.

Engines:
    - auto:
        Use shapely if available, otherwise pure-python fallback.
    - shapely:
        Use shapely centroid.
    - python:
        Pure-python centroid approximation/calculation.

Supported by python engine:
    - Point
    - MultiPoint
    - LineString
    - MultiLineString
    - Polygon
    - MultiPolygon
    - GeometryCollection

Notes:
    For production-grade geometric robustness, install shapely:
        pip install shapely
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.exceptions import SDKDependencyError

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "centroid_extractor"

VALID_ENGINES = {"auto", "shapely", "python"}


def _load_centroid_config() -> dict[str, Any]:
    """
    Load config/plugins/centroid_extractor.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    """
    Return current UTC timestamp as ISO string.
    """
    return datetime.now(timezone.utc).isoformat()


def _validate_engine(engine: str) -> str:
    """
    Validate centroid extraction engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _to_float(value: Any, field_name: str = "value") -> float:
    """
    Convert value to float.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric.")

    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be numeric.") from exc


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return coordinate precision.
    """
    value = config.get("coordinate_precision", 8)

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


def _round_coord(value: float, precision: int | None) -> float:
    """
    Round coordinate if precision is configured.
    """
    if precision is None:
        return float(value)
    return round(float(value), precision)


def _configured_fields(config: dict[str, Any]) -> dict[str, bool]:
    """
    Return output metadata field flags.
    """
    fields = config.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("fields in centroid_extractor config must be a dict.")

    return {
        "add_source_geometry_type": bool(fields.get("add_source_geometry_type", True)),
        "add_source_feature_index": bool(fields.get("add_source_feature_index", True)),
        "add_centroid_status": bool(fields.get("add_centroid_status", True)),
        "add_engine_used": bool(fields.get("add_engine_used", True)),
    }


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


def _is_number(value: Any) -> bool:
    """
    Return True if value is int/float but not bool.
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


def _validate_position(position: Any) -> tuple[float, float]:
    """
    Validate and convert a coordinate position.
    """
    if not _is_position(position):
        raise ValueError(f"Invalid coordinate position: {position!r}")

    return float(position[0]), float(position[1])


def _iter_positions(coords: Any) -> list[tuple[float, float]]:
    """
    Recursively collect coordinate positions.
    """
    if _is_position(coords):
        return [_validate_position(coords)]

    if isinstance(coords, (list, tuple)):
        result: list[tuple[float, float]] = []
        for item in coords:
            result.extend(_iter_positions(item))
        return result

    return []


def _mean_point(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """
    Calculate arithmetic mean of points.
    """
    if not points:
        return None

    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)

    return sx / len(points), sy / len(points)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    """
    Euclidean distance.
    """
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _linestring_centroid(coords: Any) -> tuple[float, float] | None:
    """
    Length-weighted centroid of a LineString.

    If length is zero, falls back to mean of points.
    """
    if not isinstance(coords, (list, tuple)):
        return None

    points = [_validate_position(p) for p in coords if _is_position(p)]

    if not points:
        return None

    if len(points) == 1:
        return points[0]

    total_length = 0.0
    weighted_x = 0.0
    weighted_y = 0.0

    for a, b in zip(points[:-1], points[1:]):
        segment_length = _distance(a, b)
        if segment_length <= 0:
            continue

        mid_x = (a[0] + b[0]) / 2.0
        mid_y = (a[1] + b[1]) / 2.0

        weighted_x += mid_x * segment_length
        weighted_y += mid_y * segment_length
        total_length += segment_length

    if total_length <= 0:
        return _mean_point(points)

    return weighted_x / total_length, weighted_y / total_length


def _polygon_ring_centroid(ring: Any) -> tuple[float, float, float] | None:
    """
    Calculate centroid and signed area of a polygon ring.

    Returns:
        (cx, cy, signed_area)
    """
    if not isinstance(ring, (list, tuple)):
        return None

    points = [_validate_position(p) for p in ring if _is_position(p)]

    if len(points) < 3:
        return None

    # Ensure closed ring for formula.
    if points[0] != points[-1]:
        points = [*points, points[0]]

    cross_sum = 0.0
    cx_sum = 0.0
    cy_sum = 0.0

    for a, b in zip(points[:-1], points[1:]):
        cross = a[0] * b[1] - b[0] * a[1]
        cross_sum += cross
        cx_sum += (a[0] + b[0]) * cross
        cy_sum += (a[1] + b[1]) * cross

    signed_area = cross_sum / 2.0

    if abs(signed_area) < 1e-15:
        mean = _mean_point(points[:-1])
        if mean is None:
            return None
        return mean[0], mean[1], 0.0

    cx = cx_sum / (6.0 * signed_area)
    cy = cy_sum / (6.0 * signed_area)

    return cx, cy, signed_area


def _polygon_centroid(coords: Any) -> tuple[float, float, float] | None:
    """
    Calculate centroid of Polygon coordinates.

    Handles holes by signed area weighting.
    Returns:
        (cx, cy, area_abs)
    """
    if not isinstance(coords, (list, tuple)) or not coords:
        return None

    weighted_x = 0.0
    weighted_y = 0.0
    total_area = 0.0

    for ring in coords:
        item = _polygon_ring_centroid(ring)
        if item is None:
            continue

        cx, cy, signed_area = item

        weighted_x += cx * signed_area
        weighted_y += cy * signed_area
        total_area += signed_area

    if abs(total_area) < 1e-15:
        points = _iter_positions(coords)
        mean = _mean_point(points)
        if mean is None:
            return None
        return mean[0], mean[1], 0.0

    return weighted_x / total_area, weighted_y / total_area, abs(total_area)


def _python_centroid_geometry(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    """
    Calculate centroid using pure-python logic.
    """
    if geometry is None:
        return None

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    gtype = geometry.get("type")

    if not isinstance(gtype, str) or not gtype:
        raise ValueError("geometry.type must be a non-empty string.")

    if gtype == "Point":
        return _validate_position(geometry.get("coordinates"))

    if gtype == "MultiPoint":
        return _mean_point(_iter_positions(geometry.get("coordinates")))

    if gtype == "LineString":
        return _linestring_centroid(geometry.get("coordinates"))

    if gtype == "MultiLineString":
        lines = geometry.get("coordinates")
        if not isinstance(lines, (list, tuple)):
            return None

        weighted_x = 0.0
        weighted_y = 0.0
        total_length = 0.0
        fallback_points: list[tuple[float, float]] = []

        for line in lines:
            centroid = _linestring_centroid(line)
            points = [_validate_position(p) for p in line if _is_position(p)] if isinstance(line, (list, tuple)) else []
            fallback_points.extend(points)

            length = sum(_distance(a, b) for a, b in zip(points[:-1], points[1:]))
            if centroid is not None and length > 0:
                weighted_x += centroid[0] * length
                weighted_y += centroid[1] * length
                total_length += length

        if total_length > 0:
            return weighted_x / total_length, weighted_y / total_length

        return _mean_point(fallback_points)

    if gtype == "Polygon":
        item = _polygon_centroid(geometry.get("coordinates"))
        if item is None:
            return None
        return item[0], item[1]

    if gtype == "MultiPolygon":
        polygons = geometry.get("coordinates")
        if not isinstance(polygons, (list, tuple)):
            return None

        weighted_x = 0.0
        weighted_y = 0.0
        total_area = 0.0
        fallback_points: list[tuple[float, float]] = []

        for polygon in polygons:
            item = _polygon_centroid(polygon)
            fallback_points.extend(_iter_positions(polygon))

            if item is None:
                continue

            cx, cy, area = item
            if area > 0:
                weighted_x += cx * area
                weighted_y += cy * area
                total_area += area

        if total_area > 0:
            return weighted_x / total_area, weighted_y / total_area

        return _mean_point(fallback_points)

    if gtype == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            return None

        centroids: list[tuple[float, float]] = []

        for sub_geometry in geometries:
            centroid = _python_centroid_geometry(sub_geometry)
            if centroid is not None:
                centroids.append(centroid)

        return _mean_point(centroids)

    raise ValueError(f"Unsupported geometry type for centroid extraction: {gtype}")


def _get_shapely_tools():
    """
    Lazy import shapely tools.
    """
    try:
        from shapely.geometry import shape
    except ImportError as exc:
        raise SDKDependencyError(
            "centroid_extractor requires 'shapely' for this engine. "
            "Install it with: pip install shapely"
        ) from exc

    return shape


def _shapely_centroid_geometry(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    """
    Calculate centroid using shapely.
    """
    if geometry is None:
        return None

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    shape = _get_shapely_tools()

    try:
        geom = shape(geometry)
    except Exception as exc:
        raise ValueError(f"cannot build shapely geometry: {exc}") from exc

    if geom.is_empty:
        return None

    centroid = geom.centroid

    if centroid.is_empty:
        return None

    return float(centroid.x), float(centroid.y)


def _calculate_centroid(
    geometry: dict[str, Any] | None,
    engine: str,
) -> tuple[tuple[float, float] | None, str]:
    """
    Calculate centroid and return (centroid, engine_used).
    """
    engine = _validate_engine(engine)

    if engine == "python":
        return _python_centroid_geometry(geometry), "python"

    if engine == "shapely":
        return _shapely_centroid_geometry(geometry), "shapely"

    # auto
    try:
        return _shapely_centroid_geometry(geometry), "shapely"
    except SDKDependencyError:
        return _python_centroid_geometry(geometry), "python"


def _make_centroid_feature(
    *,
    centroid: tuple[float, float] | None,
    source_feature: dict[str, Any],
    feature_index: int,
    engine_used: str,
    precision: int | None,
    preserve_properties: bool,
    fields: dict[str, bool],
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Build output centroid feature.
    """
    source_geometry = source_feature.get("geometry")
    source_geometry_type = None

    if isinstance(source_geometry, dict):
        source_geometry_type = source_geometry.get("type")
    elif source_geometry is None:
        source_geometry_type = "Null"
    else:
        source_geometry_type = "Invalid"

    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}

    if fields.get("add_source_geometry_type", True):
        properties["_source_geometry_type"] = source_geometry_type

    if fields.get("add_source_feature_index", True):
        properties["_source_feature_index"] = feature_index

    if fields.get("add_centroid_status", True):
        properties["_centroid_status"] = status
        if reason:
            properties["_centroid_reason"] = reason

    if fields.get("add_engine_used", True):
        properties["_centroid_engine"] = engine_used

    if centroid is None:
        geometry = None
    else:
        geometry = {
            "type": "Point",
            "coordinates": [
                _round_coord(centroid[0], precision),
                _round_coord(centroid[1], precision),
            ],
        }

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": properties,
    }


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox for Point geometry.
    """
    if not geometry:
        return None

    if geometry.get("type") != "Point":
        return None

    coords = geometry.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return None

    try:
        x = float(coords[0])
        y = float(coords[1])
    except Exception:
        return None

    return [x, y, x, y]


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
            bbox = _geometry_bbox(geometry)
            if bbox is not None:
                bboxes.append(bbox)
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
    name="extract_centroids",
    keywords=[
        "centroid",
        "extract centroid",
        "geometry center",
        "center point",
        "polygon centroid",
        "feature center",
        "مرکز هندسی",
        "استخراج مرکز",
        "مرکز عارضه",
        "مرکز پلیگون",
        "نقطه مرکزی",
        "سنتراید",
    ],
    description="Extract centroid Point features from vector geometries.",
    required_inputs=["features"],
    optional_inputs=[
        "engine",
        "precision",
        "drop_failed",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "centroid",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_robust_geometry": True,
        "routable": True,
    },
)
def extract_centroids(
    features: Any,
    engine: str | None = None,
    precision: int | None = None,
    drop_failed: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Extract centroid point features from input vector geometries.

    Args:
        features:
            VectorOut, list[Feature], FeatureCollection dict or single Feature dict.
        engine:
            auto | shapely | python.
        precision:
            Coordinate precision. If None, config coordinate_precision is used.
        drop_failed:
            If True, features whose centroid cannot be calculated are dropped.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with centroid Point features.
    """
    config = _load_centroid_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
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

    preserve_properties = bool(config.get("preserve_properties", True))
    fields = _configured_fields(config)

    input_features, source_info = _extract_features(features)

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    success_count = 0
    failed_count = 0
    dropped_count = 0

    for idx, feature in enumerate(input_features):
        try:
            centroid, engine_used = _calculate_centroid(
                geometry=feature.get("geometry"),
                engine=final_engine,
            )
            engines_used.add(engine_used)

            if centroid is None:
                failed_count += 1
                if final_drop_failed:
                    dropped_count += 1
                    continue

                output_features.append(
                    _make_centroid_feature(
                        centroid=None,
                        source_feature=feature,
                        feature_index=idx,
                        engine_used=engine_used,
                        precision=final_precision,
                        preserve_properties=preserve_properties,
                        fields=fields,
                        status="failed",
                        reason="centroid could not be calculated",
                    )
                )
                continue

            success_count += 1
            output_features.append(
                _make_centroid_feature(
                    centroid=centroid,
                    source_feature=feature,
                    feature_index=idx,
                    engine_used=engine_used,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    fields=fields,
                    status="success",
                )
            )

        except Exception as exc:
            failed_count += 1
            engine_used = final_engine
            engines_used.add(engine_used)

            if final_drop_failed:
                dropped_count += 1
                continue

            output_features.append(
                _make_centroid_feature(
                    centroid=None,
                    source_feature=feature,
                    feature_index=idx,
                    engine_used=engine_used,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    fields=fields,
                    status="failed",
                    reason=str(exc),
                )
            )

    stats = _build_vector_metadata(output_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "centroid_extractor",
        "loader": PLUGIN_ID,
        "operation": "centroid",
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "coordinate_precision": final_precision,
        "drop_failed": final_drop_failed,
        "input_feature_count": len(input_features),
        "output_feature_count": len(output_features),
        "success_count": success_count,
        "failed_count": failed_count,
        "dropped_count": dropped_count,
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
    name="Centroid Extractor",
    description=(
        "Extracts centroid Point features from vector geometries. Uses shapely "
        "when available and includes a pure-python fallback."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
