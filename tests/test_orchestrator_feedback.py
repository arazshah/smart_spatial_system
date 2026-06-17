"""
Tests for FeedbackCollector.

Run:
    pytest tests/test_orchestrator_feedback.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.feedback import (  # noqa: E402
    FeedbackCollector,
    FeedbackConfig,
    UserFeedbackInput,
)


def _sample_audit() -> dict:
    return {
        "request_id": "req-feedback-001",
        "query": "از تصویر NDVI بگیر و پلیگون بساز",
        "query_hash": "abc123",
        "status": "success",
        "intent": {
            "intent_name": "extract_vegetation_polygons_from_ndvi_threshold",
            "index_name": "NDVI",
            "threshold_value": 0.3,
        },
        "router_decision": {
            "level": "medium",
            "llm_action": "optional",
            "top_candidate": {
                "capability_name": "raster_to_vector",
                "plugin_id": "raster_to_vector",
            },
        },
        "outputs_summary": {
            "vegetation_polygons": {
                "kind": "vector",
                "feature_count": 3,
            }
        },
    }


def test_feedback_collector_accepts_correct_feedback() -> None:
    collector = FeedbackCollector()

    record = collector.submit(
        _sample_audit(),
        UserFeedbackInput(
            rating="correct",
            comment="خروجی درست بود",
            user_id="user-1",
        ),
        feedback_id="fb-001",
    )

    payload = record.to_dict()

    assert payload["feedback_id"] == "fb-001"
    assert payload["request_id"] == "req-feedback-001"
    assert payload["query_hash"] == "abc123"
    assert payload["rating"] == "correct"
    assert payload["comment"] == "خروجی درست بود"
    assert payload["user_id"] == "user-1"

    assert payload["observed_intent_name"] == "extract_vegetation_polygons_from_ndvi_threshold"
    assert payload["observed_top_capability"] == "raster_to_vector"
    assert payload["observed_top_plugin_id"] == "raster_to_vector"
    assert payload["observed_llm_action"] == "optional"
    assert payload["observed_confidence_level"] == "medium"
    assert payload["observed_feature_count"] == 3


def test_feedback_collector_accepts_structured_correction() -> None:
    collector = FeedbackCollector()

    record = collector.submit(
        _sample_audit(),
        UserFeedbackInput(
            rating="incorrect",
            issue_types=[
                "route_error",
                "threshold_error",
                "output_type_error",
            ],
            comment="باید threshold بالاتر و خروجی raster باشد",
            expected_capability="threshold_raster",
            expected_plugin_id="raster_threshold",
            expected_output_kind="raster",
            expected_threshold_value=0.5,
            expected_threshold_operator=">",
        ),
    )

    assert record.rating == "incorrect"
    assert record.issue_types == [
        "route_error",
        "threshold_error",
        "output_type_error",
    ]
    assert record.expected_capability == "threshold_raster"
    assert record.expected_plugin_id == "raster_threshold"
    assert record.expected_output_kind == "raster"
    assert record.expected_threshold_value == 0.5
    assert record.expected_threshold_operator == ">"


def test_feedback_collector_rejects_invalid_rating() -> None:
    collector = FeedbackCollector()

    with pytest.raises(ValueError, match="Invalid rating"):
        collector.submit(
            _sample_audit(),
            UserFeedbackInput(
                rating="bad_rating",
            ),
        )


def test_feedback_collector_rejects_invalid_issue_type() -> None:
    collector = FeedbackCollector()

    with pytest.raises(ValueError, match="Invalid issue_types"):
        collector.submit(
            _sample_audit(),
            UserFeedbackInput(
                rating="incorrect",
                issue_types=["unknown_issue"],
            ),
        )


def test_feedback_collector_attach_to_audit_does_not_mutate_original() -> None:
    collector = FeedbackCollector()
    audit = _sample_audit()

    record = collector.submit(
        audit,
        UserFeedbackInput(
            rating="partially_correct",
            issue_types=["parameter_error"],
        ),
        feedback_id="fb-attach",
    )

    enriched = collector.attach_to_audit(audit, record)

    assert "feedback" not in audit
    assert "feedback" in enriched
    assert enriched["feedback"]["feedback_id"] == "fb-attach"
    assert enriched["feedback"]["rating"] == "partially_correct"


def test_feedback_collector_submit_and_attach() -> None:
    collector = FeedbackCollector()

    enriched = collector.submit_and_attach(
        _sample_audit(),
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["plugin_error"],
            expected_plugin_id="spectral_indices",
        ),
        feedback_id="fb-submit-attach",
    )

    assert enriched["feedback"]["feedback_id"] == "fb-submit-attach"
    assert enriched["feedback"]["rating"] == "incorrect"
    assert enriched["feedback"]["issue_types"] == ["plugin_error"]
    assert enriched["feedback"]["expected_plugin_id"] == "spectral_indices"


def test_feedback_collector_summary() -> None:
    collector = FeedbackCollector()

    audit = _sample_audit()

    collector.submit(
        audit,
        UserFeedbackInput(
            rating="correct",
        ),
    )

    collector.submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["route_error", "threshold_error"],
            expected_capability="threshold_raster",
        ),
    )

    collector.submit(
        audit,
        UserFeedbackInput(
            rating="partially_correct",
            issue_types=["parameter_error"],
            expected_capability="raster_to_vector",
        ),
    )

    summary = collector.summarize()

    assert summary["total_feedback"] == 3
    assert summary["rating_counts"]["correct"] == 1
    assert summary["rating_counts"]["incorrect"] == 1
    assert summary["rating_counts"]["partially_correct"] == 1
    assert summary["incorrect_or_partial_count"] == 2

    assert summary["issue_type_counts"]["route_error"] == 1
    assert summary["issue_type_counts"]["threshold_error"] == 1
    assert summary["issue_type_counts"]["parameter_error"] == 1

    assert summary["expected_capability_counts"]["threshold_raster"] == 1
    assert summary["expected_capability_counts"]["raster_to_vector"] == 1
    assert summary["observed_top_capability_counts"]["raster_to_vector"] == 3
    assert len(summary["records"]) == 3


def test_feedback_collector_respects_max_records_limit() -> None:
    collector = FeedbackCollector(
        FeedbackConfig(
            max_records=2,
        )
    )

    audit = _sample_audit()

    for idx in range(5):
        collector.submit(
            audit,
            UserFeedbackInput(
                rating="incorrect",
                comment=f"feedback {idx}",
            ),
            feedback_id=f"fb-{idx}",
        )

    summary = collector.summarize()

    assert summary["total_feedback"] == 2
    assert [item["feedback_id"] for item in summary["records"]] == [
        "fb-3",
        "fb-4",
    ]


def test_feedback_config_can_include_or_hide_query_text() -> None:
    audit = _sample_audit()

    collector_hidden = FeedbackCollector(
        FeedbackConfig(
            allow_query_text=False,
        )
    )

    hidden_record = collector_hidden.submit(
        audit,
        UserFeedbackInput(
            rating="correct",
        ),
    )

    assert hidden_record.query is None

    collector_visible = FeedbackCollector(
        FeedbackConfig(
            allow_query_text=True,
        )
    )

    visible_record = collector_visible.submit(
        audit,
        UserFeedbackInput(
            rating="correct",
        ),
    )

    assert visible_record.query == audit["query"]


def test_feedback_config_rejects_invalid_max_records() -> None:
    with pytest.raises(ValueError, match="max_records"):
        FeedbackConfig(max_records=-1)


def test_feedback_collector_reset() -> None:
    collector = FeedbackCollector()

    collector.submit(
        _sample_audit(),
        UserFeedbackInput(
            rating="correct",
        ),
    )

    assert collector.summarize()["total_feedback"] == 1

    collector.reset()

    assert collector.summarize()["total_feedback"] == 0
