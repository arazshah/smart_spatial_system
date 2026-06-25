from __future__ import annotations

from typing import Any, Callable

from orchestrator.feedback import UserFeedbackInput
from orchestrator.weight_proposals import WeightProposal
from orchestrator.weight_store_persistence import WeightStorePersistenceError


class FeedbackProposalServiceError(RuntimeError):
    """Raised when feedback/proposal workflow fails."""


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        if isinstance(payload, dict):
            return payload

    if hasattr(value, "__dict__"):
        return dict(value.__dict__)

    raise TypeError(f"Object is not serializable to dict: {type(value)!r}")


class FeedbackProposalService:
    def __init__(
        self,
        *,
        feedback_collector: Any,
        learning_signal_builder: Any,
        weight_proposal_engine: Any,
        weight_proposal_collector: Any,
        weight_store: Any,
        persistence: Any,
        config: Any,
        get_request: Callable[[str], dict[str, Any] | None],
        get_weights: Callable[[], dict[str, Any]],
    ) -> None:
        self.feedback_collector = feedback_collector
        self.learning_signal_builder = learning_signal_builder
        self.weight_proposal_engine = weight_proposal_engine
        self.weight_proposal_collector = weight_proposal_collector
        self.weight_store = weight_store
        self.persistence = persistence
        self.config = config
        self.get_request = get_request
        self.get_weights = get_weights

    def submit_feedback(
        self,
        *,
        request_id: str,
        rating: str,
        issue_types: list[str] | None = None,
        expected_capability: str | None = None,
        expected_plugin_id: str | None = None,
        comment: str | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Submit user feedback for a previous request.

        This method:
            1. Finds the stored audit record
            2. Creates FeedbackRecord
            3. Builds learning signals
            4. Builds weight proposals
            5. Stores proposals in proposal collector

        Important:
            It does NOT auto-apply proposals.
        """
        record = self.get_request(request_id)

        if record is None:
            raise FeedbackProposalServiceError(
                f"Unknown request_id: {request_id}"
            )

        audit_record = record.get("audit_record")

        if not isinstance(audit_record, dict):
            raise FeedbackProposalServiceError(
                f"Request has no audit_record: {request_id}"
            )

        feedback_input_kwargs: dict[str, Any] = {
            "rating": rating,
        }

        if issue_types is not None:
            feedback_input_kwargs["issue_types"] = issue_types

        if expected_capability is not None:
            feedback_input_kwargs["expected_capability"] = expected_capability

        if expected_plugin_id is not None:
            feedback_input_kwargs["expected_plugin_id"] = expected_plugin_id

        # Keep compatibility if UserFeedbackInput does not define comment/user_context.
        try:
            if comment is not None:
                feedback_input_kwargs["comment"] = comment
            if user_context is not None:
                feedback_input_kwargs["user_context"] = user_context

            feedback_input = UserFeedbackInput(**feedback_input_kwargs)
        except TypeError:
            feedback_input_kwargs.pop("comment", None)
            feedback_input_kwargs.pop("user_context", None)
            feedback_input = UserFeedbackInput(**feedback_input_kwargs)

        feedback_record = self.feedback_collector.submit(
            audit_record,
            feedback_input,
        )

        signals = self.learning_signal_builder.build(
            audit_record=audit_record,
            feedback_record=feedback_record,
        )

        proposals = self.weight_proposal_engine.build(
            signals,
            weight_store=self.weight_store,
        )

        self.weight_proposal_collector.ingest_many(proposals)

        feedback_payload = {
            "request_id": request_id,
            "feedback": _to_dict(feedback_record),
            "signals": [
                _to_dict(signal)
                for signal in signals
            ],
            "proposals": [
                _to_dict(proposal)
                for proposal in proposals
            ],
            "proposal_summary": self.weight_proposal_collector.summarize(),
        }

        record["feedback"] = feedback_payload

        return feedback_payload

    def approve_and_apply_proposal(
        self,
        proposal: WeightProposal | dict[str, Any],
        *,
        save: bool | None = None,
    ) -> dict[str, Any]:
        """
        Approve and apply a proposal to the active weight store.

        This is intended for admin/policy review workflows.
        """
        proposal_obj = self._ensure_proposal(proposal)

        approved = self.weight_proposal_engine.approve(proposal_obj)

        applied = self.weight_proposal_engine.apply(
            approved,
            weight_store=self.weight_store,
        )

        should_save = (
            self.config.auto_save_weights_after_apply
            if save is None
            else save
        )

        saved_payload = None

        if should_save:
            try:
                saved_payload = self.persistence.save(
                    self.weight_store,
                    metadata={
                        "source": "FeedbackProposalService.approve_and_apply_proposal",
                    },
                )
            except WeightStorePersistenceError as exc:
                raise FeedbackProposalServiceError(str(exc)) from exc

        return {
            "approved": _to_dict(approved),
            "applied": _to_dict(applied),
            "weights": self.get_weights(),
            "saved": saved_payload is not None,
            "saved_payload": saved_payload,
        }

    @staticmethod
    def _ensure_proposal(
        proposal: WeightProposal | dict[str, Any],
    ) -> WeightProposal:
        if isinstance(proposal, WeightProposal):
            return proposal

        if not isinstance(proposal, dict):
            raise TypeError("proposal must be WeightProposal or dict.")

        required = {
            "proposal_id",
            "created_at",
            "target",
            "name",
            "current_weight",
            "proposed_weight",
            "delta",
            "evidence_count",
            "signal_ids",
            "severity_counts",
            "signal_type_counts",
        }

        missing = sorted(required - set(proposal.keys()))

        if missing:
            raise ValueError(f"proposal dict missing fields: {missing}")

        return WeightProposal(
            proposal_id=str(proposal["proposal_id"]),
            created_at=str(proposal["created_at"]),
            target=str(proposal["target"]),
            name=str(proposal["name"]),
            current_weight=float(proposal["current_weight"]),
            proposed_weight=float(proposal["proposed_weight"]),
            delta=float(proposal["delta"]),
            evidence_count=int(proposal["evidence_count"]),
            signal_ids=list(proposal["signal_ids"]),
            severity_counts=dict(proposal["severity_counts"]),
            signal_type_counts=dict(proposal["signal_type_counts"]),
            status=str(proposal.get("status", "pending_review")),
            metadata=dict(proposal.get("metadata", {})),
        )
