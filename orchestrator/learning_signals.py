"""
orchestrator.learning_signals

Router learning signal builder.

This module converts audit records + user feedback into compact learning
signals.

Important:
    This module DOES NOT mutate router weights yet.
    It only produces deterministic learning signals that can later feed:
        - router weight tuning
        - plugin quality scoring
        - low-confidence review queues
        - ambiguity analysis
        - self-learning pipelines
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class LearningSignalConfig:
    """
    Learning signal builder configuration.
    """

    include_positive_signals: bool = True
    include_review_signals: bool = True

    positive_weight_delta: float = 0.05
    medium_weight_delta: float = 0.08
    high_weight_delta: float = 0.15

    def __post_init__(self) -> None:
        if self.positive_weight_delta < 0:
            raise ValueError("positive_weight_delta must be >= 0.")

        if self.medium_weight_delta < 0:
            raise ValueError("medium_weight_delta must be >= 0.")

        if self.high_weight_delta < 0:
            raise ValueError("high_weight_delta must be >= 0.")


@dataclass(frozen=True)
class RouterLearningSignal:
    """
    A compact learning signal derived from feedback/audit evidence.
    """

    signal_id: str
    created_at: str

    signal_type: str
    action: str
    severity: str

    request_id: str | None
    query_hash: str | None
    rating: str | None
    issue_types: list[str]

    observed_capability: str | None
    expected_capability: str | None

    observed_plugin_id: str | None
    expected_plugin_id: str | None

    observed_output_kind: str | None
    expected_output_kind: str | None

    observed_confidence_level: str | None
    observed_llm_action: str | None
    observed_top_score: float | None
    observed_competitive_gap: float | None
    observed_is_ambiguous: bool | None

    weight_adjustments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RouterLearningSignalBuilder:
    """
    Builds learning signals from audit + feedback records.
    """

    def __init__(
        self,
        config: LearningSignalConfig | None = None,
    ) -> None:
        self.config = config or LearningSignalConfig()

    def build(
        self,
        *,
        audit_record: dict[str, Any],
        feedback_record: Any,
        statistics_summary: dict[str, Any] | None = None,
    ) -> list[RouterLearningSignal]:
        """
        Build learning signals.

        Args:
            audit_record:
                Execution audit record.
            feedback_record:
                FeedbackRecord dataclass or feedback dict.
            statistics_summary:
                Optional aggregate statistics snapshot.
        """
        feedback = _to_dict(feedback_record)

        signals: list[RouterLearningSignal] = []

        rating = feedback.get("rating")
        issue_types = list(feedback.get("issue_types", []) or [])

        observed_capability = self._observed_capability(audit_record, feedback)
        expected_capability = feedback.get("expected_capability")

        observed_plugin_id = self._observed_plugin_id(audit_record, feedback)
        expected_plugin_id = feedback.get("expected_plugin_id")

        observed_output_kind = self._observed_output_kind(audit_record)
        expected_output_kind = feedback.get("expected_output_kind")

        router_meta = self._router_meta(audit_record, feedback)
        stats_meta = self._statistics_meta(
            statistics_summary=statistics_summary,
            observed_capability=observed_capability,
            expected_capability=expected_capability,
            observed_plugin_id=observed_plugin_id,
            expected_plugin_id=expected_plugin_id,
        )

        common = {
            "request_id": audit_record.get("request_id") or feedback.get("request_id"),
            "query_hash": audit_record.get("query_hash") or feedback.get("query_hash"),
            "rating": rating,
            "issue_types": issue_types,
            "observed_capability": observed_capability,
            "expected_capability": expected_capability,
            "observed_plugin_id": observed_plugin_id,
            "expected_plugin_id": expected_plugin_id,
            "observed_output_kind": observed_output_kind,
            "expected_output_kind": expected_output_kind,
            "observed_confidence_level": router_meta["level"],
            "observed_llm_action": router_meta["llm_action"],
            "observed_top_score": router_meta["top_score"],
            "observed_competitive_gap": router_meta["competitive_gap"],
            "observed_is_ambiguous": router_meta["is_ambiguous"],
        }

        if (
            rating == "correct"
            and self.config.include_positive_signals
            and observed_capability
        ):
            signals.append(
                self._make_signal(
                    signal_type="positive_confirmation",
                    action="reinforce_observed_route",
                    severity="low",
                    common=common,
                    weight_adjustments={
                        "capabilities": {
                            observed_capability: self.config.positive_weight_delta,
                        },
                        "plugins": _positive_plugin_adjustment(
                            observed_plugin_id,
                            self.config.positive_weight_delta,
                        ),
                    },
                    metadata={
                        "reason": "user_confirmed_result_correct",
                        **stats_meta,
                    },
                )
            )

        if rating in {"incorrect", "partially_correct"}:
            severity = self._severity_for_rating(rating)
            delta = self._delta_for_severity(severity)

            if (
                expected_capability
                and observed_capability
                and expected_capability != observed_capability
            ):
                signals.append(
                    self._make_signal(
                        signal_type="route_correction",
                        action="decrease_observed_increase_expected",
                        severity=severity,
                        common=common,
                        weight_adjustments={
                            "capabilities": {
                                observed_capability: -delta,
                                expected_capability: delta,
                            }
                        },
                        metadata={
                            "reason": "user_expected_different_capability",
                            **stats_meta,
                        },
                    )
                )

            if (
                expected_plugin_id
                and observed_plugin_id
                and expected_plugin_id != observed_plugin_id
            ):
                signals.append(
                    self._make_signal(
                        signal_type="plugin_correction",
                        action="decrease_observed_plugin_increase_expected_plugin",
                        severity=severity,
                        common=common,
                        weight_adjustments={
                            "plugins": {
                                observed_plugin_id: -delta,
                                expected_plugin_id: delta,
                            }
                        },
                        metadata={
                            "reason": "user_expected_different_plugin",
                            **stats_meta,
                        },
                    )
                )

            if any(
                issue in issue_types
                for issue in {"parameter_error", "threshold_error"}
            ):
                signals.append(
                    self._make_signal(
                        signal_type="parameter_correction",
                        action="review_and_adjust_parameter_extraction",
                        severity=severity,
                        common=common,
                        weight_adjustments={},
                        metadata={
                            "reason": "user_reported_parameter_or_threshold_error",
                            "expected_threshold_value": feedback.get(
                                "expected_threshold_value"
                            ),
                            "expected_threshold_operator": feedback.get(
                                "expected_threshold_operator"
                            ),
                            **stats_meta,
                        },
                    )
                )

            if "output_type_error" in issue_types:
                signals.append(
                    self._make_signal(
                        signal_type="output_type_correction",
                        action="review_expected_output_kind",
                        severity=severity,
                        common=common,
                        weight_adjustments={},
                        metadata={
                            "reason": "user_reported_output_type_error",
                            **stats_meta,
                        },
                    )
                )

            if not signals:
                signals.append(
                    self._make_signal(
                        signal_type="general_negative_feedback",
                        action="send_to_human_or_llm_review",
                        severity=severity,
                        common=common,
                        weight_adjustments={},
                        metadata={
                            "reason": "negative_feedback_without_structured_correction",
                            **stats_meta,
                        },
                    )
                )

        if self.config.include_review_signals and rating != "correct":
            if router_meta["level"] == "low":
                signals.append(
                    self._make_signal(
                        signal_type="low_confidence_review",
                        action="review_router_thresholds_or_keywords",
                        severity="medium" if rating != "incorrect" else "high",
                        common=common,
                        weight_adjustments={},
                        metadata={
                            "reason": "router_confidence_low",
                            **stats_meta,
                        },
                    )
                )

            if router_meta["is_ambiguous"] is True:
                signals.append(
                    self._make_signal(
                        signal_type="ambiguity_review",
                        action="review_competing_capabilities",
                        severity="medium" if rating != "incorrect" else "high",
                        common=common,
                        weight_adjustments={},
                        metadata={
                            "reason": "router_detected_competitive_gap",
                            **stats_meta,
                        },
                    )
                )

        return signals

    def build_dicts(
        self,
        *,
        audit_record: dict[str, Any],
        feedback_record: Any,
        statistics_summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build learning signals and return JSON-like dicts.
        """
        return [
            signal.to_dict()
            for signal in self.build(
                audit_record=audit_record,
                feedback_record=feedback_record,
                statistics_summary=statistics_summary,
            )
        ]

    def _make_signal(
        self,
        *,
        signal_type: str,
        action: str,
        severity: str,
        common: dict[str, Any],
        weight_adjustments: dict[str, Any],
        metadata: dict[str, Any],
    ) -> RouterLearningSignal:
        return RouterLearningSignal(
            signal_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            signal_type=signal_type,
            action=action,
            severity=severity,
            request_id=common.get("request_id"),
            query_hash=common.get("query_hash"),
            rating=common.get("rating"),
            issue_types=list(common.get("issue_types", []) or []),
            observed_capability=common.get("observed_capability"),
            expected_capability=common.get("expected_capability"),
            observed_plugin_id=common.get("observed_plugin_id"),
            expected_plugin_id=common.get("expected_plugin_id"),
            observed_output_kind=common.get("observed_output_kind"),
            expected_output_kind=common.get("expected_output_kind"),
            observed_confidence_level=common.get("observed_confidence_level"),
            observed_llm_action=common.get("observed_llm_action"),
            observed_top_score=common.get("observed_top_score"),
            observed_competitive_gap=common.get("observed_competitive_gap"),
            observed_is_ambiguous=common.get("observed_is_ambiguous"),
            weight_adjustments=weight_adjustments,
            metadata=metadata,
        )

    @staticmethod
    def _severity_for_rating(rating: str | None) -> str:
        if rating == "incorrect":
            return "high"

        if rating == "partially_correct":
            return "medium"

        return "low"

    def _delta_for_severity(self, severity: str) -> float:
        if severity == "high":
            return self.config.high_weight_delta

        if severity == "medium":
            return self.config.medium_weight_delta

        return self.config.positive_weight_delta

    @staticmethod
    def _observed_capability(
        audit_record: dict[str, Any],
        feedback: dict[str, Any],
    ) -> str | None:
        if feedback.get("observed_top_capability"):
            return feedback.get("observed_top_capability")

        router_decision = audit_record.get("router_decision")

        if isinstance(router_decision, dict):
            top_candidate = router_decision.get("top_candidate")

            if isinstance(top_candidate, dict):
                return top_candidate.get("capability_name")

        return None

    @staticmethod
    def _observed_plugin_id(
        audit_record: dict[str, Any],
        feedback: dict[str, Any],
    ) -> str | None:
        if feedback.get("observed_top_plugin_id"):
            return feedback.get("observed_top_plugin_id")

        router_decision = audit_record.get("router_decision")

        if isinstance(router_decision, dict):
            top_candidate = router_decision.get("top_candidate")

            if isinstance(top_candidate, dict):
                return top_candidate.get("plugin_id")

        return None

    @staticmethod
    def _observed_output_kind(audit_record: dict[str, Any]) -> str | None:
        outputs_summary = audit_record.get("outputs_summary")

        if not isinstance(outputs_summary, dict):
            return None

        for summary in outputs_summary.values():
            if isinstance(summary, dict) and summary.get("kind"):
                return summary.get("kind")

        return None

    @staticmethod
    def _router_meta(
        audit_record: dict[str, Any],
        feedback: dict[str, Any],
    ) -> dict[str, Any]:
        router_decision = audit_record.get("router_decision")

        if not isinstance(router_decision, dict):
            router_decision = {}

        return {
            "level": feedback.get("observed_confidence_level")
            or router_decision.get("level"),
            "llm_action": feedback.get("observed_llm_action")
            or router_decision.get("llm_action"),
            "top_score": router_decision.get("top_score"),
            "competitive_gap": router_decision.get("competitive_gap"),
            "is_ambiguous": router_decision.get("is_ambiguous"),
        }

    @staticmethod
    def _statistics_meta(
        *,
        statistics_summary: dict[str, Any] | None,
        observed_capability: str | None,
        expected_capability: str | None,
        observed_plugin_id: str | None,
        expected_plugin_id: str | None,
    ) -> dict[str, Any]:
        if not isinstance(statistics_summary, dict):
            return {}

        capabilities = statistics_summary.get("capabilities", {})
        plugins = statistics_summary.get("plugins", {})

        capability_usage = {}
        plugin_usage = {}

        if isinstance(capabilities, dict):
            capability_usage = capabilities.get("usage_counts", {}) or {}

        if isinstance(plugins, dict):
            plugin_usage = plugins.get("usage_counts", {}) or {}

        return {
            "statistics_context": {
                "observed_capability_usage": capability_usage.get(
                    observed_capability, 0
                )
                if observed_capability
                else 0,
                "expected_capability_usage": capability_usage.get(
                    expected_capability, 0
                )
                if expected_capability
                else 0,
                "observed_plugin_usage": plugin_usage.get(observed_plugin_id, 0)
                if observed_plugin_id
                else 0,
                "expected_plugin_usage": plugin_usage.get(expected_plugin_id, 0)
                if expected_plugin_id
                else 0,
            }
        }


