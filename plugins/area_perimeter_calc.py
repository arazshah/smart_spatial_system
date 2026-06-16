"""
area_perimeter_calc.py

GeoChat SDK Plugin
==================

Plugin ID:
    area_perimeter_calc

Purpose:
    Calculate planar area, perimeter, and length metrics for GeoJSON-like
    vector geometries.

Capability:
    - calculate_area_perimeter

Engines:
    - auto:
        Use shapely if available, otherwise pure-python fallback.
    - shapely:
        Use shapely for robust metric calculation.
    - python:
        Use pure-python planar formulas.

Notes:
    - Calculations are planar, not geodesic.
    - For geographic CRS (e.g. EPSG:4326), values are not physically reliable
      unless the data has already been reprojected appropriately.
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


PLUGIN_ID = "area_perimeter_calc"

VALID_ENGINES = {"auto", "shapely", "python"}


def _load_metric_config() -> dict[str, Any]:
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_engine(engine: str) -> str:
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()
    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")
    return engine


def _configured_precision(config: dict[str, Any]) -> int | None:
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
    if value is None:
        return None
    if precision is None:
        return float(value)
    return round(float(value), precision)


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    fields = config.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("fields in area_perimeter_calc config must be a dict.")

    return {
        "area_field": str(fields.get("area_field", "_area")),
        "perimeter_field": str(fields.get("perimeter_field", "_perimeter")),
        "length_field": str(fields.get("length_field", "_length")),
        "status_field": str(fields.get("status_field", "_metric_status")),
        "engine_field": str(fields.get("engine_field", "_metric_engine")),
        "geometry_type_field": str(fields.get("geometry_type_field", "_source_geometry_type")),
    }


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
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
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_position(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _validate_position(position: Any) -> tuple[float, float]:
    if not _is_position(position):
        raise ValueError(f"Invalid coordinate position: {position!r}")
    return float(position[0]), float(position[1])


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _ring_length(ring: Any) -> float:
    if not isinstance(ring, (list, tuple)):
        return 0.0

    points = [_validate_position(p) for p in ring if _is_position(p)]
    if len(points) < 2:
        return 0.0

    return sum(_distance(a, b) for a, b in zip(points[:-1], points[1:]))


def _ring_area_abs(ring: Any) -> float:
    if not isinstance(ring, (list, tuple)):
        return 0.0

    points = [_validate_position(p) for p in ring if _is_position(p)]
    if len(points) < 3:
        return 0.0

    if points[0] != points[-1]:
        points = [*points, points[0]]

    cross_sum = 0.0
    for a, b in zip(points[:-1], points[1:]):
        cross_sum += a[0] * b[1] - b[0] * a[1]

    return abs(cross_sum) / 2.0


def _python_metrics_geometry(geometry: dict[str, Any] | None) -> dict[str, float | None]:
    if geometry is None:
        return {"area": None, "perimeter": None, "length": None}

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    gtype = geometry.get("type")
    if not isinstance(gtype, str) or not gtype:
        raise ValueError("geometry.type must be a non-empty string.")

    if gtype == "Point":
        return {"area": 0.0, "perimeter": 0.0, "length": 0.0}

    if gtype == "MultiPoint":
        return {"area": 0.0, "perimeter": 0.0, "length": 0.0}

    if gtype == "LineString":
        length = _ring_length(geometry.get("coordinates"))
        return {"area": 0.0, "perimeter": 0.0, "length": length}

    if gtype == "MultiLineString":
        lines = geometry.get("coordinates")
        if not isinstance(lines, (list, tuple)):
            return {"area": 0.0, "perimeter": 0.0, "length": 0.0}

        length = sum(_ring_length(line) for line in lines)
        return {"area": 0.0, "perimeter": 0.0, "length": length}

    if gtype == "Polygon":
        rings = geometry.get("coordinates")
        if not isinstance(rings, (list, tuple)) or not rings:
            return {"area": 0.0, "perimeter": 0.0, "length": 0.0}

        outer = rings[0]
        holes = list(rings[1:])

        area = _ring_area_abs(outer) - sum(_ring_area_abs(hole) for hole in holes)
        perimeter = _ring_length(outer) + sum(_ring_length(hole) for hole in holes)

        return {"area": max(area, 0.0), "perimeter": perimeter, "length": perimeter}

    if gtype == "MultiPolygon":
        polygons = geometry.get("coordinates")
        if not isinstance(polygons, (list, tuple)):
            return {"area": 0.0, "perimeter": 0.0, "length": 0.0}

        total_area = 0.0
        total_perimeter = 0.0

        for polygon in polygons:
            item = _python_metrics_geometry({"type": "Polygon", "coordinates": polygon})
            total_area += float(item["area"] or 0.0)
            total_perimeter += float(item["perimeter"] or 0.0)

        return {"area": total_area, "perimeter": total_perimeter, "length": total_perimeter}

    if gtype == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            return {"area": 0.0, "perimeter": 0.0, "length": 0.0}

        total_area = 0.0
        total_perimeter = 0.0
        total_length = 0.0

        for sub in geometries:
            item = _python_metrics_geometry(sub)
            total_area += float(item["area"] or 0.0)
            total_perimeter += float(item["perimeter"] or 0.0)
            total_length += float(item["length"] or 0.0)

        return {"area": total_area, "perimeter": total_perimeter, "length": total_length}

    raise ValueError(f"Unsupported geometry type for metric calculation: {gtype}")


def _get_shapely_tools():
    try:
        from shapely.geometry import shape
    except ImportError as exc:
        raise SDKDependencyError(
            "area_perimeter_calc requires 'shapely' for this engine. "
            "Install it with: pip install shapely"
        ) from exc

    return shape


def _shapely_metrics_geometry(geometry: dict[str, Any] | None) -> dict[str, float | None]:
    if geometry is None:
        return {"area": None, "perimeter": None, "length": None}

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    shape = _get_shapely_tools()

    try:
        geom = shape(geometry)
    except Exception as exc:
        raise ValueError(f"cannot build shapely geometry: {exc}") from exc

    if geom.is_empty:
        return {"area": 0.0, "perimeter": 0.0, "length": 0.0}

    gtype = geom.geom_type

    if gtype in {"Polygon", "MultiPolygon"}:
        return {
            "area": float(geom.area),
            "perimeter": float(geom.length),
            "length": float(geom.length),
        }

    if gtype in {"LineString", "MultiLineString"}:
        return {
            "area": 0.0,
            "perimeter": 0.0,
            "length": float(geom.length),
        }

    if gtype in {"Point", "MultiPoint"}:
        return {
            "area": 0.0,
            "perimeter": 0.0,
            "length": 0.0,
        }

    return {
        "area": float(getattr(geom, "area", 0.0) or 0.0),
        "perimeter": float(getattr(geom, "length", 0.0) or 0.0),
        "length": float(getattr(geom, "length", 0.0) or 0.0),
    }


def _calculate_metrics(
    geometry: dict[str, Any] | None,
    engine: str,
) -> tuple[dict[str, float | None], str]:
    engine = _validate_engine(engine)

    if engine == "python":
        return _python_metrics_geometry(geometry), "python"

    if engine == "shapely":
        return _shapely_metrics_geometry(geometry), "shapely"

    try:
        return _shapely_metrics_geometry(geometry), "shapely"
    except SDKDependencyError:
        return _python_metrics_geometry(geometry), "python"


def _is_geographic_crs(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    text = value.strip().upper()
    return text in {
        "EPSG:4326",
        "CRS:84",
        "OGC:CRS84",
    }


def _make_feature_with_metrics(
    *,
    source_feature: dict[str, Any],
    metrics: dict[str, float | None],
    engine_used: str,
    fields: dict[str, str],
    precision: int | None,
    preserve_properties: bool,
    always_add_fields: bool,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}
    geometry = source_feature.get("geometry")

    geometry_type = None
    if isinstance(geometry, dict):
        geometry_type = geometry.get("type")
    elif geometry is None:
        geometry_type = "Null"
    else:
        geometry_type = "Invalid"

    area_value = _round_value(metrics.get("area"), precision)
    perimeter_value = _round_value(metrics.get("perimeter"), precision)
    length_value = _round_value(metrics.get("length"), precision)

    if always_add_fields or area_value is not None:
        properties[fields["area_field"]] = area_value

    if always_add_fields or perimeter_value is not None:
        properties[fields["perimeter_field"]] = perimeter_value

    if always_add_fields or length_value is not None:
        properties[fields["length_field"]] = length_value

    properties[fields["status_field"]] = status
    properties[fields["engine_field"]] = engine_used
    properties[fields["geometry_type_field"]] = geometry_type

    if reason:
        properties["_metric_reason"] = reason

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": properties,
    }


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
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

    if geometry.get("type") == "GeometryCollection":
        for sub in geometry.get("geometries", []):
            if isinstance(sub, dict):
                bbox = _geometry_bbox(sub)
                if bbox:
                    xs.extend([bbox[0], bbox[2]])
                    ys.extend([bbox[1], bbox[3]])
    else:
        walk(coords)

    if not xs or not ys:
        return None

    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_bbox_arrays(bboxes: list[list[float]]) -> dict[str, float] | None:
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
    name="calculate_area_perimeter",
    keywords=[
        "area",
        "perimeter",
        "length",
        "calculate area",
        "polygon area",
        "line length",
        "parcel area",
        "spatial metrics",
        "مساحت",
        "محیط",
        "طول",
        "محاسبه مساحت",
        "محاسبه محیط",
        "محاسبه طول",
        "متریک هندسی",
    ],
    description="Calculate planar area, perimeter, and length for vector geometries.",
    required_inputs=["features"],
    optional_inputs=[
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
        "operation": "metrics",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_robust_geometry": True,
        "planar_only": True,
        "routable": True,
    },
)
def calculate_area_perimeter(
    features: Any,
    engine: str | None = None,
    precision: int | None = None,
    drop_failed: bool | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Calculate planar metrics for vector geometries.

    Args:
        features:
            VectorOut, list[Feature], FeatureCollection dict or single Feature dict.
        engine:
            auto | shapely | python.
        precision:
            Rounding precision for metric values.
        drop_failed:
            If True, failed features are removed.
        source_crs:
            Optional source CRS hint, e.g. EPSG:4326.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut with metric fields added to each feature.
    """
    config = _load_metric_config()

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

    final_source_crs = pick_first(source_crs, config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", True))

    preserve_properties = bool(config.get("preserve_properties", True))
    always_add_fields = bool(config.get("always_add_fields", True))
    fields = _configured_fields(config)

    input_features, source_info = _extract_features(features)

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    success_count = 0
    failed_count = 0
    dropped_count = 0

    for feature in input_features:
        try:
            metrics, engine_used = _calculate_metrics(
                geometry=feature.get("geometry"),
                engine=final_engine,
            )
            engines_used.add(engine_used)

            output_features.append(
                _make_feature_with_metrics(
                    source_feature=feature,
                    metrics=metrics,
                    engine_used=engine_used,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    always_add_fields=always_add_fields,
                    status="success",
                )
            )
            success_count += 1

        except Exception as exc:
            failed_count += 1
            engines_used.add(final_engine)

            if final_drop_failed:
                dropped_count += 1
                continue

            output_features.append(
                _make_feature_with_metrics(
                    source_feature=feature,
                    metrics={"area": None, "perimeter": None, "length": None},
                    engine_used=final_engine,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    always_add_fields=always_add_fields,
                    status="failed",
                    reason=str(exc),
                )
            )

    stats = _build_vector_metadata(output_features)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Planar metric calculation is being performed on a geographic CRS. "
            "Reproject to a projected CRS for reliable physical area/length values."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "area_perimeter_calc",
        "loader": PLUGIN_ID,
        "operation": "metrics",
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "coordinate_precision": final_precision,
        "drop_failed": final_drop_failed,
        "source_crs": final_source_crs,
        "planar_only": True,
        "warning": geographic_warning,
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
    name="Area & Perimeter Calculator",
    description=(
        "Calculates planar area, perimeter, and length metrics for vector geometries. "
        "Uses shapely when available and includes a pure-python fallback."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
