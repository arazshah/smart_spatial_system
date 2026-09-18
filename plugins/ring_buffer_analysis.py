"""
ring_buffer_analysis.py

GeoChat SDK Plugin
==================

Plugin ID:
    ring_buffer_analysis

Purpose:
    Perform vector ring (annulus) buffer analysis on GeoJSON-like features.

Capability:
    - generate_ring_buffers:
        Create concentric ring polygons around vector features at multiple
        distances and return VectorOut. Unlike chaining buffer_vector_features
        multiple times - which only ever produces nested full-circle disks -
        this capability returns the actual annulus area BETWEEN consecutive
        radii, e.g. the ring between 200m and 500m, not the full 500m disk.

Config-aware behavior:
    Reads config/plugins/ring_buffer_analysis.yaml.

Engines:
    - shapely (or auto, when shapely is installed):
        Requires shapely. Builds a real buffer() at each sorted radius and
        subtracts consecutive buffers to produce true annulus geometry.
    - python:
        Not supported. There is no pure-python way to compute the difference
        between two polygons, so this engine always raises SDKDependencyError.

Important:
    GeoJSON coordinates are treated as planar coordinates. If coordinates are
    longitude/latitude degrees, distance is interpreted in coordinate units unless
    data has already been projected. Metadata stores the requested units, but no
    CRS transformation is performed in this plugin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import (
    load_plugin_config,
    pick_first,
    resolve_env_refs,
)
from plugins.buffer_analysis import (
    _build_vector_metadata,
    _configured_allowed_geometry_types,
    _extract_features,
    _get_shapely_tools,
    _to_float,
    _to_int,
    _validate_geometry_type,
)

PLUGIN_ID = "ring_buffer_analysis"

VALID_ENGINES = {"auto", "shapely", "python"}

_NO_SHAPELY_MESSAGE = "ring buffers require shapely - install shapely or use engine=shapely"


def _load_ring_buffer_config() -> dict[str, Any]:
    """
    Load config/plugins/ring_buffer_analysis.yaml if available.
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
    Validate ring buffer engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


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


def _validate_distances(distances: Any) -> list[float]:
    """
    Validate ring distances: must be a non-empty list of positive numbers,
    with no duplicates. Automatically sorted ascending.
    """
    if not isinstance(distances, (list, tuple)) or len(distances) == 0:
        raise ValueError("distances must be a non-empty list of positive numbers.")

    values: list[float] = []
    for item in distances:
        value = _to_float(item, "distances")
        if value <= 0:
            raise ValueError("distances must contain only positive numbers.")
        values.append(value)

    if len(set(values)) != len(values):
        raise ValueError("distances must not contain duplicate values.")

    return sorted(values)


def _format_ring_label(inner: float, outer: float, units: str) -> str:
    """
    Format a ring label, e.g. "0-200m".
    """

    def _fmt(value: float) -> str:
        if value == int(value):
            return str(int(value))
        return str(value)

    return f"{_fmt(inner)}-{_fmt(outer)}{units}"


def _ring_geometry_shapely(
    geometry: dict[str, Any] | None,
    inner_distance: float,
    outer_distance: float,
    quad_segs: int,
    cap_style: str,
    join_style: str,
    mitre_limit: float,
) -> dict[str, Any] | None:
    """
    Build a single annulus ring geometry between inner_distance and
    outer_distance using shapely. When inner_distance is 0, the ring is just
    the outer buffer itself (no subtraction needed).
    """
    if geometry is None:
        return None

    shape, mapping, _ = _get_shapely_tools()

    geom = shape(geometry)

    def _buffer_at(distance: float):
        try:
            return geom.buffer(
                distance,
                quad_segs=quad_segs,
                cap_style=cap_style,
                join_style=join_style,
                mitre_limit=mitre_limit,
            )
        except TypeError:
            # Compatibility with older shapely versions.
            return geom.buffer(
                distance,
                resolution=quad_segs,
                cap_style=cap_style,
                join_style=join_style,
                mitre_limit=mitre_limit,
            )

    outer_buffer = _buffer_at(outer_distance)

    if inner_distance <= 0:
        ring_geom = outer_buffer
    else:
        inner_buffer = _buffer_at(inner_distance)
        ring_geom = outer_buffer.difference(inner_buffer)

    return dict(mapping(ring_geom))


