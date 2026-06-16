"""
crs_transformer.py

GeoChat SDK Plugin
==================

Plugin ID:
    crs_transformer

Purpose:
    Transform GeoJSON-like vector geometries between coordinate reference systems.

Capability:
    - transform_vector_crs:
        Transform vector feature geometries from source_crs to target_crs.

Config-aware behavior:
    Reads config/plugins/crs_transformer.yaml.

Engines:
    - auto:
        Use pyproj if available, otherwise pure-python fallback.
    - pyproj:
        Full CRS transformation support through pyproj.
    - python:
        Pure-python fallback. Supports:
            EPSG:4326 -> EPSG:3857
            EPSG:3857 -> EPSG:4326
            identity transform

Important:
    For scientific/production CRS transformation, install pyproj:
        pip install pyproj
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.exceptions import SDKDependencyError

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "crs_transformer"

VALID_ENGINES = {"auto", "pyproj", "python"}

WEB_MERCATOR_MAX_LAT = 85.05112878
EARTH_RADIUS = 6378137.0


def _load_crs_config() -> dict[str, Any]:
    """
    Load config/plugins/crs_transformer.yaml if available.
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
    Validate CRS transformation engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _normalize_crs(crs: Any) -> str:
    """
    Normalize CRS input.

    Accepts:
        - 4326
        - "4326"
        - "EPSG:4326"
        - "epsg:4326"
    """
    if crs is None:
        raise ValueError("CRS must not be None.")

    if isinstance(crs, bool):
        raise ValueError("CRS must be a string or EPSG integer.")

    if isinstance(crs, int):
        return f"EPSG:{crs}"

    if not isinstance(crs, str) or not crs.strip():
        raise ValueError("CRS must be a non-empty string or EPSG integer.")

    value = crs.strip().upper().replace(" ", "")

    if value.isdigit():
        return f"EPSG:{value}"

    return value


def _configured_allowed_crs(config: dict[str, Any]) -> set[str]:
    """
    Return allowed CRS values from config.

    Empty set means unrestricted.
    """
    values = config.get("allowed_crs") or []

    if not isinstance(values, list):
        raise ValueError("allowed_crs in crs_transformer config must be a list.")

    return {_normalize_crs(item) for item in values}


def _validate_crs_allowed(source_crs: str, target_crs: str, allowed_crs: set[str]) -> None:
    """
    Validate source/target CRS against allowed CRS list.
    """
    if not allowed_crs:
        return

    if source_crs not in allowed_crs:
        raise ValueError(f"source_crs '{source_crs}' is not allowed. Allowed CRS: {sorted(allowed_crs)}")

    if target_crs not in allowed_crs:
        raise ValueError(f"target_crs '{target_crs}' is not allowed. Allowed CRS: {sorted(allowed_crs)}")


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return coordinate precision.

    None means no rounding.
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


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize a GeoJSON Feature object.
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


def _validate_position(value: Any) -> None:
    """
    Validate coordinate position.
    """
    if not _is_position(value):
        raise ValueError(f"Invalid coordinate position: {value!r}")


def _lonlat_to_webmercator(lon: float, lat: float) -> tuple[float, float]:
    """
    Convert EPSG:4326 lon/lat to EPSG:3857 x/y.
    """
    lat = max(min(float(lat), WEB_MERCATOR_MAX_LAT), -WEB_MERCATOR_MAX_LAT)
    lon = float(lon)

    x = EARTH_RADIUS * math.radians(lon)
    y = EARTH_RADIUS * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))

    return x, y


def _webmercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """
    Convert EPSG:3857 x/y to EPSG:4326 lon/lat.
    """
    lon = math.degrees(float(x) / EARTH_RADIUS)
    lat = math.degrees(2.0 * math.atan(math.exp(float(y) / EARTH_RADIUS)) - math.pi / 2.0)

    return lon, lat


def _python_transform_position(
    position: list[Any] | tuple[Any, ...],
    source_crs: str,
    target_crs: str,
    precision: int | None = None,
) -> list[float]:
    """
    Transform one coordinate position using pure-python fallback.

    Supports:
        EPSG:4326 -> EPSG:3857
        EPSG:3857 -> EPSG:4326
        identity
    """
    _validate_position(position)

    x = float(position[0])
    y = float(position[1])
    extra = list(position[2:])

    if source_crs == target_crs:
        nx, ny = x, y
    elif source_crs == "EPSG:4326" and target_crs == "EPSG:3857":
        nx, ny = _lonlat_to_webmercator(x, y)
    elif source_crs == "EPSG:3857" and target_crs == "EPSG:4326":
        nx, ny = _webmercator_to_lonlat(x, y)
    else:
        raise SDKDependencyError(
            "Pure-python CRS engine only supports EPSG:4326 <-> EPSG:3857 "
            "and identity transforms. Install pyproj for full CRS support: pip install pyproj"
        )

    return [_round_coord(nx, precision), _round_coord(ny, precision), *extra]


