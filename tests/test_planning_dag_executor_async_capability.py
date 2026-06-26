import pytest

from orchestrator.planning.dag import DagNode, DagPlan
from orchestrator.planning.dag_executor import DagExecutor


def test_dag_executor_awaits_async_capability_output() -> None:
    async def async_capability(value: int, increment: int) -> dict[str, int]:
        return {
            "value": value,
            "increment": increment,
            "result": value + increment,
        }

    plan = DagPlan(
        nodes=[
            DagNode(
                id="async_result",
                capability_name="async_add",
                inputs={"value": "$inputs.value"},
                static_params={"increment": 7},
                produces="json",
            )
        ],
        output_nodes=["async_result"],
    )

    executor = DagExecutor(
        capability_resolver=lambda capability_name: async_capability
    )

    result = executor.execute(
        plan,
        initial_inputs={"value": 5},
    )

    assert result.success is True
    assert result.error is None
    assert result.outputs["async_result"] == {
        "value": 5,
        "increment": 7,
        "result": 12,
    }
    assert result.output_nodes["async_result"]["result"] == 12
    assert result.trace[0].status == "success"
    assert result.trace[0].output_summary["type"] == "dict"


def test_dag_executor_reports_async_capability_exception() -> None:
    async def failing_async_capability(value: int) -> dict[str, int]:
        raise RuntimeError(f"async boom: {value}")

    plan = DagPlan(
        nodes=[
            DagNode(
                id="async_failure",
                capability_name="async_fail",
                inputs={"value": "$inputs.value"},
                produces="json",
            )
        ],
        output_nodes=["async_failure"],
    )

    executor = DagExecutor(
        capability_resolver=lambda capability_name: failing_async_capability
    )

    result = executor.execute(
        plan,
        initial_inputs={"value": 9},
    )

    assert result.success is False
    assert "async boom: 9" in str(result.error)
    assert result.trace[0].status == "failed"
    assert "async boom: 9" in str(result.trace[0].error)
    assert result.structured_error is not None


@pytest.mark.filterwarnings("error:coroutine.*was never awaited")
def test_dag_executor_does_not_store_coroutine_object_for_async_capability() -> None:
    async def async_capability() -> str:
        return "done"

    plan = DagPlan(
        nodes=[
            DagNode(
                id="result",
                capability_name="async_cap",
                produces="json",
            )
        ],
        output_nodes=["result"],
    )

    executor = DagExecutor(
        capability_resolver=lambda capability_name: async_capability
    )

    result = executor.execute(plan)

    assert result.success is True
    assert result.outputs["result"] == "done"
    assert result.output_nodes["result"] == "done"
