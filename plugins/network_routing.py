"""
network_routing.py

GeoChat SDK Plugin
==================

Plugin ID:
    network_routing

Capabilities:
    - find_shortest_path:
        Least-cost route between two locations over a line network.
    - calculate_service_area:
        Everything reachable from one or more origins within a cost budget
        (isochrone / service area), as polygons or lines per cost band.

How the network is built:
    The network is a layer of LineString/MultiLineString features supplied by the
    caller (for example roads loaded with load_vector or query_database). Lines
    are noded at their intersections (``node_crossings``, default true), so two
    roads that cross become connected; with false, lines connect only where they
    share an end point. Edges are undirected. Edge cost = length x multiplier,
    where the multiplier is the numeric ``weight_field`` property (cost per unit
    length, e.g. 1/speed for travel time), 1 when the property is missing;
    values <= 0 or non-numeric mark an edge impassable (barrier).

Snapping:
    Origins and destinations snap to the nearest network node (not to the middle
    of an edge); the snap distance is reported. ``snap_tolerance`` makes a
    farther snap an error instead of a silent long detour.

Config-aware behavior:
    Reads config/plugins/network_routing.yaml (max_edges, max_origins, max_bands).

Important:
    Coordinates and lengths are planar; use a projected CRS for metric costs.
"""

from __future__ import annotations

import heapq
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

PLUGIN_ID = "network_routing"

DEFAULTS = {"max_edges": 200_000, "max_origins": 50, "max_bands": 10}
OUTPUTS = {"polygon", "lines", "both"}
_ROUND = 9


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _node(x: float, y: float) -> tuple[float, float]:
    return (round(x, _ROUND), round(y, _ROUND))


class _Graph:
    def __init__(self) -> None:
        self.adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float, int]]] = {}
        self.edges: list[dict[str, Any]] = []

    def add_edge(self, coords: list[tuple[float, float]], length: float, cost: float) -> None:
        u, v = _node(*coords[0][:2]), _node(*coords[-1][:2])
        index = len(self.edges)
        self.edges.append({"u": u, "v": v, "coords": coords, "length": length, "cost": cost})
        self.adjacency.setdefault(u, []).append((v, cost, index))
        self.adjacency.setdefault(v, []).append((u, cost, index))

    def nearest_node(self, x: float, y: float) -> tuple[tuple[float, float], float]:
        best, best_d2 = None, math.inf
        for node in self.adjacency:
            d2 = (node[0] - x) ** 2 + (node[1] - y) ** 2
            if d2 < best_d2:
                best, best_d2 = node, d2
        if best is None:
            raise ValueError("The network has no usable edges.")
        return best, math.sqrt(best_d2)


def _multiplier(properties: dict[str, Any], field: str | None) -> float | None:
    """Cost per unit length for an edge; None means impassable."""
    if not field:
        return 1.0
    raw = properties.get(field)
    if raw is None:
        return 1.0
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 and math.isfinite(value) else None


def _build_graph(network: Any, weight_field: str | None, node_crossings: bool,
                 max_edges: int) -> tuple[_Graph, dict[str, Any]]:
    shape, _, unary_union = get_shapely(PLUGIN_ID)
    from shapely import STRtree

    features, source_info = extract_features(network)
    lines: list[tuple[Any, dict[str, Any]]] = []
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shape(geometry)
        if geom.geom_type not in {"LineString", "MultiLineString"}:
            raise ValueError("network must contain only LineString/MultiLineString geometries.")
        parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        props = feature.get("properties") or {}
        lines.extend((part, props) for part in parts if not part.is_empty and part.length > 0)
    if not lines:
        raise ValueError("The network layer has no line geometries.")
    check_limit(len(lines), max_edges, "network line count")

    if node_crossings:
        merged = unary_union([g for g, _ in lines])
        pieces = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        tree = STRtree([g for g, _ in lines])
        resolved = []
        for piece in pieces:
            if piece.geom_type != "LineString" or piece.length == 0:
                continue
            owner = int(tree.nearest(piece.interpolate(0.5, normalized=True)))
            resolved.append((piece, lines[owner][1]))
    else:
        resolved = lines
    check_limit(len(resolved), max_edges, "network edge count")

    graph = _Graph()
    blocked = 0
    for piece, props in resolved:
        factor = _multiplier(props, weight_field)
        if factor is None:
            blocked += 1
            continue
        coords = [(c[0], c[1]) for c in piece.coords]
        graph.add_edge(coords, piece.length, piece.length * factor)
    if not graph.edges:
        raise ValueError("Every network edge is impassable.")
    return graph, {**source_info, "edge_count": len(graph.edges), "blocked_edge_count": blocked}