def _get_pyproj_transformer(source_crs: str, target_crs: str):
    """
    Lazy import pyproj and create transformer.
    """
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise SDKDependencyError(
            "crs_transformer requires 'pyproj' for this engine/CRS pair. "
            "Install it with: pip install pyproj"
        ) from exc

    try:
        return Transformer.from_crs(source_crs, target_crs, always_xy=True)
    except Exception as exc:
        raise ValueError(f"Invalid CRS transformation {source_crs} -> {target_crs}: {exc}") from exc


def _pyproj_position_transformer(
    source_crs: str,
    target_crs: str,
    precision: int | None,
) -> Callable[[list[Any] | tuple[Any, ...]], list[float]]:
    """
    Build a position transformer using pyproj.
    """
    transformer = _get_pyproj_transformer(source_crs, target_crs)

    def transform_position(position: list[Any] | tuple[Any, ...]) -> list[float]:
        _validate_position(position)

        x = float(position[0])
        y = float(position[1])
        extra = list(position[2:])

        if extra:
            try:
                result = transformer.transform(x, y, float(extra[0]))
                nx = result[0]
                ny = result[1]
                nz = result[2]
                return [_round_coord(nx, precision), _round_coord(ny, precision), _round_coord(nz, precision), *extra[1:]]
            except Exception:
                result = transformer.transform(x, y)
                nx = result[0]
                ny = result[1]
                return [_round_coord(nx, precision), _round_coord(ny, precision), *extra]

        result = transformer.transform(x, y)
        nx = result[0]
        ny = result[1]
        return [_round_coord(nx, precision), _round_coord(ny, precision)]

    return transform_position


def _python_position_transformer(
    source_crs: str,
    target_crs: str,
    precision: int | None,
) -> Callable[[list[Any] | tuple[Any, ...]], list[float]]:
    """
    Build a position transformer using pure-python fallback.
    """
    def transform_position(position: list[Any] | tuple[Any, ...]) -> list[float]:
        return _python_transform_position(
            position=position,
            source_crs=source_crs,
            target_crs=target_crs,
            precision=precision,
        )

    return transform_position


def _select_position_transformer(
    source_crs: str,
    target_crs: str,
    engine: str,
    precision: int | None,
) -> tuple[Callable[[list[Any] | tuple[Any, ...]], list[float]], str]:
    """
    Select transformation function and return (transformer, engine_used).
    """
    engine = _validate_engine(engine)

    if engine == "python":
        return _python_position_transformer(source_crs, target_crs, precision), "python"

    if engine == "pyproj":
        return _pyproj_position_transformer(source_crs, target_crs, precision), "pyproj"

    # auto
    try:
        return _pyproj_position_transformer(source_crs, target_crs, precision), "pyproj"
    except SDKDependencyError:
        return _python_position_transformer(source_crs, target_crs, precision), "python"


def _transform_coordinates(
    coords: Any,
    transform_position: Callable[[list[Any] | tuple[Any, ...]], list[float]],
    counter: dict[str, int],
) -> Any:
    """
    Recursively transform GeoJSON coordinates.
    """
    if _is_position(coords):
        counter["count"] += 1
        return transform_position(coords)

    if isinstance(coords, list):
        return [_transform_coordinates(item, transform_position, counter) for item in coords]

    if isinstance(coords, tuple):
        return [_transform_coordinates(item, transform_position, counter) for item in coords]

    raise ValueError(f"Invalid coordinate structure: {coords!r}")


