from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geochat_kernel.models import PlanStep, QueryPlan

from orchestrator.planning.dag import DagNode, DagPlan
from orchestrator.planning.kernel_plan_adapter import (
    dag_node_to_plan_step,
    dag_plan_to_query_plan,
    query_spec_to_query_plan,
)
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec


def test_dag_node_to_plan_step_maps_basic_fields() -> None:
    node = DagNode(
        id="ranked",
        capability_name="rank_features",
        inputs={
            "vector": "$node.scored",
            "threshold": 10,
            "project": "$inputs.project",
        },
        static_params={
            "score_field": "investment_score",
        },
        needs=["scored"],
        produces="vector",
        metadata={
            "datasource_ids": ["ds_1"],
            "timeout_s": 30,
            "max_retries": 2,
            "cacheable": False,
        },
    )

    step = dag_node_to_plan_step(node)

    assert isinstance(step, PlanStep)
    assert step.id == "ranked"
    assert step.type == "rank_features"
    assert step.name == "rank_features"
    assert step.datasource_ids == ["ds_1"]
    assert step.dependencies == ["scored"]
    assert step.input_map == {"vector": "scored"}
    assert step.parameters["score_field"] == "investment_score"

    assert step.timeout_s == 30.0
    assert step.max_retries == 2
    assert step.cacheable is False

    assert step.metadata["source"] == "smart_spatial_system.dag"
    assert step.metadata["capability_name"] == "rank_features"
    assert step.metadata["produces"] == "vector"
    assert step.metadata["external_input_map"] == {"project": "project"}
    assert step.metadata["literal_inputs"] == {"threshold": 10}


def test_dag_plan_to_query_plan_preserves_dependencies_outputs_and_metadata() -> None:
    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="load_features",
                inputs={
                    "source": "$inputs.properties",
                },
                produces="vector",
            ),
            DagNode(
                id="n2",
                capability_name="rank_features",
                inputs={
                    "vector": "$node.n1",
                },
                static_params={
                    "score_field": "score",
                },
                needs=["n1"],
                produces="vector",
            ),
        ],
        output_nodes=["n2"],
        metadata={
            "language": "fa",
        },
    )

    plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_test_001",
        plan_id="plan_test_001",
    )

    assert isinstance(plan, QueryPlan)
    assert plan.id == "plan_test_001"
    assert plan.query_ir_id == "query_ir_test_001"
    assert plan.planner_name == "smart_spatial_system.deterministic_planner"
    assert plan.parallel_execution_allowed is False
    assert plan.metadata["output_nodes"] == ["n2"]
    assert plan.metadata["dag_metadata"]["language"] == "fa"

    assert len(plan.steps) == 2
    assert plan.steps[0].id == "n1"
    assert plan.steps[0].metadata["external_input_map"] == {"source": "properties"}

    assert plan.steps[1].id == "n2"
    assert plan.steps[1].dependencies == ["n1"]
    assert plan.steps[1].input_map == {"vector": "n1"}

    assert plan.validate_dag() == []


def test_query_spec_to_query_plan_uses_deterministic_planner() -> None:
    query_spec = QuerySpec(
        raw_query="املاک را امتیاز بده و رتبه‌بندی کن",
        goal="rank_properties",
        entities=[
            EntitySpec(ref="properties", kind="vector"),
        ],
        operations=[
            OperationSpec(
                op="score_features",
                inputs={
                    "vector": "properties",
                },
                params={
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [],
                    }
                },
                output="scored",
            ),
            OperationSpec(
                op="rank_features",
                inputs={
                    "vector": "scored",
                },
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
        metadata={
            "query_ir_id": "query_ir_from_spec",
        },
    )

    plan = query_spec_to_query_plan(
        query_spec,
        plan_id="plan_from_query_spec",
    )

    assert isinstance(plan, QueryPlan)
    assert plan.id == "plan_from_query_spec"
    assert plan.query_ir_id == "query_ir_from_spec"
    assert plan.metadata["output_nodes"] == ["ranked"]
    assert plan.metadata["query_spec"]["goal"] == "rank_properties"

    assert [step.id for step in plan.steps] == ["scored", "ranked"]
    assert plan.steps[0].type == "score_features"
    assert plan.steps[1].type == "rank_features"
    assert plan.steps[1].dependencies == ["scored"]
    assert plan.steps[1].input_map == {"features": "scored"}

    assert plan.validate_dag() == []


def test_kernel_plan_to_summary_returns_public_safe_plan_summary() -> None:
    from orchestrator.planning.kernel_plan_adapter import kernel_plan_to_summary

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
                        "output_field": "score",
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
                    "score_field": "score",
                },
                needs=["scored"],
                produces="vector",
            ),
        ],
        output_nodes=["ranked"],
    )

    plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_summary_001",
        plan_id="plan_summary_001",
    )

    summary = kernel_plan_to_summary(plan)

    assert summary is not None
    assert summary["id"] == "plan_summary_001"
    assert summary["query_ir_id"] == "query_ir_summary_001"
    assert summary["planner_name"] == "smart_spatial_system.deterministic_planner"
    assert summary["step_count"] == 2
    assert summary["valid"] is True
    assert summary["problems"] == []
    assert summary["output_nodes"] == ["ranked"]

    assert summary["steps"][0]["id"] == "scored"
    assert summary["steps"][0]["type"] == "score_features"
    assert summary["steps"][0]["parameter_keys"] == ["scoring_spec"]
    assert "scoring_spec" not in summary["steps"][0]

    assert summary["steps"][1]["id"] == "ranked"
    assert summary["steps"][1]["dependencies"] == ["scored"]
    assert summary["steps"][1]["input_names"] == ["features"]
    assert summary["steps"][1]["input_sources"] == {"features": "scored"}
    assert summary["steps"][1]["parameter_keys"] == ["score_field"]


