from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from geochat_kernel.models import QueryPlan

from orchestrator.planning.capability_resolver import (
    CapabilityResolutionError,
    RegistryCapabilityResolver,
    StaticCapabilityResolver,
)
from orchestrator.planning.kernel_plan_adapter import kernel_plan_to_summary
from orchestrator.planning.runner import (
    make_registry_planning_runner,
    make_static_planning_runner,
)
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec
from plugins.feature_scoring import rank_features, score_features


def _sample_query_spec():
    return QuerySpec(
        raw_query="املاک را امتیاز بده و رتبه‌بندی کن",
        goal="rank_properties",
        entities=[
            EntitySpec(ref="properties", kind="vector"),
        ],
        operations=[
            OperationSpec(
                op="score_features",
                inputs={"vector": "properties"},
                params={
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "near_poi",
                                "field": "distance_to_poi",
                                "type": "inverse_distance",
                                "max_distance": 500,
                                "weight": 0.7,
                            },
                            {
                                "name": "buildable",
                                "field": "__in_polygon__",
                                "type": "boolean",
                                "weight": 0.3,
                            },
                        ],
                    }
                },
                output="scored",
            ),
            OperationSpec(
                op="rank_features",
                inputs={"vector": "scored"},
                params={
                    "score_field": "investment_score",
                    "rank_field": "investment_rank",
                },
                output="ranked",
            ),
        ],
        outputs=[
            OutputSpec(kind="vector", source="ranked"),
        ],
    )


def _sample_features():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {
                    "name": "A",
                    "distance_to_poi": 100,
                    "__in_polygon__": True,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 1]},
                "properties": {
                    "name": "B",
                    "distance_to_poi": 500,
                    "__in_polygon__": False,
                },
            },
        ],
    }


def test_static_capability_resolver_resolves_callable():
    resolver = StaticCapabilityResolver({"score_features": score_features})

    fn = resolver("score_features")

    assert fn is score_features


def test_static_capability_resolver_missing_capability():
    resolver = StaticCapabilityResolver({})

    with pytest.raises(CapabilityResolutionError):
        resolver("missing")


def test_registry_capability_resolver_supports_get_capability():
    class FakeRegistry:
        def get_capability(self, name):
            return {
                "score_features": score_features,
            }.get(name)

    resolver = RegistryCapabilityResolver(FakeRegistry())

    assert resolver("score_features") is score_features


def test_registry_capability_resolver_supports_mapping_attr():
    class FakeRegistry:
        def __init__(self):
            self.capabilities = {
                "rank_features": rank_features,
            }

    resolver = RegistryCapabilityResolver(FakeRegistry())

    assert resolver("rank_features") is rank_features


def test_planning_runner_executes_query_spec_end_to_end():
    runner = make_static_planning_runner(
        {
            "score_features": score_features,
            "rank_features": rank_features,
        }
    )

    result = runner.run(
        _sample_query_spec(),
        initial_inputs={
            "properties": _sample_features(),
        },
    )

    assert result.success is True
    assert result.plan.output_nodes == ["ranked"]
    assert "ranked" in result.output_nodes

    assert isinstance(result.kernel_plan, QueryPlan)
    assert result.kernel_plan.metadata["output_nodes"] == ["ranked"]
    assert [step.id for step in result.kernel_plan.steps] == ["scored", "ranked"]
    assert result.kernel_plan.steps[0].type == "score_features"
    assert result.kernel_plan.steps[1].type == "rank_features"
    assert result.kernel_plan.steps[1].dependencies == ["scored"]
    assert result.kernel_plan.steps[1].input_map == {"features": "scored"}
    assert result.kernel_plan.validate_dag() == []

    kernel_plan_summary = kernel_plan_to_summary(result.kernel_plan)
    assert kernel_plan_summary is not None
    assert kernel_plan_summary["valid"] is True
    assert kernel_plan_summary["step_count"] == 2
    assert kernel_plan_summary["output_nodes"] == ["ranked"]
    assert kernel_plan_summary["steps"][1]["id"] == "ranked"
    assert kernel_plan_summary["steps"][1]["input_sources"] == {"features": "scored"}

    ranked = result.output_nodes["ranked"]

    assert len(ranked.features) == 2
    assert ranked.features[0]["properties"]["name"] == "A"
    assert ranked.features[0]["properties"]["investment_rank"] == 1
    assert ranked.features[0]["properties"]["investment_score"] > ranked.features[1]["properties"]["investment_score"]

    assert len(result.trace) == 2
    assert result.trace[0].status == "success"
    assert result.trace[1].status == "success"


def test_make_registry_planning_runner_with_fake_service_registry():
    class FakeService:
        def __init__(self):
            self.registry = {
                "score_features": score_features,
                "rank_features": rank_features,
            }

    runner = make_registry_planning_runner(FakeService())

    result = runner.run(
        _sample_query_spec(),
        initial_inputs={
            "properties": _sample_features(),
        },
    )

    assert result.success is True
    assert result.output_nodes["ranked"].features[0]["properties"]["name"] == "A"


