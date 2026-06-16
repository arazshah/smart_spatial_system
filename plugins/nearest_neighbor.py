"""
nearest_neighbor.py

GeoChat SDK Plugin
==================

Plugin ID:
    nearest_neighbor

Purpose:
    Find k nearest target features for each source feature using planar distance.

Capability:
    - find_nearest_neighbors

Engines:
    - auto:
        Use shapely if available, otherwise pure-python fallback from distance_calculator.
    - shapely:
        Robust geometry distance through shapely.
    - python:
        Pure-python planar distance fallback.

Important:
    Calculations are planar, not geodesic. Reproject geographic data first
    using crs_transformer for reliable meter-based distances.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs
from plugins.distance_calculator import (
    _calculate_distance,
    _geometry_bbox,
    _is_geographic_crs,
    _validate_engine,
)


PLUGIN_ID = "nearest_neighbor"


def _load_nearest_config() -> dict[str, Any]:
    """
    Load config/plugins/nearest_neighbor.yaml if available.
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


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return coordinate/distance precision.
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


def _validate_k(value: Any, *, max_k: int | None = None) -> int:
    """
    Validate k nearest count.
    """
    if isinstance(value, bool):
        raise ValueError("k must be a positive integer.")

    try:
        k = int(value)
    except Exception as exc:
        raise ValueError("k must be a positive integer.") from exc

    if k <= 0:
        raise ValueError("k must be > 0.")

    if max_k is not None and k > max_k:
        raise ValueError(f"k is too large. Maximum allowed value is {max_k}.")

    return k


