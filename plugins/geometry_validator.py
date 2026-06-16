"""
geometry_validator.py

GeoChat SDK Plugin
==================

Plugin ID:
    geometry_validator

Purpose:
    Validate and optionally repair GeoJSON-like vector geometries.

Capabilities:
    - validate_geometries:
        Check each feature geometry, flag validity, and return VectorOut.

    - repair_geometries:
        Validate and attempt to repair invalid geometries (requires shapely),
        and return VectorOut.

Design decisions:
    - Invalid geometries are NOT dropped by default. They are flagged with
      _valid=false and _validity_reason in properties.
    - Repair is OFF by default. It only runs when explicitly requested.

Engines:
    - auto:
        Use shapely if available; otherwise pure-python structural checks.
    - shapely:
        Full validity check (self-intersection, ring closure, etc).
    - python:
        Structural checks only (types, coordinate counts, numeric coords,
        ring closure for polygons).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.exceptions import SDKDependencyError

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "geometry_validator"

VALID_ENGINES = {"auto", "shapely", "python"}

DEFAULT_ALLOWED_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}

DEFAULT_MIN_COORDINATES = {
    "LineString": 2,
    "Polygon": 4,
    "MultiPoint": 1,
    "MultiLineString": 2,
    "MultiPolygon": 4,
}


def _load_validator_config() -> dict[str, Any]:
    """
    Load config/plugins/geometry_validator.yaml if available.
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
    Validate engine name.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _configured_allowed_geometry_types(config: dict[str, Any]) -> set[str]:
    """
    Return allowed geometry types.
    """
    values = config.get("allowed_geometry_types")
    if not values:
        return set(DEFAULT_ALLOWED_GEOMETRY_TYPES)

    if not isinstance(values, list):
        raise ValueError("allowed_geometry_types must be a list.")

    return {str(item) for item in values}


def _configured_min_coordinates(config: dict[str, Any]) -> dict[str, int]:
    """
    Return min coordinate rules.
    """
    values = config.get("min_coordinates")
    if not values:
        return dict(DEFAULT_MIN_COORDINATES)

    if not isinstance(values, dict):
        raise ValueError("min_coordinates must be a dict.")

    result: dict[str, int] = {}
    for key, val in values.items():
        try:
            result[str(key)] = int(val)
        except Exception as exc:
            raise ValueError(f"min_coordinates['{key}'] must be an integer.") from exc

    return result


