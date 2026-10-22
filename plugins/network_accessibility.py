"""
network_accessibility.py

GeoChat SDK Plugin
==================

Plugin ID:
    network_accessibility

Capabilities:
    - calculate_od_cost_matrix:
        Network cost between every origin and every destination (a table, optionally with
        straight lines for display).
    - calculate_accessibility_2sfca:
        Two-step floating catchment area (2SFCA / enhanced 2SFCA) accessibility of demand
        locations (people) to supply locations (clinics, schools...) along a road network.

It builds on network_routing: same network model (lines noded at crossings, undirected edges,
cost = length x optional weight_field, points snapped to the nearest node), same limits philosophy.

2SFCA in brief:
    step 1  for every facility j: R_j = S_j / sum_k(P_k * W(d_kj))   (supply per demand in its catchment)
    step 2  for every demand location i: A_i = sum_j(R_j * W(d_ij))   (accessible supply)
    W is 1 within the catchment (binary 2SFCA) or decays to 0 at the catchment cost
    (``gaussian`` = enhanced 2SFCA weights, ``linear``). Costs are network costs between snapped nodes.

Config-aware behavior:
    Reads config/plugins/network_accessibility.yaml (and the network limits of network_routing).
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
from plugins.csv_xy_loader import parse_number
from plugins.network_routing import PLUGIN_ID as ROUTING_ID
from plugins.network_routing import (
    _build_graph,
    _dijkstra,
    _snap,
    _tolerance,
)

PLUGIN_ID = "network_accessibility"

DEFAULTS = {
    "max_origins": 200,
    "max_destinations": 2000,
    "max_pairs": 100_000,
    "max_facilities": 300,
    "max_demand": 20_000,
}
_META = {
    "category": "analysis",
    "data_type": "vector",
    "returns": "VectorOut",
    "artifact_kind": "features",
    "access_scope": "spatial_analysis",
    "config_aware": True,
    "routable": True,
}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _points(features: list[dict[str, Any]], name: str):
    shape = get_shapely(PLUGIN_ID)[0]
    located = []
    for index, feature in enumerate(features):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shape(geometry)
        if geom.is_empty:
            continue
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        located.append((index, (point.x, point.y)))
    if not located:
        raise ValueError(f"{name} has no usable point location.")
    return located


@capability(
    name="calculate_od_cost_matrix",
    keywords=[
        "od matrix", "origin destination matrix", "cost matrix", "travel time matrix", "distance matrix",
        "all pairs distance", "network distance table",
        "ماتریس مبدا مقصد", "ماتریس هزینه", "ماتریس فاصله", "ماتریس زمان سفر", "فاصله شبکه ای",
    ],
    description=(
        "Origin-destination cost matrix over a line network: the network cost from every origin to "
        "every destination, as a table (null when unreachable)."
    ),
    required_inputs=["network", "origins", "destinations"],
    optional_inputs=["weight_field", "max_cost", "origin_name_field", "destination_name_field",
                     "include_lines", "snap_tolerance", "node_crossings", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "od_cost_matrix"},
)
def calculate_od_cost_matrix(
    network: Any,
    origins: Any,
    destinations: Any,
    weight_field: str | None = None,
    max_cost: float | None = None,
    origin_name_field: str | None = None,
    destination_name_field: str | None = None,
    include_lines: bool = False,
    snap_tolerance: float | None = None,
    node_crossings: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    OD cost matrix.

    Args:
        network: LineString features forming the network.
        origins: Origin points.
        destinations: Destination points.
        weight_field: Property with cost per unit length (<= 0 / non-numeric = barrier).
        max_cost: Stop searching beyond this cost; farther pairs are reported unreachable.
        origin_name_field / destination_name_field: Properties copied as origin_name / destination_name.
        include_lines: Add a straight origin-destination line as geometry (for display only).
        snap_tolerance: Maximum allowed distance from a point to the network.
        node_crossings: Connect lines that cross without sharing a vertex.
        metadata: Extra output metadata.

    Returns:
        VectorOut with one row per pair: origin_index, destination_index, cost, reachable, names.
    """
    config = load_config(PLUGIN_ID)
    routing_config = load_config(ROUTING_ID)
    cutoff = math.inf if max_cost is None else to_float(max_cost, "max_cost")
    if cutoff <= 0:
        raise ValueError("max_cost must be positive.")
    origin_features, source_info = extract_features(origins)
    destination_features, _ = extract_features(destinations)
    start_points = _points(origin_features, "origins")
    end_points = _points(destination_features, "destinations")
    check_limit(len(start_points), _limit(config, "max_origins"), "origin count")
    check_limit(len(end_points), _limit(config, "max_destinations"), "destination count")
    check_limit(len(start_points) * len(end_points), _limit(config, "max_pairs"), "origin x destination pairs")
    graph, info = _build_graph(network, weight_field, bool(node_crossings),
                               to_int(routing_config.get("max_edges", 200_000), "max_edges"))
    tolerance = _tolerance(snap_tolerance)
    snapped_ends = [(i, xy, *_snap(graph, xy, tolerance, f"destination {i}")) for i, xy in end_points]

    rows: list[dict[str, Any]] = []
    unreachable = 0
    for origin_index, origin_xy in start_points:
        node, snap_distance = _snap(graph, origin_xy, tolerance, f"origin {origin_index}")
        dist, _ = _dijkstra(graph, node, cutoff=cutoff)
        for destination_index, destination_xy, end_node, end_snap in snapped_ends:
            cost = dist.get(end_node)
            unreachable += cost is None
            props: dict[str, Any] = {
                "origin_index": origin_index,
                "destination_index": destination_index,
                "cost": cost,
                "reachable": cost is not None,
                "origin_snap_distance": snap_distance,
                "destination_snap_distance": end_snap,
            }
            if origin_name_field:
                props["origin_name"] = (origin_features[origin_index].get("properties") or {}).get(origin_name_field)
            if destination_name_field:
                props["destination_name"] = (destination_features[destination_index].get("properties") or {}).get(
                    destination_name_field)
            geometry = ({"type": "LineString", "coordinates": [list(origin_xy), list(destination_xy)]}
                        if include_lines else None)
            rows.append({"type": "Feature", "geometry": geometry, "properties": props})
    return make_vector_out(
        rows, plugin_id=PLUGIN_ID, operation="od_cost_matrix", source_info={**source_info, **info},
        extra={"origin_count": len(start_points), "destination_count": len(end_points),
               "unreachable_pairs": unreachable, "max_cost": None if cutoff == math.inf else cutoff,
               "weight_field": weight_field},
        user_metadata=metadata,
    )


