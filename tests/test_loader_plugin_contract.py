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
