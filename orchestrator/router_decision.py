"""
orchestrator.router_decision

Router decision layer for confidence, ambiguity detection, and LLM fallback policy.

This module does not call LLM.
It only decides whether LLM is needed, optional, or not needed.

Agreed policy:

    HIGH confidence   >= 0.85  -> no LLM required
    MEDIUM confidence >= 0.50  -> LLM optional depending on budget
    LOW confidence    < 0.50   -> LLM required

    competitive gap < 0.10 -> LLM required even if top score is HIGH

All thresholds are configurable through RouterDecisionConfig.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestrator.models import ScoredCapability


@dataclass(frozen=True)
class RouterDecisionConfig:
    """
    Configurable thresholds and LLM policy for router decisions.
    """

    high_threshold: float = 0.85
    medium_threshold: float = 0.50
    competitive_gap_threshold: float = 0.10

    llm_required_on_low: bool = True
    llm_required_on_ambiguity: bool = True
    llm_optional_on_medium: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.medium_threshold <= 1:
            raise ValueError("medium_threshold must be between 0 and 1.")

        if not 0 <= self.high_threshold <= 1:
            raise ValueError("high_threshold must be between 0 and 1.")

        if self.medium_threshold > self.high_threshold:
            raise ValueError("medium_threshold must be <= high_threshold.")

        if not 0 <= self.competitive_gap_threshold <= 1:
            raise ValueError("competitive_gap_threshold must be between 0 and 1.")


@dataclass(frozen=True)
class RouterDecision:
    """
    Final routing decision for a ranked candidate list.
    """

    level: str
    llm_action: str
    route_without_llm: bool
    llm_required: bool
    llm_optional: bool

    top_candidate: ScoredCapability | None
    second_candidate: ScoredCapability | None

    top_score: float
    second_score: float | None
    competitive_gap: float | None
    is_ambiguous: bool

    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert decision to JSON-like dict for trace/audit/debug.
        """
        return {
            "level": self.level,
            "llm_action": self.llm_action,
            "route_without_llm": self.route_without_llm,
            "llm_required": self.llm_required,
            "llm_optional": self.llm_optional,
            "top_candidate": _candidate_to_dict(self.top_candidate),
            "second_candidate": _candidate_to_dict(self.second_candidate),
            "top_score": self.top_score,
            "second_score": self.second_score,
            "competitive_gap": self.competitive_gap,
            "is_ambiguous": self.is_ambiguous,
            "reasons": list(self.reasons),
        }


class RouterDecisionLayer:
    """
    Decides confidence level and LLM fallback policy from scored candidates.
    """

    def __init__(
        self,
        config: RouterDecisionConfig | None = None,
    ) -> None:
        self.config = config or RouterDecisionConfig()

    def decide(
        self,
        candidates: list[ScoredCapability],
    ) -> RouterDecision:
        """
        Decide routing confidence and LLM policy from sorted/unsorted candidates.

        Args:
            candidates:
                List of ScoredCapability. It may be unsorted.

        Returns:
            RouterDecision.
        """
        if not candidates:
            return RouterDecision(
                level="low",
                llm_action="required",
                route_without_llm=False,
                llm_required=True,
                llm_optional=False,
                top_candidate=None,
                second_candidate=None,
                top_score=0.0,
                second_score=None,
                competitive_gap=None,
                is_ambiguous=False,
                reasons=[
                    "no_candidates",
                    "llm_required_no_candidates",
                ],
            )

        ranked = sorted(
            candidates,
            key=lambda item: (
                item.score,
                len(item.matched_terms),
                item.capability_name,
            ),
            reverse=True,
        )

        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None

        top_score = top.score
        second_score = second.score if second is not None else None
        competitive_gap = (
            round(top_score - second_score, 4)
            if second_score is not None
            else None
        )

        reasons: list[str] = []

        level = self._level_for_score(top_score)
        reasons.append(f"confidence_{level}")

        is_ambiguous = (
            competitive_gap is not None
            and competitive_gap < self.config.competitive_gap_threshold
        )

        if is_ambiguous:
            reasons.append("competitive_gap_below_threshold")

        llm_required = False
        llm_optional = False

        if level == "low" and self.config.llm_required_on_low:
            llm_required = True
            reasons.append("llm_required_low_confidence")

        if is_ambiguous and self.config.llm_required_on_ambiguity:
            llm_required = True
            reasons.append("llm_required_ambiguity")

        if (
            level == "medium"
            and not llm_required
            and self.config.llm_optional_on_medium
        ):
            llm_optional = True
            reasons.append("llm_optional_medium_confidence")

        if llm_required:
            llm_action = "required"
            route_without_llm = False
        elif llm_optional:
            llm_action = "optional"
            route_without_llm = True
        else:
            llm_action = "skip"
            route_without_llm = True
            reasons.append("llm_skipped_sufficient_confidence")

        return RouterDecision(
            level=level,
            llm_action=llm_action,
            route_without_llm=route_without_llm,
            llm_required=llm_required,
            llm_optional=llm_optional,
            top_candidate=top,
            second_candidate=second,
            top_score=top_score,
            second_score=second_score,
            competitive_gap=competitive_gap,
            is_ambiguous=is_ambiguous,
            reasons=reasons,
        )

    def decide_query(
        self,
        router: Any,
        query: str,
        *,
        expected_output_kind: str | None = None,
        min_score: float = 0.0,
        top_k: int | None = None,
    ) -> RouterDecision:
        """
        Score a query using a scoring router, then decide confidence/LLM policy.

        The router must expose:

            score_query(query, expected_output_kind=..., min_score=..., top_k=...)
        """
        candidates = router.score_query(
            query,
            expected_output_kind=expected_output_kind,
            min_score=min_score,
            top_k=top_k,
        )
        return self.decide(candidates)

    def _level_for_score(self, score: float) -> str:
        """
        Convert score to confidence level.
        """
        if score >= self.config.high_threshold:
            return "high"

        if score >= self.config.medium_threshold:
            return "medium"

        return "low"


def _candidate_to_dict(candidate: ScoredCapability | None) -> dict[str, Any] | None:
    """
    Convert candidate to JSON-like dict.
    """
    if candidate is None:
        return None

    return {
        "capability_name": candidate.capability_name,
        "plugin_id": candidate.plugin_id,
        "output_kind": candidate.output_kind,
        "score": candidate.score,
        "matched_terms": list(candidate.matched_terms),
        "reasons": list(candidate.reasons),
    }
