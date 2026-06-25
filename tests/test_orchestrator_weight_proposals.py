"""
Tests for RouterWeightProposalEngine.

Run:
    pytest tests/test_orchestrator_weight_proposals.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.feedback import FeedbackCollector, UserFeedbackInput  # noqa: E402
from orchestrator.learning_signals import RouterLearningSignalBuilder  # noqa: E402
from orchestrator.weight_proposals import (  # noqa: E402
    InMemoryRouterWeightStore,
    RouterWeightProposalCollector,
    RouterWeightProposalEngine,
    WeightProposalConfig,
    WeightStoreConfig,
)


def _sample_audit() -> dict:
    return {
        "request_id": "req-weight-001",
        "query_hash": "hash-weight-001",
        "status": "success",
        "intent": {
            "intent_name": "extract_vegetation_polygons_from_ndvi_threshold",
            "index_name": "NDVI",
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
            },
        },
        "outputs_summary": {
            "vegetation_polygons": {
                "kind": "vector",
                "feature_count": 3,
            }
        },
    }


def _route_correction_signals():
    audit = _sample_audit()
    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=["route_error", "plugin_error"],
            expected_capability="threshold_raster",
            expected_plugin_id="raster_threshold",
        ),
    )

    return RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )


def test_weight_store_returns_default_weight() -> None:
    store = InMemoryRouterWeightStore()

    assert store.get_weight("capability", "threshold_raster") == 1.0
    assert store.get_weight("plugin", "raster_threshold") == 1.0


def test_weight_store_can_set_and_clamp_weights() -> None:
    store = InMemoryRouterWeightStore(
        WeightStoreConfig(
            default_weight=1.0,
            min_weight=0.0,
            max_weight=2.0,
        )
    )

    store.set_weight("capability", "threshold_raster", 1.5)
    assert store.get_weight("capability", "threshold_raster") == 1.5

    store.set_weight("plugin", "raster_threshold", 3.5)
    assert store.get_weight("plugin", "raster_threshold") == 2.0


def test_weight_proposal_engine_creates_capability_and_plugin_proposals() -> None:
    signals = _route_correction_signals()

    proposals = RouterWeightProposalEngine().build(signals)

    names = {
        (proposal.target, proposal.name)
        for proposal in proposals
    }

    assert ("capability", "raster_to_vector") in names
    assert ("capability", "threshold_raster") in names
    assert ("plugin", "raster_to_vector") in names
    assert ("plugin", "raster_threshold") in names

    proposal_by_key = {
        (proposal.target, proposal.name): proposal
        for proposal in proposals
    }

    assert proposal_by_key[("capability", "raster_to_vector")].delta == -0.15
    assert proposal_by_key[("capability", "threshold_raster")].delta == 0.15

    assert proposal_by_key[("plugin", "raster_to_vector")].delta == -0.15
    assert proposal_by_key[("plugin", "raster_threshold")].delta == 0.15

    for proposal in proposals:
        assert proposal.status == "pending_review"
        assert proposal.evidence_count == 1
        assert proposal.signal_ids
        assert proposal.severity_counts["high"] == 1


def test_weight_proposal_engine_uses_existing_weights() -> None:
    signals = _route_correction_signals()

    store = InMemoryRouterWeightStore(
        capability_weights={
            "raster_to_vector": 1.2,
            "threshold_raster": 0.8,
        },
        plugin_weights={
            "raster_to_vector": 1.1,
            "raster_threshold": 0.9,
        },
    )

    proposals = RouterWeightProposalEngine().build(
        signals,
        weight_store=store,
    )

    proposal_by_key = {
        (proposal.target, proposal.name): proposal
        for proposal in proposals
    }

    assert proposal_by_key[("capability", "raster_to_vector")].current_weight == 1.2
    assert proposal_by_key[("capability", "raster_to_vector")].proposed_weight == 1.05

    assert proposal_by_key[("capability", "threshold_raster")].current_weight == 0.8
    assert proposal_by_key[("capability", "threshold_raster")].proposed_weight == 0.95


def test_weight_proposal_engine_aggregates_multiple_signals() -> None:
    signals = _route_correction_signals()
    all_signals = signals + signals

    proposals = RouterWeightProposalEngine().build(all_signals)

    proposal_by_key = {
        (proposal.target, proposal.name): proposal
        for proposal in proposals
    }

    assert proposal_by_key[("capability", "raster_to_vector")].delta == -0.3
    assert proposal_by_key[("capability", "threshold_raster")].delta == 0.3
    assert proposal_by_key[("capability", "threshold_raster")].evidence_count == 2
    assert proposal_by_key[("capability", "threshold_raster")].severity_counts["high"] == 2


def test_weight_proposal_engine_clamps_proposed_weights() -> None:
    signals = _route_correction_signals()

    store = InMemoryRouterWeightStore(
        WeightStoreConfig(
            default_weight=1.0,
            min_weight=0.0,
            max_weight=1.05,
        )
    )

    proposals = RouterWeightProposalEngine().build(
        signals,
        weight_store=store,
    )

    proposal_by_key = {
        (proposal.target, proposal.name): proposal
        for proposal in proposals
    }

    assert proposal_by_key[("capability", "threshold_raster")].proposed_weight == 1.05
    assert proposal_by_key[("capability", "threshold_raster")].metadata["clamped"] is True


def test_weight_proposal_engine_build_dicts() -> None:
    signals = _route_correction_signals()

    payloads = RouterWeightProposalEngine().build_dicts(signals)

    assert isinstance(payloads, list)
    assert isinstance(payloads[0], dict)
    assert payloads[0]["status"] == "pending_review"


def test_approve_reject_and_apply_proposal() -> None:
    signals = _route_correction_signals()
    store = InMemoryRouterWeightStore()

    proposal = [
        item
        for item in RouterWeightProposalEngine().build(signals)
        if item.target == "capability" and item.name == "threshold_raster"
    ][0]

    engine = RouterWeightProposalEngine()

    approved = engine.approve(proposal)
    assert approved.status == "approved"

    applied = engine.apply(
        approved,
        weight_store=store,
    )

    assert applied.status == "applied"
    assert store.get_weight("capability", "threshold_raster") == proposal.proposed_weight

    rejected = engine.reject(proposal)
    assert rejected.status == "rejected"


def test_apply_requires_approved_proposal() -> None:
    signals = _route_correction_signals()
    proposal = RouterWeightProposalEngine().build(signals)[0]

    with pytest.raises(ValueError, match="approved"):
        RouterWeightProposalEngine.apply(
            proposal,
            weight_store=InMemoryRouterWeightStore(),
        )


def test_weight_proposal_collector_summarizes() -> None:
    signals = _route_correction_signals()
    proposals = RouterWeightProposalEngine().build(signals)

    collector = RouterWeightProposalCollector()
    collector.ingest_many(proposals)

    summary = collector.summarize()

    assert summary["total_proposals"] == len(proposals)
    assert summary["target_counts"]["capability"] == 2
    assert summary["target_counts"]["plugin"] == 2
    assert summary["status_counts"]["pending_review"] == len(proposals)
    assert len(summary["proposals"]) == len(proposals)


def test_weight_proposal_collector_respects_limit() -> None:
    signals = _route_correction_signals()
    proposals = RouterWeightProposalEngine().build(signals)

    collector = RouterWeightProposalCollector(max_proposals=2)
    collector.ingest_many(proposals)

    assert collector.summarize()["total_proposals"] == 2


def test_configs_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="default_weight"):
        WeightStoreConfig(default_weight=-1)

    with pytest.raises(ValueError, match="max_weight"):
        WeightStoreConfig(min_weight=2, max_weight=1)

    with pytest.raises(ValueError, match="min_abs_delta"):
        WeightProposalConfig(min_abs_delta=-1)

    with pytest.raises(ValueError, match="round_digits"):
        WeightProposalConfig(round_digits=-1)

    with pytest.raises(ValueError, match="max_proposals"):
        RouterWeightProposalCollector(max_proposals=-1)


def test_in_memory_router_weight_store_replace_with_preserves_identity() -> None:
    original = InMemoryRouterWeightStore()
    original.set_weight("capability", "threshold_raster", 1.25)

    replacement = InMemoryRouterWeightStore(
        config=WeightStoreConfig(
            default_weight=0.75,
            min_weight=0.0,
            max_weight=5.0,
        ),
        capability_weights={
            "threshold_raster": 2.5,
            "raster_to_vector": 1.5,
        },
        plugin_weights={
            "demo_plugin": 1.75,
        },
    )

    original_id = id(original)

    original.replace_with(replacement)

    assert id(original) == original_id
    assert original.config.default_weight == 0.75
    assert original.config.max_weight == 5.0
    assert original.get_weight("capability", "threshold_raster") == 2.5
    assert original.get_weight("capability", "raster_to_vector") == 1.5
    assert original.get_weight("plugin", "demo_plugin") == 1.75