def _dijkstra(graph: _Graph, source: tuple[float, float], cutoff: float = math.inf,
              target: tuple[float, float] | None = None):
    dist = {source: 0.0}
    previous: dict[tuple[float, float], tuple[tuple[float, float], int]] = {}
    heap = [(0.0, source)]
    done: set[tuple[float, float]] = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in done:
            continue
        done.add(node)
        if node == target:
            break
        for neighbour, cost, edge in graph.adjacency.get(node, ()):
            nd = d + cost
            if nd <= cutoff and nd < dist.get(neighbour, math.inf):
                dist[neighbour] = nd
                previous[neighbour] = (node, edge)
                heapq.heappush(heap, (nd, neighbour))
    return dist, previous


def _location(value: Any, name: str) -> list[tuple[float, float]]:
    """Accept [x, y] or point features; return a list of (x, y)."""
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
    ):
        return [(float(value[0]), float(value[1]))]
    shape, _, _ = get_shapely(PLUGIN_ID)
    features, _ = extract_features(value)
    points = []
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shape(geometry)
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        points.append((point.x, point.y))
    if not points:
        raise ValueError(f"{name} has no usable point location.")
    return points


def _snap(graph: _Graph, xy: tuple[float, float], tolerance: float | None, name: str):
    node, distance = graph.nearest_node(*xy)
    if tolerance is not None and distance > tolerance:
        raise ValueError(
            f"{name} is {distance:.6g} from the nearest network node (snap_tolerance {tolerance:g})."
        )
    return node, distance


def _tolerance(value: Any) -> float | None:
    if value is None:
        return None
    tol = to_float(value, "snap_tolerance")
    if tol < 0 or not math.isfinite(tol):
        raise ValueError("snap_tolerance must be a non-negative number.")
    return tol


def _route_coords(graph: _Graph, previous, source, target) -> list[tuple[float, float]]:
    steps = []
    node = target
    while node != source:
        parent, edge = previous[node]
        steps.append((parent, node, edge))
        node = parent
    coords: list[tuple[float, float]] = []
    for parent, node, edge_id in reversed(steps):
        edge_coords = graph.edges[edge_id]["coords"]
        if _node(*edge_coords[0]) != parent:
            edge_coords = edge_coords[::-1]
        coords.extend(edge_coords if not coords else edge_coords[1:])
    return coords


_PATH_META = {
    "category": "analysis",
    "data_type": "vector",
    "returns": "VectorOut",
    "artifact_kind": "features",
    "access_scope": "spatial_analysis",
    "config_aware": True,
    "routable": True,
}


