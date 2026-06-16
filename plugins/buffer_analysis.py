"""
buffer_analysis.py

GeoChat SDK Plugin
==================

Plugin ID:
    buffer_analysis

Purpose:
    Perform vector buffer analysis on GeoJSON-like features.

Capability:
    - buffer_vector_features:
        Create buffer polygons around vector features and return VectorOut.

Config-aware behavior:
    Reads config/plugins/buffer_analysis.yaml.

Engines:
    - auto:
        Use shapely if available, otherwise fallback to pure-python Point buffer.
    - shapely:
        Requires shapely. Supports real GIS buffer for supported geometries.
    - python:
        Pure-python fallback. Currently supports Point geometry only.

Important:
    GeoJSON coordinates are treated as planar coordinates. If coordinates are
    longitude/latitude degrees, distance is interpreted in coordinate units unless
    data has already been projected. Metadata stores the requested units, but no
    CRS transformation is performed in this plugin.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.exceptions import SDKDependencyError

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "buffer_analysis"

DEFAULT_ALLOWED_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}

VALID_ENGINES = {"auto", "shapely", "python"}


def _load_buffer_config() -> dict[str, Any]:
    """
    Load config/plugins/buffer_analysis.yaml if available.
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


def _to_float(value: Any, field_name: str) -> float:
    """
    Convert value to float.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number.")

    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be a number.") from exc


def _to_int(value: Any, field_name: str) -> int:
    """
    Convert value to int.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")

    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc


def _validate_distance(distance: Any, allow_negative: bool = False) -> float:
    """
    Validate buffer distance.
    """
    distance = _to_float(distance, "distance")

    if distance == 0:
        raise ValueError("distance must not be zero.")

    if distance < 0 and not allow_negative:
        raise ValueError("negative distance is not allowed by config.")

    return distance


def _validate_quad_segs(value: Any) -> int:
    """
    Validate quad_segs.
    """
    quad_segs = _to_int(value, "quad_segs")

    if quad_segs < 1:
        raise ValueError("quad_segs must be greater than or equal to 1.")

    if quad_segs > 128:
        raise ValueError("quad_segs is too large. Maximum allowed value is 128.")

    return quad_segs


