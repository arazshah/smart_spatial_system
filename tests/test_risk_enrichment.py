from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.op_catalog import get_op, is_supported
from orchestrator.planning.planner import DeterministicPlanner
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec
from plugins.feature_scoring import rank_features, score_features
from plugins.risk_enrichment import enrich_risk


def _features():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {
                    "id": "p1",
                    "name": "A",
                    "flood_zone": "C",
                    "distance_to_poi": 100,
                    "distance_to_road": 80,
                    "inside_buildable_zone": True,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 1]},
                "properties": {
                    "id": "p2",
                    "name": "B",
                    "flood_zone": "A",
                    "distance_to_poi": 400,
                    "distance_to_road": 500,
                    "inside_buildable_zone": True,
                },
            },
        ],
    }


def test_enrich_risk_adds_default_risk_fields():
    result = enrich_risk(
        _features(),
        default_risks={
            "flood_risk": "low",
            "earthquake_risk": "medium",
            "fire_risk": "low",
        },
    )

    props = result.features[0]["properties"]

    assert props["flood_risk"] == "low"
    assert props["earthquake_risk"] == "medium"
    assert props["fire_risk"] == "low"

    assert result.metadata["operation"] == "enrich_risk"
    assert result.metadata["feature_count"] == 2


def test_enrich_risk_supports_overrides_by_id():
    result = enrich_risk(
        _features(),
        overrides={
            "p1": {
                "flood_risk": "high",
                "fire_risk": "medium",
            }
        },
    )

    props1 = result.features[0]["properties"]
    props2 = result.features[1]["properties"]

    assert props1["flood_risk"] == "high"
    assert props1["fire_risk"] == "medium"

    assert props2["flood_risk"] == "low"
    assert props2["fire_risk"] == "low"

    assert result.metadata["override_applied_count"] == 2


def test_enrich_risk_supports_mapping_rules_with_overwrite():
    result = enrich_risk(
        _features(),
        rules=[
            {
                "target": "flood_risk",
                "source": "flood_zone",
                "mapping": {
                    "A": "low",
                    "B": "medium",
                    "C": "high",
                },
            }
        ],
        overwrite=True,
    )

    props1 = result.features[0]["properties"]
    props2 = result.features[1]["properties"]

    assert props1["flood_risk"] == "high"
    assert props2["flood_risk"] == "low"
    assert result.metadata["rule_applied_count"] == 2


def test_op_catalog_contains_enrich_risk():
    assert is_supported("enrich_risk")

    op = get_op("enrich_risk")

    assert op.capability_name == "enrich_risk"
    assert op.input_map["vector"] == "features"
    assert op.param_map["default_risks"] == "default_risks"


def test_planner_executor_runs_risk_score_rank_chain():
    scoring_spec = {
        "output_field": "investment_score",
        "scale": 100,
        "factors": [
            {
                "name": "near_poi",
                "field": "distance_to_poi",
                "type": "inverse_distance",
                "max_distance": 500,
                "weight": 0.35,
            },
            {
                "name": "buildable",
                "field": "inside_buildable_zone",
                "type": "boolean",
                "weight": 0.25,
            },
            {
                "name": "near_road",
                "field": "distance_to_road",
                "type": "inverse_distance",
                "max_distance": 1000,
                "weight": 0.20,
            },
            {
                "name": "flood",
                "field": "flood_risk",
                "type": "risk_level",
                "weight": 0.10,
            },
            {
                "name": "earthquake",
                "field": "earthquake_risk",
                "type": "risk_level",
                "weight": 0.05,
            },
            {
                "name": "fire",
                "field": "fire_risk",
                "type": "risk_level",
                "weight": 0.05,
            },
        ],
    }

    spec = QuerySpec(
        raw_query="املاک را از نظر ریسک هم امتیاز بده",
        goal="risk_score_rank",
        entities=[
            EntitySpec(ref="properties", kind="vector"),
        ],
        operations=[
            OperationSpec(
                op="enrich_risk",
                inputs={"vector": "properties"},
                params={
                    "rules": [
                        {
                            "target": "flood_risk",
                            "source": "flood_zone",
                            "mapping": {
                                "A": "low",
                                "B": "medium",
                                "C": "high",
                            },
                        }
                    ],
                    "default_risks": {
                        "flood_risk": "low",
                        "earthquake_risk": "low",
                        "fire_risk": "low",
                    },
                    "overwrite": True,
                },
                output="risk_enriched",
            ),
            OperationSpec(
                op="score_features",
                inputs={"vector": "risk_enriched"},
                params={
                    "scoring_spec": scoring_spec,
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

    plan = DeterministicPlanner().build(spec)

    capabilities = {
        "enrich_risk": enrich_risk,
        "score_features": score_features,
        "rank_features": rank_features,
    }

    executor = DagExecutor(lambda name: capabilities[name])

    result = executor.execute(
        plan,
        initial_inputs={
            "properties": _features(),
        },
    )

    assert result.success is True

    ranked = result.output_nodes["ranked"]
    assert len(ranked.features) == 2

    first = ranked.features[0]["properties"]
    second = ranked.features[1]["properties"]

    assert first["investment_rank"] == 1
    assert first["investment_score"] >= second["investment_score"]
    assert "flood_risk" in first
    assert "earthquake_risk" in first
    assert "fire_risk" in first
