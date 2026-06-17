"""
Tests for the extracted natural-query orchestrator.

Run:
    pytest tests/test_orchestrator_natural_query_pipeline.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_router import SimpleCapabilityRouter  # noqa: E402
from orchestrator.natural_query_runner import run_natural_query  # noqa: E402
from orchestrator.plan_builder import SimplePlanBuilder  # noqa: E402
from orchestrator.query_parser import SimpleNaturalLanguageParser  # noqa: E402


SATELLITE_RASTER_2BAND = {
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


def _get_raster_data(result):
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


def test_extracted_parser_extracts_ndvi_threshold_query() -> None:
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


def test_extracted_router_has_required_capabilities() -> None:
    router = SimpleCapabilityRouter()

    assert router.registered_capability_names() == [
        "calculate_spectral_index",
        "raster_to_vector",
        "threshold_raster",
    ]

    assert router.resolve("calculate_spectral_index").plugin_id == "spectral_indices"
    assert router.resolve("threshold_raster").plugin_id == "raster_threshold"
    assert router.resolve("raster_to_vector").plugin_id == "raster_to_vector"


def test_extracted_plan_builder_creates_expected_pipeline() -> None:
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


def test_extracted_orchestrator_runs_natural_query_end_to_end() -> None:
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

    assert response["metadata"]["feature_count"] == 3
    assert response["map"]["layers"][0]["id"] == "vegetation_polygons"
    assert response["map"]["layers"][0]["type"] == "vector"

    assert [item["plugin_id"] for item in response["trace"]] == [
        "spectral_indices",
        "raster_threshold",
        "raster_to_vector",
    ]

    assert "تحلیل انجام شد" in response["answer"]


def test_extracted_parser_rejects_unsupported_intent() -> None:
    parser = SimpleNaturalLanguageParser()

    with pytest.raises(ValueError, match="Only NDVI"):
        parser.parse("نزدیک‌ترین رستوران‌ها را پیدا کن")