def _configured_flags(config: dict[str, Any]) -> dict[str, bool]:
    """
    Return flag options.
    """
    flags = config.get("flags") or {}
    if not isinstance(flags, dict):
        raise ValueError("flags must be a dict.")

    return {
        "add_validity_flag": bool(flags.get("add_validity_flag", True)),
        "add_reason_field": bool(flags.get("add_reason_field", True)),
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


def _is_number(value: Any) -> bool:
    """
    Return True if value is int/float but not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_position(value: Any) -> bool:
    """
    Return True if value is a valid coordinate position [x, y, ...].
    """
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _count_positions(coords: Any) -> int:
    """
    Count valid positions recursively.
    """
    if _is_position(coords):
        return 1

    if isinstance(coords, (list, tuple)):
        return sum(_count_positions(item) for item in coords)

    return 0


def _all_positions_numeric(coords: Any) -> bool:
    """
    Return True if all leaf positions are numeric pairs.
    """
    if _is_position(coords):
        return True

    if isinstance(coords, (list, tuple)):
        if not coords:
            return True
        # If this is a flat list of two numbers but not a valid position,
        # it means a malformed coordinate.
        if all(_is_number(x) for x in coords):
            return _is_position(coords)
        return all(_all_positions_numeric(item) for item in coords)

    return False


def _ring_is_closed(ring: Any) -> bool:
    """
    Return True if polygon ring is closed.
    """
    if not isinstance(ring, (list, tuple)) or len(ring) < 4:
        return False

    first = ring[0]
    last = ring[-1]

    if not _is_position(first) or not _is_position(last):
        return False

    return float(first[0]) == float(last[0]) and float(first[1]) == float(last[1])


def _python_check_geometry(
    geometry: dict[str, Any] | None,
    allowed_types: set[str],
    min_coordinates: dict[str, int],
) -> tuple[bool, str]:
    """
    Pure-python structural validity check.

    Returns:
        (is_valid, reason)
    """
    if geometry is None:
        return False, "geometry is null"

    if not isinstance(geometry, dict):
        return False, "geometry is not an object"

    gtype = geometry.get("type")
    if not isinstance(gtype, str) or not gtype:
        return False, "geometry.type missing or invalid"

    if gtype not in allowed_types:
        return False, f"geometry type '{gtype}' is not allowed"

    if gtype == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            return False, "GeometryCollection.geometries must be a list"

        for sub in geometries:
            ok, reason = _python_check_geometry(sub, allowed_types, min_coordinates)
            if not ok:
                return False, f"invalid sub-geometry: {reason}"

        return True, "valid (structural)"

    coords = geometry.get("coordinates")
    if coords is None:
        return False, "coordinates missing"

    if not _all_positions_numeric(coords):
        return False, "coordinates contain non-numeric or malformed positions"

    if gtype == "Point":
        if not _is_position(coords):
            return False, "Point coordinates must be a single position"
        return True, "valid (structural)"

    position_count = _count_positions(coords)
    min_required = min_coordinates.get(gtype, 1)

    if position_count < min_required:
        return False, f"{gtype} requires at least {min_required} positions, got {position_count}"

    if gtype == "Polygon":
        if not isinstance(coords, (list, tuple)) or not coords:
            return False, "Polygon must have at least one ring"
        for ring in coords:
            if not _ring_is_closed(ring):
                return False, "Polygon ring is not closed"

    if gtype == "MultiPolygon":
        if not isinstance(coords, (list, tuple)) or not coords:
            return False, "MultiPolygon must have at least one polygon"
        for polygon in coords:
            if not isinstance(polygon, (list, tuple)) or not polygon:
                return False, "MultiPolygon polygon must have at least one ring"
            for ring in polygon:
                if not _ring_is_closed(ring):
                    return False, "MultiPolygon ring is not closed"

    return True, "valid (structural)"


def _get_shapely_tools():
    """
    Lazy import shapely tools.
    """
    try:
        from shapely.geometry import shape, mapping
        from shapely.validation import explain_validity
    except ImportError as exc:
        raise SDKDependencyError(
            "geometry_validator requires 'shapely' for this engine/operation. "
            "Install it with: pip install shapely"
        ) from exc

    return shape, mapping, explain_validity


def _shapely_check_geometry(
    geometry: dict[str, Any] | None,
    allowed_types: set[str],
) -> tuple[bool, str]:
    """
    Shapely-based validity check.

    Returns:
        (is_valid, reason)
    """
    if geometry is None:
        return False, "geometry is null"

    if not isinstance(geometry, dict):
        return False, "geometry is not an object"

    gtype = geometry.get("type")
    if not isinstance(gtype, str) or gtype not in allowed_types:
        return False, f"geometry type '{gtype}' is not allowed"

    shape, _, explain_validity = _get_shapely_tools()

    try:
        geom = shape(geometry)
    except Exception as exc:
        return False, f"cannot build geometry: {exc}"

    if geom.is_empty:
        return False, "geometry is empty"

    if geom.is_valid:
        return True, "valid (shapely)"

    try:
        reason = explain_validity(geom)
    except Exception:
        reason = "invalid geometry"

    return False, str(reason)


def _shapely_repair_geometry(geometry: dict[str, Any]) -> dict[str, Any] | None:
    """
    Attempt to repair geometry using shapely.
    """
    shape, mapping, _ = _get_shapely_tools()

    geom = shape(geometry)

    repaired = None

    try:
        from shapely.validation import make_valid
        repaired = make_valid(geom)
    except Exception:
        repaired = None

    if repaired is None or repaired.is_empty:
        try:
            repaired = geom.buffer(0)
        except Exception:
            repaired = None

    if repaired is None or repaired.is_empty:
        return None

    return dict(mapping(repaired))


def _check_geometry(
    geometry: dict[str, Any] | None,
    engine: str,
    allowed_types: set[str],
    min_coordinates: dict[str, int],
) -> tuple[bool, str, str]:
    """
    Validate geometry and return (is_valid, reason, engine_used).
    """
    engine = _validate_engine(engine)

    if engine == "python":
        ok, reason = _python_check_geometry(geometry, allowed_types, min_coordinates)
        return ok, reason, "python"

    if engine == "shapely":
        ok, reason = _shapely_check_geometry(geometry, allowed_types)
        return ok, reason, "shapely"

    # auto
    try:
        ok, reason = _shapely_check_geometry(geometry, allowed_types)
        return ok, reason, "shapely"
    except SDKDependencyError:
        ok, reason = _python_check_geometry(geometry, allowed_types, min_coordinates)
        return ok, reason, "python"


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox from GeoJSON geometry.
    """
    if not geometry:
        return None

    coords = geometry.get("coordinates")
    if coords is None:
        return None

    xs: list[float] = []
    ys: list[float] = []

    def walk(obj: Any) -> None:
        if _is_position(obj):
            xs.append(float(obj[0]))
            ys.append(float(obj[1]))
            return
        if isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(coords)

    if not xs or not ys:
        return None

    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_bboxes(bboxes: list[list[float]]) -> dict[str, float] | None:
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
    Build vector metadata.
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
        "bounds": _merge_bboxes(bboxes),
    }


