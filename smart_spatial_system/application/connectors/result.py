"""
smart_spatial_system.application.connectors.result

The normalized result every connector returns, plus helpers that build its
metadata from the raw plugin output (VectorOut / RasterOut / table rows).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smart_spatial_system.shared.jsonable import as_feature_collection, to_jsonable
from smart_spatial_system.shared.net_policy import redact_url

# Parameter names whose values must never be echoed back (provenance, logs, errors).
SECRET_PARAMS = frozenset(
    {"dsn", "password", "passwd", "user", "token", "api_key", "key", "secret", "headers",
     "authorization", "credentials", "connection_string", "host", "port"}
)
KINDS = ("vector", "raster", "table")


def redact_ref(params: dict[str, Any]) -> dict[str, Any]:
    """Provenance-safe copy of the call parameters (no credentials, no URL secrets)."""
    ref: dict[str, Any] = {}
    for name, value in params.items():
        if name in SECRET_PARAMS:
            ref[name] = "<redacted>"
        elif isinstance(value, str) and "://" in value:
            ref[name] = redact_url(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            ref[name] = value
        else:
            ref[name] = f"<{type(value).__name__}>"
    return ref


def _walk_bounds(coords: Any, box: list[float]) -> None:
    if isinstance(coords, (list, tuple)) and coords:
        if isinstance(coords[0], (int, float)) and len(coords) >= 2:
            x, y = float(coords[0]), float(coords[1])
            box[0], box[1] = min(box[0], x), min(box[1], y)
            box[2], box[3] = max(box[2], x), max(box[3], y)
        else:
            for item in coords:
                _walk_bounds(item, box)


def vector_summary(features: list[dict[str, Any]]) -> dict[str, Any]:
    geometry_types: dict[str, int] = {}
    box = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    fields: dict[str, str] = {}
    seen_names: set[str] = set()
    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        if isinstance(geometry, dict):
            kind = str(geometry.get("type") or "Unknown")
            geometry_types[kind] = geometry_types.get(kind, 0) + 1
            _walk_bounds(geometry.get("coordinates"), box)
        else:
            geometry_types["Null"] = geometry_types.get("Null", 0) + 1
        for name, value in ((feature.get("properties") or {}) if isinstance(feature, dict) else {}).items():
            if value is None or str(name) in fields:
                continue
            fields[str(name)] = (
                "boolean" if isinstance(value, bool)
                else "number" if isinstance(value, (int, float))
                else "string" if isinstance(value, str)
                else "object"
            )
        for name in (feature.get("properties") or {}) if isinstance(feature, dict) else ():
            seen_names.add(str(name))
    for name in seen_names:  # properties that were null everywhere
        fields.setdefault(name, "unknown")
    bbox = box if box[0] != float("inf") else None
    return {
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bbox": bbox,
        "fields": [{"name": n, "type": t} for n, t in fields.items()],
    }


def raster_summary(data: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    shape: list[int] = []
    node = data
    while isinstance(node, list) and len(shape) < 4:
        shape.append(len(node))
        node = node[0] if node else None
    summary: dict[str, Any] = {"shape": shape}
    for key in ("width", "height", "band_count", "transform", "nodata", "bounds", "dtype"):
        if key in metadata:
            summary[key] = metadata[key]
    return summary


@dataclass
class ConnectorResult:
    """Normalized connector output. ``data`` is the raw plugin object; ``metadata`` is JSON-safe."""

    kind: str
    source_type: str
    capability: str
    data: Any
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_data: bool = True, max_features: int | None = None) -> dict[str, Any]:
        """JSON-safe form. Vector data is a GeoJSON FeatureCollection (optionally truncated)."""
        payload: dict[str, Any] = {
            "kind": self.kind,
            "source_type": self.source_type,
            "capability": self.capability,
            "metadata": self.metadata,
        }
        if not include_data:
            return payload
        data = as_feature_collection(to_jsonable(self.data))
        if self.kind == "vector" and isinstance(data, dict) and isinstance(data.get("features"), list):
            if max_features is not None and len(data["features"]) > max_features:
                data = {**data, "features": data["features"][:max_features]}
                payload["metadata"] = {**self.metadata, "preview_truncated": True}
        payload["data"] = data
        return payload


def normalize(
    raw: Any,
    *,
    kind: str,
    source_type: str,
    capability: str,
    params: dict[str, Any],
    elapsed_ms: int,
    limit: int | None,
    fetched_at: str,
) -> ConnectorResult:
    """Build a ConnectorResult from a plugin's raw return value."""
    metadata: dict[str, Any] = {
        "source_type": source_type,
        "fetched_at": fetched_at,
        "elapsed_ms": elapsed_ms,
        "source_ref": redact_ref(params),
        "warnings": [],
    }
    plugin_meta = getattr(raw, "metadata", None)
    plugin_meta = plugin_meta if isinstance(plugin_meta, dict) else (
        raw.get("metadata") if isinstance(raw, dict) and isinstance(raw.get("metadata"), dict) else {}
    )
    crs = plugin_meta.get("crs") or plugin_meta.get("output_srid") or plugin_meta.get("srs_name")
    if crs is not None:
        metadata["crs"] = f"EPSG:{crs}" if isinstance(crs, int) else str(crs)

    if kind == "vector":
        features = getattr(raw, "features", None)
        if features is None and isinstance(raw, dict):
            features = raw.get("features")
        if not isinstance(features, list):
            raise TypeError(f"{capability} returned no feature list for a vector connector.")
        metadata.update(vector_summary(features))
        metadata["truncated"] = bool(plugin_meta.get("truncated")) or (
            limit is not None and len(features) >= limit
        )
        if metadata["truncated"]:
            metadata["warnings"].append("Result may be truncated by the row limit.")
    elif kind == "raster":
        data = getattr(raw, "data", None)
        if data is None and isinstance(raw, dict):
            data = raw.get("data")
        metadata.update(raster_summary(data, plugin_meta))
        path = getattr(raw, "path", None)
        if path:
            metadata["has_file"] = True
    else:
        rows = raw if isinstance(raw, list) else (raw.get("rows") if isinstance(raw, dict) else None)
        if not isinstance(rows, list):
            raise TypeError(f"{capability} returned no rows for a table connector.")
        metadata["row_count"] = len(rows)
    return ConnectorResult(kind=kind, source_type=source_type, capability=capability, data=raw,
                           metadata=metadata)