def _validate_engine(engine: str) -> str:
    """
    Validate buffer engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _configured_allowed_geometry_types(config: dict[str, Any]) -> set[str]:
    """
    Return allowed geometry types from config.
    """
    values = config.get("allowed_geometry_types")

    if not values:
        return set(DEFAULT_ALLOWED_GEOMETRY_TYPES)

    if not isinstance(values, list):
        raise ValueError("allowed_geometry_types in buffer_analysis config must be a list.")

    return {str(item) for item in values}


def _validate_geometry_type(geometry: dict[str, Any] | None, allowed_types: set[str]) -> None:
    """
    Validate geometry type.
    """
    if geometry is None:
        return

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    gtype = geometry.get("type")

    if not isinstance(gtype, str) or not gtype:
        raise ValueError("geometry.type must be a non-empty string.")

    if gtype not in allowed_types:
        raise ValueError(
            f"Geometry type '{gtype}' is not allowed. "
            f"Allowed types: {sorted(allowed_types)}"
        )


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


def _get_shapely_tools():
    """
    Lazy import shapely tools.
    """
    try:
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
    except ImportError as exc:
        raise SDKDependencyError(
            "buffer_analysis requires 'shapely' for this operation. "
            "Install it with: pip install shapely"
        ) from exc

    return shape, mapping, unary_union


def _python_buffer_point_geometry(
    geometry: dict[str, Any],
    distance: float,
    quad_segs: int,
) -> dict[str, Any]:
    """
    Pure-python buffer for Point geometry.

    Produces a polygon approximating a circle.
    """
    if geometry.get("type") != "Point":
        raise SDKDependencyError(
            "Pure-python buffer engine only supports Point geometry. "
            "Install shapely for full geometry support: pip install shapely"
        )

    coords = geometry.get("coordinates")

    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        raise ValueError("Point coordinates must contain at least x and y.")

    x = float(coords[0])
    y = float(coords[1])

    segments = max(8, int(quad_segs) * 4)
    ring: list[list[float]] = []

    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        ring.append([
            x + distance * math.cos(angle),
            y + distance * math.sin(angle),
        ])

    ring.append(ring[0])

    return {
        "type": "Polygon",
        "coordinates": [ring],
    }


def _buffer_geometry_python(
    geometry: dict[str, Any] | None,
    distance: float,
    quad_segs: int,
) -> dict[str, Any] | None:
    """
    Buffer geometry using pure-python fallback.
    """
    if geometry is None:
        return None

    return _python_buffer_point_geometry(
        geometry=geometry,
        distance=distance,
        quad_segs=quad_segs,
    )


def _buffer_geometry_shapely(
    geometry: dict[str, Any] | None,
    distance: float,
    quad_segs: int,
    cap_style: str,
    join_style: str,
    mitre_limit: float,
) -> dict[str, Any] | None:
    """
    Buffer geometry using shapely.
    """
    if geometry is None:
        return None

    shape, mapping, _ = _get_shapely_tools()

    geom = shape(geometry)

    try:
        buffered = geom.buffer(
            distance,
            quad_segs=quad_segs,
            cap_style=cap_style,
            join_style=join_style,
            mitre_limit=mitre_limit,
        )
    except TypeError:
        # Compatibility with older shapely versions.
        buffered = geom.buffer(
            distance,
            resolution=quad_segs,
            cap_style=cap_style,
            join_style=join_style,
            mitre_limit=mitre_limit,
        )

    return dict(mapping(buffered))


def _buffer_geometry(
    geometry: dict[str, Any] | None,
    distance: float,
    quad_segs: int,
    engine: str,
    cap_style: str,
    join_style: str,
    mitre_limit: float,
) -> tuple[dict[str, Any] | None, str]:
    """
    Buffer one geometry and return geometry + engine_used.
    """
    engine = _validate_engine(engine)

    if engine == "python":
        return _buffer_geometry_python(geometry, distance, quad_segs), "python"

    if engine == "shapely":
        return _buffer_geometry_shapely(
            geometry=geometry,
            distance=distance,
            quad_segs=quad_segs,
            cap_style=cap_style,
            join_style=join_style,
            mitre_limit=mitre_limit,
        ), "shapely"

    # auto
    try:
        return _buffer_geometry_shapely(
            geometry=geometry,
            distance=distance,
            quad_segs=quad_segs,
            cap_style=cap_style,
            join_style=join_style,
            mitre_limit=mitre_limit,
        ), "shapely"
    except SDKDependencyError:
        return _buffer_geometry_python(geometry, distance, quad_segs), "python"


def _is_number(value: Any) -> bool:
    """
    Return True if value is int/float but not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
        if (
            isinstance(obj, (list, tuple))
            and len(obj) >= 2
            and _is_number(obj[0])
            and _is_number(obj[1])
        ):
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


