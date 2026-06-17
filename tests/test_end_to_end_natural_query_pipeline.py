"""
End-to-End natural query pipeline test.

This test validates the first real intelligent workflow:

Natural user query
    -> parser
    -> capability router
    -> query plan
    -> executor
    -> real plugins
    -> response with text + map spec + trace

Run:
    pytest tests/test_end_to_end_natural_query_pipeline.py -v
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from plugins.spectral_indices import calculate_spectral_index  # noqa: E402
from plugins.raster_threshold import threshold_raster  # noqa: E402
from plugins.raster_to_vector import raster_to_vector  # noqa: E402


# ---------------------------------------------------------------------
# Test-time models
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class QueryIntent:
    """
    Parsed natural-language intent.
    """

    raw_query: str
    intent_name: str
    index_name: str
    threshold_operator: str
    threshold_value: float
    vectorize: bool
    output_geometry: str


@dataclass(frozen=True)
class CapabilityBinding:
    """
    Router binding between abstract capability name and actual plugin function.
    """

    name: str
    plugin_id: str
    callable: Callable[..., Any]
    output_kind: str
    keywords: list[str]


@dataclass(frozen=True)
class PlanNode:
    """
    One executable DAG node.
    """

    id: str
    capability_name: str
    params: dict[str, Any]
    output_key: str


@dataclass(frozen=True)
class QueryPlan:
    """
    Simple linear query plan for the first E2E workflow.

    Later this can become a real DAG with dependencies and parallel execution.
    """

    intent: QueryIntent
    nodes: list[PlanNode]


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------


class SimpleNaturalLanguageParser:
    """
    Minimal deterministic parser for the first E2E test.

    It intentionally does not use LLM.
    """

    def parse(self, query: str) -> QueryIntent:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string.")

        normalized = query.strip().lower()

        if "ndvi" not in normalized and "پوشش گیاهی" not in normalized:
            raise ValueError("Only NDVI vegetation extraction query is supported in this E2E test.")

        threshold = self._extract_threshold(normalized)

        vectorize = any(
            token in normalized
            for token in [
                "پلیگون",
                "polygon",
                "vector",
                "وکتور",
                "تبدیل کن",
                "استخراج کن",
            ]
        )

        return QueryIntent(
            raw_query=query,
            intent_name="extract_vegetation_polygons_from_ndvi_threshold",
            index_name="ndvi",
            threshold_operator="gt",
            threshold_value=threshold,
            vectorize=vectorize,
            output_geometry="polygon" if vectorize else "raster_mask",
        )

    @staticmethod
    def _extract_threshold(query: str) -> float:
        """
        Extract threshold from Persian/English query.

        Supported examples:
            بیشتر از 0.3
            بالاتر از 0.3
            greater than 0.3
            > 0.3
        """
        patterns = [
            r"بیشتر\s+از\s+([0-9]+(?:\.[0-9]+)?)",
            r"بالاتر\s+از\s+([0-9]+(?:\.[0-9]+)?)",
            r"greater\s+than\s+([0-9]+(?:\.[0-9]+)?)",
            r">\s*([0-9]+(?:\.[0-9]+)?)",
        ]

        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                return float(match.group(1))

        # Conservative default for vegetation extraction.
        return 0.3


# ---------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------


class SimpleCapabilityRouter:
    """
    Minimal capability router.

    This router maps abstract operation names to real plugin functions.
    Later it should be replaced by the actual registry-backed router.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, CapabilityBinding] = {
            "calculate_spectral_index": CapabilityBinding(
                name="calculate_spectral_index",
                plugin_id="spectral_indices",
                callable=calculate_spectral_index,
                output_kind="raster",
                keywords=[
                    "ndvi",
                    "spectral index",
                    "شاخص طیفی",
                    "شاخص پوشش گیاهی",
                ],
            ),
            "threshold_raster": CapabilityBinding(
                name="threshold_raster",
                plugin_id="raster_threshold",
                callable=threshold_raster,
                output_kind="raster",
                keywords=[
                    "threshold",
                    "raster mask",
                    "binary mask",
                    "آستانه",
                    "ماسک",
                ],
            ),
            "raster_to_vector": CapabilityBinding(
                name="raster_to_vector",
                plugin_id="raster_to_vector",
                callable=raster_to_vector,
                output_kind="vector",
                keywords=[
                    "polygon",
                    "vectorize",
                    "raster to vector",
                    "پلیگون",
                    "وکتور",
                ],
            ),
        }

    def resolve(self, capability_name: str) -> CapabilityBinding:
        if capability_name not in self._bindings:
            raise ValueError(f"Capability '{capability_name}' is not registered in test router.")
        return self._bindings[capability_name]

    def registered_capability_names(self) -> list[str]:
        return sorted(self._bindings.keys())


