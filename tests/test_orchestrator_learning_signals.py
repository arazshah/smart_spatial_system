"""
Tests for RouterLearningSignalBuilder.

Run:
    pytest tests/test_orchestrator_learning_signals.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.feedback import FeedbackCollector, UserFeedbackInput  # noqa: E402
from orchestrator.learning_signals import (  # noqa: E402
    LearningSignalConfig,
    RouterLearningSignalBuilder,
    RouterLearningSignalCollector,
)


def _sample_audit() -> dict:
    return {
        "request_id": "req-learning-001",
        "query": "از تصویر NDVI بگیر و پلیگون بساز",
        "query_hash": "hash-learning-001",
        "status": "success",
        "intent": {
            "intent_name": "extract_vegetation_polygons_from_ndvi_threshold",
            "index_name": "NDVI",
            "threshold_value": 0.3,
        },
        "router_decision": {
            "level": "medium",
            "llm_action": "optional",
            "top_score": 0.72,
            "competitive_gap": 0.18,
            "is_ambiguous": False,
            "top_candidate": {
                "capability_name": "raster_to_vector",
                "plugin_id": "raster_to_vector",
                "output_kind": "vector",
            },
        },
        "outputs_summary": {
            "vegetation_polygons": {
                "kind": "vector",
                "feature_count": 3,
            }
        },
    }


def _statistics_summary() -> dict:
    return {
        "capabilities": {
            "usage_counts": {
                "raster_to_vector": 10,
                "threshold_raster": 5,
            }
        },
        "plugins": {
            "usage_counts": {
                "raster_to_vector": 10,
                "raster_threshold": 5,
            }
        },
    }


def test_correct_feedback_produces_positive_confirmation_signal() -> None:
    audit = _sample_audit()
    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="correct",
        ),
        feedback_id="fb-learning-correct",
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    assert len(signals) == 1

    signal = signals[0].to_dict()

    assert signal["signal_type"] == "positive_confirmation"
    assert signal["action"] == "reinforce_observed_route"
    assert signal["severity"] == "low"

    assert signal["request_id"] == "req-learning-001"
    assert signal["query_hash"] == "hash-learning-001"
    assert signal["rating"] == "correct"

    assert signal["observed_capability"] == "raster_to_vector"
    assert signal["observed_plugin_id"] == "raster_to_vector"

    assert signal["weight_adjustments"]["capabilities"]["raster_to_vector"] == 0.05
    assert signal["weight_adjustments"]["plugins"]["raster_to_vector"] == 0.05


def test_incorrect_feedback_with_expected_capability_produces_route_correction() -> None:
    audit = _sample_audit()
    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["route_error"],
            expected_capability="threshold_raster",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
        statistics_summary=_statistics_summary(),
    )

    route_signals = [
        signal
        for signal in signals
        if signal.signal_type == "route_correction"
    ]

    assert len(route_signals) == 1

    signal = route_signals[0].to_dict()

    assert signal["severity"] == "high"
    assert signal["action"] == "decrease_observed_increase_expected"

    assert signal["observed_capability"] == "raster_to_vector"
    assert signal["expected_capability"] == "threshold_raster"

    assert signal["weight_adjustments"]["capabilities"]["raster_to_vector"] == -0.15
    assert signal["weight_adjustments"]["capabilities"]["threshold_raster"] == 0.15

    assert signal["metadata"]["statistics_context"]["observed_capability_usage"] == 10
    assert signal["metadata"]["statistics_context"]["expected_capability_usage"] == 5


def test_partially_correct_feedback_uses_medium_delta() -> None:
    audit = _sample_audit()
    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="partially_correct",
            issue_types=["route_error"],
            expected_capability="threshold_raster",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    signal = [
        item
        for item in signals
        if item.signal_type == "route_correction"
    ][0].to_dict()

    assert signal["severity"] == "medium"
    assert signal["weight_adjustments"]["capabilities"]["raster_to_vector"] == -0.08
    assert signal["weight_adjustments"]["capabilities"]["threshold_raster"] == 0.08


def test_plugin_correction_signal_is_created() -> None:
    audit = _sample_audit()
    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["plugin_error"],
            expected_plugin_id="raster_threshold",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    plugin_signals = [
        signal
        for signal in signals
        if signal.signal_type == "plugin_correction"
    ]

    assert len(plugin_signals) == 1

    signal = plugin_signals[0].to_dict()

    assert signal["observed_plugin_id"] == "raster_to_vector"
    assert signal["expected_plugin_id"] == "raster_threshold"
    assert signal["weight_adjustments"]["plugins"]["raster_to_vector"] == -0.15
    assert signal["weight_adjustments"]["plugins"]["raster_threshold"] == 0.15


def test_parameter_and_output_type_signals_are_created() -> None:
    audit = _sample_audit()
    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=[
                "threshold_error",
                "output_type_error",
            ],
            expected_threshold_value=0.5,
            expected_threshold_operator=">",
            expected_output_kind="raster",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    signal_types = {
        signal.signal_type
        for signal in signals
    }

    assert "parameter_correction" in signal_types
    assert "output_type_correction" in signal_types

    parameter_signal = [
        signal
        for signal in signals
        if signal.signal_type == "parameter_correction"
    ][0].to_dict()

    assert parameter_signal["metadata"]["expected_threshold_value"] == 0.5
    assert parameter_signal["metadata"]["expected_threshold_operator"] == ">"


def test_low_confidence_and_ambiguity_review_signals_are_created() -> None:
    audit = _sample_audit()
    audit["router_decision"]["level"] = "low"
    audit["router_decision"]["is_ambiguous"] = True
    audit["router_decision"]["competitive_gap"] = 0.03

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["route_error"],
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    signal_types = {
        signal.signal_type
        for signal in signals
    }

    assert "low_confidence_review" in signal_types
    assert "ambiguity_review" in signal_types

    for signal in signals:
        if signal.signal_type in {"low_confidence_review", "ambiguity_review"}:
            assert signal.severity == "high"


def test_negative_feedback_without_structured_correction_creates_general_signal() -> None:
    audit = _sample_audit()

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    assert len(signals) == 1
    assert signals[0].signal_type == "general_negative_feedback"
    assert signals[0].action == "send_to_human_or_llm_review"


def test_build_dicts_returns_json_like_payloads() -> None:
    audit = _sample_audit()

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="correct",
        ),
    )

    payloads = RouterLearningSignalBuilder().build_dicts(
        audit_record=audit,
        feedback_record=feedback,
    )

    assert isinstance(payloads, list)
    assert isinstance(payloads[0], dict)
    assert payloads[0]["signal_type"] == "positive_confirmation"


def test_learning_signal_config_custom_deltas() -> None:
    audit = _sample_audit()

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["route_error"],
            expected_capability="threshold_raster",
        ),
    )

    builder = RouterLearningSignalBuilder(
        LearningSignalConfig(
            high_weight_delta=0.25,
        )
    )

    signals = builder.build(
        audit_record=audit,
        feedback_record=feedback,
    )

    signal = [
        item
        for item in signals
        if item.signal_type == "route_correction"
    ][0].to_dict()

    assert signal["weight_adjustments"]["capabilities"]["raster_to_vector"] == -0.25
    assert signal["weight_adjustments"]["capabilities"]["threshold_raster"] == 0.25


def test_learning_signal_config_rejects_invalid_deltas() -> None:
    with pytest.raises(ValueError, match="positive_weight_delta"):
        LearningSignalConfig(positive_weight_delta=-1)

    with pytest.raises(ValueError, match="medium_weight_delta"):
        LearningSignalConfig(medium_weight_delta=-1)

    with pytest.raises(ValueError, match="high_weight_delta"):
        LearningSignalConfig(high_weight_delta=-1)


def test_learning_signal_collector_summarizes_signals() -> None:
    audit = _sample_audit()

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["route_error"],
            expected_capability="threshold_raster",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    collector = RouterLearningSignalCollector()
    collector.ingest_many(signals)

    summary = collector.summarize()

    assert summary["total_signals"] == len(signals)
    assert summary["signal_type_counts"]["route_correction"] == 1
    assert summary["severity_counts"]["high"] >= 1
    assert summary["action_counts"]["decrease_observed_increase_expected"] == 1
    assert len(summary["signals"]) == len(signals)


def test_learning_signal_collector_respects_max_signals() -> None:
    audit = _sample_audit()

    collector = RouterLearningSignalCollector(max_signals=2)

    for idx in range(5):
        feedback = FeedbackCollector().submit(
            audit,
            UserFeedbackInput(
                rating="correct",
            ),
            feedback_id=f"fb-{idx}",
        )

        signals = RouterLearningSignalBuilder().build(
            audit_record=audit,
            feedback_record=feedback,
        )

        collector.ingest_many(signals)

    summary = collector.summarize()

    assert summary["total_signals"] == 2


def test_learning_signal_collector_rejects_invalid_max_signals() -> None:
    with pytest.raises(ValueError, match="max_signals"):
        RouterLearningSignalCollector(max_signals=-1)
