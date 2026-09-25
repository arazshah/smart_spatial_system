"""
Regression test: the planner had no operation for polygon area/perimeter or
for summarizing feature attributes.

calculate_area_perimeter and calculate_attribute_statistics are registered
plugin capabilities, but OP_CATALOG mapped no logical op to them, so a
question like "how large is the largest water body?" or "total area per
class" could not be expressed as a plan at all: after raster_to_vector the
LLM had nowhere to go (it tried summarize_vector, which returns a
feature-count summary, or invented an op name that validation rejected).
Found in the Lake Urmia case study.

Run:
    pytest tests/test_op_catalog_vector_metrics.py -v
"""

from __future__ import annotations

import json

import pytest

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.llm_spec_generator import LLMQuerySpecGenerator, StaticLLMClient
from orchestrator.planning.op_catalog import get_op
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig

RASTER = {
    "data": [[1, 1, 0, 2],
             [0, 0, 0, 2],
             [1, 0, 0, 0]],
    "metadata": {"transform": [10.0, 0.0, 500000.0, 0.0, -10.0, 4200000.0], "crs": "EPSG:32638"},
}


def test_ops_are_catalogued():
    assert get_op("area_perimeter").capability_name == "calculate_area_perimeter"
    assert get_op("attribute_statistics").capability_name == "calculate_attribute_statistics"


def test_polygonize_measure_and_total_area_per_class():
    plan = {
        "raw_query": "area of each class",
        "goal": "area_per_class",
        "entities": [{"ref": "classes", "kind": "raster"}],
        "operations": [
            {"op": "raster_to_vector", "inputs": {"raster": "classes"},
             "params": {"mode": "components", "include_values": [1, 2]}, "output": "polys"},
            {"op": "area_perimeter", "inputs": {"vector": "polys"}, "params": {}, "output": "measured"},
            {"op": "attribute_statistics", "inputs": {"vector": "measured"},
             "params": {"fields": ["_area"], "group_by": "class_value"}, "output": "per_class"},
        ],
        "outputs": [{"kind": "json", "source": "per_class", "config": {}}],
    }
    spec = LLMQuerySpecGenerator(StaticLLMClient(json.dumps(plan))).generate("area of each class")
    dag = DeterministicPlanner(PlannerConfig(strict_params=True)).build(spec)
    result = DagExecutor(RegistryCapabilityResolver(CapabilityRegistry.from_plugin_modules())).execute(
        dag, initial_inputs={"classes": RASTER})
    assert result.success, result.error

    measured = result.outputs["measured"]
    areas = sorted(f["properties"]["_area"] for f in measured.features)
    # class 1: a 2-cell bar and a single cell; class 2: a 2-cell bar (100 m2 cells)
    assert areas == pytest.approx([100.0, 200.0, 200.0])

    per_class = {f["properties"]["_group_value"]: f["properties"] for f in result.outputs["per_class"].features}
    assert {k: v["_sum"] for k, v in per_class.items()} == {1: pytest.approx(300.0), 2: pytest.approx(200.0)}
    assert per_class[1]["_count"] == 2