def _transform_geometry(
    geometry: dict[str, Any] | None,
    transform_position: Callable[[list[Any] | tuple[Any, ...]], list[float]],
    counter: dict[str, int],
) -> dict[str, Any] | None:
    """
    Transform a GeoJSON geometry.
    """
    if geometry is None:
        return None

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    gtype = geometry.get("type")

    if not isinstance(gtype, str) or not gtype:
        raise ValueError("geometry.type must be a non-empty string.")

    if gtype == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            raise ValueError("GeometryCollection.geometries must be a list.")

        return {
            "type": "GeometryCollection",
            "geometries": [
                _transform_geometry(sub_geometry, transform_position, counter)
                for sub_geometry in geometries
            ],
        }

    if "coordinates" not in geometry:
        raise ValueError(f"{gtype} geometry must contain coordinates.")

    return {
        "type": gtype,
        "coordinates": _transform_coordinates(
            geometry.get("coordinates"),
            transform_position,
            counter,
        ),
    }


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox from GeoJSON geometry.
    """
    if not geometry:
        return None

    if geometry.get("type") == "GeometryCollection":
        bboxes = [
            _geometry_bbox(sub)
            for sub in geometry.get("geometries", [])
            if isinstance(sub, dict)
        ]
        merged = _merge_bbox_arrays([b for b in bboxes if b])
        if not merged:
            return None
        return [merged["minx"], merged["miny"], merged["maxx"], merged["maxy"]]

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
        "bounds": _merge_bbox_arrays(bboxes),
    }


@capability(
    name="transform_vector_crs",
    keywords=[
        "transform crs",
        "reproject",
        "reprojection",
        "coordinate transform",
        "change projection",
        "convert crs",
        "epsg transform",
        "تبدیل سیستم مختصات",
        "تبدیل CRS",
        "بازفرافکنی",
        "تبدیل تصویر",
        "تبدیل مختصات",
        "تغییر سیستم مختصات",
    ],
    description="Transform vector feature geometries from source CRS to target CRS.",
    required_inputs=["features", "source_crs", "target_crs"],
    optional_inputs=[
        "engine",
        "precision",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "utility",
        "data_type": "vector",
        "operation": "crs_transform",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_reference",
        "config_aware": True,
        "requires_pyproj_for_full_support": True,
        "routable": True,
    },
)
def transform_vector_crs(
    features: Any,
    source_crs: str | int | None = None,
    target_crs: str | int | None = None,
    engine: str | None = None,
    precision: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Transform vector feature geometries between CRS.

    Args:
        features:
            VectorOut, list[Feature], FeatureCollection dict or single Feature dict.
        source_crs:
            Source CRS, e.g. EPSG:4326.
        target_crs:
            Target CRS, e.g. EPSG:3857.
        engine:
            auto | pyproj | python.
        precision:
            Coordinate precision. If None, config coordinate_precision is used.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with transformed geometries.
    """
    config = _load_crs_config()

    final_source_crs = _normalize_crs(
        pick_first(source_crs, config.get("default_source_crs"), default="EPSG:4326")
    )
    final_target_crs = _normalize_crs(
        pick_first(target_crs, config.get("default_target_crs"), default="EPSG:3857")
    )
    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    # Important:
    # pick_first cannot distinguish explicit None for precision from "not provided",
    # so if precision is None we use config.
    final_precision = _configured_precision(config) if precision is None else precision
    if final_precision is not None:
        if isinstance(final_precision, bool):
            raise ValueError("precision must be an integer or None.")
        final_precision = int(final_precision)
        if final_precision < 0 or final_precision > 15:
            raise ValueError("precision must be between 0 and 15.")

    allow_identity = bool(config.get("allow_identity", True))

    if final_source_crs == final_target_crs and not allow_identity:
        raise ValueError("Identity CRS transform is not allowed by config.")

    allowed_crs = _configured_allowed_crs(config)
    _validate_crs_allowed(final_source_crs, final_target_crs, allowed_crs)

    input_features, source_info = _extract_features(features)

    preserve_properties = bool(config.get("preserve_properties", True))

    transform_position, engine_used = _select_position_transformer(
        source_crs=final_source_crs,
        target_crs=final_target_crs,
        engine=final_engine,
        precision=final_precision,
    )

    counter = {"count": 0}

    output_features: list[dict[str, Any]] = []

    for feature in input_features:
        transformed_geometry = _transform_geometry(
            geometry=feature.get("geometry"),
            transform_position=transform_position,
            counter=counter,
        )

        properties = deepcopy(feature.get("properties") or {}) if preserve_properties else {}

        output_features.append({
            "type": "Feature",
            "geometry": transformed_geometry,
            "properties": properties,
        })

    stats = _build_vector_metadata(output_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "crs_transformer",
        "loader": PLUGIN_ID,
        "operation": "crs_transform",
        "source_crs": final_source_crs,
        "target_crs": final_target_crs,
        "engine_requested": final_engine,
        "engines_used": [engine_used],
        "coordinate_precision": final_precision,
        "coordinate_transform_count": counter["count"],
        "input_feature_count": len(input_features),
        "output_feature_count": len(output_features),
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
    name="CRS Transformer",
    description=(
        "Transforms vector geometries between coordinate reference systems. "
        "Uses pyproj for full CRS support and includes a pure-python EPSG:4326/3857 fallback."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
