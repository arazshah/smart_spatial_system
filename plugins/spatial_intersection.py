"""
spatial_intersection.py

GeoChat SDK Plugin
==================

Plugin ID:
    spatial_intersection

Purpose:
    Perform spatial intersection operations between GeoJSON-like vector features.

Capability:
    - intersect_features:
        Intersect source features with target features.

Modes:
    - filter:
        Return source features that intersect target features. Geometry remains
        the original source geometry.

    - pairwise:
        Return pairwise intersection geometries for all intersecting
        source-target pairs.

Engines:
    - auto:
        Use shapely if available, otherwise pure-python bbox fallback.
    - shapely:
        Exact geometric intersection using shapely.
    - python:
        Pure-python bbox-based fallback.

Important:
    The python engine is bbox-based and approximate. For production-grade
    spatial intersection, install shapely:
        pip install shapely
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "spatial_intersection"

VALID_ENGINES = {"auto", "shapely", "python"}
VALID_MODES = {"filter", "pairwise"}

EPSILON = 1e-12


def _load_intersection_config() -> dict[str, Any]:
    """
    Load config/plugins/spatial_intersection.yaml if available.
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
    Validate intersection engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _validate_mode(mode: str) -> str:
    """
    Validate intersection mode.
    """
    if not isinstance(mode, str) or not mode.strip():
        raise ValueError("mode must be a non-empty string.")

    mode = mode.strip().lower()

    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode '{mode}'. Valid modes: {sorted(VALID_MODES)}")

    return mode


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return configured numeric precision.
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
    Round numeric value if precision is configured.
    """
    if value is None:
        return None

    if precision is None:
        return float(value)

    return round(float(value), precision)


def _round_position(position: list[Any] | tuple[Any, ...], precision: int | None) -> list[Any]:
    """
    Round first two coordinate values.
    """
    if not _is_position(position):
        raise ValueError(f"Invalid coordinate position: {position!r}")

    extra = list(position[2:])

    return [
        _round_value(float(position[0]), precision),
        _round_value(float(position[1]), precision),
        *extra,
    ]


def _round_geometry_coordinates(obj: Any, precision: int | None) -> Any:
    """
    Recursively round coordinates.
    """
    if obj is None:
        return None

    if _is_position(obj):
        return _round_position(obj, precision)

    if isinstance(obj, list):
        return [_round_geometry_coordinates(item, precision) for item in obj]

    if isinstance(obj, tuple):
        return [_round_geometry_coordinates(item, precision) for item in obj]

    return obj


def _round_geometry(geometry: dict[str, Any] | None, precision: int | None) -> dict[str, Any] | None:
    """
    Round coordinates in GeoJSON geometry.
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
        "coordinates": _round_geometry_coordinates(geometry.get("coordinates"), precision),
    }


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    """
    Return output field names.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in spatial_intersection config must be a dict.")

    return {
        "intersects_field": str(fields.get("intersects_field", "_intersects")),
        "source_index_field": str(fields.get("source_index_field", "_source_index")),
        "target_index_field": str(fields.get("target_index_field", "_target_index")),
        "status_field": str(fields.get("status_field", "_intersection_status")),
        "engine_field": str(fields.get("engine_field", "_intersection_engine")),
        "mode_field": str(fields.get("mode_field", "_intersection_mode")),
        "area_field": str(fields.get("area_field", "_intersection_area")),
        "target_properties_field": str(fields.get("target_properties_field", "_target_properties")),
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


def _extract_features(input_data: Any, label: str = "features") -> tuple[list[dict[str, Any]], dict[str, Any]]:
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

    gtype = geometry.get("type")

    if gtype == "GeometryCollection":
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


def _bbox_intersection(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    """
    Return intersection bbox.
    """
    if not _bboxes_intersect(a, b):
        return None

    assert a is not None
    assert b is not None

    minx = max(a[0], b[0])
    miny = max(a[1], b[1])
    maxx = min(a[2], b[2])
    maxy = min(a[3], b[3])

    if minx > maxx + EPSILON or miny > maxy + EPSILON:
        return None

    return [minx, miny, maxx, maxy]


def _bbox_area(bbox: list[float] | None) -> float | None:
    """
    Area of bbox.
    """
    if not bbox:
        return None

    width = max(0.0, float(bbox[2]) - float(bbox[0]))
    height = max(0.0, float(bbox[3]) - float(bbox[1]))

    return width * height


def _bbox_to_geometry(bbox: list[float] | None, precision: int | None = None) -> dict[str, Any] | None:
    """
    Convert bbox intersection to GeoJSON geometry.

    Degenerate cases:
        - point bbox -> Point
        - line bbox  -> LineString
        - area bbox  -> Polygon
    """
    if bbox is None:
        return None

    minx, miny, maxx, maxy = bbox

    minx = float(minx)
    miny = float(miny)
    maxx = float(maxx)
    maxy = float(maxy)

    if abs(maxx - minx) <= EPSILON and abs(maxy - miny) <= EPSILON:
        return {
            "type": "Point",
            "coordinates": [
                _round_value(minx, precision),
                _round_value(miny, precision),
            ],
        }

    if abs(maxx - minx) <= EPSILON:
        return {
            "type": "LineString",
            "coordinates": [
                [_round_value(minx, precision), _round_value(miny, precision)],
                [_round_value(maxx, precision), _round_value(maxy, precision)],
            ],
        }

    if abs(maxy - miny) <= EPSILON:
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


def _python_intersection_geometry(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
    precision: int | None = None,
) -> tuple[bool, dict[str, Any] | None, float | None]:
    """
    Bbox-based pure-python intersection.

    Returns:
        (intersects, intersection_geometry, intersection_area)
    """
    source_bbox = _geometry_bbox(source_geometry)
    target_bbox = _geometry_bbox(target_geometry)

    intersection_bbox = _bbox_intersection(source_bbox, target_bbox)

    if intersection_bbox is None:
        return False, None, None

    intersection_geometry = _bbox_to_geometry(intersection_bbox, precision=precision)
    area = _bbox_area(intersection_bbox)

    return True, intersection_geometry, area


def _get_shapely_tools():
    """
    Lazy import shapely tools.
    """
    try:
        from shapely.geometry import shape, mapping
    except ImportError as exc:
        raise SDKDependencyError(
            "spatial_intersection requires 'shapely' for this engine. "
            "Install it with: pip install shapely"
        ) from exc

    return shape, mapping


def _shapely_intersection_geometry(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
    precision: int | None = None,
) -> tuple[bool, dict[str, Any] | None, float | None]:
    """
    Exact intersection using shapely.
    """
    if source_geometry is None or target_geometry is None:
        return False, None, None

    if not isinstance(source_geometry, dict) or not isinstance(target_geometry, dict):
        raise ValueError("source_geometry and target_geometry must be dict/object or null.")

    shape, mapping = _get_shapely_tools()

    try:
        source_geom = shape(source_geometry)
        target_geom = shape(target_geometry)
    except Exception as exc:
        raise ValueError(f"cannot build shapely geometry: {exc}") from exc

    if source_geom.is_empty or target_geom.is_empty:
        return False, None, None

    if not source_geom.intersects(target_geom):
        return False, None, None

    intersection = source_geom.intersection(target_geom)

    if intersection.is_empty:
        return False, None, None

    geometry = dict(mapping(intersection))
    geometry = _round_geometry(geometry, precision)

    area = float(getattr(intersection, "area", 0.0) or 0.0)

    return True, geometry, area


def _calculate_intersection(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
    engine: str,
    precision: int | None = None,
) -> tuple[bool, dict[str, Any] | None, float | None, str]:
    """
    Calculate intersection.

    Returns:
        (intersects, intersection_geometry, intersection_area, engine_used)
    """
    engine = _validate_engine(engine)

    if engine == "python":
        intersects, geometry, area = _python_intersection_geometry(
            source_geometry,
            target_geometry,
            precision=precision,
        )
        return intersects, geometry, area, "python"

    if engine == "shapely":
        intersects, geometry, area = _shapely_intersection_geometry(
            source_geometry,
            target_geometry,
            precision=precision,
        )
        return intersects, geometry, area, "shapely"

    try:
        intersects, geometry, area = _shapely_intersection_geometry(
            source_geometry,
            target_geometry,
            precision=precision,
        )
        return intersects, geometry, area, "shapely"
    except SDKDependencyError:
        intersects, geometry, area = _python_intersection_geometry(
            source_geometry,
            target_geometry,
            precision=precision,
        )
        return intersects, geometry, area, "python"


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


def _make_output_feature(
    *,
    source_feature: dict[str, Any],
    source_index: int,
    target_feature: dict[str, Any] | None,
    target_index: int | None,
    output_geometry: dict[str, Any] | None,
    intersects: bool,
    intersection_area: float | None,
    engine_used: str,
    mode: str,
    fields: dict[str, str],
    precision: int | None,
    preserve_properties: bool,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Build output feature with intersection metadata.
    """
    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}

    properties[fields["intersects_field"]] = bool(intersects)
    properties[fields["source_index_field"]] = source_index
    properties[fields["target_index_field"]] = target_index
    properties[fields["status_field"]] = status
    properties[fields["engine_field"]] = engine_used
    properties[fields["mode_field"]] = mode
    properties[fields["area_field"]] = _round_value(intersection_area, precision)

    if target_feature is not None:
        properties[fields["target_properties_field"]] = deepcopy(target_feature.get("properties") or {})

    if reason:
        properties["_intersection_reason"] = reason

    return {
        "type": "Feature",
        "geometry": output_geometry,
        "properties": properties,
    }


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
    name="intersect_features",
    keywords=[
        "intersection",
        "intersect",
        "spatial intersection",
        "overlay intersection",
        "clip by layer",
        "find overlapping features",
        "overlap",
        "تقاطع مکانی",
        "اشتراک مکانی",
        "همپوشانی",
        "برش لایه",
        "عارضه‌های متقاطع",
        "تقاطع دو لایه",
    ],
    description="Intersect source vector features with target vector features.",
    required_inputs=["source_features", "target_features"],
    optional_inputs=[
        "mode",
        "engine",
        "precision",
        "drop_non_intersecting",
        "drop_failed",
        "source_crs",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "intersection",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_exact_geometry": True,
        "python_engine": "bbox_fallback",
        "routable": True,
    },
)
def intersect_features(
    source_features: Any,
    target_features: Any,
    mode: str | None = None,
    engine: str | None = None,
    precision: int | None = None,
    drop_non_intersecting: bool | None = None,
    drop_failed: bool | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Intersect source features with target features.

    Args:
        source_features:
            Source VectorOut, FeatureCollection, Feature, or list[Feature].
        target_features:
            Target VectorOut, FeatureCollection, Feature, or list[Feature].
        mode:
            filter | pairwise.
        engine:
            auto | shapely | python.
        precision:
            Rounding precision for output coordinates and area field.
        drop_non_intersecting:
            In filter mode, controls whether non-intersecting source features are returned.
            In pairwise mode, non-intersecting pairs are normally not emitted.
        drop_failed:
            If True, failed items are dropped.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with filtered source features or intersection geometries.
    """
    config = _load_intersection_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_mode = _validate_mode(
        str(pick_first(mode, config.get("default_mode"), default="filter"))
    )

    final_precision = _configured_precision(config) if precision is None else precision
    if final_precision is not None:
        if isinstance(final_precision, bool):
            raise ValueError("precision must be an integer or None.")
        final_precision = int(final_precision)
        if final_precision < 0 or final_precision > 15:
            raise ValueError("precision must be between 0 and 15.")

    final_drop_non_intersecting = bool(
        pick_first(drop_non_intersecting, config.get("drop_non_intersecting"), default=True)
    )

    final_drop_failed = bool(
        pick_first(drop_failed, config.get("drop_failed"), default=False)
    )

    final_source_crs = pick_first(source_crs, config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    preserve_properties = bool(config.get("preserve_properties", True))
    fields = _configured_fields(config)

    source_items, source_info = _extract_features(source_features, label="source")
    target_items, target_info = _extract_features(target_features, label="target")

    if not target_items:
        raise ValueError("target_features must contain at least one feature.")

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    pair_count = 0
    intersecting_pair_count = 0
    non_intersecting_pair_count = 0
    success_count = 0
    failed_count = 0
    dropped_count = 0

    if final_mode == "pairwise":
        for source_index, source_feature in enumerate(source_items):
            for target_index, target_feature in enumerate(target_items):
                pair_count += 1

                try:
                    intersects, intersection_geometry, intersection_area, engine_used = _calculate_intersection(
                        source_geometry=source_feature.get("geometry"),
                        target_geometry=target_feature.get("geometry"),
                        engine=final_engine,
                        precision=final_precision,
                    )
                    engines_used.add(engine_used)

                    if not intersects:
                        non_intersecting_pair_count += 1

                        # In pairwise mode, non-intersecting pairs are emitted only
                        # if explicitly requested.
                        if final_drop_non_intersecting:
                            dropped_count += 1
                            continue

                        output_features.append(
                            _make_output_feature(
                                source_feature=source_feature,
                                source_index=source_index,
                                target_feature=target_feature,
                                target_index=target_index,
                                output_geometry=None,
                                intersects=False,
                                intersection_area=None,
                                engine_used=engine_used,
                                mode=final_mode,
                                fields=fields,
                                precision=final_precision,
                                preserve_properties=preserve_properties,
                                status="no_intersection",
                                reason="features do not intersect",
                            )
                        )
                        continue

                    intersecting_pair_count += 1
                    success_count += 1

                    output_features.append(
                        _make_output_feature(
                            source_feature=source_feature,
                            source_index=source_index,
                            target_feature=target_feature,
                            target_index=target_index,
                            output_geometry=intersection_geometry,
                            intersects=True,
                            intersection_area=intersection_area,
                            engine_used=engine_used,
                            mode=final_mode,
                            fields=fields,
                            precision=final_precision,
                            preserve_properties=preserve_properties,
                            status="success",
                        )
                    )

                except Exception as exc:
                    failed_count += 1
                    engines_used.add(final_engine)

                    if final_drop_failed:
                        dropped_count += 1
                        continue

                    output_features.append(
                        _make_output_feature(
                            source_feature=source_feature,
                            source_index=source_index,
                            target_feature=target_feature,
                            target_index=target_index,
                            output_geometry=None,
                            intersects=False,
                            intersection_area=None,
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
            best_target_index: int | None = None
            best_target_feature: dict[str, Any] | None = None
            best_area: float | None = None
            best_engine_used = final_engine
            best_error: str | None = None

            source_intersects = False

            for target_index, target_feature in enumerate(target_items):
                pair_count += 1

                try:
                    intersects, _intersection_geometry, intersection_area, engine_used = _calculate_intersection(
                        source_geometry=source_feature.get("geometry"),
                        target_geometry=target_feature.get("geometry"),
                        engine=final_engine,
                        precision=final_precision,
                    )
                    engines_used.add(engine_used)
                    best_engine_used = engine_used

                    if not intersects:
                        non_intersecting_pair_count += 1
                        continue

                    intersecting_pair_count += 1
                    source_intersects = True

                    if best_target_index is None:
                        best_target_index = target_index
                        best_target_feature = target_feature
                        best_area = intersection_area
                    else:
                        old_area = best_area if best_area is not None else -1.0
                        new_area = intersection_area if intersection_area is not None else -1.0
                        if new_area > old_area:
                            best_target_index = target_index
                            best_target_feature = target_feature
                            best_area = intersection_area

                except Exception as exc:
                    best_error = str(exc)
                    engines_used.add(final_engine)

            if source_intersects:
                success_count += 1
                output_features.append(
                    _make_output_feature(
                        source_feature=source_feature,
                        source_index=source_index,
                        target_feature=best_target_feature,
                        target_index=best_target_index,
                        output_geometry=source_feature.get("geometry"),
                        intersects=True,
                        intersection_area=best_area,
                        engine_used=best_engine_used,
                        mode=final_mode,
                        fields=fields,
                        precision=final_precision,
                        preserve_properties=preserve_properties,
                        status="success",
                    )
                )
                continue

            failed_count += 1

            if final_drop_non_intersecting or final_drop_failed:
                dropped_count += 1
                continue

            output_features.append(
                _make_output_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=None,
                    target_index=None,
                    output_geometry=source_feature.get("geometry"),
                    intersects=False,
                    intersection_area=None,
                    engine_used=best_engine_used,
                    mode=final_mode,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    status="no_intersection",
                    reason=best_error or "source feature does not intersect any target feature",
                )
            )

    stats = _build_vector_metadata(output_features)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Spatial intersection is being evaluated on a geographic CRS. "
            "For metric overlay workflows, consider reprojecting first."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "spatial_intersection",
        "loader": PLUGIN_ID,
        "operation": "intersection",
        "mode": final_mode,
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "coordinate_precision": final_precision,
        "drop_non_intersecting": final_drop_non_intersecting,
        "drop_failed": final_drop_failed,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "source_feature_count": len(source_items),
        "target_feature_count": len(target_items),
        "pair_count": pair_count,
        "intersecting_pair_count": intersecting_pair_count,
        "non_intersecting_pair_count": non_intersecting_pair_count,
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
    name="Spatial Intersection",
    description=(
        "Performs spatial intersection between vector features. Uses shapely for exact "
        "geometry when available and includes a bbox-based pure-python fallback."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
