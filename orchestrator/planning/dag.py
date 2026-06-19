"""
orchestrator.planning.dag

Capability-bound DAG model.

DagPlan is executable. Each DagNode maps to a real capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DagNode:
    """
    One executable node in a capability-bound DAG.

    id:
        Unique node id.

    capability_name:
        Registered capability name, for example:
            filter_points_in_polygon
            find_nearest_neighbors
            fetch_postgis_layer

    inputs:
        Mapping from capability parameter name to a reference.

        Supported references:
            "$inputs.name"       initial input
            "$input.name"        initial input alias
            "$node.node_id"      previous node output
            "$nodes.node_id"     previous node output alias
            "$entity.name"       initial input/entity alias

        Raw values are also allowed.

    static_params:
        Literal parameters passed to the capability.

    needs:
        Explicit node dependencies.

    produces:
        Logical output kind:
            vector | raster | json | file | report | map_layer
    """

    id: str
    capability_name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    static_params: dict[str, Any] = field(default_factory=dict)
    needs: list[str] = field(default_factory=list)
    produces: str = "json"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DagPlan:
    """
    Executable DAG plan.
    """

    nodes: list[DagNode] = field(default_factory=list)
    output_nodes: list[str] = field(default_factory=list)
    query_spec: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
