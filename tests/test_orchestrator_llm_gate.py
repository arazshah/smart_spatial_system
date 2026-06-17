"""
Tests for LLM Gate / LLM Policy Stub.

Run:
    pytest tests/test_orchestrator_llm_gate.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.llm_gate import (  # noqa: E402
    LLMGate,
    LLMProviderStub,
    LLMBudgetPolicy,
)


def _router_decision(action: str) -> dict:
    return {
        "level": "high" if action == "skip" else "medium",
        "llm_action": action,
        "route_without_llm": action != "required",
        "llm_required": action == "required",
        "llm_optional": action == "optional",
        "top_score": 0.75,
        "competitive_gap": 0.20,
        "is_ambiguous": False,
        "reasons": ["test"],
    }


def test_llm_gate_skip_never_calls_provider() -> None:
    provider = LLMProviderStub(available=True)
    gate = LLMGate(provider=provider)

    result = gate.evaluate(_router_decision("skip")).to_dict()

    assert result["llm_action"] == "skip"
    assert result["status"] == "skipped"
    assert result["requested_by_router"] is False
    assert result["provider_called"] is False
    assert result["blocked"] is False
    assert result["fallback_to_deterministic"] is True
    assert provider.call_count == 0


def test_llm_gate_optional_budget_denied_falls_back_to_deterministic() -> None:
    provider = LLMProviderStub(available=True)
    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_optional=False,
            max_optional_calls=0,
        ),
    )

    result = gate.evaluate(_router_decision("optional")).to_dict()

    assert result["status"] == "optional_budget_denied"
    assert result["requested_by_router"] is True
    assert result["allowed_by_budget"] is False
    assert result["provider_called"] is False
    assert result["blocked"] is False
    assert result["fallback_to_deterministic"] is True
    assert provider.call_count == 0


def test_llm_gate_optional_provider_unavailable_falls_back_to_deterministic() -> None:
    provider = LLMProviderStub(available=False)
    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_optional=True,
            max_optional_calls=1,
        ),
    )

    result = gate.evaluate(_router_decision("optional")).to_dict()

    assert result["status"] == "optional_provider_unavailable"
    assert result["allowed_by_budget"] is True
    assert result["provider_available"] is False
    assert result["provider_called"] is False
    assert result["blocked"] is False
    assert result["fallback_to_deterministic"] is True
    assert provider.call_count == 0


def test_llm_gate_optional_calls_provider_when_budget_and_provider_available() -> None:
    provider = LLMProviderStub(available=True)
    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_optional=True,
            max_optional_calls=1,
        ),
    )

    result = gate.evaluate(
        _router_decision("optional"),
        context={
            "query": "test query",
        },
    ).to_dict()

    assert result["status"] == "called"
    assert result["allowed_by_budget"] is True
    assert result["provider_available"] is True
    assert result["provider_called"] is True
    assert result["blocked"] is False
    assert result["fallback_to_deterministic"] is False
    assert result["llm_payload"]["provider"] == "stub"
    assert result["llm_payload"]["context_keys"] == ["query"]
    assert provider.call_count == 1


def test_llm_gate_required_provider_unavailable_blocks() -> None:
    provider = LLMProviderStub(available=False)
    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_required=True,
            max_required_calls=1,
        ),
    )

    result = gate.evaluate(_router_decision("required")).to_dict()

    assert result["status"] == "required_provider_unavailable"
    assert result["requested_by_router"] is True
    assert result["allowed_by_budget"] is True
    assert result["provider_available"] is False
    assert result["provider_called"] is False
    assert result["blocked"] is True
    assert result["fallback_to_deterministic"] is False
    assert provider.call_count == 0


def test_llm_gate_required_budget_denied_blocks() -> None:
    provider = LLMProviderStub(available=True)
    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_required=False,
            max_required_calls=0,
        ),
    )

    result = gate.evaluate(_router_decision("required")).to_dict()

    assert result["status"] == "required_budget_denied"
    assert result["allowed_by_budget"] is False
    assert result["provider_called"] is False
    assert result["blocked"] is True
    assert result["fallback_to_deterministic"] is False
    assert provider.call_count == 0


def test_llm_gate_required_calls_provider_when_available() -> None:
    provider = LLMProviderStub(available=True)
    gate = LLMGate(
        provider=provider,
        budget_policy=LLMBudgetPolicy(
            allow_required=True,
            max_required_calls=1,
        ),
    )

    result = gate.evaluate(_router_decision("required")).to_dict()

    assert result["status"] == "called"
    assert result["provider_called"] is True
    assert result["blocked"] is False
    assert result["fallback_to_deterministic"] is False
    assert result["llm_payload"]["provider"] == "stub"
    assert provider.call_count == 1


def test_llm_gate_rejects_unknown_action() -> None:
    gate = LLMGate()

    with pytest.raises(ValueError, match="Unsupported"):
        gate.evaluate(_router_decision("unknown"))


def test_llm_budget_policy_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError, match="max_optional_calls"):
        LLMBudgetPolicy(max_optional_calls=-1)

    with pytest.raises(ValueError, match="max_required_calls"):
        LLMBudgetPolicy(max_required_calls=-1)
