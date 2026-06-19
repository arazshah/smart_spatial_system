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
from orchestrator.planning.dag import DagPlan
from orchestrator.planning.dag_executor import DagExecutionResult, DagExecutor
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

    success:
        Convenience mirror of execution.success.
    """

    success: bool
    plan: DagPlan
    execution: DagExecutionResult

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

    def run(
        self,
        query_spec: QuerySpec,
        *,
        initial_inputs: dict[str, Any] | None = None,
        fail_fast: bool = True,
    ) -> PlanningRunResult:
        plan = self.build_plan(query_spec)
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
