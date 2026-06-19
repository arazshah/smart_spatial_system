from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.planner import DeterministicPlanner, PlanningError
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec
from plugins.feature_scoring import rank_features, score_features


def test_planner_builds_scoring_and_ranking_dag():
    spec = QuerySpec(
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
                output="scored_properties",
            ),
            OperationSpec(
                op="rank_features",
                inputs={"vector": "scored_properties"},
                params={
                    "score_field": "investment_score",
                    "rank_field": "investment_rank",
                },
                output="ranked_properties",
            ),
        ],
        outputs=[
            OutputSpec(kind="map_layer", source="ranked_properties"),
        ],
    )

    plan = DeterministicPlanner().build(spec)

    assert len(plan.nodes) == 2
    assert plan.nodes[0].id == "scored_properties"
    assert plan.nodes[0].capability_name == "score_features"
    assert plan.nodes[0].inputs["features"] == "$inputs.properties"

    assert plan.nodes[1].id == "ranked_properties"
    assert plan.nodes[1].capability_name == "rank_features"
    assert plan.nodes[1].inputs["features"] == "$node.scored_properties"
    assert plan.nodes[1].needs == ["scored_properties"]

    assert plan.output_nodes == ["ranked_properties"]


def test_planner_builds_real_estate_spatial_chain_shape():
    spec = QuerySpec(
        raw_query="املاک نزدیک مترو، داخل محدوده مجاز، امتیازدهی و رتبه‌بندی",
        goal="rank_real_estate",
        entities=[
            EntitySpec(ref="properties", kind="vector"),
            EntitySpec(ref="poi", kind="vector"),
            EntitySpec(ref="buildable_zone", kind="vector"),
        ],
        operations=[
            OperationSpec(
                op="filter_by_distance",
                inputs={
                    "vector": "properties",
                    "reference": "poi",
                },
                params={
                    "max_distance_m": 500,
                    "k": 1,
                    "drop_unmatched": True,
                },
                output="near_poi_properties",
            ),
            OperationSpec(
                op="filter_points_in_polygon",
                inputs={
                    "vector": "near_poi_properties",
                    "polygon": "buildable_zone",
                },
                params={
                    "predicate": "within",
                    "drop_outside": True,
                },
                output="buildable_near_poi_properties",
            ),
            OperationSpec(
                op="score_features",
                inputs={
                    "vector": "buildable_near_poi_properties",
                },
                params={
                    "scoring_spec": {
                        "output_field": "investment_score",
                        "scale": 100,
                        "factors": [
                            {
                                "name": "inside_buildable",
                                "field": "__in_polygon__",
                                "type": "boolean",
                                "weight": 1,
                            }
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
            OutputSpec(kind="report", source="ranked", format="pdf"),
        ],
    )

    plan = DeterministicPlanner().build(spec)

    assert [node.capability_name for node in plan.nodes] == [
        "find_nearest_neighbors",
        "filter_points_in_polygon",
        "score_features",
        "rank_features",
    ]

    assert plan.nodes[0].inputs["source_features"] == "$inputs.properties"
    assert plan.nodes[0].inputs["target_features"] == "$inputs.poi"

    assert plan.nodes[1].inputs["points"] == "$node.near_poi_properties"
    assert plan.nodes[1].inputs["polygons"] == "$inputs.buildable_zone"
    assert plan.nodes[1].needs == ["near_poi_properties"]

    assert plan.nodes[2].needs == ["buildable_near_poi_properties"]
    assert plan.nodes[3].needs == ["scored"]

    assert plan.output_nodes == ["ranked"]


def test_planner_and_executor_run_scoring_ranking_end_to_end():
    features = {
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

    spec = QuerySpec(
        raw_query="امتیازدهی و رتبه‌بندی",
        goal="rank",
        entities=[EntitySpec(ref="properties", kind="vector")],
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
        outputs=[OutputSpec(kind="vector", source="ranked")],
    )

    plan = DeterministicPlanner().build(spec)

    capabilities = {
        "score_features": score_features,
        "rank_features": rank_features,
    }

    executor = DagExecutor(lambda name: capabilities[name])

    result = executor.execute(
        plan,
        initial_inputs={
            "properties": features,
        },
    )

    assert result.success is True

    ranked = result.output_nodes["ranked"]
    assert len(ranked.features) == 2

    first = ranked.features[0]["properties"]
    second = ranked.features[1]["properties"]

    assert first["name"] == "A"
    assert first["investment_rank"] == 1
    assert first["investment_score"] > second["investment_score"]


def test_planner_rejects_unknown_operation():
    spec = QuerySpec(
        raw_query="unknown",
        goal="bad",
        operations=[
            OperationSpec(
                op="does_not_exist",
                output="x",
            )
        ],
    )

    with pytest.raises(PlanningError):
        DeterministicPlanner().build(spec)


def test_planner_rejects_missing_required_input_role():
    spec = QuerySpec(
        raw_query="rank",
        goal="bad",
        entities=[EntitySpec(ref="properties", kind="vector")],
        operations=[
            OperationSpec(
                op="rank_features",
                inputs={},
                params={"score_field": "score"},
                output="ranked",
            )
        ],
    )

    with pytest.raises(PlanningError):
        DeterministicPlanner().build(spec)
