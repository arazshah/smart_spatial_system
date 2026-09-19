"""
s3geo

Public entry point for this package: a one-call natural-language query
(`s3geo.query`), plus the building blocks it wires up, re-exported here so
`import s3geo` is enough on its own - no need to know the underlying
pipeline lives in `orchestrator.*` to reach it.

    import s3geo

    result = s3geo.query(
        "For every station, find amenity points within 300 meters, "
        "reproject to a metric CRS first.",
        layers={"stations": stations_gdf, "amenity": amenity_gdf},
    )

    result.goal          # str - the LLM-identified analysis goal
    result.operations    # list[str] - operation names, in execution order
    result.output        # the final DAG output

For finer-grained control than a single `query()` call - inspecting a
plan before executing it, resolving a specific plugin capability, using a
different LLM client - the same classes `query()` uses internally are
available directly as `s3geo.<Name>`:

    registry = s3geo.registry()                    # CapabilityRegistry, all plugins loaded
    spec = s3geo.LLMQuerySpecGenerator(s3geo.OpenAICompatibleLLMClient()).generate(
        "buffer the sites by 100 meters", context={}, system_hints="",
    )
    plan = s3geo.DeterministicPlanner().build(spec)
    result = s3geo.DagExecutor(s3geo.RegistryCapabilityResolver(registry)).execute(
        plan, initial_inputs={"sites": sites_geojson},
    )

This module adds no analysis logic of its own, in either form - `query()`
and `registry()` are thin wrappers, and every re-exported name is the
exact same class defined in `orchestrator.planning` / `orchestrator.
capability_registry`, not a copy. Those modules remain directly usable
and unchanged; this is only a second, shorter spelling for reaching them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import (
    CapabilityResolutionError,
    RegistryCapabilityResolver,
)
from orchestrator.planning.dag_executor import DagExecutionError, DagExecutor
from orchestrator.planning.llm_spec_generator import (
    LLMQuerySpecGenerator,
    LLMSpecGenerationError,
    OpenAICompatibleLLMClient,
    StaticLLMClient,
)
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig, PlanningError

__all__ = [
    "query",
    "registry",
    "S3GeoResult",
    # Planning pipeline classes `query()` wires up internally - re-exported
    # so they are reachable as `s3geo.<Name>` without importing orchestrator.
    "CapabilityRegistry",
    "DeterministicPlanner",
    "PlannerConfig",
    "DagExecutor",
    "RegistryCapabilityResolver",
    "LLMQuerySpecGenerator",
    "OpenAICompatibleLLMClient",
    "StaticLLMClient",
    # Errors `query()` (and the manual pipeline above) can raise.
    "LLMSpecGenerationError",
    "PlanningError",
    "DagExecutionError",
    "CapabilityResolutionError",
]


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


def registry(*, tolerant: bool = True) -> CapabilityRegistry:
    """
    Build a CapabilityRegistry with every discoverable plugin loaded -
    the same call query() makes internally, exposed directly for callers
    who want to resolve or inspect a specific capability themselves
    (`s3geo.registry().resolve("buffer_analysis").callable`) instead of
    going through a full query() call.

    tolerant:
        When True (the default), a plugin whose import fails (e.g. a
        missing optional dependency such as rasterio or weasyprint) is
        skipped rather than raising - see registry.skipped_plugins.
    """
    return CapabilityRegistry.from_plugin_modules(tolerant=tolerant)


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
    tolerant: bool = True,
    strict_params: bool = True,
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
        tolerant:
            Forwarded to CapabilityRegistry.from_plugin_modules() (same
            meaning as registry()'s tolerant argument). Defaults to True
            so a plugin with a missing optional dependency (e.g.
            ndvi_analysis without rasterio installed) is skipped rather
            than crashing every query() call, even ones that never touch
            that plugin - matching OrchestratorService's own registry
            build. Pass False to opt back into strict, fail-fast import
            behavior.
        strict_params:
            Forwarded to PlannerConfig. Defaults to True so a params key
            the LLM's plan uses that isn't in OP_CATALOG's param_map for
            that operation (e.g. a typo'd or hallucinated parameter name)
            is rejected with a clear PlanningError at planning time,
            instead of being passed straight through to the plugin
            function and failing later with a raw, misleading
            TypeError - matching every PlannerConfig this codebase's own
            test suite constructs. Pass False to opt back into permissive
            pass-through of unrecognized params.

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

    plan = DeterministicPlanner(PlannerConfig(strict_params=strict_params)).build(query_spec)

    reg = registry(tolerant=tolerant)
    executor = DagExecutor(RegistryCapabilityResolver(reg))
    dag_result = executor.execute(plan, initial_inputs=initial_inputs)

    if not dag_result.success:
        raise RuntimeError(dag_result.error)

    return S3GeoResult(
        goal=query_spec.goal,
        operations=[operation.op for operation in query_spec.operations],
        output=dag_result.outputs[query_spec.operations[-1].output],
    )
