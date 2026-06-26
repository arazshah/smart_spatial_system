"""
Vector GeoJSON helper functions.

These helpers find and summarize GeoJSON-like payloads inside arbitrary inputs.
They contain no query orchestration and no plugin execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


GEOJSON_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


def read_geojson_path_if_possible(value: Any) -> dict[str, Any] | None:
    """
    Read a local GeoJSON-like path if value points to one.
    """
    try:
        if not isinstance(value, str):
            return None

        path = Path(value)

        if not path.exists() or not path.is_file():
            return None

        if path.suffix.lower() not in {".geojson", ".json"}:
            return None

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception:
        return None

    return None


def find_geojson_like(
    obj: Any,
    *,
    max_depth: int = 8,
) -> dict[str, Any] | None:
    """
    Recursively find a GeoJSON FeatureCollection/Feature/Geometry in inputs.

    Handles common shapes:
    - {"type": "FeatureCollection", ...}
    - {"geojson": {...}}
    - {"payload": {...}}
    - {"data": {...}}
    - {"path": "/.../file.geojson"}
    - dataclass/object with __dict__
    """
    if max_depth < 0 or obj is None:
        return None

    if isinstance(obj, dict):
        geo_type = obj.get("type")

        if geo_type == "FeatureCollection":
            features = obj.get("features")
            if isinstance(features, list):
                return obj

        if geo_type == "Feature":
            return {
                "type": "FeatureCollection",
                "features": [obj],
            }

        if geo_type in GEOJSON_GEOMETRY_TYPES:
            return {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": obj,
                    }
                ],
            }

        for path_key in [
            "path",
            "file_path",
            "local_path",
            "stored_path",
            "absolute_path",
            "source_path",
        ]:
            loaded = read_geojson_path_if_possible(obj.get(path_key))
            if loaded is not None:
                found = find_geojson_like(loaded, max_depth=max_depth - 1)
                if found is not None:
                    return found

        priority_keys = [
            "geojson",
            "feature_collection",
            "payload",
            "data",
            "vector",
            "vector_data",
            "content",
            "result",
            "output",
            "outputs",
            "inputs",
            "active_data",
        ]

        for key in priority_keys:
            if key in obj:
                found = find_geojson_like(obj.get(key), max_depth=max_depth - 1)
                if found is not None:
                    return found

        for value in obj.values():
            found = find_geojson_like(value, max_depth=max_depth - 1)
            if found is not None:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = find_geojson_like(item, max_depth=max_depth - 1)
            if found is not None:
                return found

    if isinstance(obj, str):
        loaded = read_geojson_path_if_possible(obj)
        if loaded is not None:
            return find_geojson_like(loaded, max_depth=max_depth - 1)

    if hasattr(obj, "__dict__"):
        try:
            return find_geojson_like(vars(obj), max_depth=max_depth - 1)
        except Exception:
            return None

    return None


def summarize_feature_collection(
    feature_collection: dict[str, Any],
) -> dict[str, Any]:
    """
    Lightweight summary for UI/debug.
    """
    raw_features = feature_collection.get("features") or []
    features = raw_features if isinstance(raw_features, list) else []

    geometry_counts: dict[str, int] = {}
    property_keys: set[str] = set()

    for feature in features:
        if not isinstance(feature, dict):
            continue

        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type") if isinstance(geometry, dict) else None
        geometry_type = str(geometry_type or "Unknown")
        geometry_counts[geometry_type] = geometry_counts.get(geometry_type, 0) + 1

        properties = feature.get("properties") or {}
        if isinstance(properties, dict):
            property_keys.update(str(key) for key in properties.keys())

    return {
        "feature_count": len(features),
        "geometry_counts": geometry_counts,
        "property_keys": sorted(property_keys),
    }