def test_planning_runner_can_run_with_optional_kernel_execution() -> None:
    runner = make_static_planning_runner(
        {
            "score_features": score_features,
            "rank_features": rank_features,
        }
    )

    query_spec = _sample_query_spec()

    initial_inputs = {
        "properties": _sample_features(),
    }

    result = runner.run_with_kernel_execution(
        query_spec,
        initial_inputs=initial_inputs,
    )

    assert result.success is True

    # Existing production DAG execution is still available.
    assert result.execution.success is True
    assert result.output_nodes["ranked"].features[0]["properties"]["investment_rank"] == 1

    # Kernel plan is still attached.
    assert result.kernel_plan is not None
    assert result.kernel_plan.validate_dag() == []

    # New optional kernel execution result is attached.
    assert result.kernel_execution is not None
    assert result.kernel_execution.success is True
    assert result.kernel_execution.error is None
    assert set(result.kernel_execution.artifacts) == {"scored", "ranked"}
    assert set(result.kernel_execution.output_artifacts) == {"ranked"}

    kernel_ranked = result.kernel_execution.output_nodes["ranked"]
    dag_ranked = result.output_nodes["ranked"]

    assert kernel_ranked.features == dag_ranked.features
    assert kernel_ranked.features[0]["properties"]["investment_rank"] == 1
    assert kernel_ranked.features[0]["properties"]["name"] == "A"


def test_planning_runner_default_run_does_not_execute_kernel_path() -> None:
    runner = make_static_planning_runner(
        {
            "score_features": score_features,
            "rank_features": rank_features,
        }
    )

    result = runner.run(
        _sample_query_spec(),
        initial_inputs={
            "properties": _sample_features(),
        },
    )

    assert result.success is True
    assert result.kernel_plan is not None
    assert result.kernel_execution is None


def test_planning_runner_kernel_execution_summary_is_available() -> None:
    from orchestrator.planning.kernel_execution_bridge import kernel_execution_to_summary

    runner = make_static_planning_runner(
        {
            "score_features": score_features,
            "rank_features": rank_features,
        }
    )

    result = runner.run_with_kernel_execution(
        _sample_query_spec(),
        initial_inputs={
            "properties": _sample_features(),
        },
    )

    summary = kernel_execution_to_summary(result.kernel_execution)

    assert summary is not None
    assert summary["success"] is True
    assert summary["artifact_count"] == 2
    assert summary["output_artifact_ids"] == ["ranked"]
    assert summary["artifacts"][0]["step_id"] == "scored"
    assert summary["artifacts"][1]["step_id"] == "ranked"
    assert summary["context"]["kernel_plan_id"] == result.kernel_plan.id


def test_planning_runner_kernel_execution_parity_summary_matches_dag_outputs() -> None:
    from orchestrator.planning.kernel_execution_bridge import (
        compare_kernel_execution_to_planning_outputs,
    )

    runner = make_static_planning_runner(
        {
            "score_features": score_features,
            "rank_features": rank_features,
        }
    )

    result = runner.run_with_kernel_execution(
        _sample_query_spec(),
        initial_inputs={
            "properties": _sample_features(),
        },
    )

    parity = compare_kernel_execution_to_planning_outputs(result)

    assert parity["available"] is True
    assert parity["success"] is True
    assert parity["dag_success"] is True
    assert parity["kernel_success"] is True
    assert parity["matching_output_node_ids"] is True
    assert parity["output_values_match"] is True
    assert parity["dag_output_node_ids"] == ["ranked"]
    assert parity["kernel_output_node_ids"] == ["ranked"]
    assert parity["missing_in_kernel"] == []
    assert parity["extra_in_kernel"] == []
    assert parity["mismatched_outputs"] == []


def test_planning_runner_kernel_execution_parity_summary_handles_default_run() -> None:
    from orchestrator.planning.kernel_execution_bridge import (
        compare_kernel_execution_to_planning_outputs,
    )

    runner = make_static_planning_runner(
        {
            "score_features": score_features,
            "rank_features": rank_features,
        }
    )

    result = runner.run(
        _sample_query_spec(),
        initial_inputs={
            "properties": _sample_features(),
        },
    )

    parity = compare_kernel_execution_to_planning_outputs(result)

    assert parity["available"] is False
    assert parity["success"] is None
    assert parity["dag_success"] is True
    assert parity["kernel_success"] is None
    assert parity["dag_output_node_ids"] == ["ranked"]
    assert parity["kernel_output_node_ids"] == []



def test_planning_run_result_exposes_structured_error_from_dag_execution() -> None:
    from orchestrator.planning.dag import DagNode, DagPlan
    from orchestrator.planning.runner import PlanningRunner
    from orchestrator.planning.spec import QuerySpec

    def bad_capability() -> dict:
        return {"ok": True}

    class FakePlanner:
        def build(self, query_spec: QuerySpec) -> DagPlan:
            return DagPlan(
                nodes=[
                    DagNode(
                        id="bad_output",
                        capability_name="bad_capability",
                        static_params={
                            "unexpected": True,
                        },
                    )
                ],
                output_nodes=["bad_output"],
                query_spec=query_spec,
            )

    runner = PlanningRunner(
        lambda name: {
            "bad_capability": bad_capability,
        }[name],
        planner=FakePlanner(),
    )

    query_spec = QuerySpec(
        raw_query="run bad capability",
        goal="test_structured_error",
        entities=[],
        operations=[],
        outputs=[],
    )

    result = runner.run(
        query_spec,
        initial_inputs={},
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("Node bad_output failed:")
    assert result.structured_error is not None
    assert result.structured_error["code"] == "capability.contract_failed"
    assert result.structured_error["category"] == "capability_contract_error"
    assert result.structured_error["source"] == "dag_executor"
    assert result.structured_error["details"]["node_id"] == "bad_output"
    assert result.structured_error["details"]["capability_name"] == "bad_capability"
    assert result.structured_error["details"]["stage"] == "capability_execution"
