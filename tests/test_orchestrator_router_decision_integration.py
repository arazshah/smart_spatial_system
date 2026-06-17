"""
Integration tests for router decision attached to routing-aware runner/response.

Run:
    pytest tests/test_orchestrator_router_decision_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.router_decision import RouterDecisionConfig  # noqa: E402
from orchestrator.routing_aware_natural_query_runner import (  # noqa: E402
    run_natural_query_with_routing_evidence,
)


SAFE_MODULES = [
    "plugins.spectral_indices",
    "plugins.raster_threshold",
    "plugins.raster_to_vector",
]


NDVI_QUERY = (
    "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است "
    "را به پلیگون تبدیل کن"
)


SATELLITE_RASTER_2BAND = {
    "data": [
        [
            [1, 1, 1],
            [1, 1, 1],
        ],
        [
            [2, 1, 4],
            [1, 3, 0.5],
        ],
    ],
    "metadata": {
        "transform": [10, 0, 100, 0, -10, 200],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


def _make_router() -> KeywordScoringCapabilityRouter:
    registry = CapabilityRegistry.from_plugin_modules(SAFE_MODULES)
    return KeywordScoringCapabilityRouter(registry=registry)


def _run_pipeline(*, decision_config: RouterDecisionConfig | None = None):
    return run_natural_query_with_routing_evidence(
        NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        router=_make_router(),
        decision_config=decision_config,
    )


def test_routing_aware_runner_returns_router_decision_at_top_level() -> None:
    result = _run_pipeline()

    assert "router_decision" in result

    decision = result["router_decision"]

    assert decision["level"] in {"high", "medium", "low"}
    assert decision["llm_action"] in {"skip", "optional", "required"}
    assert isinstance(decision["route_without_llm"], bool)
    assert isinstance(decision["llm_required"], bool)
    assert isinstance(decision["llm_optional"], bool)
    assert decision["top_candidate"] is not None
    assert decision["top_score"] > 0
    assert isinstance(decision["reasons"], list)


def test_routing_aware_runner_attaches_router_decision_to_execution_result() -> None:
    result = _run_pipeline()

    execution = result["execution"]

    assert execution["status"] == "success"
    assert "router_decision" in execution
    assert execution["router_decision"] == result["router_decision"]


def test_routing_aware_response_contains_router_decision() -> None:
    result = _run_pipeline()

    response = result["response"]
    decision = result["router_decision"]

    assert response["status"] == "success"
    assert "router_decision" in response
    assert response["router_decision"] == decision

    assert "router_decision" in response["metadata"]

    metadata_decision = response["metadata"]["router_decision"]

    assert metadata_decision["level"] == decision["level"]
    assert metadata_decision["llm_action"] == decision["llm_action"]
    assert metadata_decision["route_without_llm"] == decision["route_without_llm"]
    assert metadata_decision["is_ambiguous"] == decision["is_ambiguous"]
    assert metadata_decision["top_score"] == decision["top_score"]
    assert metadata_decision["competitive_gap"] == decision["competitive_gap"]


def test_routing_aware_trace_still_contains_node_routing_evidence() -> None:
    result = _run_pipeline()

    trace = result["response"]["trace"]

    assert len(trace) == 3

    for item in trace:
        assert "routing_evidence" in item
        assert item["routing_evidence"]["capability_name"] == item["capability_name"]
        assert item["routing_evidence"]["score"] > 0
        assert item["routing_evidence"]["matched_terms"]
        assert item["routing_evidence"]["reasons"]


def test_router_decision_custom_config_can_change_policy() -> None:
    """
    With a very low high_threshold, the same query can become high-confidence
    unless ambiguity requires LLM.

    This test verifies that the config is actually used by the integrated runner.
    """
    result = _run_pipeline(
        decision_config=RouterDecisionConfig(
            high_threshold=0.20,
            medium_threshold=0.10,
            competitive_gap_threshold=0.0,
        )
    )

    decision = result["router_decision"]

    assert decision["level"] == "high"
    assert decision["is_ambiguous"] is False
    assert decision["llm_action"] == "skip"
    assert decision["route_without_llm"] is True


def test_response_still_contains_expected_map_and_artifacts_with_router_decision() -> None:
    result = _run_pipeline()

    response = result["response"]

    assert response["map"]["layers"][0]["id"] == "vegetation_polygons"
    assert response["map"]["layers"][0]["type"] == "vector"
    assert response["map"]["layers"][0]["feature_count"] == 3

    assert response["metadata"]["feature_count"] == 3
    assert response["metadata"]["final_output"] == "vegetation_polygons"

    artifact_ids = [item["id"] for item in response["artifacts"]]

    assert artifact_ids == [
        "ndvi_raster",
        "vegetation_mask",
        "vegetation_polygons",
    ]

    assert "تحلیل انجام شد" in response["answer"]