# ---------------------------------------------------------------------
# Plan Builder
# ---------------------------------------------------------------------


class SimplePlanBuilder:
    """
    Builds the first real workflow plan:

        raster
        -> NDVI
        -> threshold mask
        -> polygon features
    """

    def __init__(self, router: SimpleCapabilityRouter) -> None:
        self.router = router

    def build(
        self,
        intent: QueryIntent,
        *,
        band_map: dict[str, int],
    ) -> QueryPlan:
        if intent.intent_name != "extract_vegetation_polygons_from_ndvi_threshold":
            raise ValueError(f"Unsupported intent: {intent.intent_name}")

        # Validate that required capabilities exist.
        self.router.resolve("calculate_spectral_index")
        self.router.resolve("threshold_raster")
        self.router.resolve("raster_to_vector")

        nodes = [
            PlanNode(
                id="node_001_calculate_ndvi",
                capability_name="calculate_spectral_index",
                output_key="ndvi_raster",
                params={
                    "raster": "$inputs.raster",
                    "index_name": intent.index_name,
                    "band_map": band_map,
                    "nodata": -9999,
                    "output_nodata": -9999,
                    "precision": 3,
                    "metadata": {
                        "pipeline_node": "node_001_calculate_ndvi",
                    },
                },
            ),
            PlanNode(
                id="node_002_threshold_ndvi",
                capability_name="threshold_raster",
                output_key="vegetation_mask",
                params={
                    "raster": "$outputs.ndvi_raster",
                    "operator": intent.threshold_operator,
                    "threshold": intent.threshold_value,
                    "true_value": 1,
                    "false_value": 0,
                    "output_nodata": -9999,
                    "precision": 3,
                    "metadata": {
                        "pipeline_node": "node_002_threshold_ndvi",
                    },
                },
            ),
            PlanNode(
                id="node_003_polygonize_mask",
                capability_name="raster_to_vector",
                output_key="vegetation_polygons",
                params={
                    "raster": "$outputs.vegetation_mask",
                    "include_values": [1],
                    "mode": "cells",
                    "precision": 3,
                    "metadata": {
                        "pipeline_node": "node_003_polygonize_mask",
                    },
                },
            ),
        ]

        return QueryPlan(
            intent=intent,
            nodes=nodes,
        )


# ---------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------


