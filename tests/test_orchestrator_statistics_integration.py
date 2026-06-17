"""
Integration tests for statistics collector using runner audit records.

Run:
    pytest tests/test_orchestrator_statistics_integration.py -v
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.routing_aware_natural_query_runner import (  # noqa: E402
    run_natural_query_with_routing_evidence,
)
from orchestrator.statistics import ExecutionStatisticsCollector  # noqa: E402


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


def _run_pipeline(request_id: str):
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
        request_id=request_id,
    )


def test_statistics_collector_consumes_runner_audit_record() -> None:
    result = _run_pipeline("req-stat-int-001")

    collector = ExecutionStatisticsCollector()
    collector.ingest(result["audit_record"])

    summary = collector.summarize()

    assert summary["total_runs"] == 1
    assert summary["status_counts"]["success"] == 1

    assert summary["capabilities"]["usage_counts"]["calculate_spectral_index"] == 1
    assert summary["capabilities"]["usage_counts"]["threshold_raster"] == 1
    assert summary["capabilities"]["usage_counts"]["raster_to_vector"] == 1

    assert summary["plugins"]["usage_counts"]["spectral_indices"] == 1
    assert summary["plugins"]["usage_counts"]["raster_threshold"] == 1
    assert summary["plugins"]["usage_counts"]["raster_to_vector"] == 1

    assert summary["outputs"]["vector_feature_total"] == 3


def test_statistics_collector_consumes_multiple_runner_audit_records() -> None:
    result_1 = _run_pipeline("req-stat-int-001")
    result_2 = _run_pipeline("req-stat-int-002")

    collector = ExecutionStatisticsCollector()
    collector.ingest_many(
        [
            result_1["audit_record"],
            result_2["audit_record"],
        ]
    )

    summary = collector.summarize()

    assert summary["total_runs"] == 2
    assert summary["status_counts"]["success"] == 2
    assert summary["capabilities"]["usage_counts"]["calculate_spectral_index"] == 2
    assert summary["plugins"]["usage_counts"]["spectral_indices"] == 2
    assert summary["outputs"]["vector_feature_total"] == 6


def test_statistics_collector_can_highlight_mutated_low_confidence_audit_for_review() -> None:
    result = _run_pipeline("req-stat-low-review")

    audit = copy.deepcopy(result["audit_record"])
    audit["router_decision"]["level"] = "low"
    audit["router_decision"]["llm_action"] = "required"
    audit["router_decision"]["top_score"] = 0.31

    collector = ExecutionStatisticsCollector()
    collector.ingest(audit)

    summary = collector.summarize()

    assert summary["router"]["confidence_level_counts"]["low"] == 1
    assert summary["router"]["llm_action_counts"]["required"] == 1
    assert summary["router"]["low_confidence_query_count"] == 1

    review_item = summary["router"]["low_confidence_queries"][0]

    assert review_item["request_id"] == "req-stat-low-review"
    assert review_item["top_score"] == 0.31
