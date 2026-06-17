"""
Integration tests for audit record attached to routing-aware runner/response.

Run:
    pytest tests/test_orchestrator_audit_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.audit import AuditConfig  # noqa: E402
from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
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


def _run_pipeline(**kwargs):
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
        **kwargs,
    )


def test_runner_returns_audit_record_at_top_level_execution_and_response() -> None:
    result = _run_pipeline(request_id="req-integration-001")

    assert "audit_record" in result
    assert "audit_record" in result["execution"]
    assert "audit_record" in result["response"]

    audit = result["audit_record"]

    assert audit["request_id"] == "req-integration-001"
    assert result["execution"]["audit_record"] == audit
    assert result["response"]["audit_record"] == audit


def test_response_metadata_contains_audit_summary() -> None:
    result = _run_pipeline(request_id="req-integration-002")

    audit = result["audit_record"]
    response = result["response"]

    assert "audit" in response["metadata"]

    audit_meta = response["metadata"]["audit"]

    assert audit_meta["request_id"] == "req-integration-002"
    assert audit_meta["created_at"] == audit["created_at"]
    assert audit_meta["status"] == "success"
    assert audit_meta["query_hash"] == audit["query_hash"]


def test_audit_record_contains_routing_policy_and_llm_gate() -> None:
    result = _run_pipeline()

    audit = result["audit_record"]

    assert audit["router_decision"] == result["router_decision"]
    assert audit["llm_gate_result"] == result["llm_gate_result"]

    assert audit["router_decision"]["llm_action"] in {"skip", "optional", "required"}
    assert audit["llm_gate_result"]["status"]


def test_audit_record_contains_plan_trace_and_output_summaries() -> None:
    result = _run_pipeline()

    audit = result["audit_record"]

    assert audit["plan_summary"]["node_count"] == 3
    assert audit["plan_summary"]["capabilities"] == [
        "calculate_spectral_index",
        "threshold_raster",
        "raster_to_vector",
    ]

    assert len(audit["trace"]) == 3

    outputs = audit["outputs_summary"]

    assert outputs["ndvi_raster"]["kind"] == "raster"
    assert outputs["vegetation_mask"]["kind"] == "raster"
    assert outputs["vegetation_polygons"]["kind"] == "vector"
    assert outputs["vegetation_polygons"]["feature_count"] == 3


def test_audit_config_exclusion_is_respected_in_runner() -> None:
    result = _run_pipeline(
        audit_config=AuditConfig(
            include_trace=False,
            include_router_decision=False,
            include_llm_gate_result=False,
            include_plan_routing_evidence=False,
        )
    )

    audit = result["audit_record"]

    assert "trace" not in audit
    assert "router_decision" not in audit
    assert "llm_gate_result" not in audit

    for node in audit["plan_summary"]["nodes"]:
        assert "routing_evidence" not in node

    # Top-level runner still returns these objects; only audit excluded them.
    assert "router_decision" in result
    assert "llm_gate_result" in result
