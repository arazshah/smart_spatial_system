"""
Integration tests for ProductionResponseBuilder with natural query runner.

Run:
    pytest tests/test_orchestrator_production_response_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.production_response import ProductionResponseBuilder  # noqa: E402
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


def test_production_response_from_runner_result() -> None:
    result = _run_pipeline(
        _make_base_router(),
        request_id="req-prod-int-001",
    )

    payload = ProductionResponseBuilder().build_dict(
        run_result=result,
    )

    assert payload["status"] == "success"
    assert payload["request_id"] == "req-prod-int-001"
    assert payload["query_hash"] == result["audit_record"]["query_hash"]

    assert payload["answer"]
    assert payload["confidence"]["level"] == result["audit_record"]["router_decision"]["level"]
    assert payload["audit_ref"]["plan_steps"] >= 1
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["next_actions"], list)


def test_production_response_from_weighted_router_result_contains_confidence() -> None:
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

    result = _run_pipeline(
        weighted_router,
        request_id="req-prod-int-002",
    )

    payload = ProductionResponseBuilder().build_dict(
        run_result=result,
        metadata={
            "router": "weighted",
        },
    )

    assert payload["status"] == "success"
    assert payload["metadata"]["router"] == "weighted"
    assert payload["confidence"]["score"] is not None
    assert payload["audit_ref"]["status"] == "success"
    assert payload["answer"]
