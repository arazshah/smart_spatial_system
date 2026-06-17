"""
orchestrator.feedback

User feedback collector / feedback-loop seed.

This module captures user feedback for an execution audit record.

It is designed for:
    - router improvement
    - plugin quality review
    - threshold/parameter correction
    - output-type correction
    - future self-learning pipelines

No database is used here. This is an in-memory, deterministic collector.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


VALID_RATINGS = {
    "correct",
    "partially_correct",
    "incorrect",
}

VALID_ISSUE_TYPES = {
    "route_error",
    "plugin_error",
    "parameter_error",
    "threshold_error",
    "output_type_error",
    "geometry_error",
    "data_error",
    "performance_issue",
    "llm_error",
    "response_error",
    "other",
}


@dataclass(frozen=True)
class FeedbackConfig:
    """
    Feedback collector config.
    """

    max_records: int = 1000
    allow_query_text: bool = False

    def __post_init__(self) -> None:
        if self.max_records < 0:
            raise ValueError("max_records must be >= 0.")


@dataclass(frozen=True)
class UserFeedbackInput:
    """
    User-provided feedback input.

    rating:
        correct | partially_correct | incorrect

    issue_types:
        Optional list of issue categories.

    correction fields:
        Optional structured corrections that can later be used by self-learning.
    """

    rating: str
    issue_types: list[str] = field(default_factory=list)
    comment: str | None = None

    expected_capability: str | None = None
    expected_plugin_id: str | None = None
    expected_output_kind: str | None = None
    expected_threshold_value: float | None = None
    expected_threshold_operator: str | None = None

    user_id: str | None = None


@dataclass(frozen=True)
class FeedbackRecord:
    """
    Stored feedback record connected to an audit record.
    """

    feedback_id: str
    request_id: str | None
    query_hash: str | None
    created_at: str

    rating: str
    issue_types: list[str]
    comment: str | None

    user_id: str | None

    expected_capability: str | None
    expected_plugin_id: str | None
    expected_output_kind: str | None
    expected_threshold_value: float | None
    expected_threshold_operator: str | None

    observed_intent_name: str | None
    observed_top_capability: str | None
    observed_top_plugin_id: str | None
    observed_llm_action: str | None
    observed_confidence_level: str | None
    observed_feature_count: int | None

    query: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FeedbackCollector:
    """
    In-memory feedback collector.
    """

    def __init__(
        self,
        config: FeedbackConfig | None = None,
    ) -> None:
        self.config = config or FeedbackConfig()
        self.records: list[FeedbackRecord] = []

    def submit(
        self,
        audit_record: dict[str, Any],
        feedback: UserFeedbackInput,
        *,
        feedback_id: str | None = None,
    ) -> FeedbackRecord:
        """
        Submit feedback for an audit record.
        """
        self._validate_feedback(feedback)

        record = FeedbackRecord(
            feedback_id=feedback_id or str(uuid.uuid4()),
            request_id=audit_record.get("request_id"),
            query_hash=audit_record.get("query_hash"),
            created_at=datetime.now(timezone.utc).isoformat(),
            rating=feedback.rating,
            issue_types=list(feedback.issue_types),
            comment=feedback.comment,
            user_id=feedback.user_id,
            expected_capability=feedback.expected_capability,
            expected_plugin_id=feedback.expected_plugin_id,
            expected_output_kind=feedback.expected_output_kind,
            expected_threshold_value=feedback.expected_threshold_value,
            expected_threshold_operator=feedback.expected_threshold_operator,
            observed_intent_name=self._observed_intent_name(audit_record),
            observed_top_capability=self._observed_top_capability(audit_record),
            observed_top_plugin_id=self._observed_top_plugin_id(audit_record),
            observed_llm_action=self._observed_llm_action(audit_record),
            observed_confidence_level=self._observed_confidence_level(audit_record),
            observed_feature_count=self._observed_feature_count(audit_record),
            query=audit_record.get("query") if self.config.allow_query_text else None,
        )

        self._append_limited(record)

        return record

    def attach_to_audit(
        self,
        audit_record: dict[str, Any],
        feedback_record: FeedbackRecord,
    ) -> dict[str, Any]:
        """
        Return a copy of audit_record with feedback section attached.

        The original audit_record is not mutated.
        """
        enriched = dict(audit_record)
        enriched["feedback"] = feedback_record.to_dict()
        return enriched

    def submit_and_attach(
        self,
        audit_record: dict[str, Any],
        feedback: UserFeedbackInput,
        *,
        feedback_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Submit feedback and return audit record copy with feedback attached.
        """
        record = self.submit(
            audit_record,
            feedback,
            feedback_id=feedback_id,
        )
        return self.attach_to_audit(audit_record, record)

    def summarize(self) -> dict[str, Any]:
        """
        Return aggregate feedback statistics.
        """
        rating_counts: dict[str, int] = {}
        issue_type_counts: dict[str, int] = {}
        expected_capability_counts: dict[str, int] = {}
        observed_top_capability_counts: dict[str, int] = {}

        for record in self.records:
            rating_counts[record.rating] = rating_counts.get(record.rating, 0) + 1

            for issue_type in record.issue_types:
                issue_type_counts[issue_type] = issue_type_counts.get(issue_type, 0) + 1

            if record.expected_capability:
                expected_capability_counts[record.expected_capability] = (
                    expected_capability_counts.get(record.expected_capability, 0) + 1
                )

            if record.observed_top_capability:
                observed_top_capability_counts[record.observed_top_capability] = (
                    observed_top_capability_counts.get(record.observed_top_capability, 0) + 1
                )

        incorrect_or_partial = sum(
            1
            for record in self.records
            if record.rating in {"incorrect", "partially_correct"}
        )

        return {
            "total_feedback": len(self.records),
            "rating_counts": rating_counts,
            "issue_type_counts": issue_type_counts,
            "incorrect_or_partial_count": incorrect_or_partial,
            "expected_capability_counts": expected_capability_counts,
            "observed_top_capability_counts": observed_top_capability_counts,
            "records": [
                record.to_dict()
                for record in self.records
            ],
        }

    def reset(self) -> None:
        """
        Remove all feedback records.
        """
        self.records.clear()

    def _append_limited(self, record: FeedbackRecord) -> None:
        if self.config.max_records == 0:
            return

        self.records.append(record)

        if len(self.records) > self.config.max_records:
            del self.records[0 : len(self.records) - self.config.max_records]

    @staticmethod
    def _validate_feedback(feedback: UserFeedbackInput) -> None:
        if feedback.rating not in VALID_RATINGS:
            raise ValueError(
                f"Invalid rating: {feedback.rating}. "
                f"Valid ratings: {sorted(VALID_RATINGS)}"
            )

        invalid_issues = [
            issue_type
            for issue_type in feedback.issue_types
            if issue_type not in VALID_ISSUE_TYPES
        ]

        if invalid_issues:
            raise ValueError(
                "Invalid issue_types: "
                + ", ".join(invalid_issues)
                + f". Valid issue_types: {sorted(VALID_ISSUE_TYPES)}"
            )

    @staticmethod
    def _observed_intent_name(audit_record: dict[str, Any]) -> str | None:
        intent = audit_record.get("intent")

        if isinstance(intent, dict):
            return intent.get("intent_name")

        return None

    @staticmethod
    def _observed_top_capability(audit_record: dict[str, Any]) -> str | None:
        router_decision = audit_record.get("router_decision")

        if not isinstance(router_decision, dict):
            return None

        top_candidate = router_decision.get("top_candidate")

        if isinstance(top_candidate, dict):
            return top_candidate.get("capability_name")

        return None

    @staticmethod
    def _observed_top_plugin_id(audit_record: dict[str, Any]) -> str | None:
        router_decision = audit_record.get("router_decision")

        if not isinstance(router_decision, dict):
            return None

        top_candidate = router_decision.get("top_candidate")

        if isinstance(top_candidate, dict):
            return top_candidate.get("plugin_id")

        return None

    @staticmethod
    def _observed_llm_action(audit_record: dict[str, Any]) -> str | None:
        router_decision = audit_record.get("router_decision")

        if isinstance(router_decision, dict):
            return router_decision.get("llm_action")

        return None

    @staticmethod
    def _observed_confidence_level(audit_record: dict[str, Any]) -> str | None:
        router_decision = audit_record.get("router_decision")

        if isinstance(router_decision, dict):
            return router_decision.get("level")

        return None

    @staticmethod
    def _observed_feature_count(audit_record: dict[str, Any]) -> int | None:
        outputs_summary = audit_record.get("outputs_summary")

        if not isinstance(outputs_summary, dict):
            return None

        for summary in outputs_summary.values():
            if isinstance(summary, dict) and summary.get("kind") == "vector":
                feature_count = summary.get("feature_count")

                if isinstance(feature_count, int):
                    return feature_count

        return None
