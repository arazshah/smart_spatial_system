"""
Tests for router decision layer.

Run:
    pytest tests/test_orchestrator_router_decision.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.models import ScoredCapability  # noqa: E402
from orchestrator.router_decision import (  # noqa: E402
    RouterDecisionConfig,
    RouterDecisionLayer,
)


SAFE_MODULES = [
    "plugins.spectral_indices",
    "plugins.raster_threshold",
    "plugins.raster_to_vector",
]


def _candidate(
    name: str,
    score: float,
    *,
    plugin_id: str | None = None,
    output_kind: str = "raster",
) -> ScoredCapability:
    return ScoredCapability(
        capability_name=name,
        plugin_id=plugin_id or name,
        output_kind=output_kind,
        score=score,
        matched_terms=[name],
        reasons=["test"],
    )


def _make_scoring_router() -> KeywordScoringCapabilityRouter:
    registry = CapabilityRegistry.from_plugin_modules(SAFE_MODULES)
    return KeywordScoringCapabilityRouter(registry=registry)


def test_router_decision_high_confidence_skips_llm() -> None:
    layer = RouterDecisionLayer()

    decision = layer.decide(
        [
            _candidate("calculate_spectral_index", 0.90),
            _candidate("threshold_raster", 0.60),
        ]
    )

    assert decision.level == "high"
    assert decision.llm_action == "skip"
    assert decision.llm_required is False
    assert decision.llm_optional is False
    assert decision.route_without_llm is True
    assert decision.top_candidate.capability_name == "calculate_spectral_index"
    assert decision.competitive_gap == 0.30
    assert decision.is_ambiguous is False
    assert "llm_skipped_sufficient_confidence" in decision.reasons


def test_router_decision_medium_confidence_makes_llm_optional() -> None:
    layer = RouterDecisionLayer()

    decision = layer.decide(
        [
            _candidate("threshold_raster", 0.65),
            _candidate("raster_to_vector", 0.30, output_kind="vector"),
        ]
    )

    assert decision.level == "medium"
    assert decision.llm_action == "optional"
    assert decision.llm_required is False
    assert decision.llm_optional is True
    assert decision.route_without_llm is True
    assert "llm_optional_medium_confidence" in decision.reasons


def test_router_decision_low_confidence_requires_llm() -> None:
    layer = RouterDecisionLayer()

    decision = layer.decide(
        [
            _candidate("unknown_candidate", 0.30),
        ]
    )

    assert decision.level == "low"
    assert decision.llm_action == "required"
    assert decision.llm_required is True
    assert decision.llm_optional is False
    assert decision.route_without_llm is False
    assert "llm_required_low_confidence" in decision.reasons


def test_router_decision_no_candidates_requires_llm() -> None:
    layer = RouterDecisionLayer()

    decision = layer.decide([])

    assert decision.level == "low"
    assert decision.llm_action == "required"
    assert decision.llm_required is True
    assert decision.route_without_llm is False
    assert decision.top_candidate is None
    assert decision.top_score == 0.0
    assert "no_candidates" in decision.reasons


def test_router_decision_ambiguity_requires_llm_even_if_high() -> None:
    layer = RouterDecisionLayer()

    decision = layer.decide(
        [
            _candidate("calculate_spectral_index", 0.91),
            _candidate("threshold_raster", 0.86),
        ]
    )

    assert decision.level == "high"
    assert decision.is_ambiguous is True
    assert decision.competitive_gap == 0.05
    assert decision.llm_action == "required"
    assert decision.llm_required is True
    assert decision.route_without_llm is False
    assert "competitive_gap_below_threshold" in decision.reasons
    assert "llm_required_ambiguity" in decision.reasons


def test_router_decision_gap_equal_threshold_is_not_ambiguous() -> None:
    layer = RouterDecisionLayer(
        RouterDecisionConfig(
            competitive_gap_threshold=0.10,
        )
    )

    decision = layer.decide(
        [
            _candidate("calculate_spectral_index", 0.90),
            _candidate("threshold_raster", 0.80),
        ]
    )

    assert decision.competitive_gap == 0.10
    assert decision.is_ambiguous is False
    assert decision.llm_required is False


def test_router_decision_custom_thresholds() -> None:
    layer = RouterDecisionLayer(
        RouterDecisionConfig(
            high_threshold=0.80,
            medium_threshold=0.40,
            competitive_gap_threshold=0.05,
        )
    )

    decision = layer.decide(
        [
            _candidate("calculate_spectral_index", 0.82),
            _candidate("threshold_raster", 0.60),
        ]
    )

    assert decision.level == "high"
    assert decision.llm_action == "skip"
    assert decision.route_without_llm is True


def test_router_decision_config_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError, match="medium_threshold"):
        RouterDecisionConfig(
            high_threshold=0.80,
            medium_threshold=0.90,
        )

    with pytest.raises(ValueError, match="competitive_gap"):
        RouterDecisionConfig(
            competitive_gap_threshold=1.5,
        )


def test_router_decision_to_dict_contains_audit_fields() -> None:
    layer = RouterDecisionLayer()

    decision = layer.decide(
        [
            _candidate("calculate_spectral_index", 0.90),
            _candidate("threshold_raster", 0.50),
        ]
    )

    payload = decision.to_dict()

    assert payload["level"] == "high"
    assert payload["llm_action"] == "skip"
    assert payload["top_candidate"]["capability_name"] == "calculate_spectral_index"
    assert payload["second_candidate"]["capability_name"] == "threshold_raster"
    assert payload["top_score"] == 0.90
    assert payload["second_score"] == 0.50
    assert payload["competitive_gap"] == 0.40
    assert payload["is_ambiguous"] is False
    assert isinstance(payload["reasons"], list)


def test_router_decision_layer_decides_real_keyword_query_for_vector_conversion() -> None:
    router = _make_scoring_router()
    layer = RouterDecisionLayer()

    decision = layer.decide_query(
        router,
        "ماسک رستر را به پلیگون تبدیل کن",
        min_score=0.0,
    )

    assert decision.top_candidate is not None
    assert decision.top_candidate.capability_name == "raster_to_vector"
    assert decision.top_candidate.plugin_id == "raster_to_vector"
    assert decision.top_candidate.output_kind == "vector"
    assert decision.route_without_llm in {True, False}
    assert decision.llm_action in {"skip", "optional", "required"}
    assert decision.level in {"high", "medium", "low"}


def test_router_decision_layer_decides_real_keyword_query_with_output_filter() -> None:
    router = _make_scoring_router()
    layer = RouterDecisionLayer()

    decision = layer.decide_query(
        router,
        "شاخص NDVI را از تصویر ماهواره‌ای محاسبه کن",
        expected_output_kind="raster",
        min_score=0.0,
    )

    assert decision.top_candidate is not None
    assert decision.top_candidate.capability_name == "calculate_spectral_index"
    assert decision.top_candidate.plugin_id == "spectral_indices"
    assert decision.top_candidate.output_kind == "raster"
