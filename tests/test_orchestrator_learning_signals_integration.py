"""
Integration tests for learning signals with runner audit + feedback + statistics.

Run:
    pytest tests/test_orchestrator_learning_signals_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.feedback import FeedbackCollector, UserFeedbackInput  # noqa: E402
from orchestrator.learning_signals import (  # noqa: E402
    RouterLearningSignalBuilder,
    RouterLearningSignalCollector,
)
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


def test_learning_signal_from_correct_runner_feedback() -> None:
    result = _run_pipeline("req-learning-int-001")
    audit = result["audit_record"]

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="correct",
        ),
        feedback_id="fb-learning-int-001",
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    assert len(signals) == 1

    signal = signals[0].to_dict()

    assert signal["signal_type"] == "positive_confirmation"
    assert signal["request_id"] == "req-learning-int-001"
    assert signal["query_hash"] == audit["query_hash"]
    assert signal["observed_capability"]
    assert signal["observed_plugin_id"]
    assert signal["observed_confidence_level"] == audit["router_decision"]["level"]


def test_learning_signal_from_incorrect_runner_feedback_with_expected_route() -> None:
    result = _run_pipeline("req-learning-int-002")
    audit = result["audit_record"]

    statistics_collector = ExecutionStatisticsCollector()
    statistics_collector.ingest(audit)
    statistics_summary = statistics_collector.summarize()

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=[
                "route_error",
                "threshold_error",
            ],
            expected_capability="threshold_raster",
            expected_plugin_id="raster_threshold",
            expected_threshold_value=0.5,
        ),
        feedback_id="fb-learning-int-002",
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
        statistics_summary=statistics_summary,
    )

    signal_types = {
        signal.signal_type
        for signal in signals
    }

    assert "route_correction" in signal_types
    assert "plugin_correction" in signal_types
    assert "parameter_correction" in signal_types

    route_signal = [
        signal
        for signal in signals
        if signal.signal_type == "route_correction"
    ][0].to_dict()

    assert route_signal["expected_capability"] == "threshold_raster"
    assert "statistics_context" in route_signal["metadata"]


def test_learning_signal_collector_with_runner_feedbacks() -> None:
    result_1 = _run_pipeline("req-learning-int-003")
    result_2 = _run_pipeline("req-learning-int-004")

    feedback_collector = FeedbackCollector()
    signal_builder = RouterLearningSignalBuilder()
    signal_collector = RouterLearningSignalCollector()

    feedback_1 = feedback_collector.submit(
        result_1["audit_record"],
        UserFeedbackInput(
            rating="correct",
        ),
    )

    feedback_2 = feedback_collector.submit(
        result_2["audit_record"],
        UserFeedbackInput(
            rating="incorrect",
            issue_types=[
                "output_type_error",
            ],
            expected_output_kind="raster",
        ),
    )

    signal_collector.ingest_many(
        signal_builder.build(
            audit_record=result_1["audit_record"],
            feedback_record=feedback_1,
        )
    )

    signal_collector.ingest_many(
        signal_builder.build(
            audit_record=result_2["audit_record"],
            feedback_record=feedback_2,
        )
    )

    summary = signal_collector.summarize()

    assert summary["total_signals"] >= 2
    assert summary["signal_type_counts"]["positive_confirmation"] == 1
    assert summary["signal_type_counts"]["output_type_correction"] == 1
    assert summary["severity_counts"]["low"] >= 1
    assert summary["severity_counts"]["high"] >= 1