def _dissolve_buffered_features(
    buffered_features: list[dict[str, Any]],
    properties: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Dissolve buffered geometries into one feature using shapely.
    """
    shape, mapping, unary_union = _get_shapely_tools()

    geometries = [
        shape(feature["geometry"])
        for feature in buffered_features
        if isinstance(feature.get("geometry"), dict)
    ]

    if not geometries:
        return [{
            "type": "Feature",
            "geometry": None,
            "properties": properties or {},
        }]

    dissolved = unary_union(geometries)

    return [{
        "type": "Feature",
        "geometry": dict(mapping(dissolved)),
        "properties": properties or {},
    }]


@capability(
    name="buffer_vector_features",
    keywords=[
        "buffer",
        "buffer analysis",
        "vector buffer",
        "spatial buffer",
        "distance buffer",
        "create buffer",
        "حریم",
        "بافر",
        "تحلیل بافر",
        "ایجاد حریم",
        "حریم مکانی",
        "حریم عوارض",
    ],
    description="Create buffer polygons around vector features and return VectorOut.",
    required_inputs=["features"],
    optional_inputs=[
        "distance",
        "units",
        "quad_segs",
        "engine",
        "dissolve",
        "cap_style",
        "join_style",
        "mitre_limit",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "buffer",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def buffer_vector_features(
    features: Any,
    distance: float | None = None,
    units: str | None = None,
    quad_segs: int | None = None,
    engine: str | None = None,
    dissolve: bool | None = None,
    cap_style: str | None = None,
    join_style: str | None = None,
    mitre_limit: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Create buffer polygons around vector features.

    Args:
        features:
            VectorOut, list[Feature], FeatureCollection dict or single Feature dict.
        distance:
            Buffer distance. If None, config default_distance is used.
        units:
            Distance units label for metadata.
        quad_segs:
            Number of segments per quarter circle.
        engine:
            auto | shapely | python.
        dissolve:
            If True, dissolve all buffer geometries into one feature.
            Requires shapely.
        cap_style:
            Shapely cap style. Usually: round, flat, square.
        join_style:
            Shapely join style. Usually: round, mitre, bevel.
        mitre_limit:
            Shapely mitre limit.
        metadata:
            Optional metadata to merge into output metadata.

    Returns:
        VectorOut.
    """
    config = _load_buffer_config()

    final_allow_negative = bool(config.get("allow_negative_distance", False))

    final_distance = _validate_distance(
        pick_first(distance, config.get("default_distance"), default=100.0),
        allow_negative=final_allow_negative,
    )

    final_units = str(pick_first(units, config.get("default_units"), default="map_units"))

    final_quad_segs = _validate_quad_segs(
        pick_first(quad_segs, config.get("default_quad_segs"), default=8)
    )

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_dissolve = bool(
        pick_first(dissolve, config.get("default_dissolve"), default=False)
    )

    final_cap_style = str(pick_first(cap_style, config.get("default_cap_style"), default="round"))
    final_join_style = str(pick_first(join_style, config.get("default_join_style"), default="round"))
    final_mitre_limit = _to_float(
        pick_first(mitre_limit, config.get("default_mitre_limit"), default=5.0),
        "mitre_limit",
    )

    allowed_geometry_types = _configured_allowed_geometry_types(config)
    preserve_properties = bool(config.get("preserve_properties", True))

    input_features, source_info = _extract_features(features)

    buffered_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    for idx, feature in enumerate(input_features):
        geometry = feature.get("geometry")
        _validate_geometry_type(geometry, allowed_geometry_types)

        buffered_geometry, engine_used = _buffer_geometry(
            geometry=geometry,
            distance=final_distance,
            quad_segs=final_quad_segs,
            engine=final_engine,
            cap_style=final_cap_style,
            join_style=final_join_style,
            mitre_limit=final_mitre_limit,
        )

        engines_used.add(engine_used)

        properties = dict(feature.get("properties") or {}) if preserve_properties else {}
        properties["_buffer_source_index"] = idx

        buffered_features.append({
            "type": "Feature",
            "geometry": buffered_geometry,
            "properties": properties,
        })

    if final_dissolve:
        if final_engine == "python" or engines_used == {"python"}:
            raise SDKDependencyError(
                "dissolve=True requires shapely. Install it with: pip install shapely"
            )

        buffered_features = _dissolve_buffered_features(
            buffered_features,
            properties={
                "dissolved": True,
                "buffer_distance": final_distance,
                "buffer_units": final_units,
            },
        )

    stats = _build_vector_metadata(buffered_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "buffer_analysis",
        "loader": PLUGIN_ID,
        "operation": "buffer",
        "distance": final_distance,
        "units": final_units,
        "quad_segs": final_quad_segs,
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "dissolve": final_dissolve,
        "cap_style": final_cap_style,
        "join_style": final_join_style,
        "mitre_limit": final_mitre_limit,
        "input_feature_count": len(input_features),
        "output_feature_count": len(buffered_features),
        "created_at": _utc_now_iso(),
        "note": (
            "Coordinates are treated as planar coordinates. "
            "No CRS transformation is performed by this plugin."
        ),
        **source_info,
        **stats,
        **user_metadata,
    }

    return VectorOut(
        features=buffered_features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Buffer Analysis",
    description=(
        "Creates buffer polygons around vector features. Supports config defaults, "
        "shapely engine for full geometry support, and pure-python Point fallback."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
