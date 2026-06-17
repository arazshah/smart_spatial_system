"""
orchestrator.llm_gate

LLM Gate / LLM Policy Stub.

This module does not integrate with a real LLM provider yet.
It defines the contract and policy layer that decides whether an LLM call
should be skipped, optional, attempted, or blocked.

Input:
    router_decision:
        {
            "llm_action": "skip" | "optional" | "required",
            ...
        }

Output:
    LLMGateResult:
        - whether LLM was requested by router
        - whether budget allowed it
        - whether provider was available
        - whether provider was called
        - whether execution should be blocked
        - whether deterministic fallback is allowed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LLMProviderUnavailableError(RuntimeError):
    """
    Raised when an LLM provider is required/attempted but unavailable.
    """


class LLMGateBlockedError(RuntimeError):
    """
    Raised when LLM gate blocks execution and enforcement is enabled.
    """


@dataclass
class LLMBudgetPolicy:
    """
    Simple in-request LLM budget policy.

    This is intentionally small and deterministic.

    Defaults:
        - optional LLM calls are not allowed
        - required LLM calls are allowed if provider exists
    """

    allow_optional: bool = False
    allow_required: bool = True
    max_optional_calls: int = 0
    max_required_calls: int = 1

    optional_calls_used: int = 0
    required_calls_used: int = 0

    def __post_init__(self) -> None:
        if self.max_optional_calls < 0:
            raise ValueError("max_optional_calls must be >= 0.")

        if self.max_required_calls < 0:
            raise ValueError("max_required_calls must be >= 0.")

    def can_call(self, action: str) -> bool:
        """
        Return whether the budget allows this LLM action.
        """
        if action == "optional":
            return (
                self.allow_optional
                and self.optional_calls_used < self.max_optional_calls
            )

        if action == "required":
            return (
                self.allow_required
                and self.required_calls_used < self.max_required_calls
            )

        if action == "skip":
            return False

        raise ValueError(f"Unsupported LLM action: {action}")

    def consume(self, action: str) -> None:
        """
        Consume one budget unit for action.
        """
        if action == "optional":
            if not self.can_call(action):
                raise ValueError("Optional LLM budget is not available.")
            self.optional_calls_used += 1
            return

        if action == "required":
            if not self.can_call(action):
                raise ValueError("Required LLM budget is not available.")
            self.required_calls_used += 1
            return

        raise ValueError(f"Unsupported LLM action for budget consumption: {action}")


@dataclass(frozen=True)
class LLMGateResult:
    """
    Result of LLM gate evaluation.
    """

    llm_action: str
    status: str

    requested_by_router: bool
    allowed_by_budget: bool
    provider_available: bool
    provider_called: bool

    blocked: bool
    fallback_to_deterministic: bool

    llm_payload: dict[str, Any] | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert result to JSON-like dict for response/trace/audit.
        """
        return {
            "llm_action": self.llm_action,
            "status": self.status,
            "requested_by_router": self.requested_by_router,
            "allowed_by_budget": self.allowed_by_budget,
            "provider_available": self.provider_available,
            "provider_called": self.provider_called,
            "blocked": self.blocked,
            "fallback_to_deterministic": self.fallback_to_deterministic,
            "llm_payload": self.llm_payload,
            "reasons": list(self.reasons),
        }


