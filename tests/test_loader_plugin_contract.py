"""
Tests for loader plugin contract.

Run:
    pytest tests/test_loader_plugin_contract.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.loader_plugin_contract import (  # noqa: E402
    LOADER_PLUGIN_CONTRACT_VERSION,
    LoaderPluginContractError,
    load_with_loader_contract,
    normalize_raster_loader_output,
    normalize_vector_loader_output,
)


SAMPLE_RASTER = {
    "data": [
        [
            [1, 1],
            [1, 1],
        ],
        [
            [2, 3],
            [4, 5],
        ],
    ],
    "metadata": {
        "crs": "EPSG:3857",
        "transform": [10, 0, 100, 0, -10, 200],
    },
}


SAMPLE_VECTOR = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "id": 1,
            },
            "geometry": {
                "type": "Point",
                "coordinates": [51.0, 35.0],
            },
        }
    ],
}


def test_load_raster_with_canonical_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("fake_contract_raster_loader")

    def load_local_raster(path: str, options: dict | None = None) -> dict:
        assert path.endswith(".tif")
        assert options["band_map"]["red"] == 1
        return SAMPLE_RASTER

    module.load_local_raster = load_local_raster
    monkeypatch.setitem(sys.modules, "fake_contract_raster_loader", module)

    result = load_with_loader_contract(
        module_name="fake_contract_raster_loader",
        kind="raster",
        file_path="/tmp/image.tif",
        options={
            "band_map": {
                "red": 1,
                "nir": 2,
            }
        },
    )

    assert result["data"]
    assert result["metadata"]["crs"] == "EPSG:3857"
    assert result["metadata"]["loader_contract_version"] == LOADER_PLUGIN_CONTRACT_VERSION
    assert result["metadata"]["loader_plugin_module"] == "fake_contract_raster_loader"


def test_load_vector_with_canonical_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("fake_contract_vector_loader")

    def load_local_vector(path: str, options: dict | None = None) -> dict:
        assert path.endswith(".geojson")
        return SAMPLE_VECTOR

    module.load_local_vector = load_local_vector
    monkeypatch.setitem(sys.modules, "fake_contract_vector_loader", module)

    result = load_with_loader_contract(
        module_name="fake_contract_vector_loader",
        kind="vector",
        file_path="/tmp/layer.geojson",
    )

    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 1
    assert result["metadata"]["loader_contract_version"] == LOADER_PLUGIN_CONTRACT_VERSION
    assert result["metadata"]["loader_plugin_module"] == "fake_contract_vector_loader"


def test_contract_supports_transitional_path_only_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_path_only_raster_loader")

    def load_local_raster(path: str) -> dict:
        assert path.endswith(".tif")
        return SAMPLE_RASTER

    module.load_local_raster = load_local_raster
    monkeypatch.setitem(sys.modules, "fake_path_only_raster_loader", module)

    result = load_with_loader_contract(
        module_name="fake_path_only_raster_loader",
        kind="raster",
        file_path="/tmp/image.tif",
    )

    assert result["metadata"]["crs"] == "EPSG:3857"


def test_contract_rejects_missing_canonical_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_bad_loader")
    monkeypatch.setitem(sys.modules, "fake_bad_loader", module)

    with pytest.raises(LoaderPluginContractError, match="load_local_raster"):
        load_with_loader_contract(
            module_name="fake_bad_loader",
            kind="raster",
            file_path="/tmp/image.tif",
        )


def test_contract_rejects_invalid_raster_output() -> None:
    with pytest.raises(LoaderPluginContractError, match="data"):
        normalize_raster_loader_output(
            {
                "metadata": {},
            }
        )


def test_contract_rejects_invalid_vector_output() -> None:
    with pytest.raises(LoaderPluginContractError, match="FeatureCollection"):
        normalize_vector_loader_output(
            {
                "type": "Point",
                "coordinates": [1, 2],
            }
        )


def test_contract_normalizes_wrapped_outputs() -> None:
    raster = normalize_raster_loader_output(
        {
            "raster": SAMPLE_RASTER,
        }
    )

    assert raster["metadata"]["crs"] == "EPSG:3857"

    vector = normalize_vector_loader_output(
        {
            "geojson": SAMPLE_VECTOR,
        }
    )

    assert vector["type"] == "FeatureCollection"
    assert len(vector["features"]) == 1


def test_loader_contract_import_failure_has_structured_error() -> None:
    with pytest.raises(LoaderPluginContractError) as exc_info:
        load_with_loader_contract(
            module_name="__missing_loader_plugin_for_structured_error_test__",
            kind="raster",
            file_path="/tmp/image.tif",
        )

    exc = exc_info.value

    assert hasattr(exc, "structured_error")
    assert exc.structured_error["code"] == "loader.plugin_import_failed"
    assert exc.structured_error["category"] == "configuration_error"
    assert exc.structured_error["source"] == "loader_plugin_contract"
    assert exc.structured_error["details"]["module"] == (
        "__missing_loader_plugin_for_structured_error_test__"
    )
    assert exc.structured_error["details"]["kind"] == "raster"
    assert exc.structured_error["details"]["stage"] == "plugin_import"


def test_loader_contract_missing_callable_has_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_missing_callable_loader")
    monkeypatch.setitem(sys.modules, "fake_missing_callable_loader", module)

    with pytest.raises(LoaderPluginContractError) as exc_info:
        load_with_loader_contract(
            module_name="fake_missing_callable_loader",
            kind="vector",
            file_path="/tmp/layer.geojson",
        )

    exc = exc_info.value

    assert exc.structured_error["code"] == "loader.contract_invalid"
    assert exc.structured_error["category"] == "capability_contract_error"
    assert exc.structured_error["details"]["module"] == "fake_missing_callable_loader"
    assert exc.structured_error["details"]["kind"] == "vector"
    assert exc.structured_error["details"]["function_name"] == "load_local_vector"


def test_loader_contract_invalid_output_has_structured_error() -> None:
    with pytest.raises(LoaderPluginContractError) as exc_info:
        normalize_raster_loader_output(
            {
                "metadata": {},
            },
            source_module="fake_invalid_output_loader",
            source_path="/tmp/image.tif",
        )

    exc = exc_info.value

    assert exc.structured_error["code"] == "loader.output_invalid"
    assert exc.structured_error["category"] == "capability_contract_error"
    assert exc.structured_error["details"]["module"] == "fake_invalid_output_loader"
    assert exc.structured_error["details"]["kind"] == "raster"
    assert exc.structured_error["details"]["stage"] == "normalize_output"


def test_loader_execution_failure_has_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_failing_loader")

    def load_local_raster(path: str, options: dict | None = None) -> dict:
        raise RuntimeError("boom")

    module.load_local_raster = load_local_raster
    monkeypatch.setitem(sys.modules, "fake_failing_loader", module)

    with pytest.raises(LoaderPluginContractError) as exc_info:
        load_with_loader_contract(
            module_name="fake_failing_loader",
            kind="raster",
            file_path="/tmp/image.tif",
        )

    exc = exc_info.value

    assert exc.structured_error["code"] == "loader.execution_failed"
    assert exc.structured_error["category"] == "provider_error"
    assert exc.structured_error["details"]["module"] == "fake_failing_loader"
    assert exc.structured_error["details"]["kind"] == "raster"
    assert exc.structured_error["details"]["function_name"] == "load_local_raster"
