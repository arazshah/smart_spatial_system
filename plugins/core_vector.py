"""
core_vector.py

Core Vector Capabilities
========================

Plugin ID:
    core_vector

Purpose:
    Official capability layer for basic vector operations that were previously
    handled by service-level direct handlers.

Capabilities:
    - inspect_vector
    - display_vector_layer
    - summarize_vector_layer

These capabilities are intentionally lightweight and dependency-free.
They make simple vector inspection/display/summary auditable and routable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect


PLUGIN_ID = "core_vector"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(feature, dict):
        raise ValueError(f"Feature at index {index} must be a dict.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if properties is None:
        properties = {}

    if not isinstance(properties, dict):
        properties = {}

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": dict(properties),
    }


def _extract_feature_collection(vector: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Extract a GeoJSON FeatureCollection from:
      - GeoJSON FeatureCollection dict
      - GeoJSON Feature dict
      - list[Feature]
      - VectorOut-like object with .features
    """
    source_info: dict[str, Any] = {}

    if hasattr(vector, "features") and not isinstance(vector, (dict, list)):
        raw_features = getattr(vector, "features")
        source_info["input_type"] = type(vector).__name__

        metadata = getattr(vector, "metadata", None)
        if isinstance(metadata, dict):
            source_info["input_metadata"] = metadata

    elif isinstance(vector, dict):
        geojson_type = vector.get("type")
        source_info["input_geojson_type"] = geojson_type

        if geojson_type == "FeatureCollection":
            raw_features = vector.get("features", [])
            if not isinstance(raw_features, list):
                raise ValueError("FeatureCollection.features must be a list.")

            crs = vector.get("crs")
            if crs is not None:
                source_info["crs"] = crs

        elif geojson_type == "Feature":
            raw_features = [vector]
        else:
            raise ValueError("Input dict must be GeoJSON FeatureCollection or Feature.")

    elif isinstance(vector, list):
        raw_features = vector
        source_info["input_geojson_type"] = "FeatureList"

    else:
        raise ValueError(
            "vector must be a VectorOut-like object, FeatureCollection, Feature, or list[Feature]."
        )

    if not isinstance(raw_features, list):
        raise ValueError("Extracted features must be a list.")

    features = [
        _normalize_feature(item, idx)
        for idx, item in enumerate(raw_features)
    ]

    feature_collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
    }

    if isinstance(vector, dict) and vector.get("type") == "FeatureCollection":
        if vector.get("crs") is not None:
            feature_collection["crs"] = vector.get("crs")

    return feature_collection, source_info


def _walk_positions(coords: Any) -> list[list[float]]:
    positions: list[list[float]] = []

    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
        and not isinstance(coords[0], bool)
        and not isinstance(coords[1], bool)
    ):
        positions.append([float(coords[0]), float(coords[1])])
        return positions

    if isinstance(coords, (list, tuple)):
        for item in coords:
            positions.extend(_walk_positions(item))

    return positions


def _bbox_for_features(features: list[dict[str, Any]]) -> list[float] | None:
    xs: list[float] = []
    ys: list[float] = []

    for feature in features:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue

        for x, y in _walk_positions(geometry.get("coordinates")):
            xs.append(x)
            ys.append(y)

    if not xs or not ys:
        return None

    return [
        min(xs),
        min(ys),
        max(xs),
        max(ys),
    ]


def _summarize_features(features: list[dict[str, Any]]) -> dict[str, Any]:
    geometry_counts: dict[str, int] = {}
    property_keys: set[str] = set()
    sample_properties: list[dict[str, Any]] = []

    for feature in features:
        geometry = feature.get("geometry")
        geometry_type = "Unknown"

        if isinstance(geometry, dict):
            geometry_type = str(geometry.get("type") or "Unknown")

        geometry_counts[geometry_type] = geometry_counts.get(geometry_type, 0) + 1

        properties = feature.get("properties")
        if isinstance(properties, dict):
            property_keys.update(str(key) for key in properties.keys())

            if len(sample_properties) < 5:
                sample_properties.append(dict(properties))

    return {
        "feature_count": len(features),
        "geometry_counts": geometry_counts,
        "property_keys": sorted(property_keys),
        "bbox": _bbox_for_features(features),
        "sample_properties": sample_properties,
    }