@dataclass
class LLMProviderStub:
    """
    Test/stub provider.

    It simulates an LLM provider without external dependencies.
    """

    available: bool = False
    response: dict[str, Any] = field(
        default_factory=lambda: {
            "provider": "stub",
            "suggestion": "keep_deterministic_route",
            "confidence_adjustment": 0.0,
        }
    )
    call_count: int = 0

    def refine_routing_decision(
        self,
        *,
        router_decision: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Simulate LLM route refinement.
        """
        if not self.available:
            raise LLMProviderUnavailableError("LLM provider stub is unavailable.")

        self.call_count += 1

        return {
            **self.response,
            "router_decision_level": router_decision.get("level"),
            "router_llm_action": router_decision.get("llm_action"),
            "context_keys": sorted((context or {}).keys()),
        }


class LLMGate:
    """
    LLM gate that evaluates router_decision against budget/provider availability.

    This class does not know about geospatial operations.
    It only controls whether LLM should be called or skipped.
    """

    def __init__(
        self,
        *,
        provider: Any | None = None,
        budget_policy: LLMBudgetPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.budget_policy = budget_policy or LLMBudgetPolicy()

    def evaluate(
        self,
        router_decision: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> LLMGateResult:
        """
        Evaluate LLM policy from router_decision.

        Args:
            router_decision:
                Decision payload from RouterDecisionLayer.to_dict().
            context:
                Optional debug/audit context.

        Returns:
            LLMGateResult.
        """
        llm_action = router_decision.get("llm_action")

        if llm_action not in {"skip", "optional", "required"}:
            raise ValueError(f"Unsupported llm_action: {llm_action}")

        if llm_action == "skip":
            return LLMGateResult(
                llm_action="skip",
                status="skipped",
                requested_by_router=False,
                allowed_by_budget=False,
                provider_available=self._provider_available(),
                provider_called=False,
                blocked=False,
                fallback_to_deterministic=True,
                llm_payload=None,
                reasons=[
                    "router_decision_skip",
                    "llm_not_called",
                ],
            )

        requested_by_router = True
        provider_available = self._provider_available()
        allowed_by_budget = self.budget_policy.can_call(llm_action)

        if not allowed_by_budget:
            if llm_action == "optional":
                return LLMGateResult(
                    llm_action=llm_action,
                    status="optional_budget_denied",
                    requested_by_router=requested_by_router,
                    allowed_by_budget=False,
                    provider_available=provider_available,
                    provider_called=False,
                    blocked=False,
                    fallback_to_deterministic=True,
                    llm_payload=None,
                    reasons=[
                        "llm_optional",
                        "budget_denied",
                        "deterministic_fallback_allowed",
                    ],
                )

            return LLMGateResult(
                llm_action=llm_action,
                status="required_budget_denied",
                requested_by_router=requested_by_router,
                allowed_by_budget=False,
                provider_available=provider_available,
                provider_called=False,
                blocked=True,
                fallback_to_deterministic=False,
                llm_payload=None,
                reasons=[
                    "llm_required",
                    "budget_denied",
                    "execution_should_be_blocked_or_escalated",
                ],
            )

        if not provider_available:
            if llm_action == "optional":
                return LLMGateResult(
                    llm_action=llm_action,
                    status="optional_provider_unavailable",
                    requested_by_router=requested_by_router,
                    allowed_by_budget=True,
                    provider_available=False,
                    provider_called=False,
                    blocked=False,
                    fallback_to_deterministic=True,
                    llm_payload=None,
                    reasons=[
                        "llm_optional",
                        "provider_unavailable",
                        "deterministic_fallback_allowed",
                    ],
                )

            return LLMGateResult(
                llm_action=llm_action,
                status="required_provider_unavailable",
                requested_by_router=requested_by_router,
                allowed_by_budget=True,
                provider_available=False,
                provider_called=False,
                blocked=True,
                fallback_to_deterministic=False,
                llm_payload=None,
                reasons=[
                    "llm_required",
                    "provider_unavailable",
                    "execution_should_be_blocked_or_escalated",
                ],
            )

        self.budget_policy.consume(llm_action)

        payload = self._call_provider(
            router_decision=router_decision,
            context=context,
        )

        return LLMGateResult(
            llm_action=llm_action,
            status="called",
            requested_by_router=requested_by_router,
            allowed_by_budget=True,
            provider_available=True,
            provider_called=True,
            blocked=False,
            fallback_to_deterministic=False,
            llm_payload=payload,
            reasons=[
                f"llm_{llm_action}",
                "budget_allowed",
                "provider_available",
                "provider_called",
            ],
        )

    def _provider_available(self) -> bool:
        """
        Return provider availability.
        """
        if self.provider is None:
            return False

        return bool(getattr(self.provider, "available", True))

    def _call_provider(
        self,
        *,
        router_decision: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Call provider using the expected stub/provider contract.
        """
        if self.provider is None:
            raise LLMProviderUnavailableError("No LLM provider configured.")

        method = getattr(self.provider, "refine_routing_decision", None)

        if method is None or not callable(method):
            raise LLMProviderUnavailableError(
                "LLM provider does not implement refine_routing_decision()."
            )

        return method(
            router_decision=router_decision,
            context=context,
        )
