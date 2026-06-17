"""
Tests for ExecutionAuditBuilder.

Run:
    pytest tests/test_orchestrator_audit.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.audit import AuditConfig, ExecutionAuditBuilder  # noqa: E402
from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.query_parser import SimpleNaturalLanguageParser  # noqa: E402
from orchestrator.routing_aware_plan_builder import RoutingAwarePlanBuilder  # noqa: E402
from orchestrator.pipeline_executor import SimplePipelineExecutor  # noqa: E402
from orchestrator.router_decision import RouterDecisionLayer  # noqa: E402
from orchestrator.llm_gate import LLMGate  # noqa: E402


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


def _build_execution_result():
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

    return intent, plan, execution


def test_audit_builder_creates_core_record() -> None:
    intent, plan, execution = _build_execution_result()

    audit = ExecutionAuditBuilder().build(
        query=NDVI_QUERY,
        intent=intent,
        plan=plan,
        execution_result=execution,
        request_id="req-test-001",
    )

    assert audit["request_id"] == "req-test-001"
    assert audit["status"] == "success"
    assert audit["query"] == NDVI_QUERY
    assert len(audit["query_hash"]) == 64
    assert audit["intent"]["intent_name"] == "extract_vegetation_polygons_from_ndvi_threshold"
    assert audit["intent"]["index_name"] == "NDVI"
    assert audit["intent"]["threshold_value"] == 0.3


def test_audit_builder_summarizes_plan() -> None:
    intent, plan, execution = _build_execution_result()

    audit = ExecutionAuditBuilder().build(
        query=NDVI_QUERY,
        intent=intent,
        plan=plan,
        execution_result=execution,
    )

    plan_summary = audit["plan_summary"]

    assert plan_summary["node_count"] == 3
    assert plan_summary["capabilities"] == [
        "calculate_spectral_index",
        "threshold_raster",
        "raster_to_vector",
    ]

    for node in plan_summary["nodes"]:
        assert node["node_id"]
        assert node["capability_name"]
        assert node["output_key"]
        assert "routing_evidence" in node
        assert node["routing_evidence"]["score"] > 0


def test_audit_builder_summarizes_outputs_without_full_payload_assertions() -> None:
    intent, plan, execution = _build_execution_result()

    audit = ExecutionAuditBuilder().build(
        query=NDVI_QUERY,
        intent=intent,
        plan=plan,
        execution_result=execution,
    )

    outputs = audit["outputs_summary"]

    assert outputs["ndvi_raster"]["kind"] == "raster"
    assert outputs["ndvi_raster"]["shape"] == [2, 3]
    assert outputs["ndvi_raster"]["numeric_stats"]["count"] == 6

    assert outputs["vegetation_mask"]["kind"] == "raster"
    assert outputs["vegetation_mask"]["shape"] == [2, 3]
    assert outputs["vegetation_mask"]["numeric_stats"]["min"] == 0.0
    assert outputs["vegetation_mask"]["numeric_stats"]["max"] == 1.0

    assert outputs["vegetation_polygons"]["kind"] == "vector"
    assert outputs["vegetation_polygons"]["format"] == "FeatureCollection"
    assert outputs["vegetation_polygons"]["feature_count"] == 3
    assert outputs["vegetation_polygons"]["geometry_types"] == ["Polygon"]


def test_audit_builder_includes_router_decision_llm_gate_and_trace() -> None:
    intent, plan, execution = _build_execution_result()

    audit = ExecutionAuditBuilder().build(
        query=NDVI_QUERY,
        intent=intent,
        plan=plan,
        execution_result=execution,
    )

    assert "router_decision" in audit
    assert "llm_gate_result" in audit
    assert "trace" in audit

    assert audit["router_decision"] == execution["router_decision"]
    assert audit["llm_gate_result"] == execution["llm_gate_result"]
    assert audit["trace"] == execution["trace"]


def test_audit_config_can_exclude_trace_and_policy_sections() -> None:
    intent, plan, execution = _build_execution_result()

    audit = ExecutionAuditBuilder(
        AuditConfig(
            include_trace=False,
            include_router_decision=False,
            include_llm_gate_result=False,
            include_plan_routing_evidence=False,
        )
    ).build(
        query=NDVI_QUERY,
        intent=intent,
        plan=plan,
        execution_result=execution,
    )

    assert "trace" not in audit
    assert "router_decision" not in audit
    assert "llm_gate_result" not in audit

    for node in audit["plan_summary"]["nodes"]:
        assert "routing_evidence" not in node
