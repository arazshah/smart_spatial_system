from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.planning.dag import DagNode, DagPlan
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.op_catalog import get_op, is_supported


def test_op_catalog_contains_spatial_predicate():
    assert is_supported("filter_points_in_polygon")
    op = get_op("filter_points_in_polygon")
    assert op.capability_name == "filter_points_in_polygon"
    assert op.input_map["vector"] == "points"
    assert op.input_map["polygon"] == "polygons"


def test_dag_executor_executes_in_dependency_order():
    calls = []

    def make_value(value):
        calls.append(("make_value", value))
        return {"value": value}

    def add_suffix(payload, suffix):
        calls.append(("add_suffix", payload["value"], suffix))
        return {"value": payload["value"] + suffix}

    capabilities = {
        "make_value": make_value,
        "add_suffix": add_suffix,
    }

    executor = DagExecutor(lambda name: capabilities[name])

    plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="make_value",
                static_params={"value": "hello"},
                produces="json",
            ),
            DagNode(
                id="n2",
                capability_name="add_suffix",
                inputs={"payload": "$node.n1"},
                static_params={"suffix": " world"},
                needs=["n1"],
                produces="json",
            ),
        ],
        output_nodes=["n2"],
    )

    result = executor.execute(plan)

    assert result.success is True
    assert result.output_nodes["n2"] == {"value": "hello world"}
    assert calls == [
        ("make_value", "hello"),
        ("add_suffix", "hello", " world"),
    ]
    assert len(result.trace) == 2
    assert result.trace[0].status == "success"
    assert result.trace[1].status == "success"


def test_dag_executor_resolves_initial_inputs():
    def count_features(features):
        return {"count": len(features)}

    executor = DagExecutor(lambda name: {"count_features": count_features}[name])

    plan = DagPlan(
        nodes=[
            DagNode(
                id="count",
                capability_name="count_features",
                inputs={"features": "$inputs.features"},
                produces="json",
            )
        ],
        output_nodes=["count"],
    )

    result = executor.execute(
        plan,
        initial_inputs={
            "features": [1, 2, 3],
        },
    )

    assert result.success is True
    assert result.output_nodes["count"] == {"count": 3}


def test_dag_executor_reports_missing_reference():
    def identity(value):
        return value

    executor = DagExecutor(lambda name: {"identity": identity}[name])

    plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="identity",
                inputs={"value": "$inputs.missing"},
            )
        ],
        output_nodes=["n1"],
    )

    result = executor.execute(plan)

    assert result.success is False
    assert "could not be resolved" in result.error


def test_dag_executor_rejects_cycle():
    executor = DagExecutor(lambda name: lambda **kwargs: kwargs)

    plan = DagPlan(
        nodes=[
            DagNode(
                id="a",
                capability_name="noop",
                needs=["b"],
            ),
            DagNode(
                id="b",
                capability_name="noop",
                needs=["a"],
            ),
        ],
        output_nodes=["a"],
    )

    result = executor.execute(plan)

    assert result.success is False
    assert "cycle" in result.error.lower()
