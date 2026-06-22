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