class SimplePipelineExecutor:
    """
    Executes a simple linear query plan.

    It calls real plugin functions.
    """

    def __init__(self, router: SimpleCapabilityRouter) -> None:
        self.router = router

    def execute(
        self,
        plan: QueryPlan,
        *,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []

        for order, node in enumerate(plan.nodes, start=1):
            binding = self.router.resolve(node.capability_name)

            resolved_params = {
                key: self._resolve_reference(value, inputs=inputs, outputs=outputs)
                for key, value in node.params.items()
            }

            result = binding.callable(**resolved_params)
            outputs[node.output_key] = result

            trace.append(
                {
                    "order": order,
                    "node_id": node.id,
                    "capability_name": node.capability_name,
                    "plugin_id": binding.plugin_id,
                    "output_key": node.output_key,
                    "output_kind": binding.output_kind,
                    "status": "success",
                }
            )

        return {
            "status": "success",
            "intent": plan.intent,
            "plan": plan,
            "outputs": outputs,
            "trace": trace,
        }

    @staticmethod
    def _resolve_reference(
        value: Any,
        *,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> Any:
        if isinstance(value, str):
            if value.startswith("$inputs."):
                key = value.replace("$inputs.", "", 1)
                return inputs[key]

            if value.startswith("$outputs."):
                key = value.replace("$outputs.", "", 1)
                return outputs[key]

        return value


# ---------------------------------------------------------------------
# Response Builder
# ---------------------------------------------------------------------


class SimpleResponseBuilder:
    """
    Builds user-facing response from pipeline outputs.
    """

    def build(self, execution_result: dict[str, Any]) -> dict[str, Any]:
        outputs = execution_result["outputs"]
        trace = execution_result["trace"]
        intent: QueryIntent = execution_result["intent"]

        vector = outputs["vegetation_polygons"]
        features = vector["features"]
        vector_metadata = vector["metadata"]

        feature_count = len(features)

        return {
            "status": "success",
            "answer": (
                f"تحلیل انجام شد. شاخص NDVI محاسبه شد، سپس پیکسل‌های با مقدار "
                f"بیشتر از {intent.threshold_value} استخراج شدند و به {feature_count} "
                f"پلیگون تبدیل شدند."
            ),
            "map": {
                "layers": [
                    {
                        "id": "vegetation_polygons",
                        "title": "Vegetation polygons from NDVI threshold",
                        "type": "vector",
                        "source_kind": "inline_geojson",
                        "feature_count": feature_count,
                        "data": vector,
                        "style": {
                            "fillColor": "#22c55e",
                            "fillOpacity": 0.45,
                            "strokeColor": "#166534",
                            "strokeWidth": 1,
                        },
                    }
                ],
                "viewport": {
                    "strategy": "fit_layers",
                    "layer_ids": ["vegetation_polygons"],
                },
            },
            "artifacts": [
                {
                    "id": "ndvi_raster",
                    "kind": "raster",
                    "description": "Calculated NDVI raster.",
                },
                {
                    "id": "vegetation_mask",
                    "kind": "raster",
                    "description": "Binary vegetation mask from NDVI threshold.",
                },
                {
                    "id": "vegetation_polygons",
                    "kind": "vector",
                    "description": "Vector polygons generated from vegetation mask.",
                    "feature_count": feature_count,
                },
            ],
            "metadata": {
                "intent_name": intent.intent_name,
                "final_output": "vegetation_polygons",
                "feature_count": feature_count,
                "selected_pixel_count": vector_metadata["selected_pixel_count"],
            },
            "trace": trace,
        }


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _get_raster_data(result: Any) -> Any:
    if hasattr(result, "data"):
        return result.data
    if hasattr(result, "array"):
        return result.array
    if hasattr(result, "payload"):
        return result.payload
    if isinstance(result, dict):
        if "data" in result:
            return result["data"]
        if "array" in result:
            return result["array"]
    raise AssertionError("Raster output has no data/array/payload.")


def run_natural_query(
    query: str,
    *,
    inputs: dict[str, Any],
    band_map: dict[str, int],
) -> dict[str, Any]:
    parser = SimpleNaturalLanguageParser()
    router = SimpleCapabilityRouter()
    planner = SimplePlanBuilder(router)
    executor = SimplePipelineExecutor(router)
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


# ---------------------------------------------------------------------
# E2E Test Data
# ---------------------------------------------------------------------


SATELLITE_RASTER_2BAND = {
    # Band-first raster:
    # band 1 = red
    # band 2 = nir
    #
    # NDVI = (nir - red) / (nir + red)
    #
    # red:
    #   1 1 1
    #   1 1 1
    #
    # nir:
    #   2   1   4
    #   1   3   0.5
    #
    # ndvi:
    #   0.333  0.000  0.600
    #   0.000  0.500 -0.333
    #
    # threshold > 0.3:
    #   1 0 1
    #   0 1 0
    "data": [
        [
            [1, 1, 1],
            [1, 1, 1],
        ],
        [
            [2, 1, 4],
            [1, 3, 0.5],
        ],
    ],
    "metadata": {
        "transform": [10, 0, 100, 0, -10, 200],
        "crs": "EPSG:3857",
        "nodata": -9999,
    },
}


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_parser_extracts_ndvi_threshold_query() -> None:
    parser = SimpleNaturalLanguageParser()

    intent = parser.parse(
        "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است را به پلیگون تبدیل کن"
    )

    assert intent.intent_name == "extract_vegetation_polygons_from_ndvi_threshold"
    assert intent.index_name == "ndvi"
    assert intent.threshold_operator == "gt"
    assert intent.threshold_value == 0.3
    assert intent.vectorize is True
    assert intent.output_geometry == "polygon"


def test_router_has_required_capabilities() -> None:
    router = SimpleCapabilityRouter()

    assert router.registered_capability_names() == [
        "calculate_spectral_index",
        "raster_to_vector",
        "threshold_raster",
    ]

    assert router.resolve("calculate_spectral_index").plugin_id == "spectral_indices"
    assert router.resolve("threshold_raster").plugin_id == "raster_threshold"
    assert router.resolve("raster_to_vector").plugin_id == "raster_to_vector"


def test_plan_builder_creates_expected_pipeline() -> None:
    parser = SimpleNaturalLanguageParser()
    router = SimpleCapabilityRouter()
    planner = SimplePlanBuilder(router)

    intent = parser.parse(
        "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است را به پلیگون تبدیل کن"
    )

    plan = planner.build(
        intent,
        band_map={
            "red": 1,
            "nir": 2,
        },
    )

    assert [node.capability_name for node in plan.nodes] == [
        "calculate_spectral_index",
        "threshold_raster",
        "raster_to_vector",
    ]

    assert [node.output_key for node in plan.nodes] == [
        "ndvi_raster",
        "vegetation_mask",
        "vegetation_polygons",
    ]


def test_end_to_end_natural_query_ndvi_threshold_to_polygons() -> None:
    result = run_natural_query(
        "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است را به پلیگون تبدیل کن",
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
    )

    execution = result["execution"]
    response = result["response"]

    assert execution["status"] == "success"
    assert response["status"] == "success"

    outputs = execution["outputs"]

    ndvi_data = _get_raster_data(outputs["ndvi_raster"])
    mask_data = _get_raster_data(outputs["vegetation_mask"])
    vector = outputs["vegetation_polygons"]

    assert ndvi_data == [
        [0.333, 0.0, 0.6],
        [0.0, 0.5, -0.333],
    ]

    assert mask_data == [
        [1, 0, 1],
        [0, 1, 0],
    ]

    assert vector["type"] == "FeatureCollection"
    assert len(vector["features"]) == 3

    assert vector["metadata"]["operation"] == "raster_to_vector"
    assert vector["metadata"]["selected_pixel_count"] == 3
    assert vector["metadata"]["feature_count"] == 3

    assert response["metadata"]["intent_name"] == "extract_vegetation_polygons_from_ndvi_threshold"
    assert response["metadata"]["final_output"] == "vegetation_polygons"
    assert response["metadata"]["feature_count"] == 3

    assert response["map"]["layers"][0]["id"] == "vegetation_polygons"
    assert response["map"]["layers"][0]["type"] == "vector"
    assert response["map"]["layers"][0]["feature_count"] == 3

    assert len(response["trace"]) == 3
    assert [item["plugin_id"] for item in response["trace"]] == [
        "spectral_indices",
        "raster_threshold",
        "raster_to_vector",
    ]

    assert "تحلیل انجام شد" in response["answer"]
    assert "3" in response["answer"]


def test_end_to_end_query_rejects_unsupported_intent() -> None:
    parser = SimpleNaturalLanguageParser()

    with pytest.raises(ValueError, match="Only NDVI"):
        parser.parse("نزدیک‌ترین رستوران‌ها را پیدا کن")
