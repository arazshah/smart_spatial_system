"""
connection_lines.py

GeoChat SDK Plugin
==================

Plugin ID:
    connection_lines

Purpose:
    Draw a straight LineString between each source feature and the target
    feature that find_nearest_neighbors matched to it.

Capability:
    - build_connection_lines:
        Nearest-neighbor output (run with include_target_geometry=true)
        -> LineString features, one per matched source/target pair.

Notes:
    The line endpoints are the source geometry and the ``_target_geometry``
    stored by nearest_neighbor. Non-point geometries are reduced to their
    centroid. Lines are built in the CRS of the input; pass ``source_crs`` and
    ``output_crs`` to reproject them back (e.g. metric CRS -> EPSG:4326) so they
    can be drawn on a web map. The distance column is taken from the nearest
    step, so it keeps the metric value computed before reprojection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, resolve_env_refs

PLUGIN_ID = "connection_lines"


def _load_config() -> dict[str, Any]:
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_features(input_data: Any) -> list[dict[str, Any]]:
    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw = getattr(input_data, "features")
    elif isinstance(input_data, dict):
        if input_data.get("type") == "FeatureCollection":
            raw = input_data.get("features", [])
        elif input_data.get("type") == "Feature":
            raw = [input_data]
        else:
            raise ValueError("features dict must be GeoJSON FeatureCollection or Feature.")
    elif isinstance(input_data, list):
        raw = input_data
    else:
        raise ValueError("features must be VectorOut, list, FeatureCollection dict or Feature dict.")

    if not isinstance(raw, list):
        raise ValueError("Extracted features must be a list.")
    return [f for f in raw if isinstance(f, dict)]


def _walk_positions(coords: Any) -> list[tuple[float, float]]:
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in coords[:2])
    ):
        return [(float(coords[0]), float(coords[1]))]

    out: list[tuple[float, float]] = []
    if isinstance(coords, (list, tuple)):
        for item in coords:
            out.extend(_walk_positions(item))
    return out


def _representative_point(geometry: Any) -> tuple[float, float] | None:
    """Return the point itself, or the centroid for non-point geometries."""
    if not isinstance(geometry, dict):
        return None

    gtype = geometry.get("type")
    if gtype == "Point":
        positions = _walk_positions(geometry.get("coordinates"))
        return positions[0] if positions else None

    try:
        from shapely.geometry import shape

        centroid = shape(geometry).centroid
        if not centroid.is_empty:
            return (float(centroid.x), float(centroid.y))
    except Exception:
        pass

    positions = _walk_positions(geometry.get("coordinates"))
    if not positions:
        return None
    return (
        sum(p[0] for p in positions) / len(positions),
        sum(p[1] for p in positions) / len(positions),
    )


def _lookup(properties: dict[str, Any], field: str | None, nested: str | None = None) -> Any:
    if not field:
        return None
    holder: Any = properties
    if nested:
        holder = properties.get(nested)
        if not isinstance(holder, dict):
            return None
    return holder.get(field)


@capability(
    name="build_connection_lines",
    description=(
        "Create a straight LineString between each source feature and its nearest target "
        "feature (output of find_nearest_neighbors with include_target_geometry=true), "
        "keeping source name, target name and distance."
    ),
    required_inputs=["features"],
    optional_inputs=[
        "source_name_field",
        "target_name_field",
        "distance_field",
        "target_geometry_field",
        "source_output_field",
        "target_output_field",
        "distance_output_field",
        "keep_source_properties",
        "source_crs",
        "output_crs",
        "metadata",
    ],
    output_kind="vector",
    keywords=[
        "line",
        "connection line",
        "connect",
        "straight line",
        "nearest line",
        "خط",
        "خط مستقیم",
        "خط اتصال",
        "ارتباط",
        "اتصال",
    ],
    metadata={
        "operation": "build_connection_lines",
        "domain": "vector",
        "module_name": "plugins.connection_lines",
    },
)
def build_connection_lines(
    features: Any,
    source_name_field: str | None = None,
    target_name_field: str | None = None,
    distance_field: str | None = None,
    target_geometry_field: str | None = None,
    source_output_field: str | None = None,
    target_output_field: str | None = None,
    distance_output_field: str | None = None,
    keep_source_properties: bool | None = None,
    source_crs: str | int | None = None,
    output_crs: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Build LineString features from nearest-neighbor output.

    Args:
        features:
            find_nearest_neighbors output run with include_target_geometry=true.
        source_name_field:
            Source property holding the source feature name (e.g. "name").
        target_name_field:
            Property inside ``_target_properties`` holding the target name.
        distance_field:
            Property with the distance, default ``_nearest_distance``.
        target_geometry_field:
            Property with the target geometry, default ``_target_geometry``.
        source_output_field / target_output_field / distance_output_field:
            Output column names, default ``source_name`` / ``target_name`` /
            ``distance_m``.
        keep_source_properties:
            Keep all source properties besides the three output columns.
        source_crs / output_crs:
            If both are given and differ, lines are reprojected to output_crs.
        metadata:
            Optional metadata to merge.
    """
    config = _load_config()
    final_distance_field = distance_field or config.get("distance_field", "_nearest_distance")
    final_target_geometry_field = target_geometry_field or config.get(
        "target_geometry_field", "_target_geometry"
    )
    final_source_out = source_output_field or config.get("source_output_field", "source_name")
    final_target_out = target_output_field or config.get("target_output_field", "target_name")
    final_distance_out = distance_output_field or config.get("distance_output_field", "distance_m")
    keep_props = bool(
        keep_source_properties
        if keep_source_properties is not None
        else config.get("keep_source_properties", False)
    )

    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    items = _extract_features(features)

    lines: list[dict[str, Any]] = []
    skipped = 0

    for feature in items:
        props = feature.get("properties")
        props = props if isinstance(props, dict) else {}

        start = _representative_point(feature.get("geometry"))
        end = _representative_point(props.get(final_target_geometry_field))

        if start is None or end is None:
            skipped += 1
            continue

        out_props: dict[str, Any] = {}
        if keep_props:
            out_props.update(props)
            for internal in ("_target_properties", final_target_geometry_field):
                out_props.pop(internal, None)

        out_props[final_source_out] = _lookup(props, source_name_field)
        out_props[final_target_out] = _lookup(props, target_name_field, "_target_properties")
        out_props[final_distance_out] = props.get(final_distance_field)

        lines.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [list(start), list(end)]},
                "properties": out_props,
            }
        )

    warnings: list[str] = []
    if skipped:
        warnings.append(
            f"{skipped} feature(s) skipped: missing source geometry or {final_target_geometry_field}. "
            "Run find_nearest_neighbors with include_target_geometry=true."
        )

    reprojected = False
    if source_crs and output_crs and str(source_crs).strip().upper() != str(output_crs).strip().upper():
        from plugins.crs_transformer import transform_vector_crs

        transformed = transform_vector_crs(
            lines, source_crs=source_crs, target_crs=output_crs
        )
        lines = list(transformed.features)
        reprojected = True

    output_metadata = {
        "source": PLUGIN_ID,
        "operation": "build_connection_lines",
        "input_feature_count": len(items),
        "line_count": len(lines),
        "skipped_count": skipped,
        "geometry_type": "LineString",
        "source_crs": str(source_crs) if source_crs else None,
        "output_crs": str(output_crs) if reprojected else (str(source_crs) if source_crs else None),
        "reprojected": reprojected,
        "warning": " | ".join(warnings) if warnings else None,
        "created_at": _utc_now_iso(),
        **(metadata or {}),
    }

    return VectorOut(features=lines, metadata=output_metadata)


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Connection Lines",
    description=(
        "Builds straight LineString connections between each source feature and its "
        "nearest target feature, with source name, target name and distance."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
