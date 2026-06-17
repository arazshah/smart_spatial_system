"""
Integration tests for WeightedCapabilityRouter with the natural query runner.

Run:
    pytest tests/test_orchestrator_weighted_router_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.routing_aware_natural_query_runner import (  # noqa: E402
    run_natural_query_with_routing_evidence,
)
from orchestrator.weight_proposals import InMemoryRouterWeightStore  # noqa: E402
from orchestrator.weighted_router import WeightedCapabilityRouter  # noqa: E402


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


def _make_base_router() -> KeywordScoringCapabilityRouter:
    registry = CapabilityRegistry.from_plugin_modules(SAFE_MODULES)
    return KeywordScoringCapabilityRouter(registry=registry)


def _run_pipeline(router, *, request_id: str):
    return run_natural_query_with_routing_evidence(
        NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        router=router,
        min_score=0.01,
        request_id=request_id,
    )


def test_weighted_router_can_run_full_natural_query_pipeline() -> None:
    store = InMemoryRouterWeightStore(
        capability_weights={
            "calculate_spectral_index": 1.0,
            "threshold_raster": 1.0,
            "raster_to_vector": 1.0,
        },
        plugin_weights={
            "spectral_indices": 1.0,
            "raster_threshold": 1.0,
            "raster_to_vector": 1.0,
        },
    )

    router = WeightedCapabilityRouter(
        _make_base_router(),
        weight_store=store,
    )

    result = _run_pipeline(
        router,
        request_id="req-weighted-router-int-001",
    )

    assert result["response"]["status"] == "success"
    assert result["audit_record"]["status"] == "success"

    routing_evidence = result["plan"].routing_evidence

    assert routing_evidence
    assert all(item.get("score_weighted") is True for item in routing_evidence)
    assert all("base_score" in item for item in routing_evidence)
    assert all("weighted_score_metadata" in item for item in routing_evidence)


def test_weighted_router_changes_scores_when_weights_are_not_one() -> None:
    base_result = _run_pipeline(
        _make_base_router(),
        request_id="req-weighted-router-base",
    )

    store = InMemoryRouterWeightStore(
        capability_weights={
            "calculate_spectral_index": 0.5,
            "threshold_raster": 1.0,
            "raster_to_vector": 1.0,
        },
        plugin_weights={
            "spectral_indices": 1.0,
            "raster_threshold": 1.0,
            "raster_to_vector": 1.0,
        },
    )

    weighted_router = WeightedCapabilityRouter(
        _make_base_router(),
        weight_store=store,
    )

    weighted_result = _run_pipeline(
        weighted_router,
        request_id="req-weighted-router-weighted",
    )

    base_scores = {
        item["capability_name"]: item["score"]
        for item in base_result["plan"].routing_evidence
    }

    weighted_scores = {
        item["capability_name"]: item["score"]
        for item in weighted_result["plan"].routing_evidence
    }

    assert weighted_scores["calculate_spectral_index"] < base_scores["calculate_spectral_index"]

    weighted_evidence = [
        item
        for item in weighted_result["plan"].routing_evidence
        if item["capability_name"] == "calculate_spectral_index"
    ][0]

    assert weighted_evidence["capability_weight"] == 0.5
    assert weighted_evidence["plugin_weight"] == 1.0
    assert weighted_evidence["score"] == round(weighted_evidence["base_score"] * 0.5, 6)


def test_weighted_router_evidence_is_visible_in_audit_plan_summary_score() -> None:
    store = InMemoryRouterWeightStore(
        capability_weights={
            "raster_to_vector": 0.5,
        },
        plugin_weights={
            "raster_to_vector": 1.0,
        },
    )

    weighted_router = WeightedCapabilityRouter(
        _make_base_router(),
        weight_store=store,
    )

    result = _run_pipeline(
        weighted_router,
        request_id="req-weighted-router-int-003",
    )

    audit_nodes = result["audit_record"]["plan_summary"]["nodes"]

    raster_to_vector_node = [
        node
        for node in audit_nodes
        if node["capability_name"] == "raster_to_vector"
    ][0]

    plan_evidence = [
        item
        for item in result["plan"].routing_evidence
        if item["capability_name"] == "raster_to_vector"
    ][0]

    assert raster_to_vector_node["routing_evidence"]["score"] == plan_evidence["score"]
    assert plan_evidence["capability_weight"] == 0.5
