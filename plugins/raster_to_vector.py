"""
raster_to_vector.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_to_vector

Purpose:
    Convert selected raster pixels/classes into vector polygon features.

Capability:
    - raster_to_vector

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Vectorization modes:
    - cells:
        each selected raster cell becomes one polygon feature.

    - components:
        connected selected cells become grouped features. Each component's
        geometry is its exact outline - the union of its cells, traced along
        cell edges, with holes - as a Polygon (or a MultiPolygon when cells
        of an 8-connected component touch only at a corner).

No external dependency is required.
"""

from __future__ import annotations

import math
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from plugins._shared.plugin_config import (
    load_plugin_config,
    pick_first,
    resolve_env_refs,
)
from plugins._shared.raster_numpy import (
    boundary_edges_by_label,
    is_ndarray,
    label_runs,
    numeric_band,
    resolve_engine,
)
from plugins.raster_clip_mask import (
    _array_shape,
    _extract_raster,
    _is_geographic_crs,
    _normalize_transform,
)

PLUGIN_ID = "raster_to_vector"

VALID_ENGINES = {"python", "numpy", "auto"}
VALID_MODES = {"cells", "components"}
VALID_CONNECTIVITY = {4, 8}

EPSILON = 1e-12


def _load_raster_to_vector_config() -> dict[str, Any]:
    """
    Load config/plugins/raster_to_vector.yaml if available.
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


def _validate_engine(engine: str) -> str:
    """
    Validate execution engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    value = engine.strip().lower()

    if value not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return value


def _validate_mode(mode: str) -> str:
    """
    Validate vectorization mode.
    """
    if not isinstance(mode, str) or not mode.strip():
        raise ValueError("mode must be a non-empty string.")

    value = mode.strip().lower()

    if value not in VALID_MODES:
        raise ValueError(f"Unsupported mode '{mode}'. Valid modes: {sorted(VALID_MODES)}")

    return value


def _validate_connectivity(value: Any) -> int:
    """
    Validate connectivity value.
    """
    if isinstance(value, bool):
        raise ValueError("connectivity must be 4 or 8.")

    try:
        connectivity = int(value)
    except Exception as exc:
        raise ValueError("connectivity must be 4 or 8.") from exc

    if connectivity not in VALID_CONNECTIVITY:
        raise ValueError("connectivity must be 4 or 8.")

    return connectivity


def _validate_precision(value: Any) -> int | None:
    """
    Validate coordinate precision.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("precision must be an integer or None.")

    try:
        precision = int(value)
    except Exception as exc:
        raise ValueError("precision must be an integer or None.") from exc

    if precision < 0 or precision > 15:
        raise ValueError("precision must be between 0 and 15.")

    return precision


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return configured precision.
    """
    return _validate_precision(config.get("coordinate_precision", 6))


def _round_value(value: Any, precision: int | None) -> Any:
    """
    Round float values.
    """
    if value is None:
        return None

    if not isinstance(value, float):
        return value

    if precision is None:
        return value

    return round(value, precision)


def _round_coord_pair(pair: tuple[float, float], precision: int | None) -> list[float]:
    """
    Round coordinate pair.
    """
    return [
        _round_value(float(pair[0]), precision),
        _round_value(float(pair[1]), precision),
    ]


