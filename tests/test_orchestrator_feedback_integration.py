"""
Integration tests for FeedbackCollector with real runner audit records.

Run:
    pytest tests/test_orchestrator_feedback_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.feedback import FeedbackCollector, UserFeedbackInput  # noqa: E402
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


def test_feedback_can_be_submitted_for_runner_audit_record() -> None:
    result = _run_pipeline("req-feedback-int-001")
    audit = result["audit_record"]

    collector = FeedbackCollector()

    record = collector.submit(
        audit,
        UserFeedbackInput(
            rating="correct",
            comment="نتیجه درست است",
        ),
        feedback_id="fb-int-001",
    )

    assert record.feedback_id == "fb-int-001"
    assert record.request_id == "req-feedback-int-001"
    assert record.query_hash == audit["query_hash"]
    assert record.rating == "correct"

    assert record.observed_intent_name == audit["intent"]["intent_name"]
    assert record.observed_llm_action == audit["router_decision"]["llm_action"]
    assert record.observed_confidence_level == audit["router_decision"]["level"]
    assert record.observed_feature_count == 3


def test_feedback_can_be_attached_to_runner_audit_record() -> None:
    result = _run_pipeline("req-feedback-int-002")
    audit = result["audit_record"]

    collector = FeedbackCollector()

    enriched_audit = collector.submit_and_attach(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=[
                "threshold_error",
            ],
            expected_threshold_value=0.5,
        ),
        feedback_id="fb-int-002",
    )

    assert "feedback" not in audit
    assert "feedback" in enriched_audit
    assert enriched_audit["feedback"]["feedback_id"] == "fb-int-002"
    assert enriched_audit["feedback"]["rating"] == "incorrect"
    assert enriched_audit["feedback"]["expected_threshold_value"] == 0.5
    assert enriched_audit["request_id"] == "req-feedback-int-002"


def test_feedback_summary_from_multiple_runner_audits() -> None:
    result_1 = _run_pipeline("req-feedback-int-003")
    result_2 = _run_pipeline("req-feedback-int-004")

    collector = FeedbackCollector()

    collector.submit(
        result_1["audit_record"],
        UserFeedbackInput(
            rating="correct",
        ),
    )

    collector.submit(
        result_2["audit_record"],
        UserFeedbackInput(
            rating="partially_correct",
            issue_types=[
                "output_type_error",
                "response_error",
            ],
            expected_output_kind="raster",
        ),
    )

    summary = collector.summarize()

    assert summary["total_feedback"] == 2
    assert summary["rating_counts"]["correct"] == 1
    assert summary["rating_counts"]["partially_correct"] == 1
    assert summary["incorrect_or_partial_count"] == 1

    assert summary["issue_type_counts"]["output_type_error"] == 1
    assert summary["issue_type_counts"]["response_error"] == 1

    assert summary["observed_top_capability_counts"]
    assert len(summary["records"]) == 2