def _apply_flags(
    properties: dict[str, Any],
    is_valid: bool,
    reason: str,
    flags: dict[str, bool],
    repaired: bool = False,
) -> dict[str, Any]:
    """
    Apply validity flags to properties.
    """
    props = dict(properties)

    if flags.get("add_validity_flag", True):
        props["_valid"] = bool(is_valid)

    if flags.get("add_reason_field", True):
        props["_validity_reason"] = reason

    if repaired:
        props["_repaired"] = True

    return props


def _process_features(
    *,
    input_data: Any,
    engine: str,
    repair: bool,
    drop_invalid: bool,
    config: dict[str, Any],
    operation_label: str,
    user_metadata: dict[str, Any] | None,
) -> VectorOut:
    """
    Core processing shared by validate and repair capabilities.
    """
    final_engine = _validate_engine(engine)
    allowed_types = _configured_allowed_geometry_types(config)
    min_coordinates = _configured_min_coordinates(config)
    flags = _configured_flags(config)
    preserve_properties = bool(config.get("preserve_properties", True))

    input_features, source_info = _extract_features(input_data)

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    valid_count = 0
    invalid_count = 0
    repaired_count = 0
    dropped_count = 0

    for feature in input_features:
        geometry = feature.get("geometry")
        base_properties = (
            dict(feature.get("properties") or {}) if preserve_properties else {}
        )

        is_valid, reason, engine_used = _check_geometry(
            geometry=geometry,
            engine=final_engine,
            allowed_types=allowed_types,
            min_coordinates=min_coordinates,
        )
        engines_used.add(engine_used)

        final_geometry = geometry
        repaired = False

        if not is_valid and repair and isinstance(geometry, dict):
            try:
                repaired_geometry = _shapely_repair_geometry(geometry)
                engines_used.add("shapely")
            except SDKDependencyError:
                raise

            if repaired_geometry is not None:
                # Re-check repaired geometry.
                ok_after, reason_after, engine_after = _check_geometry(
                    geometry=repaired_geometry,
                    engine=final_engine,
                    allowed_types=allowed_types,
                    min_coordinates=min_coordinates,
                )
                engines_used.add(engine_after)

                final_geometry = repaired_geometry
                is_valid = ok_after
                reason = f"repaired -> {reason_after}"
                repaired = True

        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1

        if repaired:
            repaired_count += 1

        if not is_valid and drop_invalid:
            dropped_count += 1
            continue

        props = _apply_flags(
            properties=base_properties,
            is_valid=is_valid,
            reason=reason,
            flags=flags,
            repaired=repaired,
        )

        output_features.append({
            "type": "Feature",
            "geometry": final_geometry,
            "properties": props,
        })

    stats = _build_vector_metadata(output_features)

    final_user_metadata = user_metadata or {}
    if not isinstance(final_user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "geometry_validator",
        "loader": PLUGIN_ID,
        "operation": operation_label,
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "repair": repair,
        "drop_invalid": drop_invalid,
        "input_feature_count": len(input_features),
        "output_feature_count": len(output_features),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "repaired_count": repaired_count,
        "dropped_count": dropped_count,
        "created_at": _utc_now_iso(),
        **source_info,
        **stats,
        **final_user_metadata,
    }

    return VectorOut(
        features=output_features,
        metadata=output_metadata,
    )


