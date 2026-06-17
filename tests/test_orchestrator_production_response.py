"""
Tests for ProductionResponseBuilder.

Run:
    pytest tests/test_orchestrator_production_response.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.production_response import (  # noqa: E402
    ProductionResponseBuilder,
    ProductionResponseConfig,
)


def _sample_audit() -> dict:
    return {
        "request_id": "req-prod-001",
        "query_hash": "hash-prod-001",
        "status": "success",
        "router_decision": {
            "level": "high",
            "top_score": 0.95,
            "llm_action": "optional",
            "is_ambiguous": False,
            "competitive_gap": 0.4,
        },
        "plan_summary": {
            "nodes": [
                {"capability_name": "calculate_spectral_index"},
                {"capability_name": "threshold_raster"},
                {"capability_name": "raster_to_vector"},
            ]
        },
        "outputs_summary": {
            "vegetation_polygons": {
                "kind": "vector",
                "feature_count": 3,
            }
        },
    }


def test_production_response_builder_creates_success_response() -> None:
    response = ProductionResponseBuilder().build(
        audit_record=_sample_audit(),
        response={
            "status": "success",
        },
    )

    payload = response.to_dict()

    assert payload["status"] == "success"
    assert payload["request_id"] == "req-prod-001"
    assert payload["query_hash"] == "hash-prod-001"

    assert "3" in payload["answer"]
    assert "پوشش گیاهی" in payload["answer"] or "vegetation" in payload["answer"]

    assert payload["confidence"]["level"] == "high"
    assert payload["confidence"]["score"] == 0.95
    assert payload["confidence"]["llm_action"] == "optional"
    assert payload["confidence"]["is_ambiguous"] is False

    assert payload["audit_ref"]["plan_steps"] == 3
    assert payload["warnings"] == []
    assert "summary" in payload["outputs"]


def test_production_response_builder_build_dict_returns_dict() -> None:
    payload = ProductionResponseBuilder().build_dict(
        audit_record=_sample_audit(),
    )

    assert isinstance(payload, dict)
    assert payload["status"] == "success"
    assert payload["request_id"] == "req-prod-001"


def test_production_response_builder_warns_for_low_confidence() -> None:
    audit = _sample_audit()
    audit["router_decision"]["level"] = "low"
    audit["router_decision"]["top_score"] = 0.22

    payload = ProductionResponseBuilder().build_dict(
        audit_record=audit,
    )

    assert payload["confidence"]["level"] == "low"
    assert payload["warnings"]
    assert any("اطمینان" in warning for warning in payload["warnings"])
    assert payload["next_actions"]


def test_production_response_builder_warns_for_ambiguous_routing() -> None:
    audit = _sample_audit()
    audit["router_decision"]["is_ambiguous"] = True

    payload = ProductionResponseBuilder().build_dict(
        audit_record=audit,
    )

    assert payload["confidence"]["is_ambiguous"] is True
    assert any("مسیر" in warning or "نزدیک" in warning for warning in payload["warnings"])


def test_production_response_builder_creates_failed_response_from_error() -> None:
    payload = ProductionResponseBuilder().build_dict(
        audit_record={
            "request_id": "req-prod-failed",
            "query_hash": "hash-prod-failed",
            "status": "failed",
        },
        error="Input raster is missing.",
    )

    assert payload["status"] == "failed"
    assert "Input raster is missing" in payload["answer"]
    assert payload["warnings"]
    assert payload["next_actions"]


def test_production_response_builder_maps_error_status_to_failed() -> None:
    payload = ProductionResponseBuilder().build_dict(
        response={
            "status": "error",
            "request_id": "req-prod-error",
        }
    )

    assert payload["status"] == "failed"


def test_production_response_builder_can_hide_outputs() -> None:
    payload = ProductionResponseBuilder(
        ProductionResponseConfig(
            include_outputs=False,
        )
    ).build_dict(
        audit_record=_sample_audit(),
    )

    assert payload["outputs"] == {}


def test_production_response_builder_can_disable_metadata() -> None:
    payload = ProductionResponseBuilder(
        ProductionResponseConfig(
            include_metadata=False,
        )
    ).build_dict(
        audit_record=_sample_audit(),
    )

    assert payload["metadata"] == {}


def test_production_response_builder_supports_english() -> None:
    payload = ProductionResponseBuilder(
        ProductionResponseConfig(
            language="en",
        )
    ).build_dict(
        audit_record=_sample_audit(),
    )

    assert "generated" in payload["answer"] or "Request completed" in payload["answer"]


def test_production_response_builder_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="max_warnings"):
        ProductionResponseConfig(max_warnings=-1)

    with pytest.raises(ValueError, match="max_next_actions"):
        ProductionResponseConfig(max_next_actions=-1)

    with pytest.raises(ValueError, match="language"):
        ProductionResponseConfig(language="bad")


def test_production_response_builder_summarizes_explicit_outputs() -> None:
    payload = ProductionResponseBuilder().build_dict(
        response={
            "status": "success",
            "request_id": "req-prod-output",
        },
        outputs={
            "features": {
                "kind": "vector",
                "features": [
                    {"id": 1},
                    {"id": 2},
                ],
            }
        },
    )

    assert payload["status"] == "success"
    assert "2" in payload["answer"]
    assert payload["outputs"]


def test_production_response_builder_limits_warnings_and_next_actions() -> None:
    audit = _sample_audit()
    audit["router_decision"]["level"] = "low"
    audit["router_decision"]["is_ambiguous"] = True
    audit["warnings"] = ["w1", "w2", "w3"]

    payload = ProductionResponseBuilder(
        ProductionResponseConfig(
            max_warnings=2,
            max_next_actions=1,
        )
    ).build_dict(
        audit_record=audit,
    )

    assert len(payload["warnings"]) == 2
    assert len(payload["next_actions"]) == 1