@capability(
    name="find_shortest_path",
    keywords=[
        "shortest path", "route", "routing", "least cost path", "directions", "path between",
        "fastest route", "shortest route",
        "کوتاه ترین مسیر", "مسیریابی", "مسیر بین", "سریع ترین مسیر", "کم هزینه ترین مسیر",
    ],
    description=(
        "Least-cost route between an origin and a destination over a line network (roads). "
        "Cost is length times an optional per-length weight property."
    ),
    required_inputs=["network", "origin", "destination"],
    optional_inputs=["weight_field", "snap_tolerance", "node_crossings", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_PATH_META, "operation": "shortest_path"},
)
def find_shortest_path(
    network: Any,
    origin: Any,
    destination: Any,
    weight_field: str | None = None,
    snap_tolerance: float | None = None,
    node_crossings: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Compute the least-cost route.

    Args:
        network: LineString features forming the network.
        origin: Point feature(s) or [x, y]; the first point is used.
        destination: Point feature(s) or [x, y]; the first point is used.
        weight_field: Property with cost per unit length (<= 0 / non-numeric = barrier).
        snap_tolerance: Maximum allowed distance from a location to the network.
        node_crossings: Connect lines that cross without sharing a vertex.
        metadata: Extra output metadata.

    Returns:
        VectorOut with one LineString route feature (cost, length, edge_count, snap distances).
    """
    config = load_config(PLUGIN_ID)
    graph, info = _build_graph(network, weight_field, bool(node_crossings),
                               _limit(config, "max_edges"))
    tolerance = _tolerance(snap_tolerance)
    start, start_d = _snap(graph, _location(origin, "origin")[0], tolerance, "origin")
    end, end_d = _snap(graph, _location(destination, "destination")[0], tolerance, "destination")

    if start == end:
        coords = [start, end]
        route_cost = route_length = 0.0
        edge_count = 0
    else:
        dist, previous = _dijkstra(graph, start, target=end)
        if end not in dist:
            raise ValueError("No route exists between origin and destination (disconnected network).")
        coords = _route_coords(graph, previous, start, end)
        route_cost = dist[end]
        edge_count = 0
        route_length = 0.0
        node = end
        while node != start:
            node, edge = previous[node]
            edge_count += 1
            route_length += graph.edges[edge]["length"]

    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]}
        if start != end else None,
        "properties": {
            "cost": route_cost,
            "length": route_length,
            "edge_count": edge_count,
            "origin_snap_distance": start_d,
            "destination_snap_distance": end_d,
        },
    }
    return make_vector_out(
        [feature], plugin_id=PLUGIN_ID, operation="shortest_path", source_info=info,
        extra={"weight_field": weight_field, "node_crossings": bool(node_crossings)},
        user_metadata=metadata,
    )


def _reach_geometry(graph: _Graph, dist: dict, cutoff: float):
    """Lines of every edge portion reachable within ``cutoff`` (partial edges are cut)."""
    from shapely.geometry import LineString
    from shapely.ops import substring

    parts = []
    for edge in graph.edges:
        u, v, cost, length = edge["u"], edge["v"], edge["cost"], edge["length"]
        du, dv = dist.get(u, math.inf), dist.get(v, math.inf)
        if du > cutoff and dv > cutoff:
            continue
        line = LineString(edge["coords"])
        if du + cost <= cutoff or dv + cost <= cutoff:  # the whole edge is within the budget
            parts.append(line)
            continue
        per_length = cost / length if length else math.inf
        covered = []
        if du <= cutoff:
            covered.append((0.0, min(length, (cutoff - du) / per_length)))
        if dv <= cutoff:
            covered.append((max(0.0, length - (cutoff - dv) / per_length), length))
        # edge coords run u -> v; the two reached pieces start at each end
        for start, end in covered:
            if end - start > 0:
                parts.append(substring(line, start, end))
    return parts


@capability(
    name="calculate_service_area",
    keywords=[
        "service area", "isochrone", "reachable area", "catchment", "travel time area", "walkshed",
        "drive time area", "accessibility area",
        "محدوده سرویس دهی", "ایزوکرون", "محدوده دسترسی", "منطقه قابل دسترس", "پیاده روی", "زمان سفر",
    ],
    description=(
        "Service area / isochrone: the part of a line network reachable from each origin within "
        "one or more cost limits, returned as polygons and/or lines per cost band."
    ),
    required_inputs=["network", "origins", "max_cost"],
    optional_inputs=["weight_field", "output", "buffer_distance", "snap_tolerance",
                     "node_crossings", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_PATH_META, "operation": "service_area"},
)
def calculate_service_area(
    network: Any,
    origins: Any,
    max_cost: float | list[float],
    weight_field: str | None = None,
    output: str = "polygon",
    buffer_distance: float | None = None,
    snap_tolerance: float | None = None,
    node_crossings: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Compute service areas.

    Args:
        network: LineString features forming the network.
        origins: Point features (or [x, y]); one service area set per origin.
        max_cost: Cost limit, or a list of limits (bands, ascending output).
        weight_field: Property with cost per unit length (<= 0 / non-numeric = barrier).
        output: ``polygon`` (buffered reach), ``lines`` (reached network) or ``both``.
        buffer_distance: Buffer applied to the reached lines for polygons; default is a
            quarter of the average edge length of the network.
        snap_tolerance: Maximum allowed distance from an origin to the network.
        node_crossings: Connect lines that cross without sharing a vertex.
        metadata: Extra output metadata.

    Returns:
        VectorOut: per origin and cost band a feature with ``origin_index``, ``cost``,
        ``geometry_kind`` (polygon|lines), ``reached_length``.
    """
    config = load_config(PLUGIN_ID)
    _, mapping, unary_union = get_shapely(PLUGIN_ID)
    kind = validate_choice(output, OUTPUTS, "output")
    limits = [max_cost] if not isinstance(max_cost, (list, tuple)) else list(max_cost)
    bands = sorted({to_float(c, "max_cost") for c in limits})
    if not bands or bands[0] <= 0 or not all(math.isfinite(b) for b in bands):
        raise ValueError("max_cost must be a positive number or a list of positive numbers.")
    check_limit(len(bands), _limit(config, "max_bands"), "number of cost bands")

    starts = _location(origins, "origins")
    check_limit(len(starts), _limit(config, "max_origins"), "origin count")
    graph, info = _build_graph(network, weight_field, bool(node_crossings),
                               _limit(config, "max_edges"))
    tolerance = _tolerance(snap_tolerance)
    if buffer_distance is None:
        buffer_size = 0.25 * sum(e["length"] for e in graph.edges) / len(graph.edges)
    else:
        buffer_size = to_float(buffer_distance, "buffer_distance")
        if buffer_size <= 0 or not math.isfinite(buffer_size):
            raise ValueError("buffer_distance must be a positive number.")

    features: list[dict[str, Any]] = []
    for origin_index, xy in enumerate(starts):
        node, snap_distance = _snap(graph, xy, tolerance, f"origin {origin_index}")
        dist, _ = _dijkstra(graph, node, cutoff=bands[-1])
        for band in bands:
            parts = _reach_geometry(graph, dist, band)
            reached = unary_union(parts) if parts else None
            reached_length = reached.length if reached is not None else 0.0
            base = {
                "origin_index": origin_index,
                "cost": band,
                "reached_length": reached_length,
                "origin_snap_distance": snap_distance,
            }
            if reached is None:
                continue
            if kind in {"polygon", "both"}:
                features.append({
                    "type": "Feature",
                    "geometry": dict(mapping(reached.buffer(buffer_size))),
                    "properties": {**base, "geometry_kind": "polygon"},
                })
            if kind in {"lines", "both"}:
                features.append({
                    "type": "Feature",
                    "geometry": dict(mapping(reached)),
                    "properties": {**base, "geometry_kind": "lines"},
                })
    return make_vector_out(
        features, plugin_id=PLUGIN_ID, operation="service_area", source_info=info,
        extra={"cost_bands": bands, "origin_count": len(starts), "output": kind,
               "buffer_distance": buffer_size, "weight_field": weight_field},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Network Routing",
    description="Shortest path and service-area (isochrone) analysis over a line network.",
    author="GeoChat Platform Team",
    permissions=[],
)
