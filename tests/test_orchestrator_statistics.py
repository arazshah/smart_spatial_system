"""
Tests for ExecutionStatisticsCollector.

Run:
    pytest tests/test_orchestrator_statistics.py -v
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.audit import ExecutionAuditBuilder  # noqa: E402
from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.llm_gate import LLMGate  # noqa: E402
from orchestrator.pipeline_executor import SimplePipelineExecutor  # noqa: E402
from orchestrator.query_parser import SimpleNaturalLanguageParser  # noqa: E402
from orchestrator.router_decision import RouterDecisionLayer  # noqa: E402
from orchestrator.routing_aware_plan_builder import RoutingAwarePlanBuilder  # noqa: E402
from orchestrator.statistics import (  # noqa: E402
    ExecutionStatisticsCollector,
    StatisticsConfig,
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


def _build_audit_record(request_id: str = "req-stats-001") -> dict:
    registry = CapabilityRegistry.from_plugin_modules(SAFE_MODULES)
    router = KeywordScoringCapabilityRouter(registry=registry)

    parser = SimpleNaturalLanguageParser()
    intent = parser.parse(NDVI_QUERY)

    plan = RoutingAwarePlanBuilder(router).build(
        intent,
        band_map={
            "red": 1,
            "nir": 2,
        },
    )

    router_decision = RouterDecisionLayer().decide(plan.routing_evidence).to_dict()
    llm_gate_result = LLMGate().evaluate(router_decision).to_dict()

    execution = SimplePipelineExecutor(router).execute(
        plan,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
    )

    execution["router_decision"] = router_decision
    execution["llm_gate_result"] = llm_gate_result

    return ExecutionAuditBuilder().build(
        query=NDVI_QUERY,
        intent=intent,
        plan=plan,
        execution_result=execution,
        request_id=request_id,
    )


def test_statistics_collector_ingests_single_audit_record() -> None:
    audit = _build_audit_record()

    collector = ExecutionStatisticsCollector()
    collector.ingest(audit)

    summary = collector.summarize()

    assert summary["total_runs"] == 1
    assert summary["status_counts"]["success"] == 1

    assert summary["capabilities"]["usage_counts"] == {
        "calculate_spectral_index": 1,
        "threshold_raster": 1,
        "raster_to_vector": 1,
    }

    assert summary["plugins"]["usage_counts"] == {
        "spectral_indices": 1,
        "raster_threshold": 1,
        "raster_to_vector": 1,
    }

    assert summary["capabilities"]["average_scores"]["calculate_spectral_index"] > 0
    assert summary["capabilities"]["average_scores"]["threshold_raster"] > 0
    assert summary["capabilities"]["average_scores"]["raster_to_vector"] > 0


def test_statistics_collector_tracks_outputs() -> None:
    audit = _build_audit_record()

    collector = ExecutionStatisticsCollector()
    collector.ingest(audit)

    summary = collector.summarize()

    assert summary["outputs"]["kind_counts"]["raster"] == 2
    assert summary["outputs"]["kind_counts"]["vector"] == 1

    assert summary["outputs"]["output_key_counts"]["ndvi_raster"] == 1
    assert summary["outputs"]["output_key_counts"]["vegetation_mask"] == 1
    assert summary["outputs"]["output_key_counts"]["vegetation_polygons"] == 1

    assert summary["outputs"]["vector_feature_total"] == 3


def test_statistics_collector_tracks_router_and_llm_gate_counts() -> None:
    audit = _build_audit_record()

    collector = ExecutionStatisticsCollector()
    collector.ingest(audit)

    summary = collector.summarize()

    decision = audit["router_decision"]
    gate = audit["llm_gate_result"]

    assert summary["router"]["confidence_level_counts"][decision["level"]] == 1
    assert summary["router"]["llm_action_counts"][decision["llm_action"]] == 1
    assert summary["llm_gate"]["status_counts"][gate["status"]] == 1

    assert summary["llm_gate"]["provider_called_count"] in {0, 1}
    assert summary["llm_gate"]["blocked_count"] in {0, 1}
    assert summary["llm_gate"]["deterministic_fallback_count"] in {0, 1}


def test_statistics_collector_tracks_low_confidence_and_ambiguous_queries() -> None:
    audit = _build_audit_record("req-low-ambiguous")

    mutated = copy.deepcopy(audit)
    mutated["router_decision"]["level"] = "low"
    mutated["router_decision"]["llm_action"] = "required"
    mutated["router_decision"]["top_score"] = 0.25
    mutated["router_decision"]["competitive_gap"] = 0.03
    mutated["router_decision"]["is_ambiguous"] = True

    collector = ExecutionStatisticsCollector(
        StatisticsConfig(
            include_query_text=True,
        )
    )

    collector.ingest(mutated)

    summary = collector.summarize()

    assert summary["router"]["confidence_level_counts"]["low"] == 1
    assert summary["router"]["llm_action_counts"]["required"] == 1
    assert summary["router"]["ambiguity_count"] == 1
    assert summary["router"]["low_confidence_query_count"] == 1
    assert summary["router"]["ambiguous_query_count"] == 1

    low_item = summary["router"]["low_confidence_queries"][0]
    ambiguous_item = summary["router"]["ambiguous_queries"][0]

    assert low_item["request_id"] == "req-low-ambiguous"
    assert low_item["query"] == NDVI_QUERY
    assert low_item["top_score"] == 0.25

    assert ambiguous_item["request_id"] == "req-low-ambiguous"
    assert ambiguous_item["is_ambiguous"] is True


def test_statistics_collector_respects_review_list_limits() -> None:
    audit = _build_audit_record()

    collector = ExecutionStatisticsCollector(
        StatisticsConfig(
            max_low_confidence_queries=2,
            max_ambiguous_queries=1,
        )
    )

    for idx in range(5):
        mutated = copy.deepcopy(audit)
        mutated["request_id"] = f"req-{idx}"
        mutated["router_decision"]["level"] = "low"
        mutated["router_decision"]["is_ambiguous"] = True
        collector.ingest(mutated)

    summary = collector.summarize()

    assert summary["router"]["low_confidence_query_count"] == 2
    assert summary["router"]["ambiguous_query_count"] == 1

    assert [item["request_id"] for item in summary["router"]["low_confidence_queries"]] == [
        "req-3",
        "req-4",
    ]

    assert summary["router"]["ambiguous_queries"][0]["request_id"] == "req-4"


def test_statistics_collector_ingest_many_and_reset() -> None:
    audit_1 = _build_audit_record("req-1")
    audit_2 = _build_audit_record("req-2")

    collector = ExecutionStatisticsCollector()
    collector.ingest_many([audit_1, audit_2])

    summary = collector.summarize()

    assert summary["total_runs"] == 2
    assert summary["status_counts"]["success"] == 2
    assert summary["capabilities"]["usage_counts"]["calculate_spectral_index"] == 2
    assert summary["outputs"]["vector_feature_total"] == 6

    collector.reset()

    summary_after_reset = collector.summarize()

    assert summary_after_reset["total_runs"] == 0
    assert summary_after_reset["status_counts"] == {}


def test_statistics_config_rejects_invalid_limits() -> None:
    import pytest

    with pytest.raises(ValueError, match="max_low_confidence_queries"):
        StatisticsConfig(max_low_confidence_queries=-1)

    with pytest.raises(ValueError, match="max_ambiguous_queries"):
        StatisticsConfig(max_ambiguous_queries=-1)