def _decay(kind: str, d: float, d0: float) -> float:
    if d > d0:
        return 0.0
    if kind == "binary":
        return 1.0
    if kind == "linear":
        return 1.0 - d / d0
    return (math.exp(-0.5 * (d / d0) ** 2) - math.exp(-0.5)) / (1.0 - math.exp(-0.5))


@capability(
    name="calculate_accessibility_2sfca",
    keywords=[
        "2sfca", "e2sfca", "floating catchment", "accessibility to services", "healthcare accessibility",
        "spatial accessibility", "supply demand ratio", "service coverage per person", "access to hospitals",
        "دسترسی مکانی", "دسترسی به خدمات", "حوزه شناور", "نسبت عرضه به تقاضا", "دسترسی به بیمارستان",
        "برابری دسترسی",
    ],
    description=(
        "Two-step floating catchment area accessibility: how much service capacity (beds, doctors, "
        "seats) is reachable per person from each demand location along the road network within a "
        "travel-cost threshold, with optional distance decay."
    ),
    required_inputs=["network", "supply", "demand", "catchment_cost"],
    optional_inputs=["supply_field", "demand_field", "decay", "weight_field", "output", "per",
                     "snap_tolerance", "node_crossings", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "accessibility_2sfca"},
)
def calculate_accessibility_2sfca(
    network: Any,
    supply: Any,
    demand: Any,
    catchment_cost: float,
    supply_field: str | None = None,
    demand_field: str | None = None,
    decay: str = "binary",
    weight_field: str | None = None,
    output: str = "demand",
    per: float = 1000.0,
    snap_tolerance: float | None = None,
    node_crossings: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    2SFCA / E2SFCA accessibility.

    Args:
        network: LineString features forming the network.
        supply: Facility points; ``supply_field`` is their capacity (default 1 each).
        demand: Demand locations (points, or polygons via a representative point);
            ``demand_field`` is the population (default 1 each).
        catchment_cost: Catchment size in network cost units (layer length units, or minutes when
            ``weight_field`` holds 1/speed).
        supply_field / demand_field: Numeric properties (Persian digits understood).
        decay: ``binary`` (classic 2SFCA), ``gaussian`` (enhanced 2SFCA) or ``linear``.
        weight_field: Property with cost per unit length (<= 0 / non-numeric = barrier).
        output: ``demand`` (default; adds ``accessibility``) or ``supply`` (adds ``supply_ratio``).
        per: Scale of the reported figure, e.g. 1000 = capacity per 1000 people.
        snap_tolerance: Maximum allowed distance from a point to the network.
        node_crossings: Connect lines that cross without sharing a vertex.
        metadata: Extra output metadata.

    Returns:
        VectorOut. ``demand`` output: the demand features plus ``accessibility`` (capacity per
        ``per`` people) and ``facilities_in_reach``; demand with nothing in reach gets 0.
        ``supply`` output: facilities plus ``supply_ratio`` and ``demand_in_catchment``.
    """
    config = load_config(PLUGIN_ID)
    routing_config = load_config(ROUTING_ID)
    d0 = to_float(catchment_cost, "catchment_cost")
    if d0 <= 0 or not math.isfinite(d0):
        raise ValueError("catchment_cost must be a positive number.")
    kind = validate_choice(decay, {"binary", "gaussian", "linear"}, "decay")
    result_kind = validate_choice(output, {"demand", "supply"}, "output")
    scale = to_float(per, "per")
    if scale <= 0:
        raise ValueError("per must be positive.")

    supply_features, source_info = extract_features(supply)
    demand_features, _ = extract_features(demand)
    facilities = _points(supply_features, "supply")
    people = _points(demand_features, "demand")
    check_limit(len(facilities), _limit(config, "max_facilities"), "facility count")
    check_limit(len(people), _limit(config, "max_demand"), "demand location count")

    def amount(features, index, field, label):
        if not field:
            return 1.0
        value = parse_number((features[index].get("properties") or {}).get(field))
        if value is None:
            return 0.0
        if value < 0:
            raise ValueError(f"{label} '{field}' must be non-negative.")
        return value

    graph, info = _build_graph(network, weight_field, bool(node_crossings),
                               to_int(routing_config.get("max_edges", 200_000), "max_edges"))
    tolerance = _tolerance(snap_tolerance)
    demand_nodes = [(i, _snap(graph, xy, tolerance, f"demand {i}")[0]) for i, xy in people]
    population = {i: amount(demand_features, i, demand_field, "demand_field") for i, _ in people}

    # step 1: supply-to-demand ratio of every facility
    ratio: dict[int, float] = {}
    covered: dict[int, list[tuple[int, float]]] = {i: [] for i, _ in people}
    catchment_population: dict[int, float] = {}
    for facility_index, xy in facilities:
        node, _ = _snap(graph, xy, tolerance, f"facility {facility_index}")
        dist, _ = _dijkstra(graph, node, cutoff=d0)
        weighted = 0.0
        for person_index, person_node in demand_nodes:
            cost = dist.get(person_node)
            if cost is None:
                continue
            w = _decay(kind, cost, d0)
            if w > 0:
                weighted += population[person_index] * w
                covered[person_index].append((facility_index, w))
        capacity = amount(supply_features, facility_index, supply_field, "supply_field")
        ratio[facility_index] = capacity / weighted if weighted > 0 else 0.0
        catchment_population[facility_index] = weighted

    # step 2: accessibility of every demand location
    access = {
        i: sum(ratio[j] * w for j, w in links) for i, links in covered.items()
    }
    total_pop = sum(population.values())
    total_cap = sum(amount(supply_features, j, supply_field, "supply_field") for j, _ in facilities)

    output_features = []
    if result_kind == "demand":
        for index, feature in enumerate(demand_features):
            props = dict(feature.get("properties") or {})
            if index in access:
                props["accessibility"] = access[index] * scale
                props["facilities_in_reach"] = len(covered[index])
            else:
                props["accessibility"] = None
                props["facilities_in_reach"] = None
            output_features.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    else:
        for index, feature in enumerate(supply_features):
            props = dict(feature.get("properties") or {})
            props["supply_ratio"] = ratio[index] * scale if index in ratio else None
            props["demand_in_catchment"] = catchment_population.get(index)
            output_features.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    return make_vector_out(
        output_features, plugin_id=PLUGIN_ID, operation="accessibility_2sfca",
        source_info={**source_info, **info},
        extra={"method": "2SFCA" if kind == "binary" else "E2SFCA", "decay": kind, "catchment_cost": d0,
               "per": scale, "output": result_kind, "total_supply": total_cap, "total_demand": total_pop,
               "mean_accessibility": (sum(a * population[i] for i, a in access.items()) / total_pop * scale
                                      if total_pop > 0 else None),
               "demand_without_access": sum(1 for links in covered.values() if not links)},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Network Accessibility",
    description="OD cost matrices and 2SFCA / E2SFCA accessibility over a line network.",
    author="GeoChat Platform Team",
    permissions=[],
)
