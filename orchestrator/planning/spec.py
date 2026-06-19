"""
orchestrator.planning.spec

Declarative query specification.

This layer represents what the user wants, not how it is executed.
It can be produced manually, by rules, by an LLM, or by a hybrid parser.

Important:
    LLM is allowed to produce QuerySpec / OutputSpec / ScoringSpec.
    LLM must not execute code or directly call plugins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EntitySpec:
    """
    A logical data entity referenced by the query.

    Examples:
        properties
        land_use
        roads
        poi
        risk_api
    """

    ref: str
    kind: str
    binding: dict[str, Any] = field(default_factory=dict)
    hints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationSpec:
    """
    A logical operation requested by the user.

    op:
        Logical operation name, such as:
            filter_by_distance
            filter_points_in_polygon
            enrich_risk
            score_features

    inputs:
        Mapping from logical input role to entity/output reference.

    params:
        Operation parameters.

    output:
        Logical output reference produced by this operation.
    """

    op: str
    inputs: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    output: str = ""


@dataclass(frozen=True)
class OutputSpec:
    """
    Declarative description of desired outputs.

    This is where LLM can be very useful:
        - report sections
        - map layer styling
        - table columns
        - highlights
        - PDF structure
    """

    kind: str
    source: str = ""
    format: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuerySpec:
    """
    Full structured query specification.
    """

    raw_query: str
    goal: str
    entities: list[EntitySpec] = field(default_factory=list)
    operations: list[OperationSpec] = field(default_factory=list)
    outputs: list[OutputSpec] = field(default_factory=list)
    source: str = "manual"
    metadata: dict[str, Any] = field(default_factory=dict)
