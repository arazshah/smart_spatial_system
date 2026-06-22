"""
orchestrator.planning.kernel_plan_adapter

Adapters from the current smart_spatial_system planning DAG models to
geochat_kernel QueryPlan / PlanStep models.

This module is intentionally structural.

It does not execute plans.
It does not replace DagExecutor yet.
It prepares the planning layer for Phase 3, where QuerySpec-derived plans can
be executed through geochat_kernel.runtime.PlanExecutor.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any
from uuid import uuid4

from geochat_kernel.models import PlanStep, QueryPlan

from orchestrator.planning.dag import DagNode, DagPlan
from orchestrator.planning.planner import DeterministicPlanner
from orchestrator.planning.spec import QuerySpec


_NODE_REF_PREFIXES = ("$node.", "$nodes.")
_EXTERNAL_REF_PREFIXES = (
    "$inputs.",
    "$input.",
    "$entity.",
    "$entities.",
)


def _jsonish(value: Any) -> Any:
    """
    Convert dataclass-like values to JSON-friendly nested structures where
    possible, while keeping unknown values as-is.

    This is used only for metadata preservation.
    """
    if is_dataclass(value):
        return asdict(value)

    if isinstance(value, dict):
        return {
            str(key): _jsonish(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _jsonish(item)
            for item in value
        ]

    return value


def _strip_prefix(value: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):]
    return None


def _node_ref_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _strip_prefix(value, _NODE_REF_PREFIXES)


def _external_ref_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _strip_prefix(value, _EXTERNAL_REF_PREFIXES)


def dag_node_to_plan_step(node: DagNode) -> PlanStep:
    """
    Convert one DagNode to a kernel PlanStep.

    Mapping notes:
    - DagNode.capability_name becomes both PlanStep.type and PlanStep.name.
      This is the safest Phase-2 mapping because the current DAG is already
      capability-bound.
    - Node references in DagNode.inputs become PlanStep.input_map entries.
    - Initial/external input references are preserved in metadata because the
      current kernel PlanStep input_map only points to producing step ids.
    - Raw literal input values are preserved in metadata.
    """
    input_map: dict[str, str] = {}
    external_input_map: dict[str, str] = {}
    literal_inputs: dict[str, Any] = {}
    inferred_dependencies: list[str] = []

    for param_name, ref in node.inputs.items():
        node_ref = _node_ref_id(ref)
        if node_ref is not None:
            input_map[param_name] = node_ref
            inferred_dependencies.append(node_ref)
            continue

        external_ref = _external_ref_id(ref)
        if external_ref is not None:
            external_input_map[param_name] = external_ref
            continue

        literal_inputs[param_name] = ref

    dependencies = list(dict.fromkeys([
        *node.needs,
        *inferred_dependencies,
    ]))

    parameters = dict(node.static_params)

    metadata: dict[str, Any] = {
        "source": "smart_spatial_system.dag",
        "capability_name": node.capability_name,
        "produces": node.produces,
        "dag_inputs": _jsonish(node.inputs),
        "dag_static_params": _jsonish(node.static_params),
        "dag_metadata": _jsonish(node.metadata),
    }

    if external_input_map:
        metadata["external_input_map"] = external_input_map

    if literal_inputs:
        metadata["literal_inputs"] = _jsonish(literal_inputs)

    datasource_ids = node.metadata.get("datasource_ids", [])
    if isinstance(datasource_ids, str):
        datasource_ids = [datasource_ids]
    if not isinstance(datasource_ids, list):
        datasource_ids = []

    timeout_s = node.metadata.get("timeout_s")
    if timeout_s is not None:
        try:
            timeout_s = float(timeout_s)
        except (TypeError, ValueError):
            timeout_s = None

    max_retries = node.metadata.get("max_retries", 0)
    try:
        max_retries = int(max_retries)
    except (TypeError, ValueError):
        max_retries = 0

    return PlanStep(
        id=node.id,
        type=str(node.metadata.get("step_type") or node.capability_name),
        name=str(node.metadata.get("step_name") or node.capability_name),
        datasource_ids=[
            str(item)
            for item in datasource_ids
        ],
        dependencies=dependencies,
        input_map=input_map,
        parameters=parameters,
        remote=bool(node.metadata.get("remote", False)),
        timeout_s=timeout_s,
        max_retries=max_retries,
        cacheable=bool(node.metadata.get("cacheable", True)),
        cost_estimate=node.metadata.get("cost_estimate"),
        metadata=metadata,
    )


def dag_plan_to_query_plan(
    dag_plan: DagPlan,
    *,
    query_ir_id: str | None = None,
    plan_id: str | None = None,
    planner_name: str = "smart_spatial_system.deterministic_planner",
    parallel_execution_allowed: bool = False,
    cache_policy: str = "default",
) -> QueryPlan:
    """
    Convert a DagPlan to a geochat_kernel QueryPlan.

    This adapter preserves the current planning model in QueryPlan.metadata so
    later migration steps can compare DAG execution and kernel execution.
    """
    resolved_query_ir_id = (
        query_ir_id
        or dag_plan.metadata.get("query_ir_id")
        or getattr(getattr(dag_plan, "query_spec", None), "metadata", {}).get("query_ir_id")
        or f"query_ir_{uuid4().hex}"
    )

    steps = [
        dag_node_to_plan_step(node)
        for node in dag_plan.nodes
    ]

    query_spec = getattr(dag_plan, "query_spec", None)

    metadata: dict[str, Any] = {
        "source": "smart_spatial_system.dag_adapter",
        "output_nodes": list(dag_plan.output_nodes),
        "dag_metadata": _jsonish(dag_plan.metadata),
    }

    if query_spec is not None:
        metadata["query_spec"] = _jsonish(query_spec)

    plan = QueryPlan(
        id=plan_id or f"plan_{uuid4().hex}",
        query_ir_id=str(resolved_query_ir_id),
        steps=steps,
        parallel_execution_allowed=parallel_execution_allowed,
        cache_policy=cache_policy,
        planner_name=planner_name,
        metadata=metadata,
    )

    return plan


def query_spec_to_query_plan(
    query_spec: QuerySpec,
    *,
    planner: DeterministicPlanner | None = None,
    query_ir_id: str | None = None,
    plan_id: str | None = None,
) -> QueryPlan:
    """
    Convenience helper:

        QuerySpec -> DeterministicPlanner -> DagPlan -> QueryPlan

    This does not execute anything.
    """
    planner = planner or DeterministicPlanner()
    dag_plan = planner.build(query_spec)

    return dag_plan_to_query_plan(
        dag_plan,
        query_ir_id=query_ir_id,
        plan_id=plan_id,
    )


def kernel_plan_to_summary(plan: QueryPlan | None) -> dict[str, Any] | None:
    """
    Build a compact, public-safe summary of a kernel QueryPlan.

    This is intended for API/metadata/debug visibility, not for execution.
    It intentionally avoids embedding full parameters or full metadata payloads.
    """
    if plan is None:
        return None

    problems = plan.validate_dag()

    steps: list[dict[str, Any]] = []
    for step in plan.steps:
        steps.append(
            {
                "id": step.id,
                "type": step.type,
                "name": step.name,
                "dependencies": list(step.dependencies),
                "input_names": sorted(step.input_map.keys()),
                "input_sources": dict(step.input_map),
                "parameter_keys": sorted(str(key) for key in step.parameters.keys()),
                "datasource_ids": list(step.datasource_ids),
                "remote": bool(step.remote),
                "cacheable": bool(step.cacheable),
                "produces": step.metadata.get("produces"),
                "capability_name": step.metadata.get("capability_name"),
            }
        )

    return {
        "id": plan.id,
        "query_ir_id": plan.query_ir_id,
        "planner_name": plan.planner_name,
        "step_count": len(plan.steps),
        "parallel_execution_allowed": bool(plan.parallel_execution_allowed),
        "cache_policy": plan.cache_policy,
        "valid": not problems,
        "problems": problems,
        "output_nodes": list(plan.metadata.get("output_nodes", [])),
        "steps": steps,
    }
