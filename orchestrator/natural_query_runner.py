"""
orchestrator.natural_query_runner

High-level runner for natural query execution.

This is the first reusable entry point for:

    natural query
    -> parser
    -> router
    -> plan builder
    -> executor
    -> response builder
"""

from __future__ import annotations

from typing import Any

from orchestrator.capability_router import SimpleCapabilityRouter
from orchestrator.pipeline_executor import SimplePipelineExecutor
from orchestrator.plan_builder import SimplePlanBuilder
from orchestrator.query_parser import SimpleNaturalLanguageParser
from orchestrator.response_builder import SimpleResponseBuilder


def run_natural_query(
    query: str,
    *,
    inputs: dict[str, Any],
    band_map: dict[str, int],
    router: Any | None = None,
) -> dict[str, Any]:
    """
    Execute a natural-language query through the simple orchestration pipeline.

    Args:
        query:
            Natural-language user query.
        inputs:
            Runtime inputs, e.g. {"raster": raster}.
        band_map:
            Band map for spectral index calculation.
        router:
            Optional router. If omitted, SimpleCapabilityRouter is used.
            This allows using RegistryBackedCapabilityRouter without changing
            parser/planner/executor code.

    DEPRECATED (REFACTOR_PLAN.md Phase 7,
    docs/PHASE7_LEGACY_CLEANUP_PLAN.md): as of this investigation, no
    confirmed production caller - a repo-wide search found no caller of
    this function anywhere in
    `smart_spatial_system/application/services/...`, i.e. nowhere in the
    actual request-handling code that builds `/query` responses. Its only
    production-code references are `orchestrator/__init__.py`'s
    package-level re-export and its own test suite
    (`tests/test_orchestrator_natural_query_pipeline.py`). Not the same as
    `run_natural_query_with_routing_evidence`
    (`orchestrator/routing_aware_natural_query_runner.py`), which IS a
    live production path - do not confuse the two when considering
    removal. "No caller found by grep" is not the same certainty as
    "confirmed dead", so this is marked deprecated rather than removed -
    re-run the same search at removal time, since new code could start
    calling it between now and then.
    """
    parser = SimpleNaturalLanguageParser(strict=False)
    final_router = router or SimpleCapabilityRouter()
    planner = SimplePlanBuilder(final_router)
    executor = SimplePipelineExecutor(final_router)
    response_builder = SimpleResponseBuilder()

    intent = parser.parse(query)
    plan = planner.build(intent, band_map=band_map)
    execution_result = executor.execute(plan, inputs=inputs)
    response = response_builder.build(execution_result)

    return {
        "intent": intent,
        "plan": plan,
        "execution": execution_result,
        "response": response,
    }
