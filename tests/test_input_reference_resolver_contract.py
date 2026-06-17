"""
Integration tests for UploadReferenceResolver with loader plugin contract.

Run:
    pytest tests/test_input_reference_resolver_contract.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.input_reference_resolver import (  # noqa: E402
    UploadReferenceResolver,
    UploadReferenceResolverConfig,
)
from orchestrator.upload_storage import UploadStorage, UploadStorageConfig  # noqa: E402


SAMPLE_RASTER = {
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


def test_resolver_loads_tif_through_contract_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_contract_resolver_raster_loader")

    def load_local_raster(path: str, options: dict | None = None) -> dict:
        assert path.endswith(".tif")
        assert "upload_metadata" in options
        return SAMPLE_RASTER

    module.load_local_raster = load_local_raster
    monkeypatch.setitem(sys.modules, "fake_contract_resolver_raster_loader", module)

    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    upload = storage.save_upload(
        filename="image.tif",
        content=b"fake-tiff",
        content_type="image/tiff",
        kind="raster",
    )

    resolver = UploadReferenceResolver(
        storage,
        UploadReferenceResolverConfig(
            raster_loader_plugin_module="fake_contract_resolver_raster_loader",
            vector_loader_plugin_module="fake_contract_resolver_vector_loader",
            enforce_loader_contract=True,
            allow_adaptive_loader_fallback=False,
        ),
    )

    resolved = resolver.resolve_inputs(
        {
            "raster_ref": upload["upload_id"],
        }
    )

    assert resolved["raster"]["metadata"]["crs"] == "EPSG:3857"
    assert (
        resolved["raster"]["metadata"]["loader_plugin_module"]
        == "fake_contract_resolver_raster_loader"
    )


def test_resolver_loads_gpkg_through_contract_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_contract_resolver_vector_loader")

    def load_local_vector(path: str, options: dict | None = None) -> dict:
        assert path.endswith(".gpkg")
        assert "upload_metadata" in options
        return SAMPLE_VECTOR

    module.load_local_vector = load_local_vector
    monkeypatch.setitem(sys.modules, "fake_contract_resolver_vector_loader", module)

    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    upload = storage.save_upload(
        filename="layer.gpkg",
        content=b"fake-gpkg",
        content_type="application/geopackage+sqlite3",
        kind="vector",
    )

    resolver = UploadReferenceResolver(
        storage,
        UploadReferenceResolverConfig(
            raster_loader_plugin_module="fake_contract_resolver_raster_loader",
            vector_loader_plugin_module="fake_contract_resolver_vector_loader",
            enforce_loader_contract=True,
            allow_adaptive_loader_fallback=False,
        ),
    )

    resolved = resolver.resolve_inputs(
        {
            "vector_ref": upload["upload_id"],
        }
    )

    assert resolved["vector"]["type"] == "FeatureCollection"
    assert len(resolved["vector"]["features"]) == 1
    assert (
        resolved["vector"]["metadata"]["loader_plugin_module"]
        == "fake_contract_resolver_vector_loader"
    )
