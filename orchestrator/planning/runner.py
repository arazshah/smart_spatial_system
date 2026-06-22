"""
orchestrator.planning.runner

High-level runner for planning-based execution.

Pipeline:
    QuerySpec
        ↓
    DeterministicPlanner
        ↓
    DagPlan
        ↓
    DagExecutor
        ↓
    Execution result

This is the operational wrapper that will later be called by:
    - API endpoint
    - natural-query service
    - LLM QuerySpec generator
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from orchestrator.planning.capability_resolver import (
    RegistryCapabilityResolver,
    StaticCapabilityResolver,
)
from geochat_kernel.models import QueryPlan

from orchestrator.planning.dag import DagPlan
from orchestrator.planning.dag_executor import DagExecutionResult, DagExecutor
from orchestrator.planning.kernel_execution_bridge import (
    KernelExecutionBridgeResult,
    execute_kernel_plan_with_capabilities_sync,
)
from orchestrator.planning.kernel_plan_adapter import dag_plan_to_query_plan
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig
from orchestrator.planning.spec import QuerySpec


@dataclass
class PlanningRunResult:
    """
    Full result of a planning run.

    plan:
        Capability-bound DAG plan.

    execution:
        DagExecutionResult with outputs, output_nodes, trace and error.

    kernel_plan:
        geochat_kernel QueryPlan equivalent of the current DagPlan.

    kernel_execution:
        Optional experimental kernel execution result produced by
        run_with_kernel_execution(). It is intentionally opt-in and does not
        affect the default run() production path.

    success:
        Convenience mirror of execution.success.
    """

    success: bool
    plan: DagPlan
    execution: DagExecutionResult
    kernel_plan: QueryPlan | None = None
    kernel_execution: KernelExecutionBridgeResult | None = None

    @property
    def outputs(self) -> dict[str, Any]:
        return self.execution.outputs

    @property
    def output_nodes(self) -> dict[str, Any]:
        return self.execution.output_nodes

    @property
    def trace(self) -> Any:
        return self.execution.trace

    @property
    def error(self) -> str | None:
        return self.execution.error

    @property
    def structured_error(self) -> dict[str, Any] | None:
        return getattr(self.execution, "structured_error", None)


class PlanningRunner:
    """
    Build and execute a DagPlan from a QuerySpec.

    Args:
        capability_resolver:
            Callable that maps capability_name -> callable.

        planner:
            Optional DeterministicPlanner.

    Example:
        runner = PlanningRunner(
            StaticCapabilityResolver({
                "score_features": score_features,
                "rank_features": rank_features,
            })
        )
        result = runner.run(query_spec, initial_inputs={...})
    """

    def __init__(
        self,
        capability_resolver: Callable[[str], Callable[..., Any]],
        planner: DeterministicPlanner | None = None,
    ) -> None:
        self.capability_resolver = capability_resolver
        self.planner = planner or DeterministicPlanner()

    def build_plan(self, query_spec: QuerySpec) -> DagPlan:
        return self.planner.build(query_spec)

    def build_kernel_plan(
        self,
        plan: DagPlan,
        *,
        query_ir_id: str | None = None,
    ) -> QueryPlan:
        """
        Build the geochat_kernel QueryPlan equivalent of a DagPlan.

        This method does not execute the kernel plan.
        It exists to make the Phase 2 migration explicit and testable.
        """
        return dag_plan_to_query_plan(
            plan,
            query_ir_id=query_ir_id,
        )

    def run(
        self,
        query_spec: QuerySpec,
        *,
        initial_inputs: dict[str, Any] | None = None,
        fail_fast: bool = True,
    ) -> PlanningRunResult:
        plan = self.build_plan(query_spec)
        kernel_plan = self.build_kernel_plan(plan)

        executor = DagExecutor(self.capability_resolver)
        execution = executor.execute(
            plan,
            initial_inputs=initial_inputs or {},
            fail_fast=fail_fast,
        )
        return PlanningRunResult(
            success=execution.success,
            plan=plan,
            execution=execution,
            kernel_plan=kernel_plan,
        )

    def run_with_kernel_execution(
        self,
        query_spec: QuerySpec,
        *,
        initial_inputs: dict[str, Any] | None = None,
        fail_fast: bool = True,
        raise_on_kernel_error: bool = False,
    ) -> PlanningRunResult:
        """
        Run the current DAG execution path and, additionally, execute the
        generated kernel QueryPlan through the Phase 3 kernel execution bridge.

        This is intentionally opt-in. The default run() method remains the
        production-safe DAG executor path.
        """
        initial_inputs = initial_inputs or {}

        plan = self.build_plan(query_spec)
        kernel_plan = self.build_kernel_plan(plan)

        executor = DagExecutor(self.capability_resolver)
        execution = executor.execute(
            plan,
            initial_inputs=initial_inputs,
            fail_fast=fail_fast,
        )

        kernel_execution = execute_kernel_plan_with_capabilities_sync(
            kernel_plan,
            capability_resolver=self.capability_resolver,
            initial_inputs=initial_inputs,
            raise_on_error=raise_on_kernel_error,
        )

        return PlanningRunResult(
            success=execution.success,
            plan=plan,
            execution=execution,
            kernel_plan=kernel_plan,
            kernel_execution=kernel_execution,
        )


def make_static_planning_runner(
    capabilities: dict[str, Callable[..., Any]],
    *,
    planner_config: PlannerConfig | None = None,
) -> PlanningRunner:
    """
    Convenience factory for tests/manual execution.
    """
    planner = DeterministicPlanner(planner_config)
    resolver = StaticCapabilityResolver(capabilities)
    return PlanningRunner(resolver, planner=planner)


def make_registry_planning_runner(
    registry_or_service: Any,
    *,
    planner_config: PlannerConfig | None = None,
) -> PlanningRunner:
    """
    Convenience factory for real registry/service-backed execution.
    """
    planner = DeterministicPlanner(planner_config)
    resolver = RegistryCapabilityResolver(registry_or_service)
    return PlanningRunner(resolver, planner=planner)