@capability(
    name="generate_ring_buffers",
    keywords=[
        "ring buffer",
        "annulus",
        "concentric buffer",
        "multi-ring buffer",
        "distance band",
        "buffer zones",
        "zonal buffer",
        "حلقه بافر",
        "بافر هم‌مرکز",
        "حریم چندگانه",
        "منطقه فاصله‌ای",
    ],
    description=(
        "Create concentric ring (annulus) polygons around vector features "
        "at multiple distances - the area between consecutive radii, not "
        "cumulative full-circle buffers."
    ),
    required_inputs=["features", "distances"],
    optional_inputs=[
        "units",
        "engine",
        "quad_segs",
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
        "operation": "ring_buffer",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def generate_ring_buffers(
    features: Any,
    distances: list[float] | None = None,
    units: str | None = None,
    quad_segs: int | None = None,
    engine: str | None = None,
    cap_style: str | None = None,
    join_style: str | None = None,
    mitre_limit: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Create concentric ring (annulus) polygons around vector features.

    Args:
        features:
            VectorOut, list[Feature], FeatureCollection dict or single Feature dict.
        distances:
            List of positive ring boundary distances. Automatically sorted
            ascending, so callers do not need to pre-sort. Consecutive values
            define one ring each: [200, 500, 800, 1200] produces rings
            0-200, 200-500, 500-800 and 800-1200.
        units:
            Distance units label used in metadata and ring_label. If None,
            config default_units is used.
        quad_segs:
            Number of segments per quarter circle.
        engine:
            auto | shapely | python. Only shapely (or auto when shapely is
            installed) can build real annulus geometry - python always
            raises SDKDependencyError.
        cap_style:
            Shapely cap style. Usually: round, flat, square.
        join_style:
            Shapely join style. Usually: round, mitre, bevel.
        mitre_limit:
            Shapely mitre limit.
        metadata:
            Optional metadata to merge into output metadata.

    Returns:
        VectorOut with one feature per (input feature x ring).
    """
    config = _load_ring_buffer_config()

    final_distances = _validate_distances(
        pick_first(distances, config.get("default_distances"), default=None)
    )

    final_units = str(pick_first(units, config.get("default_units"), default="m"))

    final_quad_segs = _validate_quad_segs(
        pick_first(quad_segs, config.get("default_quad_segs"), default=8)
    )

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    final_cap_style = str(pick_first(cap_style, config.get("default_cap_style"), default="round"))
    final_join_style = str(pick_first(join_style, config.get("default_join_style"), default="round"))
    final_mitre_limit = _to_float(
        pick_first(mitre_limit, config.get("default_mitre_limit"), default=5.0),
        "mitre_limit",
    )

    if final_engine == "python":
        raise SDKDependencyError(_NO_SHAPELY_MESSAGE)

    try:
        _get_shapely_tools()
    except SDKDependencyError as exc:
        raise SDKDependencyError(_NO_SHAPELY_MESSAGE) from exc

    allowed_geometry_types = _configured_allowed_geometry_types(config)
    preserve_properties = bool(config.get("preserve_properties", True))

    input_features, source_info = _extract_features(features)

    ring_boundaries = [0.0, *final_distances]

    ring_features: list[dict[str, Any]] = []

    for feature_index, feature in enumerate(input_features):
        geometry = feature.get("geometry")
        _validate_geometry_type(geometry, allowed_geometry_types)

        source_properties = dict(feature.get("properties") or {}) if preserve_properties else {}

        for ring_index in range(len(final_distances)):
            inner = ring_boundaries[ring_index]
            outer = ring_boundaries[ring_index + 1]

            ring_geometry = _ring_geometry_shapely(
                geometry=geometry,
                inner_distance=inner,
                outer_distance=outer,
                quad_segs=final_quad_segs,
                cap_style=final_cap_style,
                join_style=final_join_style,
                mitre_limit=final_mitre_limit,
            )

            properties = dict(source_properties)
            properties["ring_index"] = ring_index
            properties["ring_inner"] = inner
            properties["ring_outer"] = outer
            properties["ring_label"] = _format_ring_label(inner, outer, final_units)
            properties["source_feature_index"] = feature_index

            ring_features.append({
                "type": "Feature",
                "geometry": ring_geometry,
                "properties": properties,
            })

    stats = _build_vector_metadata(ring_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "ring_buffer_analysis",
        "loader": PLUGIN_ID,
        "operation": "ring_buffer",
        "distances": final_distances,
        "units": final_units,
        "quad_segs": final_quad_segs,
        "engine_requested": final_engine,
        "cap_style": final_cap_style,
        "join_style": final_join_style,
        "mitre_limit": final_mitre_limit,
        "input_feature_count": len(input_features),
        "ring_count": len(final_distances),
        "output_feature_count": len(ring_features),
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
        features=ring_features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Ring Buffer Analysis",
    description=(
        "Creates concentric ring (annulus) polygons around vector features at "
        "multiple distances - the area between consecutive radii, computed with "
        "shapely difference()."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
