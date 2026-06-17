"""
Tests for RoutingAwarePlanBuilder.

Run:
    pytest tests/test_orchestrator_routing_aware_plan_builder.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.query_parser import SimpleNaturalLanguageParser  # noqa: E402
from orchestrator.routing_aware_natural_query_runner import (  # noqa: E402
    run_natural_query_with_routing_evidence,
)
from orchestrator.routing_aware_plan_builder import RoutingAwarePlanBuilder  # noqa: E402


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


def _get_raster_data(result):
    if hasattr(result, "data"):
        return result.data
    if hasattr(result, "array"):
        return result.array
    if hasattr(result, "payload"):
        return result.payload
    if isinstance(result, dict):
        if "data" in result:
            return result["data"]
        if "array" in result:
            return result["array"]
    raise AssertionError("Raster output has no data/array/payload.")


def test_routing_aware_plan_builder_attaches_plan_level_evidence() -> None:
    parser = SimpleNaturalLanguageParser()
    router = _make_router()
    builder = RoutingAwarePlanBuilder(router)

    intent = parser.parse(NDVI_QUERY)

    plan = builder.build(
        intent,
        band_map={
            "red": 1,
            "nir": 2,
        },
    )

    assert [node.capability_name for node in plan.nodes] == [
        "calculate_spectral_index",
        "threshold_raster",
        "raster_to_vector",
    ]

    assert len(plan.routing_evidence) >= 3

    evidence_names = {
        candidate.capability_name
        for candidate in plan.routing_evidence
    }

    assert "calculate_spectral_index" in evidence_names
    assert "threshold_raster" in evidence_names
    assert "raster_to_vector" in evidence_names


def test_routing_aware_plan_builder_attaches_node_level_evidence() -> None:
    parser = SimpleNaturalLanguageParser()
    router = _make_router()
    builder = RoutingAwarePlanBuilder(router)

    intent = parser.parse(NDVI_QUERY)

    plan = builder.build(
        intent,
        band_map={
            "red": 1,
            "nir": 2,
        },
    )

    for node in plan.nodes:
        assert node.routing_evidence is not None
        assert node.routing_evidence["capability_name"] == node.capability_name
        assert node.routing_evidence["score"] > 0
        assert node.routing_evidence["plugin_id"]
        assert isinstance(node.routing_evidence["matched_terms"], list)
        assert isinstance(node.routing_evidence["reasons"], list)

    by_node = {
        node.capability_name: node
        for node in plan.nodes
    }

    assert "ndvi" in by_node["calculate_spectral_index"].routing_evidence["matched_terms"]
    assert "بیشتر از" in by_node["threshold_raster"].routing_evidence["matched_terms"]
    assert "پلیگون" in by_node["raster_to_vector"].routing_evidence["matched_terms"]


def test_routing_aware_runner_executes_end_to_end_with_trace_evidence() -> None:
    router = _make_router()

    result = run_natural_query_with_routing_evidence(
        NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        router=router,
    )

    execution = result["execution"]
    response = result["response"]
    plan = result["plan"]

    assert execution["status"] == "success"
    assert response["status"] == "success"

    outputs = execution["outputs"]

    ndvi_data = _get_raster_data(outputs["ndvi_raster"])
    mask_data = _get_raster_data(outputs["vegetation_mask"])
    vector = outputs["vegetation_polygons"]

    assert ndvi_data == [
        [0.333, 0.0, 0.6],
        [0.0, 0.5, -0.333],
    ]

    assert mask_data == [
        [1, 0, 1],
        [0, 1, 0],
    ]

    assert vector["type"] == "FeatureCollection"
    assert len(vector["features"]) == 3

    assert len(plan.routing_evidence) >= 3

    trace = response["trace"]

    assert len(trace) == 3

    assert [item["plugin_id"] for item in trace] == [
        "spectral_indices",
        "raster_threshold",
        "raster_to_vector",
    ]

    for item in trace:
        assert "routing_evidence" in item
        assert item["routing_evidence"]["capability_name"] == item["capability_name"]
        assert item["routing_evidence"]["score"] > 0
        assert item["routing_evidence"]["matched_terms"]

    assert response["metadata"]["feature_count"] == 3
    assert "تحلیل انجام شد" in response["answer"]


def test_routing_aware_builder_rejects_when_required_capabilities_missing() -> None:
    parser = SimpleNaturalLanguageParser()

    registry = CapabilityRegistry.from_plugin_modules(
        [
            "plugins.spectral_indices",
        ]
    )
    router = KeywordScoringCapabilityRouter(registry=registry)
    builder = RoutingAwarePlanBuilder(router)

    intent = parser.parse(NDVI_QUERY)

    with pytest.raises(ValueError, match="required capabilities"):
        builder.build(
            intent,
            band_map={
                "red": 1,
                "nir": 2,
            },
        )
