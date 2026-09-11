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


def compare_dag_plan_to_query_plan(
    dag_plan: DagPlan,
    query_plan: QueryPlan,
) -> dict[str, Any]:
    """
    Compare the current DagPlan with its geochat_kernel QueryPlan equivalent.

    This is a structural/debug utility for Phase 2/3 migration.

    It does not execute either plan.
    It reports mismatches in a machine-readable form so tests and diagnostics
    can verify that the kernel plan is a faithful representation of the current
    DAG plan.
    """
    problems: list[str] = []

    dag_node_by_id = {
        node.id: node
        for node in dag_plan.nodes
    }
    step_by_id = {
        step.id: step
        for step in query_plan.steps
    }

    dag_node_ids = set(dag_node_by_id)
    kernel_step_ids = set(step_by_id)

    missing_steps = sorted(dag_node_ids - kernel_step_ids)
    extra_steps = sorted(kernel_step_ids - dag_node_ids)

    for node_id in missing_steps:
        problems.append(f"DagNode '{node_id}' is missing from QueryPlan.steps.")

    for step_id in extra_steps:
        problems.append(f"QueryPlan step '{step_id}' does not exist in DagPlan.nodes.")

    dag_output_nodes = list(dag_plan.output_nodes)
    kernel_output_nodes = list(query_plan.metadata.get("output_nodes", []))
    output_nodes_match = dag_output_nodes == kernel_output_nodes

    if not output_nodes_match:
        problems.append(
            "Output nodes mismatch: "
            f"DagPlan.output_nodes={dag_output_nodes!r}, "
            f"QueryPlan.metadata.output_nodes={kernel_output_nodes!r}."
        )

    kernel_dag_problems = query_plan.validate_dag()
    for problem in kernel_dag_problems:
        problems.append(f"Kernel QueryPlan validation problem: {problem}")

    step_comparisons: list[dict[str, Any]] = []

    for node in dag_plan.nodes:
        step = step_by_id.get(node.id)
        if step is None:
            step_comparisons.append(
                {
                    "id": node.id,
                    "exists": False,
                    "valid": False,
                    "problems": [
                        f"Missing QueryPlan step for DagNode '{node.id}'."
                    ],
                }
            )
            continue

        expected_step = dag_node_to_plan_step(node)
        step_problems: list[str] = []

        type_matches = step.type == expected_step.type
        if not type_matches:
            step_problems.append(
                f"type mismatch: expected {expected_step.type!r}, got {step.type!r}."
            )

        name_matches = step.name == expected_step.name
        if not name_matches:
            step_problems.append(
                f"name mismatch: expected {expected_step.name!r}, got {step.name!r}."
            )

        dependencies_match = list(step.dependencies) == list(expected_step.dependencies)
        if not dependencies_match:
            step_problems.append(
                "dependencies mismatch: "
                f"expected {list(expected_step.dependencies)!r}, "
                f"got {list(step.dependencies)!r}."
            )

        input_map_matches = dict(step.input_map) == dict(expected_step.input_map)
        if not input_map_matches:
            step_problems.append(
                "input_map mismatch: "
                f"expected {dict(expected_step.input_map)!r}, "
                f"got {dict(step.input_map)!r}."
            )

        parameter_keys_match = (
            sorted(str(key) for key in step.parameters.keys())
            == sorted(str(key) for key in expected_step.parameters.keys())
        )
        if not parameter_keys_match:
            step_problems.append(
                "parameter keys mismatch: "
                f"expected {sorted(str(key) for key in expected_step.parameters.keys())!r}, "
                f"got {sorted(str(key) for key in step.parameters.keys())!r}."
            )

        parameters_match = dict(step.parameters) == dict(expected_step.parameters)
        if not parameters_match:
            step_problems.append("parameters payload mismatch.")

        produces_matches = (
            step.metadata.get("produces")
            == expected_step.metadata.get("produces")
        )
        if not produces_matches:
            step_problems.append(
                "produces metadata mismatch: "
                f"expected {expected_step.metadata.get('produces')!r}, "
                f"got {step.metadata.get('produces')!r}."
            )

        capability_matches = (
            step.metadata.get("capability_name")
            == expected_step.metadata.get("capability_name")
        )
        if not capability_matches:
            step_problems.append(
                "capability_name metadata mismatch: "
                f"expected {expected_step.metadata.get('capability_name')!r}, "
                f"got {step.metadata.get('capability_name')!r}."
            )

        external_input_map_matches = (
            step.metadata.get("external_input_map", {})
            == expected_step.metadata.get("external_input_map", {})
        )
        if not external_input_map_matches:
            step_problems.append(
                "external_input_map metadata mismatch: "
                f"expected {expected_step.metadata.get('external_input_map', {})!r}, "
                f"got {step.metadata.get('external_input_map', {})!r}."
            )

        literal_inputs_match = (
            step.metadata.get("literal_inputs", {})
            == expected_step.metadata.get("literal_inputs", {})
        )
        if not literal_inputs_match:
            step_problems.append(
                "literal_inputs metadata mismatch: "
                f"expected {expected_step.metadata.get('literal_inputs', {})!r}, "
                f"got {step.metadata.get('literal_inputs', {})!r}."
            )

        if step_problems:
            problems.extend(
                f"Step '{node.id}': {problem}"
                for problem in step_problems
            )

        step_comparisons.append(
            {
                "id": node.id,
                "exists": True,
                "valid": not step_problems,
                "type_matches": type_matches,
                "name_matches": name_matches,
                "dependencies_match": dependencies_match,
                "input_map_matches": input_map_matches,
                "parameter_keys_match": parameter_keys_match,
                "parameters_match": parameters_match,
                "produces_matches": produces_matches,
                "capability_matches": capability_matches,
                "external_input_map_matches": external_input_map_matches,
                "literal_inputs_match": literal_inputs_match,
                "problems": step_problems,
            }
        )

    return {
        "valid": not problems,
        "problems": problems,
        "dag_node_count": len(dag_plan.nodes),
        "kernel_step_count": len(query_plan.steps),
        "missing_steps": missing_steps,
        "extra_steps": extra_steps,
        "dag_output_nodes": dag_output_nodes,
        "kernel_output_nodes": kernel_output_nodes,
        "output_nodes_match": output_nodes_match,
        "kernel_validate_dag_problems": kernel_dag_problems,
        "steps": step_comparisons,
    }


def format_plan_comparison_report(comparison: dict[str, Any]) -> str:
    """
    Format compare_dag_plan_to_query_plan output as a compact debug report.
    """
    lines: list[str] = []

    status = "VALID" if comparison.get("valid") else "INVALID"
    lines.append(f"DagPlan -> QueryPlan comparison: {status}")
    lines.append(
        "Nodes/Steps: "
        f"dag={comparison.get('dag_node_count')}, "
        f"kernel={comparison.get('kernel_step_count')}"
    )
    lines.append(
        "Output nodes match: "
        f"{comparison.get('output_nodes_match')}"
    )

    missing_steps = comparison.get("missing_steps") or []
    extra_steps = comparison.get("extra_steps") or []
    if missing_steps:
        lines.append(f"Missing steps: {missing_steps}")
    if extra_steps:
        lines.append(f"Extra steps: {extra_steps}")

    problems = comparison.get("problems") or []
    if problems:
        lines.append("Problems:")
        for problem in problems:
            lines.append(f"- {problem}")

    return "\n".join(lines)
