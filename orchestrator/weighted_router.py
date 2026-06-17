"""
orchestrator.weighted_router

Weighted capability router wrapper.

This module wraps an existing router and applies approved/applied router weights
from InMemoryRouterWeightStore to candidate scores.

Formula:
    weighted_score = base_score * capability_weight * plugin_weight

Important:
    This wrapper does not learn by itself.
    It only consumes weights that were already placed in a weight store.

Typical flow:
    User Feedback
    -> Learning Signals
    -> Weight Proposals
    -> Approve / Apply
    -> Weight Store
    -> WeightedCapabilityRouter
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from orchestrator.weight_proposals import InMemoryRouterWeightStore


@dataclass(frozen=True)
class WeightedRouterConfig:
    """
    Weighted router configuration.
    """

    clamp_score: bool = True
    max_score: float = 1.0
    round_digits: int = 6
    sort_ranked_lists: bool = True
    annotate_evidence: bool = True

    def __post_init__(self) -> None:
        if self.max_score < 0:
            raise ValueError("max_score must be >= 0.")

        if self.round_digits < 0:
            raise ValueError("round_digits must be >= 0.")


class WeightedCapabilityRouter:
    """
    Generic wrapper around any capability router.

    The wrapper intercepts delegated router method calls and post-processes
    returned routing evidence/candidates.

    It supports common JSON-like router outputs:
        - dict candidate
        - list[dict]
        - tuple containing candidates
        - nested dict/list structures

    A candidate/evidence dict is weighted when it contains:
        - score
        - and capability/plugin identity fields

    Supported identity fields:
        capability:
            capability_name | name
        plugin:
            plugin_id | plugin_name | plugin
    """

    def __init__(
        self,
        base_router: Any,
        *,
        weight_store: InMemoryRouterWeightStore | None = None,
        config: WeightedRouterConfig | None = None,
    ) -> None:
        self.base_router = base_router
        self.weight_store = weight_store or InMemoryRouterWeightStore()
        self.config = config or WeightedRouterConfig()

    def __getattr__(self, name: str) -> Any:
        """
        Delegate all unknown attributes/methods to the base router.

        If the delegated attribute is callable, wrap its return value and apply
        weighting to routing evidence.
        """
        attr = getattr(self.base_router, name)

        if not callable(attr):
            return attr

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            return self._weight_result(result)

        return wrapped

    def weight_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """
        Return a weighted copy of a candidate/routing evidence dict.

        The original candidate is not mutated.
        """
        return self._weight_candidate(candidate)

    def weight_result(self, result: Any) -> Any:
        """
        Public helper for weighting an arbitrary router result.
        """
        return self._weight_result(result)

    def _weight_result(self, result: Any) -> Any:
        """
        Recursively weight router outputs.
        """
        if isinstance(result, list):
            weighted_items = [
                self._weight_result(item)
                for item in result
            ]

            if self.config.sort_ranked_lists and _looks_like_ranked_candidate_list(
                weighted_items
            ):
                return sorted(
                    weighted_items,
                    key=lambda item: float(item.get("score", 0.0)),
                    reverse=True,
                )

            return weighted_items

        if isinstance(result, tuple):
            return tuple(
                self._weight_result(item)
                for item in result
            )

        if isinstance(result, dict):
            if _is_score_candidate(result):
                return self._weight_candidate(result)

            return {
                key: self._weight_result(value)
                for key, value in result.items()
            }

        return result

    def _weight_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        score = candidate.get("score")

        if not isinstance(score, (int, float)):
            return dict(candidate)

        capability_name = _capability_name(candidate)
        plugin_id = _plugin_id(candidate)

        if capability_name:
            capability_weight = self.weight_store.get_weight(
                "capability",
                capability_name,
            )
        else:
            capability_weight = 1.0

        if plugin_id:
            plugin_weight = self.weight_store.get_weight(
                "plugin",
                plugin_id,
            )
        else:
            plugin_weight = 1.0

        base_score = float(score)
        weighted_score = base_score * capability_weight * plugin_weight

        if self.config.clamp_score:
            weighted_score = min(self.config.max_score, weighted_score)

        weighted_score = round(weighted_score, self.config.round_digits)

        payload = dict(candidate)
        payload["score"] = weighted_score

        if self.config.annotate_evidence:
            payload["base_score"] = round(base_score, self.config.round_digits)
            payload["weighted_score"] = weighted_score
            payload["capability_weight"] = round(
                float(capability_weight),
                self.config.round_digits,
            )
            payload["plugin_weight"] = round(
                float(plugin_weight),
                self.config.round_digits,
            )
            payload["score_weighted"] = True

            reasons = list(payload.get("reasons", []) or [])
            reasons.append(
                "weighted_score=base_score*capability_weight*plugin_weight"
            )
            payload["reasons"] = reasons

            payload["weighted_score_metadata"] = {
                "base_score": round(base_score, self.config.round_digits),
                "capability_name": capability_name,
                "capability_weight": round(
                    float(capability_weight),
                    self.config.round_digits,
                ),
                "plugin_id": plugin_id,
                "plugin_weight": round(
                    float(plugin_weight),
                    self.config.round_digits,
                ),
                "weighted_score": weighted_score,
                "clamp_score": self.config.clamp_score,
                "max_score": self.config.max_score,
            }

        return payload


def _is_score_candidate(value: dict[str, Any]) -> bool:
    if "score" not in value:
        return False

    return bool(
        _capability_name(value)
        or _plugin_id(value)
    )


def _looks_like_ranked_candidate_list(items: list[Any]) -> bool:
    if not items:
        return False

    return all(
        isinstance(item, dict)
        and isinstance(item.get("score"), (int, float))
        for item in items
    )


def _capability_name(candidate: dict[str, Any]) -> str | None:
    for key in ("capability_name", "name"):
        value = candidate.get(key)

        if isinstance(value, str) and value:
            return value

    return None


def _plugin_id(candidate: dict[str, Any]) -> str | None:
    for key in ("plugin_id", "plugin_name", "plugin"):
        value = candidate.get(key)

        if isinstance(value, str) and value:
            return value

    return None
