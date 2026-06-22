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


def test_dag_executor_capability_contract_failure_has_structured_error() -> None:
    from orchestrator.planning.dag import DagNode, DagPlan
    from orchestrator.planning.dag_executor import DagExecutor

    plan = DagPlan(
        nodes=[
            DagNode(
                id="bad_node",
                capability_name="bad_capability",
                static_params={
                    "unexpected": True,
                },
            )
        ],
        output_nodes=["bad_node"],
    )

    def bad_capability() -> dict:
        return {"ok": True}

    result = DagExecutor(
        lambda name: {
            "bad_capability": bad_capability,
        }[name]
    ).execute(plan)

    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("Node bad_node failed:")
    assert result.structured_error is not None
    assert result.structured_error["code"] == "capability.contract_failed"
    assert result.structured_error["category"] == "capability_contract_error"
    assert result.structured_error["source"] == "dag_executor"
    assert result.structured_error["retryable"] is False
    assert result.structured_error["details"]["node_id"] == "bad_node"
    assert result.structured_error["details"]["capability_name"] == "bad_capability"
    assert result.structured_error["details"]["stage"] == "capability_execution"
    assert result.structured_error["details"]["exception_type"] == "TypeError"


def test_dag_executor_unresolved_reference_has_structured_error() -> None:
    from orchestrator.planning.dag import DagNode, DagPlan
    from orchestrator.planning.dag_executor import DagExecutor

    plan = DagPlan(
        nodes=[
            DagNode(
                id="needs_input",
                capability_name="identity",
                inputs={
                    "value": "$inputs.missing_value",
                },
            )
        ],
        output_nodes=["needs_input"],
    )

    def identity(value):
        return value

    result = DagExecutor(
        lambda name: {
            "identity": identity,
        }[name]
    ).execute(
        plan,
        initial_inputs={},
    )

    assert result.success is False
    assert result.error is not None
    assert "missing_value" in result.error
    assert result.structured_error is not None
    assert result.structured_error["code"] == "dag.reference_resolution_failed"
    assert result.structured_error["category"] == "validation_error"
    assert result.structured_error["source"] == "dag_executor"
    assert result.structured_error["details"]["node_id"] == "needs_input"
    assert result.structured_error["details"]["capability_name"] == "identity"
    assert result.structured_error["details"]["stage"] == "input_resolution"


def test_dag_executor_plan_validation_failure_has_structured_error() -> None:
    from orchestrator.planning.dag import DagNode, DagPlan
    from orchestrator.planning.dag_executor import DagExecutor

    plan = DagPlan(
        nodes=[
            DagNode(
                id="node_a",
                capability_name="identity",
            )
        ],
        output_nodes=["unknown_output"],
    )

    result = DagExecutor(lambda name: lambda **kwargs: kwargs).execute(plan)

    assert result.success is False
    assert result.error == "Unknown output node: unknown_output."
    assert result.structured_error is not None
    assert result.structured_error["code"] == "dag.validation_failed"
    assert result.structured_error["category"] == "validation_error"
    assert result.structured_error["source"] == "dag_executor"
    assert result.structured_error["details"]["stage"] == "plan_validation"


def test_dag_executor_preserves_provider_structured_error() -> None:
    from orchestrator.planning.dag import DagNode, DagPlan
    from orchestrator.planning.dag_executor import DagExecutor
    from orchestrator.provider_error_mapping import make_provider_execution_error

    def broken_provider():
        exc = RuntimeError("connection refused")
        raise make_provider_execution_error(
            exc,
            provider="postgis",
            operation="execute_query",
            source="postgis_connector",
            message="Failed to execute PostGIS query. Error: connection refused",
        ) from exc

    plan = DagPlan(
        nodes=[
            DagNode(
                id="provider_node",
                capability_name="query_database_postgis",
            )
        ],
        output_nodes=["provider_node"],
    )

    result = DagExecutor(
        lambda name: {
            "query_database_postgis": broken_provider,
        }[name]
    ).execute(plan)

    assert result.success is False
    assert result.error is not None
    assert result.structured_error is not None
    assert result.structured_error["code"] == "provider.connection_failed"
    assert result.structured_error["category"] == "provider_error"
    assert result.structured_error["source"] == "postgis_connector"
    assert result.structured_error["details"]["provider"] == "postgis"
    assert result.structured_error["details"]["operation"] == "execute_query"
    assert result.structured_error["details"]["node_id"] == "provider_node"
    assert result.structured_error["details"]["capability_name"] == "query_database_postgis"
    assert result.structured_error["details"]["stage"] == "capability_execution"
