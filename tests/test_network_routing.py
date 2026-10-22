"""Tests for the network_routing plugin."""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")
from shapely.geometry import shape  # noqa: E402

from plugins.network_routing import calculate_service_area, find_shortest_path  # noqa: E402


def line(coords, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]}}


def pt(x, y):
    return {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [x, y]}}


def lattice(n=3, **props):
    """n x n grid of unit streets: (n-1)*n horizontal + vertical segments of length 1."""
    streets = []
    for i in range(n):
        for j in range(n - 1):
            streets.append(line([(j, i), (j + 1, i)], **props))
            streets.append(line([(i, j), (i, j + 1)], **props))
    return streets


def test_shortest_path_on_lattice():
    out = find_shortest_path(lattice(), pt(0, 0), pt(2, 2))
    props = out.features[0]["properties"]
    assert props["cost"] == pytest.approx(4) and props["length"] == pytest.approx(4)
    assert props["edge_count"] == 4
    route = shape(out.features[0]["geometry"])
    assert route.length == pytest.approx(4)
    assert list(route.coords)[0] == (0.0, 0.0) and list(route.coords)[-1] == (2.0, 2.0)


def test_crossing_lines_connect_only_with_node_crossings():
    cross = [line([(0, 0), (2, 0)]), line([(1, -1), (1, 1)])]  # a plus sign, no shared vertex
    out = find_shortest_path(cross, pt(0, 0), pt(1, 1))
    assert out.features[0]["properties"]["cost"] == pytest.approx(2)
    with pytest.raises(ValueError, match="No route"):
        find_shortest_path(cross, pt(0, 0), pt(1, 1), node_crossings=False)


def test_weights_barriers_and_detours():
    fast_detour = [
        line([(0, 0), (1, 0)], w=1.0), line([(1, 0), (2, 0)], w=10.0),  # direct, slow
        line([(0, 0), (0, 1)], w=1.0), line([(0, 1), (2, 1)], w=1.0), line([(2, 1), (2, 0)], w=1.0),
    ]
    direct = find_shortest_path(fast_detour, pt(0, 0), pt(2, 0)).features[0]["properties"]
    assert direct["cost"] == pytest.approx(2)
    weighted = find_shortest_path(fast_detour, pt(0, 0), pt(2, 0), weight_field="w").features[0]["properties"]
    assert weighted["cost"] == pytest.approx(1 + 2 + 1) and weighted["length"] == pytest.approx(4)
    barrier = [line([(0, 0), (1, 0)], w=0), line([(1, 0), (2, 0)], w=1)]
    # the blocked edge is removed, so (0,0) is no longer on the network
    with pytest.raises(ValueError, match="snap_tolerance"):
        find_shortest_path(barrier, pt(0, 0), pt(2, 0), weight_field="w", snap_tolerance=0.1)
    two_parts = [line([(0, 0), (1, 0)]), line([(5, 0), (6, 0)])]
    with pytest.raises(ValueError, match="No route"):
        find_shortest_path(two_parts, pt(0, 0), pt(6, 0))


def test_snapping_reporting_and_tolerance():
    out = find_shortest_path(lattice(), [0.1, 0.0], pt(2, 2.2))
    props = out.features[0]["properties"]
    assert props["origin_snap_distance"] == pytest.approx(0.1)
    assert props["destination_snap_distance"] == pytest.approx(0.2)
    with pytest.raises(ValueError, match="snap_tolerance"):
        find_shortest_path(lattice(), pt(10, 10), pt(2, 2), snap_tolerance=1)


def test_same_location_and_input_validation():
    out = find_shortest_path(lattice(), pt(0, 0), pt(0, 0))
    assert out.features[0]["properties"]["cost"] == 0 and out.features[0]["geometry"] is None
    point_network = [pt(0, 0)]
    with pytest.raises(ValueError, match="LineString"):
        find_shortest_path(point_network, pt(0, 0), pt(1, 1))
    with pytest.raises(ValueError):
        find_shortest_path([], pt(0, 0), pt(1, 1))
    with pytest.raises(ValueError, match="origin"):
        find_shortest_path(lattice(), [], pt(1, 1))


def test_service_area_bands_and_partial_edges():
    street = [line([(0, 0), (10, 0)])]
    out = calculate_service_area(street, pt(0, 0), [2.5, 6], output="lines")
    by_cost = {f["properties"]["cost"]: f for f in out.features}
    assert by_cost[2.5]["properties"]["reached_length"] == pytest.approx(2.5)
    assert by_cost[6]["properties"]["reached_length"] == pytest.approx(6)
    assert shape(by_cost[2.5]["geometry"]).bounds == (0.0, 0.0, 2.5, 0.0)


def test_service_area_polygon_both_and_lattice():
    out = calculate_service_area(lattice(), pt(0, 0), 2, buffer_distance=0.1, output="both")
    kinds = sorted(f["properties"]["geometry_kind"] for f in out.features)
    assert kinds == ["lines", "polygon"]
    polygon = next(f for f in out.features if f["properties"]["geometry_kind"] == "polygon")
    assert shape(polygon["geometry"]).geom_type in {"Polygon", "MultiPolygon"}
    lines_feature = next(f for f in out.features if f["properties"]["geometry_kind"] == "lines")
    # within cost 2 of a corner of a unit lattice: 6 unit edges are fully reachable
    assert lines_feature["properties"]["reached_length"] == pytest.approx(6)
    assert out.metadata["buffer_distance"] == 0.1


def test_service_area_multiple_origins_and_validation():
    out = calculate_service_area(lattice(), [pt(0, 0), pt(2, 2)], 1, output="lines")
    assert sorted({f["properties"]["origin_index"] for f in out.features}) == [0, 1]
    for kwargs in ({"max_cost": 0}, {"max_cost": -1}, {"max_cost": []}, {"max_cost": 1, "output": "x"},
                   {"max_cost": 1, "buffer_distance": -2}):
        with pytest.raises(ValueError):
            calculate_service_area(lattice(), pt(0, 0), **kwargs)


def test_limits(monkeypatch):
    import plugins.network_routing as module

    monkeypatch.setattr(module, "load_config", lambda _id: {"max_edges": 3, "max_origins": 1, "max_bands": 1})
    with pytest.raises(ValueError, match="exceeds"):
        find_shortest_path(lattice(), pt(0, 0), pt(2, 2))
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_origins": 1, "max_bands": 1})
    with pytest.raises(ValueError, match="origin count"):
        calculate_service_area(lattice(), [pt(0, 0), pt(1, 1)], 1)
    with pytest.raises(ValueError, match="bands"):
        calculate_service_area(lattice(), pt(0, 0), [1, 2])
