"""
Integration tests for LLM Gate attached to routing-aware runner/response.

Run:
    pytest tests/test_orchestrator_llm_gate_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402
from orchestrator.llm_gate import (  # noqa: E402
    LLMGate,
    LLMGateBlockedError,
    LLMProviderStub,
    LLMBudgetPolicy,
)
from orchestrator.router_decision import RouterDecisionConfig  # noqa: E402
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


def _run_pipeline(
    *,
    decision_config: RouterDecisionConfig | None = None,
    llm_gate: LLMGate | None = None,
    enforce_llm_gate: bool = False,
):
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
        decision_config=decision_config,
        llm_gate=llm_gate,
        enforce_llm_gate=enforce_llm_gate,
    )


def test_runner_attaches_llm_gate_result_to_top_level_execution_and_response() -> None:
    result = _run_pipeline()

    assert "llm_gate_result" in result
    assert "llm_gate_result" in result["execution"]
    assert "llm_gate_result" in result["response"]

    assert result["execution"]["llm_gate_result"] == result["llm_gate_result"]
    assert result["response"]["llm_gate_result"] == result["llm_gate_result"]

    metadata_gate = result["response"]["metadata"]["llm_gate"]

    assert metadata_gate["llm_action"] == result["llm_gate_result"]["llm_action"]
    assert metadata_gate["status"] == result["llm_gate_result"]["status"]
    assert metadata_gate["provider_called"] == result["llm_gate_result"]["provider_called"]
    assert metadata_gate["blocked"] == result["llm_gate_result"]["blocked"]


def test_runner_skip_policy_does_not_call_provider() -> None:
    provider = LLMProviderStub(available=True)

    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_optional=True,
            max_optional_calls=1,
            allow_required=True,
            max_required_calls=1,
        ),
    )

    result = _run_pipeline(
        decision_config=RouterDecisionConfig(
            high_threshold=0.20,
            medium_threshold=0.10,
            competitive_gap_threshold=0.0,
        ),
        llm_gate=gate,
    )

    decision = result["router_decision"]
    gate_result = result["llm_gate_result"]

    assert decision["level"] == "high"
    assert decision["llm_action"] == "skip"

    assert gate_result["status"] == "skipped"
    assert gate_result["provider_called"] is False
    assert gate_result["fallback_to_deterministic"] is True
    assert provider.call_count == 0


def test_runner_optional_policy_can_call_provider_when_budget_allows() -> None:
    provider = LLMProviderStub(available=True)

    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_optional=True,
            max_optional_calls=1,
        ),
    )

    result = _run_pipeline(
        decision_config=RouterDecisionConfig(
            high_threshold=1.0,
            medium_threshold=0.10,
            competitive_gap_threshold=0.0,
        ),
        llm_gate=gate,
    )

    decision = result["router_decision"]
    gate_result = result["llm_gate_result"]

    assert decision["level"] == "medium"
    assert decision["llm_action"] == "optional"

    assert gate_result["status"] == "called"
    assert gate_result["provider_called"] is True
    assert gate_result["blocked"] is False
    assert provider.call_count == 1


def test_runner_optional_policy_budget_denied_continues_deterministic_execution() -> None:
    provider = LLMProviderStub(available=True)

    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_optional=False,
            max_optional_calls=0,
        ),
    )

    result = _run_pipeline(
        decision_config=RouterDecisionConfig(
            high_threshold=1.0,
            medium_threshold=0.10,
            competitive_gap_threshold=0.0,
        ),
        llm_gate=gate,
    )

    gate_result = result["llm_gate_result"]

    assert gate_result["status"] == "optional_budget_denied"
    assert gate_result["provider_called"] is False
    assert gate_result["blocked"] is False
    assert gate_result["fallback_to_deterministic"] is True
    assert result["response"]["status"] == "success"
    assert result["response"]["metadata"]["feature_count"] == 3
    assert provider.call_count == 0


def test_runner_required_policy_provider_unavailable_records_block_but_can_continue_if_not_enforced() -> None:
    provider = LLMProviderStub(available=False)

    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_required=True,
            max_required_calls=1,
        ),
    )

    result = _run_pipeline(
        decision_config=RouterDecisionConfig(
            high_threshold=1.0,
            medium_threshold=0.99,
            competitive_gap_threshold=0.0,
        ),
        llm_gate=gate,
        enforce_llm_gate=False,
    )

    decision = result["router_decision"]
    gate_result = result["llm_gate_result"]

    assert decision["llm_action"] == "required"
    assert gate_result["status"] == "required_provider_unavailable"
    assert gate_result["blocked"] is True
    assert gate_result["fallback_to_deterministic"] is False

    # Not enforced, so the deterministic pipeline still completed.
    assert result["response"]["status"] == "success"
    assert result["response"]["metadata"]["feature_count"] == 3


def test_runner_required_policy_provider_unavailable_raises_when_enforced() -> None:
    provider = LLMProviderStub(available=False)

    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_required=True,
            max_required_calls=1,
        ),
    )

    with pytest.raises(LLMGateBlockedError, match="blocked"):
        _run_pipeline(
            decision_config=RouterDecisionConfig(
                high_threshold=1.0,
                medium_threshold=0.99,
                competitive_gap_threshold=0.0,
            ),
            llm_gate=gate,
            enforce_llm_gate=True,
        )
