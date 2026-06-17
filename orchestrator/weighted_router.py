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

It supports:
    - dict candidates
    - dataclass candidates such as ScoredCapability
    - object candidates with score/capability/plugin fields
    - nested dict/list/tuple router results
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

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


class WeightedEvidence(dict):
    """
    Dict evidence with attribute-style access.

    This keeps compatibility with code that expects:
        evidence["score"]
        evidence.get("score")
        evidence.score
        getattr(evidence, "score")
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class WeightedCapabilityRouter:
    """
    Generic wrapper around any capability router.

    The wrapper delegates calls to the base router, then post-processes returned
    candidates/routing evidence.

    A candidate is weighted when it has:
        - score
        - capability identity and/or plugin identity
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
        Delegate unknown attributes/methods to the base router.

        Callable results are weighted recursively.
        """
        attr = getattr(self.base_router, name)

        if not callable(attr):
            return attr

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            return self._weight_result(result)

        return wrapped

    def weight_candidate(self, candidate: Any) -> dict[str, Any]:
        """
        Return weighted copy of a candidate/routing evidence item.
        """
        if not _is_score_candidate(candidate):
            if isinstance(candidate, dict):
                return dict(candidate)

            return _candidate_to_dict(candidate)

        return self._weight_candidate(candidate)

    def weight_result(self, result: Any) -> Any:
        """
        Public helper for weighting arbitrary router output.
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

        if _is_score_candidate(result):
            return self._weight_candidate(result)

        return result

    def _weight_candidate(self, candidate: Any) -> WeightedEvidence:
        payload = _candidate_to_dict(candidate)

        score = payload.get("score")

        if not isinstance(score, (int, float)):
            return WeightedEvidence(payload)

        capability_name = _capability_name(payload)
        plugin_id = _plugin_id(payload)

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

        return WeightedEvidence(payload)


def _is_score_candidate(value: Any) -> bool:
    score = _field(value, "score")

    if not isinstance(score, (int, float)):
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


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    """
    Convert dict/dataclass/object candidate to normalized dict.
    """
    if isinstance(candidate, dict):
        payload = dict(candidate)
    elif is_dataclass(candidate):
        payload = asdict(candidate)
    else:
        payload = dict(getattr(candidate, "__dict__", {}) or {})

    # Add direct attributes/properties if available and missing.
    for key in (
        "score",
        "capability_name",
        "name",
        "plugin_id",
        "plugin_name",
        "plugin",
        "output_kind",
        "matched_terms",
        "reasons",
    ):
        if key not in payload:
            try:
                value = getattr(candidate, key)
            except Exception:
                continue
            payload[key] = value

    capability_name = _capability_name(payload)
    plugin_id = _plugin_id(payload)

    if capability_name and not payload.get("capability_name"):
        payload["capability_name"] = capability_name

    if plugin_id and not payload.get("plugin_id"):
        payload["plugin_id"] = plugin_id

    if "reasons" in payload and payload["reasons"] is None:
        payload["reasons"] = []

    if "matched_terms" in payload and payload["matched_terms"] is None:
        payload["matched_terms"] = []

    return payload


def _field(candidate: Any, key: str) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(key)

    try:
        return getattr(candidate, key)
    except Exception:
        return None


def _nested_field(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)

    try:
        return getattr(value, key)
    except Exception:
        return None


def _capability_name(candidate: Any) -> str | None:
    for key in ("capability_name", "name"):
        value = _field(candidate, key)

        if isinstance(value, str) and value:
            return value

    capability = _field(candidate, "capability")

    if capability is not None:
        for key in ("capability_name", "name", "id"):
            value = _nested_field(capability, key)

            if isinstance(value, str) and value:
                return value

    return None


def _plugin_id(candidate: Any) -> str | None:
    for key in ("plugin_id", "plugin_name", "plugin"):
        value = _field(candidate, key)

        if isinstance(value, str) and value:
            return value

    capability = _field(candidate, "capability")

    if capability is not None:
        for key in ("plugin_id", "plugin_name", "plugin", "id"):
            value = _nested_field(capability, key)

            if isinstance(value, str) and value:
                return value

    return None
