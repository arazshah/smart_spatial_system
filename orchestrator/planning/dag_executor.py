"""
orchestrator.planning.dag_executor

Generic DAG executor for capability-bound plans.

This executor is intentionally independent from the current natural-query
pipeline. It can execute any DagPlan when provided with a capability resolver.

MVP features:
    - topological execution
    - explicit dependency validation
    - input reference resolution
    - per-node trace
    - graceful structured errors
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from orchestrator.error_contract import (
    CATEGORY_CAPABILITY_CONTRACT,
    CATEGORY_CAPABILITY_RESOLUTION,
    CATEGORY_INTERNAL,
    CATEGORY_VALIDATION,
    exception_to_error,
)
from orchestrator.planning.dag import DagNode, DagPlan


CapabilityResolver = Callable[[str], Callable[..., Any]]


class DagExecutionError(RuntimeError):
    pass


class DagValidationError(ValueError):
    pass


@dataclass
class DagNodeTrace:
    node_id: str
    capability_name: str
    status: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    input_keys: list[str] = field(default_factory=list)
    output_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class DagExecutionResult:
    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    output_nodes: dict[str, Any] = field(default_factory=dict)
    trace: list[DagNodeTrace] = field(default_factory=list)
    error: str | None = None
    structured_error: dict[str, Any] | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_awaitable_sync(awaitable: Any) -> Any:
    """
    Run an awaitable from the synchronous DagExecutor path.

    Most planning execution is synchronous, but some plugin capabilities are
    async functions. Without this bridge, DagExecutor would store a coroutine
    object as the node output instead of the actual capability result.

    If no event loop is running in the current thread, use asyncio.run().
    If an event loop is already running, execute the awaitable in a short-lived
    worker thread with its own event loop. This keeps the public execute()
    method synchronous while still supporting async plugin callables.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result_box["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - defensive bridge
            error_box["error"] = exc

    thread = threading.Thread(target=runner, name="dag-executor-awaitable-runner")
    thread.start()
    thread.join()

    if "error" in error_box:
        raise error_box["error"]

    return result_box.get("value")


def _summarize_output(value: Any) -> dict[str, Any]:
    """
    Compact output summary for trace/audit.
    Does not store full payload.
    """
    summary: dict[str, Any] = {
        "type": type(value).__name__,
    }

    if hasattr(value, "features"):
        try:
            summary["feature_count"] = len(getattr(value, "features") or [])
        except Exception:
            pass

    if hasattr(value, "metadata"):
        metadata = getattr(value, "metadata")
        if isinstance(metadata, dict):
            for key in ("feature_count", "output_feature_count", "matched_count"):
                if key in metadata:
                    summary[key] = metadata[key]

    if isinstance(value, dict):
        summary["keys"] = sorted(str(k) for k in value.keys())[:20]
        if "features" in value and isinstance(value["features"], list):
            summary["feature_count"] = len(value["features"])

    if isinstance(value, list):
        summary["length"] = len(value)

    return summary



def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)

        if isinstance(cause, BaseException):
            current = cause
        elif isinstance(context, BaseException):
            current = context
        else:
            current = None

    return chain