@capability(
    name="inspect_vector",
    description="Inspect a GeoJSON-like vector layer and return feature count, geometry types, bbox and properties.",
    required_inputs=["vector"],
    optional_inputs=[],
    output_kind="json",
    keywords=[
        "inspect",
        "inspection",
        "vector inspect",
        "geojson inspect",
        "بررسی",
        "بازرسی",
        "اطلاعات لایه",
        "ساختار وکتور",
        "لایه وکتور",
    ],
    metadata={
        "operation": "inspect_vector",
        "domain": "vector",
        "module_name": "plugins.core_vector",
    },
)
def inspect_vector(
    vector: Any,
) -> dict[str, Any]:
    feature_collection, source_info = _extract_feature_collection(vector)
    features = feature_collection.get("features") or []

    summary = _summarize_features(features)

    return {
        "type": "vector_inspection",
        "status": "succeeded",
        "summary": summary,
        "feature_count": summary["feature_count"],
        "geometry_counts": summary["geometry_counts"],
        "property_keys": summary["property_keys"],
        "bbox": summary["bbox"],
        "sample_properties": summary["sample_properties"],
        "source_info": source_info,
        "created_at": _utc_now_iso(),
    }


@capability(
    name="display_vector_layer",
    description="Prepare a GeoJSON-like vector layer for map display.",
    required_inputs=["vector"],
    optional_inputs=["layer_id", "name", "visible"],
    output_kind="map_layer",
    keywords=[
        "display",
        "show",
        "map",
        "layer",
        "geojson",
        "vector",
        "نمایش",
        "نشان بده",
        "روی نقشه",
        "لایه",
        "نقاط",
        "وکتور",
    ],
    metadata={
        "operation": "display_vector_layer",
        "domain": "vector",
        "module_name": "plugins.core_vector",
    },
)
def display_vector_layer(
    vector: Any,
    layer_id: str = "active_vector",
    name: str | None = None,
    visible: bool = True,
) -> dict[str, Any]:
    feature_collection, source_info = _extract_feature_collection(vector)
    features = feature_collection.get("features") or []
    summary = _summarize_features(features)

    final_layer_id = str(layer_id or "active_vector")
    final_name = str(name or final_layer_id)

    layer = {
        "id": final_layer_id,
        "name": final_name,
        "type": "vector",
        "format": "geojson",
        "visible": bool(visible),
        "geojson": feature_collection,
        "summary": summary,
    }

    vector_output = {
        "id": final_layer_id,
        "name": final_name,
        "format": "geojson",
        "role": "map_layer",
        "geojson": feature_collection,
        "summary": summary,
    }

    return {
        "type": "vector_display",
        "status": "succeeded",
        "message": "Vector layer is ready for map display.",
        "layer": layer,
        "layers": [layer],
        "outputs": {
            "vectors": [vector_output],
            "rasters": [],
            "tables": [],
        },
        "layer_ids": [final_layer_id],
        "feature_count": summary["feature_count"],
        "geometry_counts": summary["geometry_counts"],
        "property_keys": summary["property_keys"],
        "summary": summary,
        "source_info": source_info,
        "created_at": _utc_now_iso(),
    }


@capability(
    name="summarize_vector_layer",
    description="Create a user-facing summary for a GeoJSON-like vector layer.",
    required_inputs=["vector"],
    optional_inputs=[],
    output_kind="json",
    keywords=[
        "summary",
        "summarize",
        "statistics",
        "count",
        "feature count",
        "geometry count",
        "خلاصه",
        "آمار",
        "تعداد",
        "چند feature",
        "چند عارضه",
        "لایه شامل",
    ],
    metadata={
        "operation": "summarize_vector_layer",
        "domain": "vector",
        "module_name": "plugins.core_vector",
    },
)
def summarize_vector_layer(
    vector: Any,
) -> dict[str, Any]:
    feature_collection, source_info = _extract_feature_collection(vector)
    features = feature_collection.get("features") or []
    summary = _summarize_features(features)

    feature_count = summary["feature_count"]
    geometry_counts = summary["geometry_counts"]

    geometry_text = ", ".join(
        f"{key}: {value}"
        for key, value in geometry_counts.items()
    ) or "No geometries"

    return {
        "type": "vector_summary",
        "status": "succeeded",
        "message": f"Vector layer contains {feature_count} features. {geometry_text}.",
        "feature_count": feature_count,
        "geometry_counts": geometry_counts,
        "property_keys": summary["property_keys"],
        "bbox": summary["bbox"],
        "summary": summary,
        "source_info": source_info,
        "created_at": _utc_now_iso(),
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Core Vector Capabilities",
    description=(
        "Official core vector capabilities for inspection, map display, "
        "and user-facing vector summaries."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
