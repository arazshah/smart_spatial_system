from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geochat_kernel.models.execution_artifact import ExecutionArtifact

from orchestrator.planning.dag import DagNode, DagPlan
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.kernel_execution_bridge import (
    CapabilityStepHandler,
    artifact_to_value,
    execute_kernel_plan_with_capabilities_sync,
    value_to_execution_artifact,
)
from orchestrator.planning.kernel_plan_adapter import dag_plan_to_query_plan


def _score_features(features: list[dict], scoring_spec: dict) -> list[dict]:
    output_field = scoring_spec.get("output_field", "score")
    scored = []
    for index, feature in enumerate(features):
        item = dict(feature)
        item[output_field] = 100 - index
        scored.append(item)
    return scored


def _rank_features(
    features: list[dict],
    score_field: str,
    rank_field: str = "rank",
) -> list[dict]:
    ranked = sorted(
        features,
        key=lambda item: item.get(score_field, 0),
        reverse=True,
    )
    output = []
    for index, feature in enumerate(ranked, start=1):
        item = dict(feature)
        item[rank_field] = index
        output.append(item)
    return output


def _capability_resolver(name: str):
    capabilities = {
        "score_features": _score_features,
        "rank_features": _rank_features,
    }
    return capabilities[name]


def test_value_to_execution_artifact_attaches_live_value() -> None:
    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="load_features",
                produces="vector",
            )
        ],
        output_nodes=["n1"],
    )
    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_artifact",
        plan_id="plan_artifact",
    )
    step = query_plan.steps[0]

    value = [{"name": "A"}]
    artifact = value_to_execution_artifact(
        value,
        step=step,
        produced_by="test",
    )

    assert isinstance(artifact, ExecutionArtifact)
    assert artifact.step_id == "n1"
    assert artifact.kind == "vector"
    assert artifact.payload["summary"]["type"] == "list"
    assert artifact.payload["summary"]["length"] == 1
    assert artifact_to_value(artifact) == value


def test_capability_step_handler_matches_registered_capability() -> None:
    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="scored",
                capability_name="score_features",
            )
        ],
        output_nodes=["scored"],
    )
    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_match",
        plan_id="plan_match",
    )

    handler = CapabilityStepHandler(_capability_resolver)

    assert handler.match_score(query_plan.steps[0]) == 1.0


def test_execute_kernel_plan_with_capabilities_runs_two_step_plan() -> None:
    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="scored",
                capability_name="score_features",
                inputs={
                    "features": "$inputs.properties",
                },
                static_params={
                    "scoring_spec": {
                        "output_field": "investment_score",
                    }
                },
                produces="vector",
            ),
            DagNode(
                id="ranked",
                capability_name="rank_features",
                inputs={
                    "features": "$node.scored",
                },
                static_params={
                    "score_field": "investment_score",
                    "rank_field": "investment_rank",
                },
                needs=["scored"],
                produces="vector",
            ),
        ],
        output_nodes=["ranked"],
    )

    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_kernel_exec",
        plan_id="plan_kernel_exec",
    )

    initial_inputs = {
        "properties": [
            {"name": "A"},
            {"name": "B"},
        ]
    }

    result = execute_kernel_plan_with_capabilities_sync(
        query_plan,
        capability_resolver=_capability_resolver,
        initial_inputs=initial_inputs,
    )

    assert result.success is True
    assert result.error is None
    assert set(result.artifacts) == {"scored", "ranked"}
    assert set(result.output_artifacts) == {"ranked"}

    scored = result.outputs["scored"]
    ranked = result.output_nodes["ranked"]

    assert scored[0]["investment_score"] == 100
    assert scored[1]["investment_score"] == 99

    assert ranked[0]["name"] == "A"
    assert ranked[0]["investment_rank"] == 1
    assert ranked[1]["name"] == "B"
    assert ranked[1]["investment_rank"] == 2

    assert result.artifacts["ranked"].step_id == "ranked"
    assert result.artifacts["ranked"].produced_by == (
        "smart_spatial_system.capability_step_handler"
    )
    assert result.context is not None
    assert result.context.metadata["kernel_plan_id"] == "plan_kernel_exec"


def test_kernel_bridge_output_matches_existing_dag_executor_output() -> None:
    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="scored",
                capability_name="score_features",
                inputs={
                    "features": "$inputs.properties",
                },
                static_params={
                    "scoring_spec": {
                        "output_field": "investment_score",
                    }
                },
                produces="vector",
            ),
            DagNode(
                id="ranked",
                capability_name="rank_features",
                inputs={
                    "features": "$node.scored",
                },
                static_params={
                    "score_field": "investment_score",
                    "rank_field": "investment_rank",
                },
                needs=["scored"],
                produces="vector",
            ),
        ],
        output_nodes=["ranked"],
    )

    initial_inputs = {
        "properties": [
            {"name": "A"},
            {"name": "B"},
            {"name": "C"},
        ]
    }

    dag_result = DagExecutor(_capability_resolver).execute(
        dag_plan,
        initial_inputs=initial_inputs,
    )

    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_compare_exec",
        plan_id="plan_compare_exec",
    )

    kernel_result = execute_kernel_plan_with_capabilities_sync(
        query_plan,
        capability_resolver=_capability_resolver,
        initial_inputs=initial_inputs,
    )

    assert dag_result.success is True
    assert kernel_result.success is True
    assert kernel_result.output_nodes["ranked"] == dag_result.output_nodes["ranked"]


def test_execute_kernel_plan_with_capabilities_returns_structured_error() -> None:
    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="scored",
                capability_name="score_features",
                inputs={
                    "features": "$inputs.missing_properties",
                },
                static_params={
                    "scoring_spec": {
                        "output_field": "investment_score",
                    }
                },
                produces="vector",
            )
        ],
        output_nodes=["scored"],
    )

    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_missing_input",
        plan_id="plan_missing_input",
    )

    result = execute_kernel_plan_with_capabilities_sync(
        query_plan,
        capability_resolver=_capability_resolver,
        initial_inputs={},
    )

    assert result.success is False
    assert result.artifacts == {}
    assert result.output_nodes == {}
    assert result.error is not None
    assert "missing_properties" in result.error
