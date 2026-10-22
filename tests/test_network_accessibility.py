"""network_accessibility: OD cost matrix and 2SFCA."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("shapely")

import plugins.network_accessibility as module  # noqa: E402
from plugins.network_accessibility import (  # noqa: E402
    calculate_accessibility_2sfca,
    calculate_od_cost_matrix,
)


def line(coords, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]}}


def pt(x, y, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def street(n):
    """Straight street 0..n with a node every unit."""
    return [line([(i, 0), (i + 1, 0)]) for i in range(n)]


# ------------------------------------------------------------------ OD matrix
def test_od_matrix_costs_names_and_unreachable():
    network = street(4) + [line([(10, 0), (11, 0)])]  # an island
    out = calculate_od_cost_matrix(
        network, [pt(0, 0, name="A"), pt(4, 0, name="B")], [pt(2, 0, nm="X"), pt(10, 0, nm="far")],
        origin_name_field="name", destination_name_field="nm")
    rows = {(f["properties"]["origin_name"], f["properties"]["destination_name"]): f["properties"]
            for f in out.features}
    assert rows[("A", "X")]["cost"] == 2 and rows[("B", "X")]["cost"] == 2
    assert rows[("A", "far")]["cost"] is None and rows[("A", "far")]["reachable"] is False
    assert out.metadata["unreachable_pairs"] == 2 and len(out.features) == 4
    assert all(f["geometry"] is None for f in out.features)


def test_od_matrix_cutoff_weights_and_lines():
    network = [line([(0, 0), (1, 0)], w=1), line([(1, 0), (2, 0)], w=10)]
    plain = calculate_od_cost_matrix(network, [pt(0, 0)], [pt(2, 0)])
    weighted = calculate_od_cost_matrix(network, [pt(0, 0)], [pt(2, 0)], weight_field="w")
    assert plain.features[0]["properties"]["cost"] == 2 and weighted.features[0]["properties"]["cost"] == 11
    capped = calculate_od_cost_matrix(network, [pt(0, 0)], [pt(2, 0)], weight_field="w", max_cost=5)
    assert capped.features[0]["properties"]["cost"] is None
    lines = calculate_od_cost_matrix(network, [pt(0, 0)], [pt(2, 0)], include_lines=True)
    assert lines.features[0]["geometry"]["type"] == "LineString"
    for kwargs in ({"max_cost": 0}, {"snap_tolerance": -1}):
        with pytest.raises(ValueError):
            calculate_od_cost_matrix(network, [pt(0, 0)], [pt(2, 0)], **kwargs)
    with pytest.raises(ValueError, match="snap_tolerance"):
        calculate_od_cost_matrix(network, [pt(50, 50)], [pt(2, 0)], snap_tolerance=1)


def test_od_matrix_limits(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_pairs": 3})
    with pytest.raises(ValueError, match="pairs"):
        calculate_od_cost_matrix(street(3), [pt(0, 0), pt(1, 0)], [pt(2, 0), pt(3, 0)])


# ----------------------------------------------------------------------- 2SFCA
def test_2sfca_hand_computed_binary():
    # street 0..3: facility F1 at x=0 (capacity 10), F2 at x=3 (capacity 20); demand at x=0,1,2,3
    # population 10 each; catchment 1.
    network = street(3)
    supply = [pt(0, 0, beds=10), pt(3, 0, beds=20)]
    demand = [pt(0, 0, pop=10), pt(1, 0, pop=10), pt(2, 0, pop=10), pt(3, 0, pop=10)]
    out = calculate_accessibility_2sfca(network, supply, demand, 1.0, "beds", "pop", per=1.0)
    # F1 reaches x=0,1 (pop 20) -> R1 = 10/20 = 0.5 ; F2 reaches x=2,3 (pop 20) -> R2 = 20/20 = 1
    access = [f["properties"]["accessibility"] for f in out.features]
    assert access == pytest.approx([0.5, 0.5, 1.0, 1.0])
    assert [f["properties"]["facilities_in_reach"] for f in out.features] == [1, 1, 1, 1]
    assert out.metadata["method"] == "2SFCA" and out.metadata["total_supply"] == 30
    ratios = calculate_accessibility_2sfca(network, supply, demand, 1.0, "beds", "pop", output="supply", per=1.0)
    assert [f["properties"]["supply_ratio"] for f in ratios.features] == pytest.approx([0.5, 1.0])
    assert [f["properties"]["demand_in_catchment"] for f in ratios.features] == [20.0, 20.0]


def test_2sfca_overlapping_catchments_and_scaling():
    network = street(2)  # x = 0, 1, 2
    supply = [pt(0, 0, c=6), pt(2, 0, c=6)]
    demand = [pt(1, 0, pop=3)]
    out = calculate_accessibility_2sfca(network, supply, demand, 1.0, "c", "pop")  # per 1000 by default
    # both facilities reach the only demand point: R = 6/3 each -> access = 4 -> 4000 per 1000 people
    assert out.features[0]["properties"]["accessibility"] == pytest.approx(4000.0)
    assert out.features[0]["properties"]["facilities_in_reach"] == 2


def test_2sfca_out_of_reach_and_decay_functions():
    network = street(3)
    supply = [pt(0, 0)]
    demand = [pt(0, 0), pt(2, 0), pt(3, 0)]
    out = calculate_accessibility_2sfca(network, supply, demand, 2.0, per=1.0)
    props = [f["properties"] for f in out.features]
    assert props[2]["accessibility"] == 0 and props[2]["facilities_in_reach"] == 0
    assert out.metadata["demand_without_access"] == 1
    linear = calculate_accessibility_2sfca(network, supply, demand, 3.0, decay="linear", per=1.0)
    gaussian = calculate_accessibility_2sfca(network, supply, demand, 3.0, decay="gaussian", per=1.0)
    for result, name in ((linear, "E2SFCA"), (gaussian, "E2SFCA")):
        assert result.metadata["method"] == name
        near, far = (result.features[0]["properties"]["accessibility"],
                     result.features[1]["properties"]["accessibility"])
        assert near > far > 0  # decay: nearer demand has more access to the single facility
    # decay weights: linear W(d)=1-d/d0; gaussian reaches 0 at d0
    assert module._decay("linear", 1.0, 2.0) == 0.5 and module._decay("binary", 2.0, 2.0) == 1.0
    assert module._decay("gaussian", 2.0, 2.0) == pytest.approx(0.0, abs=1e-12)
    assert module._decay("gaussian", 0.0, 2.0) == pytest.approx(1.0)
    assert module._decay("linear", 3.0, 2.0) == 0.0


def test_2sfca_barriers_and_validation():
    network = [line([(0, 0), (1, 0)], w=1), line([(1, 0), (2, 0)], w=0)]  # second edge is a barrier
    # without a tolerance the demand beyond the barrier snaps to the nearest usable node (reported
    # as a snap distance); with one it is refused rather than silently moved
    out = calculate_accessibility_2sfca(network, [pt(0, 0)], [pt(1, 0), pt(2, 0)], 5.0,
                                        weight_field="w", per=1.0)
    assert out.features[0]["properties"]["facilities_in_reach"] == 1
    with pytest.raises(ValueError, match="snap_tolerance"):
        calculate_accessibility_2sfca(network, [pt(0, 0)], [pt(1, 0), pt(2, 0)], 5.0,
                                      weight_field="w", per=1.0, snap_tolerance=0.5)
    for kwargs in ({"catchment_cost": 0}, {"decay": "step"}, {"output": "both"}, {"per": 0}):
        with pytest.raises(ValueError):
            calculate_accessibility_2sfca(street(2), [pt(0, 0)], [pt(1, 0)], **{"catchment_cost": 1, **kwargs})
    with pytest.raises(ValueError, match="non-negative"):
        calculate_accessibility_2sfca(street(2), [pt(0, 0, c=-1)], [pt(1, 0)], 1.0, "c")
    assert math.isfinite(calculate_accessibility_2sfca(
        street(2), [pt(0, 0, c="۱۰")], [pt(1, 0, p="۵")], 1.0, "c", "p").features[0]["properties"]["accessibility"])


def test_2sfca_limits(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_facilities": 1})
    with pytest.raises(ValueError, match="facility count"):
        calculate_accessibility_2sfca(street(2), [pt(0, 0), pt(1, 0)], [pt(1, 0)], 1.0)