def _validate_max_distance(value: Any) -> float | None:
    """
    Validate max_distance.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("max_distance must be numeric or None.")

    try:
        distance = float(value)
    except Exception as exc:
        raise ValueError("max_distance must be numeric or None.") from exc

    if distance < 0:
        raise ValueError("max_distance must be >= 0.")

    return distance


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    """
    Return output field names.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in nearest_neighbor config must be a dict.")

    return {
        "distance_field": str(fields.get("distance_field", "_nearest_distance")),
        "rank_field": str(fields.get("rank_field", "_neighbor_rank")),
        "source_index_field": str(fields.get("source_index_field", "_source_index")),
        "target_index_field": str(fields.get("target_index_field", "_target_index")),
        "status_field": str(fields.get("status_field", "_nearest_status")),
        "engine_field": str(fields.get("engine_field", "_nearest_engine")),
        "target_properties_field": str(fields.get("target_properties_field", "_target_properties")),
        "target_geometry_field": str(fields.get("target_geometry_field", "_target_geometry")),
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


def _make_neighbor_feature(
    *,
    source_feature: dict[str, Any],
    source_index: int,
    target_feature: dict[str, Any] | None,
    target_index: int | None,
    distance: float | None,
    rank: int | None,
    engine_used: str,
    fields: dict[str, str],
    precision: int | None,
    preserve_properties: bool,
    include_target_geometry: bool,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Build output nearest-neighbor feature.
    """
    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}

    properties[fields["distance_field"]] = _round_value(distance, precision)
    properties[fields["rank_field"]] = rank
    properties[fields["source_index_field"]] = source_index
    properties[fields["target_index_field"]] = target_index
    properties[fields["status_field"]] = status
    properties[fields["engine_field"]] = engine_used

    if target_feature is not None:
        properties[fields["target_properties_field"]] = deepcopy(target_feature.get("properties") or {})

        if include_target_geometry:
            properties[fields["target_geometry_field"]] = deepcopy(target_feature.get("geometry"))

    if reason:
        properties["_nearest_reason"] = reason

    return {
        "type": "Feature",
        "geometry": source_feature.get("geometry"),
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
    name="find_nearest_neighbors",
    keywords=[
        "nearest neighbor",
        "nearest neighbours",
        "nearest feature",
        "closest feature",
        "k nearest",
        "knn",
        "proximity",
        "find closest",
        "نزدیکترین همسایه",
        "نزدیک‌ترین همسایه",
        "نزدیکترین عارضه",
        "نزدیک‌ترین عارضه",
        "k نزدیکترین",
        "تحلیل مجاورت",
    ],
    description="Find k nearest target features for each source vector feature.",
    required_inputs=["source_features", "target_features"],
    optional_inputs=[
        "k",
        "max_distance",
        "engine",
        "precision",
        "drop_unmatched",
        "include_target_geometry",
        "source_crs",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "nearest_neighbor",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_robust_geometry": True,
        "planar_only": True,
        "routable": True,
    },
)
def find_nearest_neighbors(
    source_features: Any,
    target_features: Any,
    k: int | None = None,
    max_distance: float | None = None,
    engine: str | None = None,
    precision: int | None = None,
    drop_unmatched: bool | None = None,
    include_target_geometry: bool | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Find k nearest target features for each source feature.

    Args:
        source_features:
            Source VectorOut, FeatureCollection, Feature, or list[Feature].
        target_features:
            Target VectorOut, FeatureCollection, Feature, or list[Feature].
        k:
            Number of nearest targets per source.
        max_distance:
            Optional maximum accepted distance.
        engine:
            auto | shapely | python.
        precision:
            Rounding precision for distance.
        drop_unmatched:
            If True, unmatched source features are removed.
        include_target_geometry:
            If True, target geometry is copied into output properties.
        source_crs:
            CRS hint. Used only for warning metadata.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut containing one output feature per matched neighbor.
    """
    config = _load_nearest_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    max_k_config = config.get("max_k", 100)
    max_k = None if max_k_config is None else int(max_k_config)

    final_k = _validate_k(
        pick_first(k, config.get("default_k"), default=1),
        max_k=max_k,
    )

    final_max_distance = _validate_max_distance(max_distance)

    final_precision = _configured_precision(config) if precision is None else precision
    if final_precision is not None:
        if isinstance(final_precision, bool):
            raise ValueError("precision must be an integer or None.")
        final_precision = int(final_precision)
        if final_precision < 0 or final_precision > 15:
            raise ValueError("precision must be between 0 and 15.")

    final_drop_unmatched = bool(
        pick_first(drop_unmatched, config.get("drop_unmatched"), default=False)
    )

    final_include_target_geometry = bool(
        pick_first(include_target_geometry, config.get("include_target_geometry"), default=False)
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

    pair_count = 0
    matched_source_count = 0
    unmatched_source_count = 0
    match_count = 0
    failed_pair_count = 0
    dropped_unmatched_count = 0

    for source_index, source_feature in enumerate(source_items):
        candidate_rows: list[tuple[float, int, dict[str, Any], str]] = []
        last_engine_used = final_engine
        last_error: str | None = None

        for target_index, target_feature in enumerate(target_items):
            pair_count += 1

            try:
                distance, engine_used = _calculate_distance(
                    source_geometry=source_feature.get("geometry"),
                    target_geometry=target_feature.get("geometry"),
                    engine=final_engine,
                )
                engines_used.add(engine_used)
                last_engine_used = engine_used

                if distance is None:
                    failed_pair_count += 1
                    continue

                if final_max_distance is not None and distance > final_max_distance:
                    continue

                candidate_rows.append((float(distance), target_index, target_feature, engine_used))

            except Exception as exc:
                failed_pair_count += 1
                last_error = str(exc)
                engines_used.add(final_engine)

        candidate_rows.sort(key=lambda item: (item[0], item[1]))
        selected = candidate_rows[:final_k]

        if not selected:
            unmatched_source_count += 1

            if final_drop_unmatched:
                dropped_unmatched_count += 1
                continue

            output_features.append(
                _make_neighbor_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=None,
                    target_index=None,
                    distance=None,
                    rank=None,
                    engine_used=last_engine_used,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    include_target_geometry=final_include_target_geometry,
                    status="unmatched",
                    reason=last_error or "no target feature matched nearest-neighbor constraints",
                )
            )
            continue

        matched_source_count += 1

        for rank, (distance, target_index, target_feature, engine_used) in enumerate(selected, start=1):
            match_count += 1

            output_features.append(
                _make_neighbor_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=target_feature,
                    target_index=target_index,
                    distance=distance,
                    rank=rank,
                    engine_used=engine_used,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    include_target_geometry=final_include_target_geometry,
                    status="matched",
                )
            )

    stats = _build_vector_metadata(output_features)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Nearest-neighbor distance is being calculated on a geographic CRS. "
            "Reproject to a projected CRS for reliable physical distance values."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "nearest_neighbor",
        "loader": PLUGIN_ID,
        "operation": "nearest_neighbor",
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "k": final_k,
        "max_distance": final_max_distance,
        "coordinate_precision": final_precision,
        "drop_unmatched": final_drop_unmatched,
        "include_target_geometry": final_include_target_geometry,
        "source_crs": final_source_crs,
        "planar_only": True,
        "warning": geographic_warning,
        "source_feature_count": len(source_items),
        "target_feature_count": len(target_items),
        "pair_count": pair_count,
        "match_count": match_count,
        "matched_source_count": matched_source_count,
        "unmatched_source_count": unmatched_source_count,
        "failed_pair_count": failed_pair_count,
        "dropped_unmatched_count": dropped_unmatched_count,
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
    name="Nearest Neighbor",
    description=(
        "Finds k nearest target features for each source feature using planar distance. "
        "Uses shapely when available and falls back to the pure-python distance engine."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