class RouterLearningSignalCollector:
    """
    In-memory collector for generated learning signals.
    """

    def __init__(self, max_signals: int = 1000) -> None:
        if max_signals < 0:
            raise ValueError("max_signals must be >= 0.")

        self.max_signals = max_signals
        self.signals: list[RouterLearningSignal] = []

    def ingest(self, signal: RouterLearningSignal) -> None:
        if self.max_signals == 0:
            return

        self.signals.append(signal)

        if len(self.signals) > self.max_signals:
            del self.signals[0 : len(self.signals) - self.max_signals]

    def ingest_many(self, signals: list[RouterLearningSignal]) -> None:
        for signal in signals:
            self.ingest(signal)

    def summarize(self) -> dict[str, Any]:
        signal_type_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        action_counts: dict[str, int] = {}

        for signal in self.signals:
            signal_type_counts[signal.signal_type] = (
                signal_type_counts.get(signal.signal_type, 0) + 1
            )
            severity_counts[signal.severity] = (
                severity_counts.get(signal.severity, 0) + 1
            )
            action_counts[signal.action] = action_counts.get(signal.action, 0) + 1

        return {
            "total_signals": len(self.signals),
            "signal_type_counts": signal_type_counts,
            "severity_counts": severity_counts,
            "action_counts": action_counts,
            "signals": [
                signal.to_dict()
                for signal in self.signals
            ],
        }

    def reset(self) -> None:
        self.signals.clear()


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()

    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)

    raise TypeError("feedback_record must be a dict or dataclass-like object.")


def _positive_plugin_adjustment(
    plugin_id: str | None,
    delta: float,
) -> dict[str, float]:
    if not plugin_id:
        return {}

    return {
        plugin_id: delta,
    }
