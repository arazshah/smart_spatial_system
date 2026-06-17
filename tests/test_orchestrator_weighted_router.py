"""
Tests for WeightedCapabilityRouter.

Run:
    pytest tests/test_orchestrator_weighted_router.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.weight_proposals import (  # noqa: E402
    InMemoryRouterWeightStore,
    WeightStoreConfig,
)
from orchestrator.weighted_router import (  # noqa: E402
    WeightedCapabilityRouter,
    WeightedRouterConfig,
)


class FakeRouter:
    label = "fake-router"

    def rank_candidates(self):
        return [
            {
                "capability_name": "capability_a",
                "plugin_id": "plugin_a",
                "score": 0.8,
                "reasons": ["base_a"],
            },
            {
                "capability_name": "capability_b",
                "plugin_id": "plugin_b",
                "score": 0.7,
                "reasons": ["base_b"],
            },
        ]

    def choose_candidate(self):
        return {
            "capability_name": "capability_a",
            "plugin_id": "plugin_a",
            "score": 0.8,
            "reasons": [],
        }

    def nested_result(self):
        return {
            "candidates": self.rank_candidates(),
            "selected": self.choose_candidate(),
        }

    def tuple_result(self):
        return (
            "metadata",
            self.rank_candidates(),
        )


def test_weighted_router_delegates_non_callable_attributes() -> None:
    router = WeightedCapabilityRouter(FakeRouter())

    assert router.label == "fake-router"


def test_weighted_router_applies_capability_and_plugin_weights() -> None:
    store = InMemoryRouterWeightStore(
        capability_weights={
            "capability_a": 0.5,
        },
        plugin_weights={
            "plugin_a": 0.5,
        },
    )

    router = WeightedCapabilityRouter(
        FakeRouter(),
        weight_store=store,
    )

    candidate = router.choose_candidate()

    assert candidate["base_score"] == 0.8
    assert candidate["capability_weight"] == 0.5
    assert candidate["plugin_weight"] == 0.5
    assert candidate["score"] == 0.2
    assert candidate["weighted_score"] == 0.2
    assert candidate["score_weighted"] is True
    assert "weighted_score=base_score*capability_weight*plugin_weight" in candidate["reasons"]


def test_weighted_router_sorts_ranked_candidate_lists_after_weighting() -> None:
    store = InMemoryRouterWeightStore(
        capability_weights={
            "capability_a": 0.5,
            "capability_b": 1.5,
        },
        plugin_weights={
            "plugin_a": 1.0,
            "plugin_b": 1.0,
        },
    )

    router = WeightedCapabilityRouter(
        FakeRouter(),
        weight_store=store,
    )

    ranked = router.rank_candidates()

    assert ranked[0]["capability_name"] == "capability_b"
    assert ranked[0]["score"] == 1.0

    assert ranked[1]["capability_name"] == "capability_a"
    assert ranked[1]["score"] == 0.4


def test_weighted_router_can_disable_clamping() -> None:
    store = InMemoryRouterWeightStore(
        capability_weights={
            "capability_b": 2.0,
        },
        plugin_weights={
            "plugin_b": 1.0,
        },
    )

    router = WeightedCapabilityRouter(
        FakeRouter(),
        weight_store=store,
        config=WeightedRouterConfig(
            clamp_score=False,
        ),
    )

    ranked = router.rank_candidates()

    candidate_b = [
        item
        for item in ranked
        if item["capability_name"] == "capability_b"
    ][0]

    assert candidate_b["score"] == 1.4


def test_weighted_router_weights_nested_results() -> None:
    store = InMemoryRouterWeightStore(
        capability_weights={
            "capability_a": 0.5,
            "capability_b": 1.0,
        },
        plugin_weights={
            "plugin_a": 1.0,
            "plugin_b": 1.0,
        },
    )

    router = WeightedCapabilityRouter(
        FakeRouter(),
        weight_store=store,
    )

    result = router.nested_result()

    assert result["selected"]["score"] == 0.4
    assert result["candidates"][0]["score_weighted"] is True
    assert result["candidates"][1]["score_weighted"] is True


def test_weighted_router_weights_tuple_results() -> None:
    store = InMemoryRouterWeightStore(
        capability_weights={
            "capability_a": 0.5,
        },
    )

    router = WeightedCapabilityRouter(
        FakeRouter(),
        weight_store=store,
    )

    metadata, ranked = router.tuple_result()

    assert metadata == "metadata"
    assert isinstance(ranked, list)
    assert all(item["score_weighted"] is True for item in ranked)


def test_weighted_router_does_not_mutate_original_candidate() -> None:
    router = WeightedCapabilityRouter(FakeRouter())

    original = {
        "capability_name": "capability_a",
        "plugin_id": "plugin_a",
        "score": 0.8,
        "reasons": [],
    }

    weighted = router.weight_candidate(original)

    assert original == {
        "capability_name": "capability_a",
        "plugin_id": "plugin_a",
        "score": 0.8,
        "reasons": [],
    }

    assert weighted is not original
    assert weighted["score_weighted"] is True


def test_weighted_router_uses_weight_store_default_weight() -> None:
    store = InMemoryRouterWeightStore(
        WeightStoreConfig(
            default_weight=2.0,
            min_weight=0.0,
            max_weight=3.0,
        )
    )

    router = WeightedCapabilityRouter(
        FakeRouter(),
        weight_store=store,
        config=WeightedRouterConfig(
            clamp_score=False,
        ),
    )

    candidate = router.choose_candidate()

    # base_score 0.8 * capability_default 2.0 * plugin_default 2.0 = 3.2
    assert candidate["score"] == 3.2


def test_weighted_router_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="max_score"):
        WeightedRouterConfig(max_score=-1)

    with pytest.raises(ValueError, match="round_digits"):
        WeightedRouterConfig(round_digits=-1)