def _is_number(value: Any) -> bool:
    """
    Return True for finite numbers excluding bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_nan(value: Any) -> bool:
    """
    Return True if value is NaN.
    """
    try:
        return isinstance(value, float) and math.isnan(value)
    except Exception:
        return False


def _is_nodata(value: Any, nodata: Any) -> bool:
    """
    Check nodata logic.
    """
    if value is None:
        return True

    if _is_nan(value):
        return True

    if nodata is None:
        return False

    if _is_nan(nodata):
        return _is_nan(value)

    try:
        return value == nodata
    except Exception:
        return False


def _values_equal(a: Any, b: Any) -> bool:
    """
    Equality helper with numeric tolerance.
    """
    if _is_number(a) and _is_number(b):
        return abs(float(a) - float(b)) <= EPSILON

    try:
        return a == b
    except Exception:
        return False


def _normalize_value_list(values: Any, *, name: str, allow_none: bool) -> list[Any] | None:
    """
    Normalize include/exclude value list.
    """
    if values is None:
        if allow_none:
            return None
        return []

    if isinstance(values, (str, bytes)):
        return [values]

    if isinstance(values, (list, tuple, set)):
        return list(values)

    return [values]


def _validate_band_index(value: Any, band_count: int) -> int:
    """
    Validate 1-based band index.
    """
    if isinstance(value, bool):
        raise ValueError("band_index must be a positive integer.")

    try:
        band_index = int(value)
    except Exception as exc:
        raise ValueError("band_index must be a positive integer.") from exc

    if band_index <= 0:
        raise ValueError("band_index must be >= 1.")

    if band_index > band_count:
        raise ValueError(f"band_index {band_index} is out of range. Raster has {band_count} band(s).")

    return band_index


def _validate_resolution(value: Any, *, name: str) -> float:
    """
    Validate positive pixel resolution.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive number.")

    try:
        result = float(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a positive number.") from exc

    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number.")

    return result


def _validate_max_features(value: Any) -> int | None:
    """
    Validate max_features.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("max_features must be a positive integer or None.")

    try:
        result = int(value)
    except Exception as exc:
        raise ValueError("max_features must be a positive integer or None.") from exc

    if result <= 0:
        raise ValueError("max_features must be a positive integer or None.")

    return result


def _as_bool(value: Any, default: bool) -> bool:
    """
    Convert common bool-like values.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False

    return bool(value)


def _band_value(
    data: Any,
    *,
    band_index: int,
    row: int,
    col: int,
    shape: tuple[int, int, int] | None = None,
) -> Any:
    """
    Read 1-based raster band value.

    `shape` lets callers pass a precomputed (bands, height, width) so this
    doesn't have to re-walk the whole array (via _array_shape) on every
    single pixel.
    """
    bands, _height, _width = shape if shape is not None else _array_shape(data)

    if bands == 1:
        # 2D raster
        if data and isinstance(data[0], list) and (not data[0] or not isinstance(data[0][0], list)):
            return data[row][col]

        # 3D single-band raster
        return data[0][row][col]

    return data[band_index - 1][row][col]


def _extract_transform(
    metadata: dict[str, Any],
    *,
    default_origin_x: Any,
    default_origin_y: Any,
    default_x_resolution: Any,
    default_y_resolution: Any,
) -> tuple[list[float], str]:
    """
    Extract affine-like transform.

    Expected transform:
        [a, b, c, d, e, f]

    Coordinate:
        x = a * col + b * row + c
        y = d * col + e * row + f

    Reuses raster_clip_mask._normalize_transform (fixed for bug #007) so a
    dict transform (or an "affine_transform" key) is parsed the same way
    clip/mask and zonal statistics parse it, instead of being silently
    ignored. If a transform is present but cannot be parsed, this raises a
    clear error rather than silently falling back. Unlike clip/mask and
    zonal statistics, raster_to_vector does no window-scanning that
    requires a non-rotated transform, so (deliberately, unlike
    raster_clip_mask._get_transform_from_metadata) it does not reject a
    rotated/axis-swapped transform - the per-pixel affine math in
    _pixel_corner works for any invertible affine transform.

    Only when there is genuinely no transform anywhere (no "transform" and
    no "affine_transform" key in metadata) is a north-up default transform
    built:
        [x_res, 0, origin_x, 0, -y_res, origin_y]
    """
    candidate = metadata.get("transform")
    if candidate is None:
        candidate = metadata.get("affine_transform")

    if candidate is not None:
        return _normalize_transform(candidate), "metadata_transform"

    x_res = _validate_resolution(default_x_resolution, name="x_resolution")
    y_res = _validate_resolution(default_y_resolution, name="y_resolution")

    try:
        origin_x = float(default_origin_x)
        origin_y = float(default_origin_y)
    except Exception as exc:
        raise ValueError("default origin values must be numeric.") from exc

    return [x_res, 0.0, origin_x, 0.0, -y_res, origin_y], "default_transform"


def _pixel_corner(
    *,
    transform: list[float],
    row: int,
    col: int,
) -> tuple[float, float]:
    """
    Convert pixel corner row/col to map coordinate.
    """
    a, b, c, d, e, f = transform
    x = a * col + b * row + c
    y = d * col + e * row + f
    return x, y


def _cell_polygon(
    *,
    transform: list[float],
    row: int,
    col: int,
    precision: int | None,
) -> dict[str, Any]:
    """
    Build GeoJSON polygon for one raster cell.
    """
    p1 = _pixel_corner(transform=transform, row=row, col=col)
    p2 = _pixel_corner(transform=transform, row=row, col=col + 1)
    p3 = _pixel_corner(transform=transform, row=row + 1, col=col + 1)
    p4 = _pixel_corner(transform=transform, row=row + 1, col=col)

    ring = [
        _round_coord_pair(p1, precision),
        _round_coord_pair(p2, precision),
        _round_coord_pair(p3, precision),
        _round_coord_pair(p4, precision),
        _round_coord_pair(p1, precision),
    ]

    return {
        "type": "Polygon",
        "coordinates": [ring],
    }


def _bbox_polygon_for_cells(
    *,
    transform: list[float],
    cells: list[tuple[int, int]],
    precision: int | None,
) -> dict[str, Any]:
    """
    Build bounding-box polygon for a set of cells.
    """
    min_row = min(row for row, _col in cells)
    max_row = max(row for row, _col in cells)
    min_col = min(col for _row, col in cells)
    max_col = max(col for _row, col in cells)

    p1 = _pixel_corner(transform=transform, row=min_row, col=min_col)
    p2 = _pixel_corner(transform=transform, row=min_row, col=max_col + 1)
    p3 = _pixel_corner(transform=transform, row=max_row + 1, col=max_col + 1)
    p4 = _pixel_corner(transform=transform, row=max_row + 1, col=min_col)

    ring = [
        _round_coord_pair(p1, precision),
        _round_coord_pair(p2, precision),
        _round_coord_pair(p3, precision),
        _round_coord_pair(p4, precision),
        _round_coord_pair(p1, precision),
    ]

    return {
        "type": "Polygon",
        "coordinates": [ring],
    }


# ---------------------------------------------------------------------------
# Exact component outline: trace the cell edges that separate the component
# from everything else. Vertices are cell-corner indices (row, col); every
# boundary edge is directed with the component on its left (x = col,
# y = -row), so exteriors come out counter-clockwise and holes clockwise.
# ---------------------------------------------------------------------------

def _component_boundary_edges(cells: set[tuple[int, int]]) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Map start corner -> end corners of the component's directed boundary edges."""
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(start: tuple[int, int], end: tuple[int, int]) -> None:
        edges.setdefault(start, []).append(end)

    for row, col in cells:
        if (row + 1, col) not in cells:  # bottom side, west -> east
            add((row + 1, col), (row + 1, col + 1))
        if (row, col + 1) not in cells:  # right side, south -> north
            add((row + 1, col + 1), (row, col + 1))
        if (row - 1, col) not in cells:  # top side, east -> west
            add((row, col + 1), (row, col))
        if (row, col - 1) not in cells:  # left side, north -> south
            add((row, col), (row + 1, col))

    return edges


def _trace_rings(edges: dict[tuple[int, int], list[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    """
    Link directed boundary edges into closed rings of corner indices.

    Where two cells touch only at a corner the corner has two outgoing
    edges; taking the left-most turn keeps each ring simple (no
    self-crossing).
    """
    rings: list[list[tuple[int, int]]] = []

    while edges:
        start = min(edges)
        ring = [start]
        prev = None
        current = start

        while True:
            outs = edges.get(current)
            if not outs:
                raise ValueError("raster_to_vector: open boundary while tracing a component outline.")

            if len(outs) == 1 or prev is None:
                nxt = outs[0]
            else:
                # direction in (x = col, y = -row)
                din = (current[1] - prev[1], -(current[0] - prev[0]))

                def turn(end: tuple[int, int]) -> int:
                    dout = (end[1] - current[1], -(end[0] - current[0]))
                    return din[0] * dout[1] - din[1] * dout[0]  # > 0: left turn

                nxt = max(outs, key=turn)

            outs.remove(nxt)
            if not outs:
                del edges[current]

            prev, current = current, nxt
            if current == start:
                break
            ring.append(current)

        rings.append(ring)

    return rings


def _split_self_touching(ring: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """
    Split a traced ring at vertices it visits more than once (two cells
    meeting only at a corner) into simple rings, so every output ring is
    valid: a pinched hole becomes two holes touching at a point, a pinched
    outline becomes an outer ring plus a hole (or two outer rings) sharing
    that corner.
    """
    out: list[list[tuple[int, int]]] = []
    stack: list[tuple[int, int]] = []
    position: dict[tuple[int, int], int] = {}
    for vertex in ring:
        if vertex in position:
            start = position[vertex]
            loop = stack[start:]
            for v in loop[1:]:
                del position[v]
            del stack[start + 1:]
            if len(loop) >= 3:
                out.append(loop)
        else:
            position[vertex] = len(stack)
            stack.append(vertex)
    if len(stack) >= 3:
        out.append(stack)
    return out


def _drop_collinear(ring: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Remove vertices in the middle of straight runs (ring without closing vertex)."""
    n = len(ring)
    out = []
    for i in range(n):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) != 0:
            out.append(b)
    return out or ring


def _ring_signed_area(ring: list[tuple[int, int]]) -> float:
    """Shoelace area in (x = col, y = -row): > 0 counter-clockwise."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        r1, c1 = ring[i]
        r2, c2 = ring[(i + 1) % n]
        total += c1 * (-r2) - c2 * (-r1)
    return total / 2.0


def _corner_ring_contains(ring: list[tuple[int, int]], row: float, col: float) -> bool:
    """Ray casting in corner-index space (point never on a ring edge here)."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        ri, ci = ring[i]
        rj, cj = ring[j]
        if (ri > row) != (rj > row) and col < (cj - ci) * (row - ri) / (rj - ri) + ci:
            inside = not inside
        j = i
    return inside


def _component_polygon(
    *,
    transform: list[float],
    cells: list[tuple[int, int]],
    precision: int | None,
) -> dict[str, Any]:
    """
    Exact GeoJSON geometry of a set of raster cells: the union of the cell
    squares, holes included. Polygon for one outer ring, MultiPolygon when
    the cells form several outer rings (8-connected corner contacts).
    """
    cell_set = set(cells)
    rings = [
        _drop_collinear(simple)
        for ring in _trace_rings(_component_boundary_edges(cell_set))
        for simple in _split_self_touching(ring)
    ]
    return _rings_to_geometry(rings, transform=transform, precision=precision)


def _rings_to_geometry(
    rings: list[list[tuple[int, int]]],
    *,
    transform: list[float],
    precision: int | None,
) -> dict[str, Any]:
    """Traced corner-index rings of one component -> Polygon / MultiPolygon."""
    exteriors = [ring for ring in rings if _ring_signed_area(ring) > 0]
    holes = [ring for ring in rings if _ring_signed_area(ring) < 0]

    polygons: list[list[list[tuple[int, int]]]] = [[ring] for ring in exteriors]
    for hole in holes:
        if len(polygons) == 1:
            polygons[0].append(hole)
            continue
        # centre of the cell just outside the component across the hole's
        # first edge: strictly inside the hole, never on any ring
        (r1, c1), (r2, c2) = hole[0], hole[1]
        dr, dc = r2 - r1, c2 - c1
        length = abs(dr) + abs(dc)
        dr, dc = dr / length, dc / length
        # the component is left of the edge; its right, (row + dc, col - dr), is the hole
        probe_row = r1 + dr * 0.5 + dc * 0.5
        probe_col = c1 + dc * 0.5 - dr * 0.5
        owner = None
        for index, polygon in enumerate(polygons):
            if _corner_ring_contains(polygon[0], probe_row, probe_col):
                if owner is None or abs(_ring_signed_area(polygon[0])) < abs(_ring_signed_area(polygons[owner][0])):
                    owner = index
        if owner is None:
            raise ValueError("raster_to_vector: hole outside every outer ring while tracing a component.")
        polygons[owner].append(hole)

    def to_map(ring: list[tuple[int, int]]) -> list[list[float]]:
        coords = [
            _round_coord_pair(_pixel_corner(transform=transform, row=row, col=col), precision)
            for row, col in ring
        ]
        coords.append(coords[0])
        return coords

    mapped = [[to_map(ring) for ring in polygon] for polygon in polygons]

    if len(mapped) == 1:
        return {"type": "Polygon", "coordinates": mapped[0]}

    return {"type": "MultiPolygon", "coordinates": mapped}

def _value_is_selected(
    value: Any,
    *,
    include_values: list[Any] | None,
    exclude_values: list[Any],
    nodata: Any,
) -> bool:
    """
    Check whether a raster value should be vectorized.
    """
    if _is_nodata(value, nodata):
        return False

    for excluded in exclude_values:
        if _values_equal(value, excluded):
            return False

    if include_values is None:
        return True

    return any(_values_equal(value, included) for included in include_values)


def _selected_grid(
    data: Any,
    *,
    band_index: int,
    height: int,
    width: int,
    include_values: list[Any] | None,
    exclude_values: list[Any],
    nodata: Any,
    shape: tuple[int, int, int] | None = None,
) -> list[list[bool]]:
    """
    Build boolean selected grid.
    """
    grid: list[list[bool]] = []

    for row in range(height):
        out_row: list[bool] = []

        for col in range(width):
            value = _band_value(data, band_index=band_index, row=row, col=col, shape=shape)
            out_row.append(
                _value_is_selected(
                    value,
                    include_values=include_values,
                    exclude_values=exclude_values,
                    nodata=nodata,
                )
            )

        grid.append(out_row)

    return grid


def _neighbors(
    *,
    row: int,
    col: int,
    height: int,
    width: int,
    connectivity: int,
) -> list[tuple[int, int]]:
    """
    Return valid neighboring cells.
    """
    deltas_4 = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    deltas_8 = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ]

    deltas = deltas_8 if connectivity == 8 else deltas_4

    result: list[tuple[int, int]] = []

    for d_row, d_col in deltas:
        n_row = row + d_row
        n_col = col + d_col

        if 0 <= n_row < height and 0 <= n_col < width:
            result.append((n_row, n_col))

    return result


def _connected_components(
    selected: list[list[bool]],
    *,
    connectivity: int,
    values: list[list[Any]] | None = None,
) -> list[list[tuple[int, int]]]:
    """
    Extract connected components from selected grid.

    When `values` is given, a component only grows across neighbours that
    are selected AND have the same pixel value (via `_values_equal`), so
    two touching regions of different classes (e.g. class 1 next to class
    2) are never merged into a single component.
    """
    height = len(selected)
    width = len(selected[0]) if height else 0

    visited = [[False for _ in range(width)] for _ in range(height)]
    components: list[list[tuple[int, int]]] = []

    for row in range(height):
        for col in range(width):
            if visited[row][col] or not selected[row][col]:
                continue

            component: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque()
            queue.append((row, col))
            visited[row][col] = True

            while queue:
                cur_row, cur_col = queue.popleft()
                component.append((cur_row, cur_col))

                for n_row, n_col in _neighbors(
                    row=cur_row,
                    col=cur_col,
                    height=height,
                    width=width,
                    connectivity=connectivity,
                ):
                    if visited[n_row][n_col] or not selected[n_row][n_col]:
                        continue

                    if values is not None and not _values_equal(
                        values[cur_row][cur_col], values[n_row][n_col]
                    ):
                        continue

                    visited[n_row][n_col] = True
                    queue.append((n_row, n_col))

            components.append(component)

    return components


# ---------------------------------------------------------------------------
# numpy engine
# ---------------------------------------------------------------------------

def _selected_mask_numpy(
    band: Any,
    *,
    include_values: list[Any] | None,
    exclude_values: list[Any],
    nodata: Any,
) -> Any | None:
    """
    Vectorized _value_is_selected for a numeric band (float64, NaN = None /
    NaN). Returns None when an include/exclude/nodata value is not a plain
    number (the python path then decides with its own equality rules).
    """
    import numpy as np

    def number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    targets = list(exclude_values) + list(include_values or [])
    if any(not number(v) for v in targets):
        return None
    if nodata is not None and not number(nodata):
        return None

    def equal(value: float) -> Any:
        if math.isfinite(value):
            with np.errstate(invalid="ignore"):
                return (band == value) | (np.abs(band - value) <= EPSILON)
        return band == value

    selected = ~np.isnan(band)
    if nodata is not None and not _is_nan(nodata):
        selected &= band != float(nodata)
    for value in exclude_values:
        selected &= ~equal(float(value))
    if include_values is not None:
        wanted = np.zeros(band.shape, dtype=bool)
        for value in include_values:
            wanted |= equal(float(value))
        selected &= wanted
    return selected


def _components_numpy(
    *,
    selected: Any,
    band: Any,
    connectivity: int,
    transform: list[float],
    precision: int | None,
) -> list[tuple[tuple[int, int], int, dict[str, Any]]]:
    """
    (first cell, pixel count, exact geometry) per component, in the python
    engine's component order. Labelling and edge extraction are vectorized;
    only the boundary edges (not the cells) are walked in python.
    """
    import numpy as np

    labels, count = label_runs(selected, band, connectivity)
    if count == 0:
        return []

    pixel_counts = np.bincount(labels.ravel(), minlength=count + 1)
    flat_first = np.full(count + 1, labels.size, dtype="int64")
    nz = np.flatnonzero(labels)
    np.minimum.at(flat_first, labels.ravel()[nz], nz)

    edges = boundary_edges_by_label(labels)
    width = labels.shape[1]
    out = []
    for lab in range(1, count + 1):
        rings = [
            _drop_collinear(simple)
            for ring in _trace_rings(edges[lab])
            for simple in _split_self_touching(ring)
        ]
        geometry = _rings_to_geometry(rings, transform=transform, precision=precision)
        first = int(flat_first[lab])
        out.append(((first // width, first % width), int(pixel_counts[lab]), geometry))
    return out


def _make_feature(
    *,
    feature_id: int,
    geometry: dict[str, Any],
    properties: dict[str, Any],
) -> dict[str, Any]:
    """
    Build GeoJSON-like Feature.
    """
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": geometry,
        "properties": properties,
    }


def _make_vector_output(
    *,
    features: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """
    Dependency-free vector output.

    Kept as a plain dict to avoid coupling with a specific VectorOut constructor.
    """
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": metadata,
    }


def _raster_to_vector_numpy_output(
    *,
    data: Any,
    band: Any,
    selected: Any,
    mode: str,
    connectivity: int,
    band_index: int,
    raster_shape: tuple[int, int, int],
    transform: list[float],
    precision: int | None,
    include_pixel_properties: bool,
    max_features: int | None,
    finish: Any,
) -> dict[str, Any]:
    """numpy-engine body of raster_to_vector: same features as the python loop."""
    import numpy as np

    def original_value(row: int, col: int) -> Any:
        if is_ndarray(data):
            plane = data if data.ndim == 2 else data[band_index - 1]
            return plane[row, col].item()
        return _band_value(data, band_index=band_index, row=row, col=col, shape=raster_shape)

    selected_pixel_count = int(selected.sum())
    features: list[dict[str, Any]] = []
    truncated = False

    if mode == "cells":
        rows, cols = np.nonzero(selected)
        if max_features is not None and len(rows) > max_features:
            rows, cols = rows[:max_features], cols[:max_features]
            truncated = True
        for feature_id, (row, col) in enumerate(zip(rows.tolist(), cols.tolist()), start=1):
            value = original_value(row, col)
            properties: dict[str, Any] = {"value": value, "class_value": value}
            if include_pixel_properties:
                properties.update({"row": row, "col": col, "pixel_id": f"r{row}_c{col}"})
            features.append(
                _make_feature(
                    feature_id=feature_id,
                    geometry=_cell_polygon(transform=transform, row=row, col=col, precision=precision),
                    properties=properties,
                )
            )
    else:
        components = _components_numpy(
            selected=selected, band=band, connectivity=connectivity, transform=transform, precision=precision
        )
        if max_features is not None and len(components) > max_features:
            components = components[:max_features]
            truncated = True
        for component_index, ((row, col), pixel_count, geometry) in enumerate(components, start=1):
            value = original_value(row, col)
            features.append(
                _make_feature(
                    feature_id=component_index,
                    geometry=geometry,
                    properties={
                        "component_id": component_index,
                        "value": value,
                        "class_value": value,
                        "pixel_count": pixel_count,
                        "bbox_mode": False,
                    },
                )
            )

    return finish(features, selected_pixel_count, truncated)


@capability(
    name="raster_to_vector",
    keywords=[
        "raster to vector",
        "polygonize raster",
        "raster polygonize",
        "mask to polygon",
        "mask to vector",
        "ndvi polygon",
        "water mask polygon",
        "vectorize raster",
        "تبدیل رستر به وکتور",
        "پلیگون سازی رستر",
        "تبدیل ماسک به پلیگون",
        "وکتور سازی رستر",
    ],
    description="Convert selected raster pixels/classes into vector polygon features.",
    required_inputs=["raster"],
    optional_inputs=[
        "band_index",
        "include_values",
        "exclude_values",
        "mode",
        "connectivity",
        "nodata",
        "engine",
        "precision",
        "source_crs",
        "metadata",
        "include_pixel_properties",
        "include_component_cells",
        "max_features",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "conversion",
        "data_type": "raster_vector",
        "operation": "raster_to_vector",
        "returns": "FeatureCollection",
        "artifact_kind": "vector",
        "access_scope": "raster_conversion",
        "config_aware": True,
        "polygonize_supported": True,
        "component_mode_supported": True,
        "routable": True,
    },
)
def raster_to_vector(
    raster: Any,
    band_index: int | None = None,
    include_values: list[Any] | Any | None = None,
    exclude_values: list[Any] | Any | None = None,
    mode: str | None = None,
    connectivity: int | None = None,
    nodata: Any = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
    include_pixel_properties: bool | None = None,
    include_component_cells: bool | None = None,
    max_features: int | None = None,
) -> dict[str, Any]:
    """
    Convert selected raster pixels/classes to vector polygon features.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        band_index:
            1-based band index.
        include_values:
            Values to vectorize. If None, config default_include_values is used.
            If final include_values is None, all valid non-nodata values are vectorized.
        exclude_values:
            Values to exclude from vectorization.
        mode:
            cells | components.
        connectivity:
            4 | 8 for component mode.
        nodata:
            Input nodata value.
        engine:
            python | numpy | auto. numpy selects pixels, labels components
            and extracts their outlines with array operations (same
            features, same order as python); auto = numpy when installed.
            include_component_cells=True keeps the python path.
        precision:
            Coordinate rounding precision.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.
        include_pixel_properties:
            Add row/col/value to cell features.
        include_component_cells:
            Add component cell list to component properties.
        max_features:
            Optional maximum number of output features.

    Returns:
        GeoJSON-like FeatureCollection dict:
            {
                "type": "FeatureCollection",
                "features": [...],
                "metadata": {...}
            }
    """
    config = _load_raster_to_vector_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    final_mode = _validate_mode(
        str(pick_first(mode, config.get("default_mode"), default="cells"))
    )

    final_connectivity = _validate_connectivity(
        pick_first(connectivity, config.get("default_connectivity"), default=4)
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)
    raster_shape = (band_count, height, width)

    final_band_index = _validate_band_index(
        pick_first(band_index, config.get("default_band_index"), default=1),
        band_count=band_count,
    )

    final_nodata = pick_first(
        nodata,
        input_metadata.get("nodata"),
        config.get("default_nodata"),
        default=None,
    )

    include_candidate = include_values
    if include_candidate is None and "default_include_values" in config:
        include_candidate = config.get("default_include_values")

    final_include_values = _normalize_value_list(
        include_candidate,
        name="include_values",
        allow_none=True,
    )

    exclude_candidate = exclude_values
    if exclude_candidate is None:
        exclude_candidate = config.get("default_exclude_values", [])

    final_exclude_values = _normalize_value_list(
        exclude_candidate,
        name="exclude_values",
        allow_none=False,
    ) or []

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    transform, transform_source = _extract_transform(
        input_metadata,
        default_origin_x=config.get("default_origin_x", 0.0),
        default_origin_y=config.get("default_origin_y", 0.0),
        default_x_resolution=config.get("default_x_resolution", 1.0),
        default_y_resolution=config.get("default_y_resolution", 1.0),
    )

    preserve_metadata = bool(config.get("preserve_metadata", True))

    final_source_crs = pick_first(
        source_crs,
        input_metadata.get("crs"),
        config.get("source_crs"),
        default=None,
    )

    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    final_include_pixel_properties = _as_bool(
        pick_first(include_pixel_properties, config.get("include_pixel_properties"), default=True),
        default=True,
    )

    final_include_component_cells = _as_bool(
        pick_first(include_component_cells, config.get("include_component_cells"), default=False),
        default=False,
    )

    final_max_features = _validate_max_features(
        pick_first(max_features, config.get("max_features"), default=None)
    )

    def _finish_output(
        *,
        features: list[dict[str, Any]],
        selected_pixel_count: int,
        truncated: bool,
        engine_used: str,
    ) -> dict[str, Any]:
        geographic_warning = None
        if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
            geographic_warning = (
                "Raster-to-vector conversion is being performed on a geographic CRS. "
                "Generated coordinates are valid, but area/length calculations may require reprojection."
            )

        transform_warning = None
        if transform_source == "default_transform":
            transform_warning = (
                "No transform found in raster metadata (no 'transform' or 'affine_transform' key); "
                "using the default identity-like transform instead."
            )

        combined_warning = "; ".join(
            message for message in (transform_warning, geographic_warning) if message
        ) or None

        user_metadata = metadata or {}
        if not isinstance(user_metadata, dict):
            raise ValueError("metadata must be a dict or None.")

        base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

        output_metadata = {
            **base_metadata,
            "source": "raster_to_vector",
            "loader": PLUGIN_ID,
            "operation": "raster_to_vector",
            "engine_requested": final_engine,
            "engine_used": engine_used,
            "input_band_count": band_count,
            "selected_band_index": final_band_index,
            "width": width,
            "height": height,
            "mode": final_mode,
            "connectivity": final_connectivity,
            "include_values": final_include_values,
            "exclude_values": final_exclude_values,
            "nodata": final_nodata,
            "selected_pixel_count": selected_pixel_count,
            "feature_count": len(features),
            "truncated": truncated,
            "max_features": final_max_features,
            "transform": transform,
            "transform_source": transform_source,
            "coordinate_precision": final_precision,
            "source_crs": final_source_crs,
            "warning": combined_warning,
            "created_at": _utc_now_iso(),
            **source_info,
            **user_metadata,
        }

        return _make_vector_output(
            features=features,
            metadata=output_metadata,
        )

    engine_used = resolve_engine(final_engine)
    numpy_band = None
    numpy_selected = None
    if engine_used == "numpy":
        numpy_band = numeric_band(data, final_band_index)
        if numpy_band is not None:
            numpy_selected = _selected_mask_numpy(
                numpy_band,
                include_values=final_include_values,
                exclude_values=final_exclude_values,
                nodata=final_nodata,
            )
        if numpy_selected is None or (final_mode == "components" and final_include_component_cells):
            engine_used = "python"

    if engine_used == "numpy":
        return _raster_to_vector_numpy_output(
            data=data,
            band=numpy_band,
            selected=numpy_selected,
            mode=final_mode,
            connectivity=final_connectivity,
            band_index=final_band_index,
            raster_shape=raster_shape,
            transform=transform,
            precision=final_precision,
            include_pixel_properties=final_include_pixel_properties,
            max_features=final_max_features,
            finish=lambda features, selected_pixel_count, truncated: _finish_output(
                features=features,
                selected_pixel_count=selected_pixel_count,
                truncated=truncated,
                engine_used="numpy",
            ),
        )

    if is_ndarray(data):
        data = data.tolist()

    selected = _selected_grid(
        data,
        band_index=final_band_index,
        height=height,
        width=width,
        include_values=final_include_values,
        exclude_values=final_exclude_values,
        nodata=final_nodata,
        shape=raster_shape,
    )

    selected_pixel_count = sum(1 for row in selected for value in row if value)

    features: list[dict[str, Any]] = []
    feature_id = 1
    truncated = False

    if final_mode == "cells":
        for row in range(height):
            for col in range(width):
                if not selected[row][col]:
                    continue

                if final_max_features is not None and len(features) >= final_max_features:
                    truncated = True
                    break

                value = _band_value(data, band_index=final_band_index, row=row, col=col, shape=raster_shape)

                properties: dict[str, Any] = {
                    "value": value,
                    "class_value": value,
                }

                if final_include_pixel_properties:
                    properties.update(
                        {
                            "row": row,
                            "col": col,
                            "pixel_id": f"r{row}_c{col}",
                        }
                    )

                features.append(
                    _make_feature(
                        feature_id=feature_id,
                        geometry=_cell_polygon(
                            transform=transform,
                            row=row,
                            col=col,
                            precision=final_precision,
                        ),
                        properties=properties,
                    )
                )
                feature_id += 1

            if truncated:
                break

    else:
        value_grid = [
            [
                _band_value(data, band_index=final_band_index, row=row, col=col, shape=raster_shape)
                for col in range(width)
            ]
            for row in range(height)
        ]
        components = _connected_components(
            selected, connectivity=final_connectivity, values=value_grid
        )

        for component_index, cells in enumerate(components, start=1):
            if final_max_features is not None and len(features) >= final_max_features:
                truncated = True
                break

            first_row, first_col = cells[0]
            first_value = value_grid[first_row][first_col]

            properties = {
                "component_id": component_index,
                "value": first_value,
                "class_value": first_value,
                "pixel_count": len(cells),
                "bbox_mode": False,
            }

            if final_include_component_cells:
                properties["cells"] = [
                    {"row": row, "col": col}
                    for row, col in cells
                ]

            features.append(
                _make_feature(
                    feature_id=feature_id,
                    geometry=_component_polygon(
                        transform=transform,
                        cells=cells,
                        precision=final_precision,
                    ),
                    properties=properties,
                )
            )
            feature_id += 1

    return _finish_output(
        features=features,
        selected_pixel_count=selected_pixel_count,
        truncated=truncated,
        engine_used="python",
    )



PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Raster To Vector",
    description=(
        "Converts selected raster pixels/classes into GeoJSON-like polygon features. "
        "Useful for turning raster masks such as NDVI, NDWI, slope, and classified rasters into vectors."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
