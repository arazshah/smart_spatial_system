"""
Tests for the s3geo top-level convenience module.

s3geo.query() is a thin wrapper around the manual pipeline
(OpenAICompatibleLLMClient + LLMQuerySpecGenerator + DeterministicPlanner
+ CapabilityRegistry + RegistryCapabilityResolver + DagExecutor). It adds
no analysis behavior of its own, so the acceptance test proves that by
running both paths against the same injected LLM response (via
StaticLLMClient, the same pattern used elsewhere in this suite) and
comparing results - not by asserting anything about s3geo.query()'s
output in isolation.

Run:
    pytest tests/test_s3geo.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import s3geo  # noqa: E402
from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.planning.capability_resolver import (  # noqa: E402
    RegistryCapabilityResolver,
)
from orchestrator.planning.dag_executor import DagExecutor  # noqa: E402
from orchestrator.planning.llm_spec_generator import (  # noqa: E402
    LLMQuerySpecGenerator,
    LLMSpecGenerationError,
    StaticLLMClient,
)
from orchestrator.planning.planner import DeterministicPlanner, PlanningError  # noqa: E402

SITES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "properties": {"id": 1, "name": "site-a"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [10.0, 10.0]},
            "properties": {"id": 2, "name": "site-b"},
        },
    ],
}

RAW_QUERY = "buffer the sites by 100 meters"


def _fixed_query_spec_json() -> dict:
    return {
        "raw_query": RAW_QUERY,
        "goal": "buffer_sites",
        "entities": [{"ref": "sites", "kind": "vector"}],
        "operations": [
            {
                "op": "buffer",
                "inputs": {"vector": "sites"},
                "params": {"distance": 100, "engine": "python"},
                "output": "buffered_sites",
            }
        ],
        "outputs": [
            {"kind": "vector_layer", "source": "buffered_sites", "config": {}}
        ],
    }


def _run_manual_path(raw_query: str, layers: dict) -> tuple[str, list[str], object]:
    llm_json = _fixed_query_spec_json()
    client = StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))

    query_spec = LLMQuerySpecGenerator(client).generate(raw_query)
    plan = DeterministicPlanner().build(query_spec)

    registry = CapabilityRegistry.from_plugin_modules()
    executor = DagExecutor(RegistryCapabilityResolver(registry))
    dag_result = executor.execute(plan, initial_inputs=layers)

    assert dag_result.success, dag_result.error

    output = dag_result.outputs[query_spec.operations[-1].output]
    return query_spec.goal, [op.op for op in query_spec.operations], output


@pytest.fixture
def static_llm(monkeypatch):
    llm_json = _fixed_query_spec_json()

    def _factory(*args, **kwargs):
        return StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))

    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", _factory)
    return llm_json


def test_query_matches_the_manual_registry_planner_executor_path(static_llm):
    result = s3geo.query(RAW_QUERY, layers={"sites": SITES})

    manual_goal, manual_operations, manual_output = _run_manual_path(
        RAW_QUERY, {"sites": SITES}
    )

    assert result.goal == manual_goal
    assert result.operations == manual_operations

    # VectorOut has no __eq__, and "created_at" in its metadata is a fresh
    # timestamp per call - compare the parts that matter instead of the
    # objects themselves.
    assert result.output.features == manual_output.features
    assert result.output.metadata["operation"] == manual_output.metadata["operation"]
    assert result.output.metadata["distance"] == manual_output.metadata["distance"]
    assert (
        result.output.metadata["output_feature_count"]
        == manual_output.metadata["output_feature_count"]
    )


def test_query_accepts_a_geodataframe_layer(static_llm):
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "name": ["site-a", "site-b"]},
        geometry=[Point(0.0, 0.0), Point(10.0, 10.0)],
        crs="EPSG:4326",
    )

    result = s3geo.query(RAW_QUERY, layers={"sites": gdf})

    assert len(result.output.features) == 2
    assert result.output.metadata["operation"] == "buffer"


def test_query_raises_llm_spec_generation_error_unchanged(monkeypatch):
    def _factory(*args, **kwargs):
        return StaticLLMClient("not valid json at all")

    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", _factory)

    with pytest.raises(LLMSpecGenerationError):
        s3geo.query(RAW_QUERY, layers={"sites": SITES})


def test_reexported_names_are_the_same_objects_as_orchestrator(monkeypatch):
    # s3geo re-exports these so `import s3geo` alone reaches them, but it
    # must not define second copies - every name has to be the exact same
    # class/function object the orchestrator modules define, so behavior,
    # isinstance checks and exception handling stay identical either way.
    assert s3geo.CapabilityRegistry is CapabilityRegistry
    assert s3geo.DeterministicPlanner is DeterministicPlanner
    assert s3geo.DagExecutor is DagExecutor
    assert s3geo.RegistryCapabilityResolver is RegistryCapabilityResolver
    assert s3geo.LLMQuerySpecGenerator is LLMQuerySpecGenerator
    assert s3geo.StaticLLMClient is StaticLLMClient
    assert s3geo.LLMSpecGenerationError is LLMSpecGenerationError
    assert s3geo.PlanningError is PlanningError

    from orchestrator.planning.capability_resolver import CapabilityResolutionError
    from orchestrator.planning.dag_executor import DagExecutionError

    assert s3geo.CapabilityResolutionError is CapabilityResolutionError
    assert s3geo.DagExecutionError is DagExecutionError


def test_registry_matches_the_manual_capability_registry_build():
    reg = s3geo.registry()

    assert isinstance(reg, CapabilityRegistry)
    manual_reg = CapabilityRegistry.from_plugin_modules(tolerant=True)
    assert set(reg._descriptors) == set(manual_reg._descriptors)  # noqa: SLF001

    # A resolved capability is directly usable, without ever importing
    # orchestrator - this is the "control the modules via s3geo" path.
    binding = reg.resolve("buffer_vector_features")
    assert callable(binding.callable)


def test_manual_pipeline_is_reachable_through_s3geo_names_only(static_llm):
    # Same manual path _run_manual_path exercises, but spelled entirely
    # through s3geo.<Name> - proving s3geo's re-exports are sufficient on
    # their own for the "advanced control" use case, not just query().
    query_spec = s3geo.LLMQuerySpecGenerator(s3geo.OpenAICompatibleLLMClient()).generate(
        RAW_QUERY
    )
    plan = s3geo.DeterministicPlanner().build(query_spec)
    reg = s3geo.registry()
    executor = s3geo.DagExecutor(s3geo.RegistryCapabilityResolver(reg))
    dag_result = executor.execute(plan, initial_inputs={"sites": SITES})

    assert dag_result.success, dag_result.error
    assert query_spec.goal == "buffer_sites"


def test_query_raises_planning_error_unchanged(monkeypatch):
    llm_json = {
        "raw_query": RAW_QUERY,
        "goal": "unsupported",
        "entities": [{"ref": "sites", "kind": "vector"}],
        "operations": [
            {
                "op": "not_a_real_operation",
                "inputs": {"vector": "sites"},
                "params": {},
                "output": "out",
            }
        ],
        "outputs": [{"kind": "vector_layer", "source": "out", "config": {}}],
    }

    def _factory(*args, **kwargs):
        return StaticLLMClient(json.dumps(llm_json, ensure_ascii=False))

    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", _factory)

    with pytest.raises(PlanningError):
        s3geo.query(RAW_QUERY, layers={"sites": SITES})
