"""0.6.1: None-safe ordering filters, nearest drop_unmatched default, sample_values,
dwithin spatial join, per-node feature counts in the DAG trace."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from orchestrator.planning.dag import DagNode, DagPlan  # noqa: E402
from orchestrator.planning.dag_executor import DagExecutor  # noqa: E402
from orchestrator.planning.input_layers import (  # noqa: E402
    describe_input_layers,
    render_input_layer_facts,
)
from plugins.nearest_neighbor import find_nearest_neighbors  # noqa: E402
from plugins.spatial_join import spatial_join_features  # noqa: E402
from plugins.spatial_query_filter import _compare_values, filter_features  # noqa: E402


def _pt(x, y, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": props}


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


@pytest.mark.parametrize(
    "op,value",
    [("lt", 5), ("lte", 5), ("gt", 5), ("gte", 5), ("between", [1, 5])],
)
def test_ordering_ops_treat_none_as_unmatched(op, value):
    assert _compare_values(None, op, value, case_sensitive=False) is False


def test_filter_features_with_none_values_does_not_raise():
    data = _fc(_pt(0, 0, price=3), _pt(1, 1, price=None), _pt(2, 2, price=9))
    out = filter_features(data, where={"field": "price", "op": "lte", "value": 5})
    assert [f["properties"]["price"] for f in out.features] == [3]


def test_nearest_drop_unmatched_defaults_true_with_max_distance():
    src = _fc(_pt(0, 0, n="near"), _pt(100, 100, n="far"))
    dst = _fc(_pt(1, 0))
    capped = find_nearest_neighbors(src, dst, k=1, max_distance=5)
    assert [f["properties"]["n"] for f in capped.features] == ["near"]
    kept = find_nearest_neighbors(src, dst, k=1, max_distance=5, drop_unmatched=False)
    assert len(kept.features) == 2
    uncapped = find_nearest_neighbors(src, dst, k=1)
    assert len(uncapped.features) == 2


def test_describe_input_layers_sample_values():
    layer = _fc(*[_pt(i, i, kind="اجاره" if i % 2 else "فروش", name=f"n{i}") for i in range(40)])
    plain = describe_input_layers({"p": layer})[0]
    assert plain.field_values == ()
    sampled = describe_input_layers({"p": layer}, sample_values=True)[0]
    values = dict(sampled.field_values)
    assert set(values["kind"]) == {"اجاره", "فروش"}
    assert "name" not in values  # 40 distinct values > 20
    text = render_input_layer_facts((sampled,))
    assert "Values of kind" in text and "اجاره" in text


def test_spatial_join_dwithin_point_to_line():
    line = _fc({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [100, 0]]}, "properties": {"street": "A"}})
    pts = _fc(_pt(50, 10, id=1), _pt(50, 200, id=2))
    assert len(spatial_join_features(pts, line, predicate="intersects").features) == 0
    out = spatial_join_features(pts, line, predicate="dwithin", distance_m=30)
    assert [f["properties"]["id"] for f in out.features] == [1]
    out_py = spatial_join_features(pts, line, predicate="dwithin", distance_m=30, engine="python")
    assert [f["properties"]["id"] for f in out_py.features] == [1]
    with pytest.raises(ValueError, match="distance_m"):
        spatial_join_features(pts, line, predicate="dwithin")


def test_dag_trace_feature_counts_and_filter_conditions():
    caps = {"filter_features": filter_features}
    plan = DagPlan(
        nodes=[
            DagNode(
                id="f",
                capability_name="filter_features",
                inputs={"features": "$inputs.pts"},
                static_params={"where": {"field": "price", "op": "lt", "value": 5}},
            )
        ],
        output_nodes=["f"],
    )
    data = _fc(_pt(0, 0, price=1), _pt(1, 1, price=None), _pt(2, 2, price=9))
    result = DagExecutor(lambda n: caps[n]).execute(plan, initial_inputs={"pts": data})
    assert result.success
    t = result.trace[0]
    assert (t.input_feature_count, t.output_feature_count) == (3, 1)
    assert t.metadata["applied_conditions"]["where"]["value"] == 5
