"""
s3geo

Minimal public entry point for running a single natural-language spatial
query end-to-end: LLM planning, deterministic DAG planning, and execution
against the registered plugin capabilities.

    import s3geo

    result = s3geo.query(
        "For every station, find amenity points within 300 meters, "
        "reproject to a metric CRS first.",
        layers={"stations": stations_gdf, "amenity": amenity_gdf},
    )

    result.goal          # str - the LLM-identified analysis goal
    result.operations    # list[str] - operation names, in execution order
    result.output        # the final DAG output

This is a thin wrapper only - it adds no analysis logic of its own. The
full pipeline it wires up (OpenAICompatibleLLMClient +
LLMQuerySpecGenerator + DeterministicPlanner + CapabilityRegistry +
RegistryCapabilityResolver + DagExecutor, all in orchestrator.planning /
orchestrator.capability_registry) is the right level of control for
advanced use or debugging, and stays directly usable and unchanged - this
module only collapses that manual wiring into one call for the common
"ask a question, get an answer" case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.llm_spec_generator import (
    LLMQuerySpecGenerator,
    OpenAICompatibleLLMClient,
)
from orchestrator.planning.planner import DeterministicPlanner

__all__ = ["query", "S3GeoResult"]


@dataclass(frozen=True)
class S3GeoResult:
    """
    Result of a single s3geo.query() call.

    goal:
        The LLM-identified analysis goal (QuerySpec.goal).
    operations:
        Names of the operations the plan executed, in order
        (QuerySpec.operations[i].op).
    output:
        The final DAG output - the same object the plan's last operation
        produced.
    """

    goal: str
    operations: list[str]
    output: Any


def _to_geojson_dict(layer: Any) -> Any:
    """
    Accept either a GeoJSON FeatureCollection dict or a
    geopandas.GeoDataFrame for a layer value.
    """
    if hasattr(layer, "to_json"):
        return json.loads(layer.to_json(default=str))
    return layer


def query(
    raw_query: str,
    *,
    layers: dict[str, Any],
    context: dict[str, Any] | None = None,
    system_hints: str | None = None,
) -> S3GeoResult:
    """
    Run a single natural-language spatial query end-to-end.

    Args:
        raw_query:
            The natural-language question.
        layers:
            Named input layers. Each value is either a GeoJSON
            FeatureCollection dict or a geopandas.GeoDataFrame (detected
            via hasattr(layer, "to_json") and converted with
            layer.to_json(default=str)).
        context:
            Optional context forwarded to LLMQuerySpecGenerator.generate,
            e.g. a semantic_planning_context for PostGIS-backed queries.
        system_hints:
            Optional system hints forwarded to
            LLMQuerySpecGenerator.generate.

    Returns:
        S3GeoResult with the identified goal, the operations the plan
        executed (in order), and the final output.

    Raises:
        LLMSpecGenerationError:
            If the LLM's plan fails generation-time validation.
        PlanningError:
            If the generated QuerySpec cannot be turned into an
            executable DAG plan.
        RuntimeError:
            If the DAG plan builds but fails during execution - the
            executor's own error message is included, not a generic one.
    """
    initial_inputs = {name: _to_geojson_dict(layer) for name, layer in layers.items()}

    client = OpenAICompatibleLLMClient()
    query_spec = LLMQuerySpecGenerator(client).generate(
        raw_query,
        context=context or {},
        system_hints=system_hints or "",
    )

    plan = DeterministicPlanner().build(query_spec)

    registry = CapabilityRegistry.from_plugin_modules()
    executor = DagExecutor(RegistryCapabilityResolver(registry))
    dag_result = executor.execute(plan, initial_inputs=initial_inputs)

    if not dag_result.success:
        raise RuntimeError(dag_result.error)

    return S3GeoResult(
        goal=query_spec.goal,
        operations=[operation.op for operation in query_spec.operations],
        output=dag_result.outputs[query_spec.operations[-1].output],
    )
