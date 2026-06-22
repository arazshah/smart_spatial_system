"""
Tests for registry-backed capability router.

Run:
    pytest tests/test_orchestrator_registry_router.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import (  # noqa: E402
    CapabilityRegistry,
    RegistryBackedCapabilityRouter,
)
from orchestrator.natural_query_runner import run_natural_query  # noqa: E402


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


def test_registry_discovers_safe_plugin_capabilities() -> None:
    registry = CapabilityRegistry.from_plugin_modules(
        [
            "plugins.spectral_indices",
            "plugins.raster_threshold",
            "plugins.raster_to_vector",
        ]
    )

    assert registry.registered_plugin_ids() == [
        "raster_threshold",
        "raster_to_vector",
        "spectral_indices",
    ]

    assert registry.registered_capability_names() == [
        "calculate_spectral_index",
        "raster_to_vector",
        "threshold_raster",
    ]


def test_registry_resolves_bindings() -> None:
    registry = CapabilityRegistry.from_plugin_modules(
        [
            "plugins.spectral_indices",
            "plugins.raster_threshold",
            "plugins.raster_to_vector",
        ]
    )

    spectral = registry.resolve("calculate_spectral_index")
    threshold = registry.resolve("threshold_raster")
    polygonize = registry.resolve("raster_to_vector")

    assert spectral.plugin_id == "spectral_indices"
    assert spectral.output_kind == "raster"
    assert callable(spectral.callable)

    assert threshold.plugin_id == "raster_threshold"
    assert threshold.output_kind == "raster"
    assert callable(threshold.callable)

    assert polygonize.plugin_id == "raster_to_vector"
    assert polygonize.output_kind == "vector"
    assert callable(polygonize.callable)


def test_registry_exposes_descriptors() -> None:
    registry = CapabilityRegistry.from_plugin_modules(
        [
            "plugins.spectral_indices",
            "plugins.raster_threshold",
            "plugins.raster_to_vector",
        ]
    )

    descriptor = registry.descriptor_for("threshold_raster")

    assert descriptor.name == "threshold_raster"
    assert descriptor.plugin_id == "raster_threshold"
    assert descriptor.output_kind == "raster"
    assert "raster" in descriptor.required_inputs
    assert descriptor.metadata["operation"] == "raster_threshold"
    assert descriptor.metadata["routable"] is True


def test_registry_debug_inventory() -> None:
    registry = CapabilityRegistry.from_plugin_modules(
        [
            "plugins.spectral_indices",
            "plugins.raster_threshold",
            "plugins.raster_to_vector",
        ]
    )

    inventory = registry.as_debug_inventory()

    assert len(inventory) == 3

    by_name = {row["capability_name"]: row for row in inventory}

    assert by_name["calculate_spectral_index"]["plugin_id"] == "spectral_indices"
    assert by_name["threshold_raster"]["plugin_id"] == "raster_threshold"
    assert by_name["raster_to_vector"]["plugin_id"] == "raster_to_vector"

    assert by_name["threshold_raster"]["metadata"]["routable"] is True


def test_registry_backed_router_runs_natural_query_end_to_end() -> None:
    router = RegistryBackedCapabilityRouter(
        plugin_module_names=[
            "plugins.spectral_indices",
            "plugins.raster_threshold",
            "plugins.raster_to_vector",
        ]
    )

    result = run_natural_query(
        "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است را به پلیگون تبدیل کن",
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        router=router,
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

    assert [item["plugin_id"] for item in response["trace"]] == [
        "spectral_indices",
        "raster_threshold",
        "raster_to_vector",
    ]


def test_registry_rejects_missing_capability() -> None:
    registry = CapabilityRegistry.from_plugin_modules(
        [
            "plugins.spectral_indices",
        ]
    )

    with pytest.raises(ValueError, match="not registered"):
        registry.resolve("missing_capability")


def test_capability_registry_tolerant_skipped_plugin_has_structured_error() -> None:
    from orchestrator.capability_registry import CapabilityRegistry

    registry = CapabilityRegistry.from_plugin_modules(
        ["plugins.__definitely_missing_plugin_for_structured_error_test__"],
        tolerant=True,
    )

    assert registry.registered_capability_names() == []
    assert len(registry.skipped_plugins) == 1

    skipped = registry.skipped_plugins[0]

    assert skipped["module"] == "plugins.__definitely_missing_plugin_for_structured_error_test__"
    assert "ModuleNotFoundError" in skipped["error"]
    assert "structured_error" in skipped

    structured_error = skipped["structured_error"]

    assert structured_error["code"] == "plugin.import_failed"
    assert structured_error["category"] == "configuration_error"
    assert structured_error["retryable"] is False
    assert structured_error["source"] == "capability_registry"
    assert structured_error["details"]["module"] == (
        "plugins.__definitely_missing_plugin_for_structured_error_test__"
    )
    assert structured_error["details"]["stage"] == "plugin_import_or_registration"
    assert structured_error["details"]["exception_type"] == "ModuleNotFoundError"


def test_capability_registry_non_tolerant_import_failure_still_raises() -> None:
    import pytest

    from orchestrator.capability_registry import CapabilityRegistry

    with pytest.raises(ModuleNotFoundError):
        CapabilityRegistry.from_plugin_modules(
            ["plugins.__definitely_missing_plugin_for_non_tolerant_test__"],
            tolerant=False,
        )
