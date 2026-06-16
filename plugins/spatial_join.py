"""
spatial_join.py

GeoChat SDK Plugin
==================

Plugin ID:
    spatial_join

Purpose:
    Join attributes from target vector features into source vector features
    based on spatial predicates.

Capability:
    - spatial_join_features

Supported predicates:
    - intersects
    - within
    - contains

Join types:
    - inner
    - left

Cardinality:
    - first
    - one_to_many

Engines:
    - auto:
        Use shapely if available, otherwise pure-python bbox fallback.
    - shapely:
        Exact spatial predicates using shapely.
    - python:
        Bbox-based fallback.

Important:
    The python engine is bbox-based and approximate. For production-grade
    spatial join, install shapely:
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


PLUGIN_ID = "spatial_join"

VALID_ENGINES = {"auto", "shapely", "python"}
VALID_PREDICATES = {"intersects", "within", "contains"}
VALID_JOIN_TYPES = {"inner", "left"}
VALID_CARDINALITIES = {"first", "one_to_many"}

EPSILON = 1e-12


def _load_join_config() -> dict[str, Any]:
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


def _validate_predicate(predicate: str) -> str:
    if not isinstance(predicate, str) or not predicate.strip():
        raise ValueError("predicate must be a non-empty string.")

    predicate = predicate.strip().lower()
    if predicate not in VALID_PREDICATES:
        raise ValueError(
            f"Unsupported predicate '{predicate}'. Valid predicates: {sorted(VALID_PREDICATES)}"
        )

    return predicate


def _validate_join_type(join_type: str) -> str:
    if not isinstance(join_type, str) or not join_type.strip():
        raise ValueError("join_type must be a non-empty string.")

    join_type = join_type.strip().lower()
    if join_type not in VALID_JOIN_TYPES:
        raise ValueError(
            f"Unsupported join_type '{join_type}'. Valid join types: {sorted(VALID_JOIN_TYPES)}"
        )

    return join_type


def _validate_cardinality(cardinality: str) -> str:
    if not isinstance(cardinality, str) or not cardinality.strip():
        raise ValueError("cardinality must be a non-empty string.")

    cardinality = cardinality.strip().lower()
    if cardinality not in VALID_CARDINALITIES:
        raise ValueError(
            f"Unsupported cardinality '{cardinality}'. "
            f"Valid cardinalities: {sorted(VALID_CARDINALITIES)}"
        )

    return cardinality


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in spatial_join config must be a dict.")

    return {
        "source_index_field": str(fields.get("source_index_field", "_source_index")),
        "target_index_field": str(fields.get("target_index_field", "_target_index")),
        "status_field": str(fields.get("status_field", "_join_status")),
        "engine_field": str(fields.get("engine_field", "_join_engine")),
        "predicate_field": str(fields.get("predicate_field", "_join_predicate")),
        "join_type_field": str(fields.get("join_type_field", "_join_type")),
        "joined_count_field": str(fields.get("joined_count_field", "_joined_count")),
        "target_properties_field": str(
            fields.get("target_properties_field", "_joined_target_properties")
        ),
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


def _extract_features(input_data: Any, label: str = "features") -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_position(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _iter_positions(coords: Any) -> list[tuple[float, float]]:
    if _is_position(coords):
        return [(float(coords[0]), float(coords[1]))]

    if isinstance(coords, (list, tuple)):
        result: list[tuple[float, float]] = []
        for item in coords:
            result.extend(_iter_positions(item))
        return result

    return []


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    if geometry is None:
        return None

    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict/object or null.")

    if geometry.get("type") == "GeometryCollection":
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
    if not a or not b:
        return False

    return not (
        a[2] < b[0] - EPSILON
        or a[0] > b[2] + EPSILON
        or a[3] < b[1] - EPSILON
        or a[1] > b[3] + EPSILON
    )


def _bbox_within(inner: list[float] | None, outer: list[float] | None) -> bool:
    if not inner or not outer:
        return False

    return (
        inner[0] >= outer[0] - EPSILON
        and inner[1] >= outer[1] - EPSILON
        and inner[2] <= outer[2] + EPSILON
        and inner[3] <= outer[3] + EPSILON
    )


def _python_predicate(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
    predicate: str,
) -> bool:
    predicate = _validate_predicate(predicate)

    source_bbox = _geometry_bbox(source_geometry)
    target_bbox = _geometry_bbox(target_geometry)

    if predicate == "intersects":
        return _bboxes_intersect(source_bbox, target_bbox)

    if predicate == "within":
        return _bbox_within(source_bbox, target_bbox)

    if predicate == "contains":
        return _bbox_within(target_bbox, source_bbox)

    raise ValueError(f"Unsupported predicate: {predicate}")


def _get_shapely_tools():
    try:
        from shapely.geometry import shape
    except ImportError as exc:
        raise SDKDependencyError(
            "spatial_join requires 'shapely' for this engine. "
            "Install it with: pip install shapely"
        ) from exc

    return shape


def _shapely_predicate(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
    predicate: str,
) -> bool:
    predicate = _validate_predicate(predicate)

    if source_geometry is None or target_geometry is None:
        return False

    if not isinstance(source_geometry, dict) or not isinstance(target_geometry, dict):
        raise ValueError("source_geometry and target_geometry must be dict/object or null.")

    shape = _get_shapely_tools()

    try:
        source_geom = shape(source_geometry)
        target_geom = shape(target_geometry)
    except Exception as exc:
        raise ValueError(f"cannot build shapely geometry: {exc}") from exc

    if source_geom.is_empty or target_geom.is_empty:
        return False

    if predicate == "intersects":
        return bool(source_geom.intersects(target_geom))

    if predicate == "within":
        return bool(source_geom.within(target_geom))

    if predicate == "contains":
        return bool(source_geom.contains(target_geom))

    raise ValueError(f"Unsupported predicate: {predicate}")


def _evaluate_predicate(
    source_geometry: dict[str, Any] | None,
    target_geometry: dict[str, Any] | None,
    predicate: str,
    engine: str,
) -> tuple[bool, str]:
    engine = _validate_engine(engine)

    if engine == "python":
        return _python_predicate(source_geometry, target_geometry, predicate), "python"

    if engine == "shapely":
        return _shapely_predicate(source_geometry, target_geometry, predicate), "shapely"

    try:
        return _shapely_predicate(source_geometry, target_geometry, predicate), "shapely"
    except SDKDependencyError:
        return _python_predicate(source_geometry, target_geometry, predicate), "python"


def _is_geographic_crs(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    text = value.strip().upper()
    return text in {"EPSG:4326", "CRS:84", "OGC:CRS84"}


def _apply_target_properties(
    properties: dict[str, Any],
    *,
    target_feature: dict[str, Any] | None,
    fields: dict[str, str],
    include_target_properties: bool,
    flatten_target_properties: bool,
    target_property_prefix: str,
) -> None:
    if target_feature is None:
        return

    target_props = deepcopy(target_feature.get("properties") or {})

    if include_target_properties:
        properties[fields["target_properties_field"]] = target_props

    if flatten_target_properties:
        for key, value in target_props.items():
            properties[f"{target_property_prefix}{key}"] = value


def _make_join_feature(
    *,
    source_feature: dict[str, Any],
    source_index: int,
    target_feature: dict[str, Any] | None,
    target_index: int | None,
    joined_count: int,
    predicate: str,
    join_type: str,
    engine_used: str,
    status: str,
    fields: dict[str, str],
    preserve_properties: bool,
    include_target_properties: bool,
    flatten_target_properties: bool,
    target_property_prefix: str,
    reason: str | None = None,
) -> dict[str, Any]:
    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}

    properties[fields["source_index_field"]] = source_index
    properties[fields["target_index_field"]] = target_index
    properties[fields["status_field"]] = status
    properties[fields["engine_field"]] = engine_used
    properties[fields["predicate_field"]] = predicate
    properties[fields["join_type_field"]] = join_type
    properties[fields["joined_count_field"]] = joined_count

    _apply_target_properties(
        properties,
        target_feature=target_feature,
        fields=fields,
        include_target_properties=include_target_properties,
        flatten_target_properties=flatten_target_properties,
        target_property_prefix=target_property_prefix,
    )

    if reason:
        properties["_join_reason"] = reason

    return {
        "type": "Feature",
        "geometry": source_feature.get("geometry"),
        "properties": properties,
    }


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
    name="spatial_join_features",
    keywords=[
        "spatial join",
        "join features",
        "join attributes",
        "attribute transfer",
        "overlay join",
        "join by location",
        "intersects join",
        "within join",
        "اتصال مکانی",
        "جوین مکانی",
        "انتقال خصوصیات",
        "اتصال لایه‌ها",
        "اتصال بر اساس مکان",
    ],
    description="Join target attributes into source vector features based on spatial predicates.",
    required_inputs=["source_features", "target_features"],
    optional_inputs=[
        "predicate",
        "join_type",
        "cardinality",
        "engine",
        "drop_failed",
        "include_target_properties",
        "flatten_target_properties",
        "target_property_prefix",
        "source_crs",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "spatial_join",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_exact_predicates": True,
        "python_engine": "bbox_fallback",
        "routable": True,
    },
)
def spatial_join_features(
    source_features: Any,
    target_features: Any,
    predicate: str | None = None,
    join_type: str | None = None,
    cardinality: str | None = None,
    engine: str | None = None,
    drop_failed: bool | None = None,
    include_target_properties: bool | None = None,
    flatten_target_properties: bool | None = None,
    target_property_prefix: str | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Spatially join target attributes into source features.

    Args:
        source_features:
            Source VectorOut, FeatureCollection, Feature, or list[Feature].
        target_features:
            Target VectorOut, FeatureCollection, Feature, or list[Feature].
        predicate:
            intersects | within | contains.
        join_type:
            inner | left.
        cardinality:
            first | one_to_many.
        engine:
            auto | shapely | python.
        drop_failed:
            If True, failed source-target checks are ignored/dropped.
        include_target_properties:
            Include target properties as nested dict.
        flatten_target_properties:
            Copy target properties into source properties using prefix.
        target_property_prefix:
            Prefix used when flatten_target_properties=True.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut containing joined source features.
    """
    config = _load_join_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_predicate = _validate_predicate(
        str(pick_first(predicate, config.get("default_predicate"), default="intersects"))
    )

    final_join_type = _validate_join_type(
        str(pick_first(join_type, config.get("default_join_type"), default="inner"))
    )

    final_cardinality = _validate_cardinality(
        str(pick_first(cardinality, config.get("default_cardinality"), default="first"))
    )

    final_drop_failed = bool(
        pick_first(drop_failed, config.get("drop_failed"), default=False)
    )

    preserve_properties = bool(config.get("preserve_properties", True))

    final_include_target_properties = bool(
        pick_first(include_target_properties, config.get("include_target_properties"), default=True)
    )

    final_flatten_target_properties = bool(
        pick_first(flatten_target_properties, config.get("flatten_target_properties"), default=False)
    )

    final_target_property_prefix = str(
        pick_first(target_property_prefix, config.get("target_property_prefix"), default="target_")
    )

    final_source_crs = pick_first(source_crs, config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    fields = _configured_fields(config)

    source_items, source_info = _extract_features(source_features, label="source")
    target_items, target_info = _extract_features(target_features, label="target")

    if not target_items:
        raise ValueError("target_features must contain at least one feature.")

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    pair_count = 0
    matched_pair_count = 0
    unmatched_source_count = 0
    matched_source_count = 0
    failed_pair_count = 0
    dropped_failed_count = 0

    for source_index, source_feature in enumerate(source_items):
        matches: list[tuple[int, dict[str, Any], str]] = []
        last_engine_used = final_engine
        last_error: str | None = None

        for target_index, target_feature in enumerate(target_items):
            pair_count += 1

            try:
                matched, engine_used = _evaluate_predicate(
                    source_geometry=source_feature.get("geometry"),
                    target_geometry=target_feature.get("geometry"),
                    predicate=final_predicate,
                    engine=final_engine,
                )

                engines_used.add(engine_used)
                last_engine_used = engine_used

                if matched:
                    matched_pair_count += 1
                    matches.append((target_index, target_feature, engine_used))

            except Exception as exc:
                failed_pair_count += 1
                last_error = str(exc)
                engines_used.add(final_engine)

                if final_drop_failed:
                    dropped_failed_count += 1
                    continue

        if not matches:
            unmatched_source_count += 1

            if final_join_type == "inner":
                continue

            output_features.append(
                _make_join_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=None,
                    target_index=None,
                    joined_count=0,
                    predicate=final_predicate,
                    join_type=final_join_type,
                    engine_used=last_engine_used,
                    status="unmatched",
                    fields=fields,
                    preserve_properties=preserve_properties,
                    include_target_properties=final_include_target_properties,
                    flatten_target_properties=final_flatten_target_properties,
                    target_property_prefix=final_target_property_prefix,
                    reason=last_error or "source feature did not match any target feature",
                )
            )
            continue

        matched_source_count += 1

        selected_matches = matches[:1] if final_cardinality == "first" else matches

        for target_index, target_feature, engine_used in selected_matches:
            output_features.append(
                _make_join_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=target_feature,
                    target_index=target_index,
                    joined_count=len(matches),
                    predicate=final_predicate,
                    join_type=final_join_type,
                    engine_used=engine_used,
                    status="matched",
                    fields=fields,
                    preserve_properties=preserve_properties,
                    include_target_properties=final_include_target_properties,
                    flatten_target_properties=final_flatten_target_properties,
                    target_property_prefix=final_target_property_prefix,
                )
            )

    stats = _build_vector_metadata(output_features)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Spatial join is being evaluated on a geographic CRS. "
            "For metric overlay workflows, consider reprojecting first."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "spatial_join",
        "loader": PLUGIN_ID,
        "operation": "spatial_join",
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "predicate": final_predicate,
        "join_type": final_join_type,
        "cardinality": final_cardinality,
        "drop_failed": final_drop_failed,
        "include_target_properties": final_include_target_properties,
        "flatten_target_properties": final_flatten_target_properties,
        "target_property_prefix": final_target_property_prefix,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "source_feature_count": len(source_items),
        "target_feature_count": len(target_items),
        "pair_count": pair_count,
        "matched_pair_count": matched_pair_count,
        "matched_source_count": matched_source_count,
        "unmatched_source_count": unmatched_source_count,
        "failed_pair_count": failed_pair_count,
        "dropped_failed_count": dropped_failed_count,
        "output_feature_count": len(output_features),
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
    name="Spatial Join",
    description=(
        "Joins target attributes into source vector features using spatial predicates. "
        "Uses shapely for exact predicates and bbox fallback otherwise."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
