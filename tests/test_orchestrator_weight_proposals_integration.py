"""
Integration tests for weight proposals with runner audit + feedback + signals.

Run:
    pytest tests/test_orchestrator_weight_proposals_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.feedback import FeedbackCollector, UserFeedbackInput  # noqa: E402
from orchestrator.learning_signals import RouterLearningSignalBuilder  # noqa: E402
from orchestrator.routing_aware_natural_query_runner import (  # noqa: E402
    run_natural_query_with_routing_evidence,
)
from orchestrator.weight_proposals import (  # noqa: E402
    InMemoryRouterWeightStore,
    RouterWeightProposalCollector,
    RouterWeightProposalEngine,
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


def test_weight_proposals_from_positive_runner_feedback() -> None:
    result = _run_pipeline("req-weight-int-001")
    audit = result["audit_record"]

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="correct",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    proposals = RouterWeightProposalEngine().build(signals)

    assert proposals

    capability_proposals = [
        proposal
        for proposal in proposals
        if proposal.target == "capability"
    ]

    plugin_proposals = [
        proposal
        for proposal in proposals
        if proposal.target == "plugin"
    ]

    assert capability_proposals
    assert plugin_proposals

    for proposal in proposals:
        assert proposal.status == "pending_review"
        assert proposal.delta > 0


def test_weight_proposals_from_negative_runner_feedback() -> None:
    result = _run_pipeline("req-weight-int-002")
    audit = result["audit_record"]

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=[
                "route_error",
                "plugin_error",
            ],
            expected_capability="threshold_raster",
            expected_plugin_id="raster_threshold",
        ),
    )

    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    proposals = RouterWeightProposalEngine().build(signals)

    proposal_by_key = {
        (proposal.target, proposal.name): proposal
        for proposal in proposals
    }

    assert ("capability", "threshold_raster") in proposal_by_key
    assert ("plugin", "raster_threshold") in proposal_by_key

    assert proposal_by_key[("capability", "threshold_raster")].delta > 0
    assert proposal_by_key[("plugin", "raster_threshold")].delta > 0

    observed_capability = feedback.observed_top_capability
    observed_plugin = feedback.observed_top_plugin_id

    assert proposal_by_key[("capability", observed_capability)].delta < 0
    assert proposal_by_key[("plugin", observed_plugin)].delta < 0


def test_weight_proposal_review_and_apply_flow() -> None:
    result = _run_pipeline("req-weight-int-003")
    audit = result["audit_record"]

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

    store = InMemoryRouterWeightStore()

    proposals = RouterWeightProposalEngine().build(
        signals,
        weight_store=store,
    )

    proposal = [
        item
        for item in proposals
        if item.target == "capability" and item.name == "threshold_raster"
    ][0]

    engine = RouterWeightProposalEngine()

    approved = engine.approve(proposal)
    applied = engine.apply(
        approved,
        weight_store=store,
    )

    assert applied.status == "applied"
    assert store.get_weight("capability", "threshold_raster") == proposal.proposed_weight


def test_weight_proposal_collector_with_runner_flow() -> None:
    result_1 = _run_pipeline("req-weight-int-004")
    result_2 = _run_pipeline("req-weight-int-005")

    feedback_collector = FeedbackCollector()
    signal_builder = RouterLearningSignalBuilder()
    proposal_engine = RouterWeightProposalEngine()
    proposal_collector = RouterWeightProposalCollector()

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
            issue_types=["route_error"],
            expected_capability="threshold_raster",
        ),
    )

    signals = []
    signals.extend(
        signal_builder.build(
            audit_record=result_1["audit_record"],
            feedback_record=feedback_1,
        )
    )
    signals.extend(
        signal_builder.build(
            audit_record=result_2["audit_record"],
            feedback_record=feedback_2,
        )
    )

    proposals = proposal_engine.build(signals)
    proposal_collector.ingest_many(proposals)

    summary = proposal_collector.summarize()

    assert summary["total_proposals"] == len(proposals)
    assert summary["target_counts"]["capability"] >= 1
    assert summary["status_counts"]["pending_review"] == len(proposals)
