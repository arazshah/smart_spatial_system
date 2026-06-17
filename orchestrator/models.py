"""
orchestrator.models

Runtime models for the first natural-query orchestration layer.

These models are intentionally lightweight.
They will later evolve into Kernel-level query plan / DAG / execution models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class QueryIntent:
    """
    Parsed natural-language intent.
    """

    raw_query: str
    intent_name: str
    index_name: str
    threshold_operator: str
    threshold_value: float
    vectorize: bool
    output_geometry: str


@dataclass(frozen=True)
class CapabilityBinding:
    """
    Router binding between abstract capability name and actual plugin function.
    """

    name: str
    plugin_id: str
    callable: Callable[..., Any]
    output_kind: str
    keywords: list[str]


@dataclass(frozen=True)
class ScoredCapability:
    """
    Scored capability candidate for a natural-language query.
    """

    capability_name: str
    plugin_id: str
    output_kind: str
    score: float
    matched_terms: list[str]
    reasons: list[str]


@dataclass(frozen=True)
class PlanNode:
    """
    One executable plan node.

    This is linear for now, but can later become a DAG node with dependencies.
    """

    id: str
    capability_name: str
    params: dict[str, Any]
    output_key: str
    routing_evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class QueryPlan:
    """
    Simple query plan.

    Later this can become a real DAG with dependencies and parallel execution.
    """

    intent: QueryIntent
    nodes: list[PlanNode]
    routing_evidence: list[ScoredCapability] = field(default_factory=list)