@capability(
    name="validate_geometries",
    keywords=[
        "validate geometry",
        "geometry validation",
        "check geometry",
        "valid geometry",
        "invalid geometry",
        "geometry check",
        "اعتبارسنجی هندسه",
        "بررسی هندسه",
        "صحت هندسه",
        "هندسه نامعتبر",
        "اعتبار سنجی عوارض",
        "بررسی صحت لایه",
    ],
    description="Validate vector feature geometries and flag validity in VectorOut.",
    required_inputs=["features"],
    optional_inputs=[
        "engine",
        "drop_invalid",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "validate",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def validate_geometries(
    features: Any,
    engine: str | None = None,
    drop_invalid: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Validate geometries.

    Args:
        features:
            VectorOut, list[Feature], FeatureCollection dict or single Feature dict.
        engine:
            auto | shapely | python.
        drop_invalid:
            If True, invalid features are removed; otherwise flagged.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with validity flags.
    """
    config = _load_validator_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_drop_invalid = bool(
        pick_first(drop_invalid, config.get("default_drop_invalid"), default=False)
    )

    return _process_features(
        input_data=features,
        engine=final_engine,
        repair=False,
        drop_invalid=final_drop_invalid,
        config=config,
        operation_label="validate",
        user_metadata=metadata,
    )


@capability(
    name="repair_geometries",
    keywords=[
        "repair geometry",
        "fix geometry",
        "geometry repair",
        "make valid",
        "clean geometry",
        "تعمیر هندسه",
        "اصلاح هندسه",
        "رفع خطای هندسه",
        "معتبرسازی هندسه",
        "پاکسازی هندسه",
    ],
    description="Validate and attempt to repair invalid vector geometries using shapely.",
    required_inputs=["features"],
    optional_inputs=[
        "engine",
        "drop_invalid",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "repair",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely": True,
        "routable": True,
    },
)
def repair_geometries(
    features: Any,
    engine: str | None = None,
    drop_invalid: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Validate and repair geometries.

    Repair requires shapely. Invalid geometries that cannot be repaired remain
    flagged as invalid (or are dropped if drop_invalid=True).

    Args:
        features:
            VectorOut, list[Feature], FeatureCollection dict or single Feature dict.
        engine:
            Validation engine. Repair always uses shapely.
        drop_invalid:
            If True, features still invalid after repair are removed.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with repaired geometries and validity flags.
    """
    config = _load_validator_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_drop_invalid = bool(
        pick_first(drop_invalid, config.get("default_drop_invalid"), default=False)
    )

    return _process_features(
        input_data=features,
        engine=final_engine,
        repair=True,
        drop_invalid=final_drop_invalid,
        config=config,
        operation_label="repair",
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Geometry Validator",
    description=(
        "Validates and optionally repairs vector feature geometries. Invalid features "
        "are flagged by default and can be repaired with shapely when requested."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