def _dag_exception_structured_error(
    exc: BaseException,
    *,
    node: DagNode | None = None,
    stage: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """
    Convert DAG/planning execution exceptions into the Phase 4 structured error
    contract.

    This is intentionally additive:
    - legacy string errors remain unchanged
    - structured_error is added for clients/debugging
    """
    chain = _exception_chain(exc)

    for item in chain:
        existing_structured_error = getattr(item, "structured_error", None)
        if isinstance(existing_structured_error, dict):
            structured_error = dict(existing_structured_error)
            details = dict(structured_error.get("details") or {})

            if node is not None:
                details.setdefault("node_id", node.id)
                details.setdefault("capability_name", node.capability_name)

            if stage is not None:
                details.setdefault("stage", stage)

            structured_error["details"] = details
            return structured_error

    chain_details = [
        {
            "type": type(item).__name__,
            "message": str(item) or type(item).__name__,
        }
        for item in chain
    ]

    combined_message = " | ".join(
        str(item) or type(item).__name__
        for item in chain
    ).lower()

    type_names = {type(item).__name__ for item in chain}

    code = "dag_execution.failed"
    category = CATEGORY_INTERNAL

    if isinstance(exc, DagValidationError) or "dependency cycle" in combined_message:
        code = "dag.validation_failed"
        category = CATEGORY_VALIDATION

    elif isinstance(exc, DagExecutionError) or stage == "input_resolution":
        code = "dag.reference_resolution_failed"
        category = CATEGORY_VALIDATION

    elif (
        stage == "capability_resolution"
        or "capability" in combined_message
        and (
            "not found" in combined_message
            or "missing" in combined_message
            or "resolve" in combined_message
            or "resolution" in combined_message
        )
    ):
        code = "capability.resolution_failed"
        category = CATEGORY_CAPABILITY_RESOLUTION

    elif (
        "TypeError" in type_names
        or "ValidationError" in type_names
        or "unexpected keyword" in combined_message
        or "got an unexpected" in combined_message
        or "missing required" in combined_message
        or "missing_properties" in combined_message
        or "missing properties" in combined_message
        or "signature" in combined_message
        or "parameter" in combined_message
        or "parameters" in combined_message
    ):
        code = "capability.contract_failed"
        category = CATEGORY_CAPABILITY_CONTRACT

    details: dict[str, Any] = {
        "exception_chain": chain_details,
    }

    if node is not None:
        details.update(
            {
                "node_id": node.id,
                "capability_name": node.capability_name,
            }
        )

    if stage is not None:
        details["stage"] = stage

    return exception_to_error(
        exc,
        code=code,
        category=category,
        retryable=False,
        source="dag_executor",
        message=message,
        details=details,
    ).to_dict()

def _node_ids(plan: DagPlan) -> set[str]:
    return {node.id for node in plan.nodes}


def _validate_plan(plan: DagPlan) -> None:
    if not isinstance(plan, DagPlan):
        raise DagValidationError("plan must be a DagPlan.")

    if not plan.nodes:
        raise DagValidationError("DagPlan must contain at least one node.")

    ids = [node.id for node in plan.nodes]
    if len(ids) != len(set(ids)):
        raise DagValidationError("DagPlan contains duplicate node ids.")

    id_set = set(ids)

    for node in plan.nodes:
        if not node.id:
            raise DagValidationError("DagNode.id must be non-empty.")
        if not node.capability_name:
            raise DagValidationError(f"DagNode {node.id} has empty capability_name.")
        for dep in node.needs:
            if dep not in id_set:
                raise DagValidationError(
                    f"DagNode {node.id} depends on unknown node {dep}."
                )

    for out in plan.output_nodes:
        if out not in id_set:
            raise DagValidationError(f"Unknown output node: {out}.")


def _topological_order(plan: DagPlan) -> list[DagNode]:
    """
    Kahn topological sort.
    """
    _validate_plan(plan)

    nodes_by_id = {node.id: node for node in plan.nodes}
    incoming: dict[str, set[str]] = {
        node.id: set(node.needs)
        for node in plan.nodes
    }
    outgoing: dict[str, set[str]] = {node.id: set() for node in plan.nodes}

    for node in plan.nodes:
        for dep in node.needs:
            outgoing[dep].add(node.id)

    ready = [node_id for node_id, deps in incoming.items() if not deps]
    ordered: list[DagNode] = []

    while ready:
        node_id = ready.pop(0)
        ordered.append(nodes_by_id[node_id])

        for child in sorted(outgoing[node_id]):
            incoming[child].discard(node_id)
            if not incoming[child]:
                ready.append(child)

    if len(ordered) != len(plan.nodes):
        raise DagValidationError("DagPlan contains a dependency cycle.")

    return ordered


def _resolve_ref(ref: Any, *, initial_inputs: dict[str, Any], state: dict[str, Any]) -> Any:
    """
    Resolve input reference.

    Supported:
        "$inputs.x"
        "$input.x"
        "$entity.x"
        "$node.node_id"
        "$nodes.node_id"
    """
    if not isinstance(ref, str) or not ref.startswith("$"):
        return ref

    prefixes = {
        "$inputs.": initial_inputs,
        "$input.": initial_inputs,
        "$entity.": initial_inputs,
        "$entities.": initial_inputs,
        "$node.": state,
        "$nodes.": state,
    }

    for prefix, source in prefixes.items():
        if ref.startswith(prefix):
            key = ref[len(prefix):]
            if key not in source:
                raise DagExecutionError(f"Reference {ref!r} could not be resolved.")
            return source[key]

    raise DagExecutionError(f"Unsupported reference syntax: {ref!r}")


def _build_kwargs(
    node: DagNode,
    *,
    initial_inputs: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    kwargs = dict(node.static_params)

    for param_name, ref in node.inputs.items():
        kwargs[param_name] = _resolve_ref(
            ref,
            initial_inputs=initial_inputs,
            state=state,
        )

    return kwargs


class DagExecutor:
    """
    Execute a DagPlan using a capability resolver.
    """

    def __init__(self, capability_resolver: CapabilityResolver) -> None:
        self.capability_resolver = capability_resolver

    def execute(
        self,
        plan: DagPlan,
        *,
        initial_inputs: dict[str, Any] | None = None,
        fail_fast: bool = True,
    ) -> DagExecutionResult:
        initial_inputs = initial_inputs or {}
        state: dict[str, Any] = {}
        trace: list[DagNodeTrace] = []

        try:
            ordered_nodes = _topological_order(plan)
        except Exception as exc:
            return DagExecutionResult(
                success=False,
                outputs={},
                output_nodes={},
                trace=[],
                error=str(exc),
                structured_error=_dag_exception_structured_error(
                    exc,
                    stage="plan_validation",
                    message=str(exc),
                ),
            )

        for node in ordered_nodes:
            started_at = _utc_now_iso()
            node_trace = DagNodeTrace(
                node_id=node.id,
                capability_name=node.capability_name,
                status="running",
                started_at=started_at,
            )

            stage = "capability_resolution"

            try:
                capability_fn = self.capability_resolver(node.capability_name)

                stage = "input_resolution"
                kwargs = _build_kwargs(
                    node,
                    initial_inputs=initial_inputs,
                    state=state,
                )
                node_trace.input_keys = sorted(kwargs.keys())

                stage = "capability_execution"
                output = capability_fn(**kwargs)
                if inspect.isawaitable(output):
                    output = _run_awaitable_sync(output)
                state[node.id] = output

                node_trace.status = "success"
                node_trace.finished_at = _utc_now_iso()
                node_trace.output_summary = _summarize_output(output)
                trace.append(node_trace)

            except Exception as exc:
                node_trace.status = "failed"
                node_trace.finished_at = _utc_now_iso()
                node_trace.error = str(exc)
                trace.append(node_trace)

                if fail_fast:
                    error = f"Node {node.id} failed: {exc}"
                    return DagExecutionResult(
                        success=False,
                        outputs=state,
                        output_nodes={
                            node_id: state[node_id]
                            for node_id in plan.output_nodes
                            if node_id in state
                        },
                        trace=trace,
                        error=error,
                        structured_error=_dag_exception_structured_error(
                            exc,
                            node=node,
                            stage=stage,
                            message=error,
                        ),
                    )

        output_nodes = {
            node_id: state[node_id]
            for node_id in plan.output_nodes
            if node_id in state
        }

        return DagExecutionResult(
            success=True,
            outputs=state,
            output_nodes=output_nodes,
            trace=trace,
            error=None,
            structured_error=None,
        )
