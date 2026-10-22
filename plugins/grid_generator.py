"""
grid_generator.py

GeoChat SDK Plugin
==================

Plugin ID:
    grid_generator

Capability:
    - generate_grid:
        Create a regular square or hexagonal grid of polygons over a bounding
        box or over the extent of input features. Grids are the standard base
        for binning points, density maps and equal-area comparisons.

Config-aware behavior:
    Reads config/plugins/grid_generator.yaml. ``max_cells`` (default 100000)
    is a hard limit: requests that would create more cells fail before any
    geometry is built.

Important:
    Coordinates are planar. ``cell_size`` is in coordinate units, so use a
    projected (metric) CRS for meaningful sizes; degrees on lon/lat data are
    legal but cells are not equal-area.
"""

from __future__ import annotations

import math
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    get_shapely,
    load_config,
    make_vector_out,
    to_float,
    to_int,
    validate_choice,
)

PLUGIN_ID = "grid_generator"

DEFAULT_MAX_CELLS = 100_000
HARD_MAX_CELLS = 1_000_000
GRID_TYPES = {"square", "hex"}
_SQRT3 = math.sqrt(3.0)


def _normalize_bbox(bbox: Any) -> tuple[float, float, float, float]:
    if isinstance(bbox, dict):
        try:
            bbox = [bbox["minx"], bbox["miny"], bbox["maxx"], bbox["maxy"]]
        except KeyError as exc:
            raise ValueError("bbox dict needs minx, miny, maxx, maxy.") from exc
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("bbox must be [minx, miny, maxx, maxy].")
    minx, miny, maxx, maxy = (to_float(v, "bbox") for v in bbox)
    if not (minx < maxx and miny < maxy):
        raise ValueError("bbox must satisfy minx < maxx and miny < maxy.")
    return minx, miny, maxx, maxy


def _extent_of(features: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    shape, _, _ = get_shapely(PLUGIN_ID)
    bounds = [shape(f["geometry"]).bounds for f in features if f.get("geometry")]
    if not bounds:
        raise ValueError("Cannot derive a grid extent from features without geometry.")
    minx = min(b[0] for b in bounds)
    miny = min(b[1] for b in bounds)
    maxx = max(b[2] for b in bounds)
    maxy = max(b[3] for b in bounds)
    if minx == maxx or miny == maxy:  # a single point or a straight line
        raise ValueError("Features have a degenerate extent; pass an explicit bbox instead.")
    return minx, miny, maxx, maxy


def _square_cells(extent, size):
    minx, miny, maxx, maxy = extent
    cols = max(1, math.ceil((maxx - minx) / size))
    rows = max(1, math.ceil((maxy - miny) / size))
    return rows, cols


def _hex_dims(extent, size):
    minx, miny, maxx, maxy = extent
    radius = size / _SQRT3
    row_step = 1.5 * radius
    cols = max(1, math.ceil((maxx - minx) / size) + 1)
    rows = max(1, math.ceil((maxy - miny) / row_step) + 1)
    return rows, cols, radius, row_step


def _hexagon(cx: float, cy: float, radius: float) -> list[list[float]]:
    ring = []
    for k in range(6):  # pointy-top
        angle = math.radians(60 * k - 30)
        ring.append([cx + radius * math.cos(angle), cy + radius * math.sin(angle)])
    ring.append(ring[0])
    return ring


@capability(
    name="generate_grid",
    keywords=[
        "grid", "fishnet", "hexagon grid", "hex bin", "square grid", "binning grid", "tessellation",
        "شبکه", "شبکه منظم", "شبکه شش ضلعی", "هگزاگون", "شبکه مربعی", "فیشنت", "سلول بندی",
    ],
    description=(
        "Generate a regular square or hexagonal polygon grid over a bounding box or over the "
        "extent of input features (optionally keeping only cells that touch them)."
    ),
    required_inputs=["cell_size"],
    optional_inputs=["features", "bbox", "grid_type", "clip_to_features", "max_cells", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "generate_grid",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def generate_grid(
    cell_size: float,
    features: Any = None,
    bbox: list[float] | dict[str, float] | None = None,
    grid_type: str = "square",
    clip_to_features: bool = False,
    max_cells: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Generate a grid of polygons.

    Args:
        cell_size: Square: side length. Hex: distance between opposite edges
            (flat-to-flat). In coordinate units.
        features: Source of the extent (and of the optional clip). Ignored for the
            extent when ``bbox`` is given.
        bbox: [minx, miny, maxx, maxy] (or dict) extent.
        grid_type: ``square`` or ``hex`` (pointy-top hexagons).
        clip_to_features: Keep only cells intersecting the input features.
        max_cells: Lower the configured cell limit for this call.
        metadata: Extra output metadata.

    Returns:
        VectorOut with Polygon cells carrying ``cell_id``, ``row``, ``col``, ``cell_area``.
    """
    config = load_config(PLUGIN_ID)
    size = to_float(cell_size, "cell_size")
    if size <= 0 or not math.isfinite(size):
        raise ValueError("cell_size must be a positive number.")
    kind = validate_choice(grid_type, GRID_TYPES, "grid_type")
    limit = min(
        to_int(config.get("max_cells", DEFAULT_MAX_CELLS), "max_cells"),
        to_int(max_cells, "max_cells") if max_cells is not None else HARD_MAX_CELLS,
        HARD_MAX_CELLS,
    )

    input_features: list[dict[str, Any]] = []
    source_info: dict[str, Any] = {}
    if features is not None:
        input_features, source_info = extract_features(features)
    if bbox is not None:
        extent = _normalize_bbox(bbox)
    elif input_features:
        extent = _extent_of(input_features)
    else:
        raise ValueError("Provide bbox or features to define the grid extent.")

    if kind == "square":
        rows, cols = _square_cells(extent, size)
    else:
        rows, cols, radius, row_step = _hex_dims(extent, size)
    check_limit(rows * cols, limit, "grid cell count")

    shape, _, unary_union = get_shapely(PLUGIN_ID)
    clip_union = None
    if clip_to_features:
        if not input_features:
            raise ValueError("clip_to_features needs input features.")
        from shapely import prepare

        clip_union = unary_union([shape(f["geometry"]) for f in input_features if f.get("geometry")])
        prepare(clip_union)

    minx, miny, _, _ = extent
    cells: list[dict[str, Any]] = []
    cell_area = size * size if kind == "square" else (_SQRT3 / 2.0) * size * size
    for row in range(rows):
        for col in range(cols):
            if kind == "square":
                x0, y0 = minx + col * size, miny + row * size
                ring = [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]
            else:
                cx = minx + col * size + (size / 2.0 if row % 2 else 0.0)
                cy = miny + row * row_step
                ring = _hexagon(cx, cy, radius)
            geometry = {"type": "Polygon", "coordinates": [ring]}
            if clip_union is not None and not clip_union.intersects(shape(geometry)):
                continue
            cells.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {
                        "cell_id": f"{row}_{col}",
                        "row": row,
                        "col": col,
                        "cell_area": cell_area,
                    },
                }
            )

    return make_vector_out(
        cells,
        plugin_id=PLUGIN_ID,
        operation="generate_grid",
        source_info=source_info,
        extra={
            "grid_type": kind,
            "cell_size": size,
            "extent": list(extent),
            "rows": rows,
            "cols": cols,
            "clip_to_features": bool(clip_to_features),
            "dropped_cells": rows * cols - len(cells),
        },
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Grid Generator",
    description="Generates square or hexagonal polygon grids over an extent or input features.",
    author="GeoChat Platform Team",
    permissions=[],
)
