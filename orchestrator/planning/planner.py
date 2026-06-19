"""
orchestrator.planning.planner

Deterministic QuerySpec → DagPlan planner.

This planner is deliberately deterministic and explicit:
    - It does not call LLM.
    - It does not execute plugins.
    - It maps logical operations from QuerySpec to capabilities using op_catalog.
    - It creates a capability-bound DagPlan executable by DagExecutor.

LLM Role:
    LLM may create QuerySpec / ScoringSpec / OutputSpec.
    This planner turns that declarative spec into an executable DAG.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from orchestrator.planning.dag import DagNode, DagPlan
from orchestrator.planning.op_catalog import OpDescriptor, get_op, is_pending
from orchestrator.planning.spec import OperationSpec, QuerySpec


class PlanningError(ValueError):
    """Raised when QuerySpec cannot be converted to a valid DagPlan."""


@dataclass(frozen=True)
class PlannerConfig:
    """
    Planner behavior configuration.

    strict_params:
        If True, operation params not listed in OpDescriptor.param_map are rejected.
        If False, unknown params are passed through with their original name.

    allow_implicit_entities:
        If True, an input reference not produced by a previous operation is treated
        as an initial input/entity reference: "$inputs.<ref>".
    """

    strict_params: bool = False
    allow_implicit_entities: bool = True


@dataclass
class PlannerContext:
    """
    Internal state while building a plan.
    """

    produced_ref_to_node_id: dict[str, str] = field(default_factory=dict)
    entity_refs: set[str] = field(default_factory=set)
    used_node_ids: set[str] = field(default_factory=set)


def _sanitize_node_id(value: str, fallback: str) -> str:
    candidate = value.strip() if value else fallback
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", candidate)
    candidate = candidate.strip("_")

    if not candidate:
        candidate = fallback

    if candidate[0].isdigit():
        candidate = f"n_{candidate}"

    return candidate


def _unique_node_id(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base

    index = 2
    while f"{base}_{index}" in used:
        index += 1

    final = f"{base}_{index}"
    used.add(final)
    return final


def _operation_node_id(index: int, operation: OperationSpec, used: set[str]) -> str:
    if operation.output:
        base = _sanitize_node_id(operation.output, fallback=f"n{index}_{operation.op}")
    else:
        base = _sanitize_node_id(f"n{index}_{operation.op}", fallback=f"n{index}")
    return _unique_node_id(base, used)


def _map_params(
    operation: OperationSpec,
    descriptor: OpDescriptor,
    *,
    strict_params: bool,
) -> dict[str, Any]:
    static_params: dict[str, Any] = {}

    for logical_name, value in operation.params.items():
        if logical_name in descriptor.param_map:
            static_params[descriptor.param_map[logical_name]] = value
            continue

        if strict_params:
            raise PlanningError(
                f"Operation {operation.op!r} has unsupported parameter "
                f"{logical_name!r} for capability {descriptor.capability_name!r}."
            )

        # MVP-friendly pass-through.
        # This allows plugins to accept new params before op_catalog is updated.
        static_params[logical_name] = value

    return static_params


def _resolve_input_ref(
    ref: str,
    *,
    context: PlannerContext,
    allow_implicit_entities: bool,
) -> tuple[str, str | None]:
    """
    Resolve logical input ref to executable DAG reference.

    Returns:
        (dag_reference, dependency_node_id_or_None)
    """
    if not isinstance(ref, str) or not ref:
        raise PlanningError("Operation input references must be non-empty strings.")

    # Explicit executor reference: keep as-is.
    if ref.startswith("$"):
        if ref.startswith("$node."):
            return ref, ref[len("$node.") :]
        if ref.startswith("$nodes."):
            return ref, ref[len("$nodes.") :]
        return ref, None

    # Produced by previous operation.
    if ref in context.produced_ref_to_node_id:
        node_id = context.produced_ref_to_node_id[ref]
        return f"$node.{node_id}", node_id

    # Entity / initial input.
    if ref in context.entity_refs or allow_implicit_entities:
        return f"$inputs.{ref}", None

    raise PlanningError(
        f"Input reference {ref!r} is neither produced by a previous operation "
        "nor declared as an entity."
    )


def _map_inputs(
    operation: OperationSpec,
    descriptor: OpDescriptor,
    *,
    context: PlannerContext,
    allow_implicit_entities: bool,
) -> tuple[dict[str, Any], list[str]]:
    node_inputs: dict[str, Any] = {}
    dependencies: list[str] = []

    for logical_role, source_ref in operation.inputs.items():
        if logical_role not in descriptor.input_map:
            raise PlanningError(
                f"Operation {operation.op!r} has unsupported input role "
                f"{logical_role!r}. Supported roles: {sorted(descriptor.input_map)}"
            )

        capability_param = descriptor.input_map[logical_role]
        dag_ref, dep = _resolve_input_ref(
            source_ref,
            context=context,
            allow_implicit_entities=allow_implicit_entities,
        )

        node_inputs[capability_param] = dag_ref

        if dep is not None and dep not in dependencies:
            dependencies.append(dep)

    # Check required logical input roles for operations with input_map.
    missing_roles = [
        role for role in descriptor.input_map
        if role not in operation.inputs
    ]
    if missing_roles:
        raise PlanningError(
            f"Operation {operation.op!r} is missing input role(s): {missing_roles}"
        )

    return node_inputs, dependencies


class DeterministicPlanner:
    """
    Build executable DagPlan from QuerySpec using op_catalog.
    """

    def __init__(self, config: PlannerConfig | None = None) -> None:
        self.config = config or PlannerConfig()

    def build(self, query_spec: QuerySpec) -> DagPlan:
        if not isinstance(query_spec, QuerySpec):
            raise PlanningError("query_spec must be a QuerySpec.")

        if not query_spec.operations:
            raise PlanningError("QuerySpec must contain at least one operation.")

        context = PlannerContext(
            entity_refs={entity.ref for entity in query_spec.entities}
        )

        nodes: list[DagNode] = []

        for index, operation in enumerate(query_spec.operations, start=1):
            if not isinstance(operation, OperationSpec):
                raise PlanningError("QuerySpec.operations must contain OperationSpec objects.")

            try:
                descriptor = get_op(operation.op)
            except KeyError as exc:
                pending = is_pending(operation.op)
                raise PlanningError(
                    f"Unsupported operation {operation.op!r}. pending={pending}"
                ) from exc

            node_id = _operation_node_id(index, operation, context.used_node_ids)

            node_inputs, dependencies = _map_inputs(
                operation,
                descriptor,
                context=context,
                allow_implicit_entities=self.config.allow_implicit_entities,
            )

            static_params = _map_params(
                operation,
                descriptor,
                strict_params=self.config.strict_params,
            )

            node = DagNode(
                id=node_id,
                capability_name=descriptor.capability_name,
                inputs=node_inputs,
                static_params=static_params,
                needs=dependencies,
                produces=descriptor.output_type,
                metadata={
                    "logical_op": operation.op,
                    "logical_output": operation.output,
                    "notes": descriptor.notes,
                },
            )
            nodes.append(node)

            # Register produced ref. Prefer explicit operation.output.
            produced_ref = operation.output or node_id
            context.produced_ref_to_node_id[produced_ref] = node_id

        output_nodes = self._resolve_output_nodes(
            query_spec=query_spec,
            produced_ref_to_node_id=context.produced_ref_to_node_id,
            nodes=nodes,
        )

        return DagPlan(
            nodes=nodes,
            output_nodes=output_nodes,
            query_spec=query_spec,
            metadata={
                "planner": "deterministic",
                "operation_count": len(query_spec.operations),
                "output_count": len(output_nodes),
            },
        )

    def _resolve_output_nodes(
        self,
        *,
        query_spec: QuerySpec,
        produced_ref_to_node_id: dict[str, str],
        nodes: list[DagNode],
    ) -> list[str]:
        resolved: list[str] = []

        for output in query_spec.outputs:
            source = output.source
            if not source:
                continue

            if source in produced_ref_to_node_id:
                node_id = produced_ref_to_node_id[source]
            else:
                node_id = source

            if node_id not in {node.id for node in nodes}:
                raise PlanningError(
                    f"Output source {source!r} does not match any produced operation output."
                )

            if node_id not in resolved:
                resolved.append(node_id)

        # If no explicit outputs are requested, output the last node.
        if not resolved:
            resolved.append(nodes[-1].id)

        return resolved
