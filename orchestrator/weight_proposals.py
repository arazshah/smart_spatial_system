"""
orchestrator.weight_proposals

Router weight store and proposal engine.

This module consumes RouterLearningSignal objects and creates pending weight
change proposals.

Important:
    This module DOES NOT automatically mutate router behavior.
    It only creates proposals that can later be reviewed/approved by:
        - a human reviewer
        - a policy engine
        - an offline evaluation process

Targets:
    - capability weights
    - plugin weights
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any


VALID_TARGETS = {
    "capability",
    "plugin",
}

VALID_PROPOSAL_STATUSES = {
    "pending_review",
    "approved",
    "rejected",
    "applied",
}


@dataclass(frozen=True)
class WeightStoreConfig:
    """
    Configuration for in-memory router weight store.
    """

    default_weight: float = 1.0
    min_weight: float = 0.0
    max_weight: float = 3.0

    def __post_init__(self) -> None:
        if self.default_weight < 0:
            raise ValueError("default_weight must be >= 0.")

        if self.min_weight < 0:
            raise ValueError("min_weight must be >= 0.")

        if self.max_weight < self.min_weight:
            raise ValueError("max_weight must be >= min_weight.")

        if not (self.min_weight <= self.default_weight <= self.max_weight):
            raise ValueError("default_weight must be between min_weight and max_weight.")


class InMemoryRouterWeightStore:
    """
    In-memory weight store for capabilities and plugins.

    This is intentionally simple and deterministic.
    """

    def __init__(
        self,
        config: WeightStoreConfig | None = None,
        *,
        capability_weights: dict[str, float] | None = None,
        plugin_weights: dict[str, float] | None = None,
    ) -> None:
        self.config = config or WeightStoreConfig()
        self.capability_weights: dict[str, float] = dict(capability_weights or {})
        self.plugin_weights: dict[str, float] = dict(plugin_weights or {})

        for name, value in self.capability_weights.items():
            self._validate_weight(value, f"capability:{name}")

        for name, value in self.plugin_weights.items():
            self._validate_weight(value, f"plugin:{name}")

    def get_weight(self, target: str, name: str) -> float:
        """
        Return configured weight or default weight.
        """
        self._validate_target(target)

        if target == "capability":
            return float(self.capability_weights.get(name, self.config.default_weight))

        return float(self.plugin_weights.get(name, self.config.default_weight))

    def set_weight(self, target: str, name: str, weight: float) -> None:
        """
        Set weight after clamping to allowed range.
        """
        self._validate_target(target)
        self._validate_weight(weight, f"{target}:{name}")

        clamped = self.clamp(weight)

        if target == "capability":
            self.capability_weights[name] = clamped
            return

        self.plugin_weights[name] = clamped

    def clamp(self, weight: float) -> float:
        """
        Clamp weight into configured range.
        """
        return round(
            max(
                self.config.min_weight,
                min(self.config.max_weight, float(weight)),
            ),
            6,
        )

    def replace_with(
        self,
        other: "InMemoryRouterWeightStore",
    ) -> None:
        """
        Replace this store's contents in-place.

        This preserves object identity so components holding a reference to this
        store, such as weighted routers and feedback services, observe reloaded
        weights without being rebuilt.
        """
        if not isinstance(other, InMemoryRouterWeightStore):
            raise TypeError("other must be InMemoryRouterWeightStore.")

        self.config = other.config
        self.capability_weights = dict(other.capability_weights)
        self.plugin_weights = dict(other.plugin_weights)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": {
                "default_weight": self.config.default_weight,
                "min_weight": self.config.min_weight,
                "max_weight": self.config.max_weight,
            },
            "capability_weights": dict(self.capability_weights),
            "plugin_weights": dict(self.plugin_weights),
        }

    @staticmethod
    def _validate_target(target: str) -> None:
        if target not in VALID_TARGETS:
            raise ValueError(f"Invalid target: {target}. Valid targets: {sorted(VALID_TARGETS)}")

    @staticmethod
    def _validate_weight(weight: float, label: str) -> None:
        if not isinstance(weight, (int, float)):
            raise TypeError(f"Weight for {label} must be numeric.")


@dataclass(frozen=True)
class WeightProposalConfig:
    """
    Configuration for proposal generation.
    """

    min_abs_delta: float = 0.0001
    round_digits: int = 6

    def __post_init__(self) -> None:
        if self.min_abs_delta < 0:
            raise ValueError("min_abs_delta must be >= 0.")

        if self.round_digits < 0:
            raise ValueError("round_digits must be >= 0.")


@dataclass(frozen=True)
class WeightProposal:
    """
    Pending proposal for changing one router weight.
    """

    proposal_id: str
    created_at: str

    target: str
    name: str

    current_weight: float
    proposed_weight: float
    delta: float

    evidence_count: int
    signal_ids: list[str]
    severity_counts: dict[str, int]
    signal_type_counts: dict[str, int]

    status: str = "pending_review"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RouterWeightProposalEngine:
    """
    Creates pending weight proposals from learning signals.
    """

    def __init__(
        self,
        config: WeightProposalConfig | None = None,
    ) -> None:
        self.config = config or WeightProposalConfig()

    def build(
        self,
        signals: list[Any],
        *,
        weight_store: InMemoryRouterWeightStore | None = None,
    ) -> list[WeightProposal]:
        """
        Build pending proposals from learning signals.
        """
        final_store = weight_store or InMemoryRouterWeightStore()

        aggregate: dict[tuple[str, str], dict[str, Any]] = {}

        for signal in signals:
            payload = _to_dict(signal)
            signal_id = payload.get("signal_id")
            severity = str(payload.get("severity") or "unknown")
            signal_type = str(payload.get("signal_type") or "unknown")

            adjustments = payload.get("weight_adjustments", {})

            if not isinstance(adjustments, dict):
                continue

            self._collect_adjustments(
                aggregate=aggregate,
                target="capability",
                adjustments=adjustments.get("capabilities", {}),
                signal_id=signal_id,
                severity=severity,
                signal_type=signal_type,
            )

            self._collect_adjustments(
                aggregate=aggregate,
                target="plugin",
                adjustments=adjustments.get("plugins", {}),
                signal_id=signal_id,
                severity=severity,
                signal_type=signal_type,
            )

        proposals: list[WeightProposal] = []

        for (target, name), row in sorted(aggregate.items()):
            delta = round(float(row["delta"]), self.config.round_digits)

            if abs(delta) < self.config.min_abs_delta:
                continue

            current_weight = final_store.get_weight(target, name)
            proposed_weight = final_store.clamp(current_weight + delta)
            effective_delta = round(proposed_weight - current_weight, self.config.round_digits)

            proposal = WeightProposal(
                proposal_id=str(uuid.uuid4()),
                created_at=datetime.now(timezone.utc).isoformat(),
                target=target,
                name=name,
                current_weight=round(current_weight, self.config.round_digits),
                proposed_weight=round(proposed_weight, self.config.round_digits),
                delta=effective_delta,
                evidence_count=int(row["evidence_count"]),
                signal_ids=list(row["signal_ids"]),
                severity_counts=dict(row["severity_counts"]),
                signal_type_counts=dict(row["signal_type_counts"]),
                status="pending_review",
                metadata={
                    "raw_delta": delta,
                    "clamped": effective_delta != delta,
                },
            )

            proposals.append(proposal)

        return proposals

    def build_dicts(
        self,
        signals: list[Any],
        *,
        weight_store: InMemoryRouterWeightStore | None = None,
    ) -> list[dict[str, Any]]:
        return [
            proposal.to_dict()
            for proposal in self.build(
                signals,
                weight_store=weight_store,
            )
        ]

    @staticmethod
    def approve(proposal: WeightProposal) -> WeightProposal:
        """
        Return approved copy of proposal.
        """
        return _with_status(proposal, "approved")

    @staticmethod
    def reject(proposal: WeightProposal) -> WeightProposal:
        """
        Return rejected copy of proposal.
        """
        return _with_status(proposal, "rejected")

    @staticmethod
    def apply(
        proposal: WeightProposal,
        *,
        weight_store: InMemoryRouterWeightStore,
    ) -> WeightProposal:
        """
        Apply an approved proposal to the store and return applied copy.

        Pending proposals are not applied directly.
        """
        if proposal.status != "approved":
            raise ValueError("Only approved proposals can be applied.")

        weight_store.set_weight(
            proposal.target,
            proposal.name,
            proposal.proposed_weight,
        )

        return _with_status(proposal, "applied")

    @staticmethod
    def _collect_adjustments(
        *,
        aggregate: dict[tuple[str, str], dict[str, Any]],
        target: str,
        adjustments: Any,
        signal_id: str | None,
        severity: str,
        signal_type: str,
    ) -> None:
        if not isinstance(adjustments, dict):
            return

        for name, delta in adjustments.items():
            if not isinstance(delta, (int, float)):
                continue

            key = (target, str(name))

            if key not in aggregate:
                aggregate[key] = {
                    "delta": 0.0,
                    "evidence_count": 0,
                    "signal_ids": [],
                    "severity_counts": {},
                    "signal_type_counts": {},
                }

            aggregate[key]["delta"] += float(delta)
            aggregate[key]["evidence_count"] += 1

            if signal_id:
                aggregate[key]["signal_ids"].append(str(signal_id))

            aggregate[key]["severity_counts"][severity] = (
                aggregate[key]["severity_counts"].get(severity, 0) + 1
            )

            aggregate[key]["signal_type_counts"][signal_type] = (
                aggregate[key]["signal_type_counts"].get(signal_type, 0) + 1
            )


class RouterWeightProposalCollector:
    """
    In-memory collector for weight proposals.
    """

    def __init__(self, max_proposals: int = 1000) -> None:
        if max_proposals < 0:
            raise ValueError("max_proposals must be >= 0.")

        self.max_proposals = max_proposals
        self.proposals: list[WeightProposal] = []

    def ingest(self, proposal: WeightProposal) -> None:
        if self.max_proposals == 0:
            return

        self.proposals.append(proposal)

        if len(self.proposals) > self.max_proposals:
            del self.proposals[0 : len(self.proposals) - self.max_proposals]

    def ingest_many(self, proposals: list[WeightProposal]) -> None:
        for proposal in proposals:
            self.ingest(proposal)

    def summarize(self) -> dict[str, Any]:
        target_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}

        for proposal in self.proposals:
            target_counts[proposal.target] = target_counts.get(proposal.target, 0) + 1
            status_counts[proposal.status] = status_counts.get(proposal.status, 0) + 1

        return {
            "total_proposals": len(self.proposals),
            "target_counts": target_counts,
            "status_counts": status_counts,
            "proposals": [
                proposal.to_dict()
                for proposal in self.proposals
            ],
        }

    def reset(self) -> None:
        self.proposals.clear()


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()

    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)

    raise TypeError("signal must be a dict or dataclass-like object.")


def _with_status(proposal: WeightProposal, status: str) -> WeightProposal:
    if status not in VALID_PROPOSAL_STATUSES:
        raise ValueError(
            f"Invalid proposal status: {status}. "
            f"Valid statuses: {sorted(VALID_PROPOSAL_STATUSES)}"
        )

    return replace(
        proposal,
        status=status,
        metadata={
            **proposal.metadata,
            "status_changed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