def test_kernel_plan_to_summary_accepts_none() -> None:
    from orchestrator.planning.kernel_plan_adapter import kernel_plan_to_summary

    assert kernel_plan_to_summary(None) is None


def test_compare_dag_plan_to_query_plan_reports_valid_equivalent_plan() -> None:
    from orchestrator.planning.kernel_plan_adapter import (
        compare_dag_plan_to_query_plan,
        format_plan_comparison_report,
    )

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
                        "output_field": "score",
                    }
                },
                produces="vector",
            ),
            DagNode(
                id="ranked",
                capability_name="rank_features",
                inputs={
                    "features": "$node.scored",
                    "limit": 10,
                },
                static_params={
                    "score_field": "score",
                },
                needs=["scored"],
                produces="vector",
            ),
        ],
        output_nodes=["ranked"],
    )

    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_compare_001",
        plan_id="plan_compare_001",
    )

    comparison = compare_dag_plan_to_query_plan(
        dag_plan,
        query_plan,
    )

    assert comparison["valid"] is True
    assert comparison["problems"] == []
    assert comparison["dag_node_count"] == 2
    assert comparison["kernel_step_count"] == 2
    assert comparison["missing_steps"] == []
    assert comparison["extra_steps"] == []
    assert comparison["output_nodes_match"] is True
    assert comparison["kernel_validate_dag_problems"] == []

    assert len(comparison["steps"]) == 2
    assert all(step["valid"] for step in comparison["steps"])

    ranked = comparison["steps"][1]
    assert ranked["id"] == "ranked"
    assert ranked["dependencies_match"] is True
    assert ranked["input_map_matches"] is True
    assert ranked["parameters_match"] is True
    assert ranked["literal_inputs_match"] is True

    report = format_plan_comparison_report(comparison)
    assert "VALID" in report
    assert "dag=2" in report
    assert "kernel=2" in report


def test_compare_dag_plan_to_query_plan_detects_missing_step() -> None:
    from geochat_kernel.models import QueryPlan

    from orchestrator.planning.kernel_plan_adapter import compare_dag_plan_to_query_plan

    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="load_features",
            )
        ],
        output_nodes=["n1"],
    )

    query_plan = QueryPlan(
        id="plan_missing_step",
        query_ir_id="query_ir_missing_step",
        steps=[],
        metadata={
            "output_nodes": ["n1"],
        },
    )

    comparison = compare_dag_plan_to_query_plan(
        dag_plan,
        query_plan,
    )

    assert comparison["valid"] is False
    assert comparison["missing_steps"] == ["n1"]
    assert any("missing" in problem.lower() for problem in comparison["problems"])


def test_compare_dag_plan_to_query_plan_detects_output_node_mismatch() -> None:
    from orchestrator.planning.kernel_plan_adapter import compare_dag_plan_to_query_plan

    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="load_features",
            )
        ],
        output_nodes=["n1"],
    )

    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_output_mismatch",
        plan_id="plan_output_mismatch",
    )
    query_plan.metadata["output_nodes"] = ["other"]

    comparison = compare_dag_plan_to_query_plan(
        dag_plan,
        query_plan,
    )

    assert comparison["valid"] is False
    assert comparison["output_nodes_match"] is False
    assert comparison["dag_output_nodes"] == ["n1"]
    assert comparison["kernel_output_nodes"] == ["other"]
    assert any("Output nodes mismatch" in problem for problem in comparison["problems"])


def test_compare_dag_plan_to_query_plan_detects_step_field_mismatch() -> None:
    from orchestrator.planning.kernel_plan_adapter import compare_dag_plan_to_query_plan

    dag_plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="load_features",
                static_params={
                    "limit": 10,
                },
                produces="vector",
            )
        ],
        output_nodes=["n1"],
    )

    query_plan = dag_plan_to_query_plan(
        dag_plan,
        query_ir_id="query_ir_step_mismatch",
        plan_id="plan_step_mismatch",
    )

    query_plan.steps[0].type = "wrong_capability"
    query_plan.steps[0].parameters = {
        "limit": 20,
    }
    query_plan.steps[0].metadata["produces"] = "json"

    comparison = compare_dag_plan_to_query_plan(
        dag_plan,
        query_plan,
    )

    assert comparison["valid"] is False
    assert comparison["steps"][0]["valid"] is False
    assert comparison["steps"][0]["type_matches"] is False
    assert comparison["steps"][0]["parameters_match"] is False
    assert comparison["steps"][0]["produces_matches"] is False
    assert any("type mismatch" in problem for problem in comparison["problems"])
    assert any("parameters payload mismatch" in problem for problem in comparison["problems"])
