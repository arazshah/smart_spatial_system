"""
Integration tests for persisted weight store with proposals and weighted router.

Run:
    pytest tests/test_orchestrator_weight_store_persistence_integration.py -v
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
    RouterWeightProposalEngine,
)
from orchestrator.weight_store_persistence import (  # noqa: E402
    RouterWeightStorePersistence,
    WeightStorePersistenceConfig,
)
from orchestrator.weighted_router import WeightedCapabilityRouter  # noqa: E402


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


def _make_base_router() -> KeywordScoringCapabilityRouter:
    registry = CapabilityRegistry.from_plugin_modules(SAFE_MODULES)
    return KeywordScoringCapabilityRouter(registry=registry)


def _run_pipeline(router, *, request_id: str):
    return run_natural_query_with_routing_evidence(
        NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        router=router,
        min_score=0.01,
        request_id=request_id,
    )


def test_applied_weight_store_can_be_saved_loaded_and_used_by_weighted_router(
    tmp_path: Path,
) -> None:
    # 1) Run once and create feedback.
    base_result = _run_pipeline(
        _make_base_router(),
        request_id="req-persist-int-001",
    )

    audit = base_result["audit_record"]

    feedback = FeedbackCollector().submit(
        audit,
        UserFeedbackInput(
            rating="incorrect",
            issue_types=[
                "route_error",
            ],
            expected_capability="threshold_raster",
        ),
    )

    # 2) Build learning signals and proposals.
    signals = RouterLearningSignalBuilder().build(
        audit_record=audit,
        feedback_record=feedback,
    )

    store = InMemoryRouterWeightStore()

    engine = RouterWeightProposalEngine()
    proposals = engine.build(
        signals,
        weight_store=store,
    )

    threshold_proposal = [
        proposal
        for proposal in proposals
        if proposal.target == "capability"
        and proposal.name == "threshold_raster"
    ][0]

    # 3) Approve and apply proposal.
    approved = engine.approve(threshold_proposal)
    applied = engine.apply(
        approved,
        weight_store=store,
    )

    assert applied.status == "applied"
    assert store.get_weight("capability", "threshold_raster") == threshold_proposal.proposed_weight

    # 4) Save to JSON.
    path = tmp_path / "weights" / "router_weights.json"

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
        )
    )

    persistence.save(
        store,
        metadata={
            "test": "applied_weight_store_can_be_saved_loaded",
        },
    )

    assert path.exists()

    # 5) Load after "restart".
    loaded_store = persistence.load()

    assert loaded_store.get_weight("capability", "threshold_raster") == threshold_proposal.proposed_weight

    # 6) Use loaded store in WeightedCapabilityRouter.
    weighted_router = WeightedCapabilityRouter(
        _make_base_router(),
        weight_store=loaded_store,
    )

    weighted_result = _run_pipeline(
        weighted_router,
        request_id="req-persist-int-002",
    )

    evidence = weighted_result["plan"].routing_evidence

    threshold_evidence = [
        item
        for item in evidence
        if item["capability_name"] == "threshold_raster"
    ][0]

    assert threshold_evidence["score_weighted"] is True
    assert threshold_evidence["capability_weight"] == threshold_proposal.proposed_weight


def test_manual_weight_store_save_load_changes_weighted_router_scores(
    tmp_path: Path,
) -> None:
    path = tmp_path / "router_weights.json"

    store = InMemoryRouterWeightStore(
        capability_weights={
            "calculate_spectral_index": 0.5,
            "threshold_raster": 1.0,
            "raster_to_vector": 1.0,
        },
        plugin_weights={
            "spectral_indices": 1.0,
            "raster_threshold": 1.0,
            "raster_to_vector": 1.0,
        },
    )

    persistence = RouterWeightStorePersistence(
        WeightStorePersistenceConfig(
            path=path,
        )
    )

    persistence.save(store)

    loaded_store = persistence.load()

    base_result = _run_pipeline(
        _make_base_router(),
        request_id="req-persist-base",
    )

    weighted_router = WeightedCapabilityRouter(
        _make_base_router(),
        weight_store=loaded_store,
    )

    weighted_result = _run_pipeline(
        weighted_router,
        request_id="req-persist-weighted",
    )

    base_scores = {
        item["capability_name"]: item["score"]
        for item in base_result["plan"].routing_evidence
    }

    weighted_scores = {
        item["capability_name"]: item["score"]
        for item in weighted_result["plan"].routing_evidence
    }

    assert weighted_scores["calculate_spectral_index"] < base_scores["calculate_spectral_index"]

    weighted_evidence = [
        item
        for item in weighted_result["plan"].routing_evidence
        if item["capability_name"] == "calculate_spectral_index"
    ][0]

    assert weighted_evidence["capability_weight"] == 0.5
    assert weighted_evidence["plugin_weight"] == 1.0
