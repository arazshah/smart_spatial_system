"""
Tests for UploadReferenceResolver.

Run:
    pytest tests/test_input_reference_resolver.py -v
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.input_reference_resolver import (  # noqa: E402
    UploadReferenceResolver,
    UploadReferenceResolverConfig,
    UploadReferenceResolverError,
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


def test_resolver_uses_json_fallback_for_json_raster(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    upload = storage.save_upload(
        filename="raster.json",
        content=json.dumps(SAMPLE_RASTER).encode("utf-8"),
        content_type="application/json",
        kind="raster",
    )

    resolver = UploadReferenceResolver(storage)

    resolved = resolver.resolve_inputs(
        {
            "raster_ref": upload["upload_id"],
        }
    )

    assert resolved["raster"]["metadata"]["crs"] == "EPSG:3857"


def test_resolver_uses_raster_loader_plugin_for_tiff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_raster_loader_plugin")

    def load_local_raster(path: str):
        assert path.endswith(".tif")
        return SAMPLE_RASTER

    module.load_local_raster = load_local_raster
    monkeypatch.setitem(sys.modules, "fake_raster_loader_plugin", module)

    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    upload = storage.save_upload(
        filename="image.tif",
        content=b"fake-tiff-bytes",
        content_type="image/tiff",
        kind="raster",
    )

    resolver = UploadReferenceResolver(
        storage,
        UploadReferenceResolverConfig(
            raster_loader_plugin_module="fake_raster_loader_plugin",
            vector_loader_plugin_module="fake_vector_loader_plugin",
        ),
    )

    resolved = resolver.resolve_inputs(
        {
            "raster_ref": upload["upload_id"],
        }
    )

    assert resolved["raster"]["metadata"]["crs"] == "EPSG:3857"


def test_resolver_uses_vector_loader_plugin_for_gpkg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_vector_loader_plugin")

    def load_local_vector(file_path: str):
        assert file_path.endswith(".gpkg")
        return SAMPLE_VECTOR

    module.load_local_vector = load_local_vector
    monkeypatch.setitem(sys.modules, "fake_vector_loader_plugin", module)

    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    upload = storage.save_upload(
        filename="layer.gpkg",
        content=b"fake-gpkg-bytes",
        content_type="application/geopackage+sqlite3",
        kind="vector",
    )

    resolver = UploadReferenceResolver(
        storage,
        UploadReferenceResolverConfig(
            raster_loader_plugin_module="fake_raster_loader_plugin",
            vector_loader_plugin_module="fake_vector_loader_plugin",
        ),
    )

    resolved = resolver.resolve_inputs(
        {
            "vector_ref": upload["upload_id"],
        }
    )

    assert resolved["vector"]["type"] == "FeatureCollection"
    assert len(resolved["vector"]["features"]) == 1


def test_resolver_rejects_unknown_ref(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    resolver = UploadReferenceResolver(storage)

    with pytest.raises(UploadReferenceResolverError):
        resolver.resolve_inputs(
            {
                "raster_ref": "upl-missing",
            }
        )


def test_resolver_config_rejects_empty_module() -> None:
    with pytest.raises(ValueError, match="raster_loader_plugin_module"):
        UploadReferenceResolverConfig(
            raster_loader_plugin_module="",
        )

    with pytest.raises(ValueError, match="vector_loader_plugin_module"):
        UploadReferenceResolverConfig(
            vector_loader_plugin_module="",
        )


def test_resolver_invalid_inputs_has_structured_error(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    resolver = UploadReferenceResolver(storage)

    with pytest.raises(UploadReferenceResolverError) as exc_info:
        resolver.resolve_inputs(["not", "a", "dict"])  # type: ignore[arg-type]

    exc = exc_info.value

    assert hasattr(exc, "structured_error")
    assert exc.structured_error["code"] == "input.invalid_payload"
    assert exc.structured_error["category"] == "validation_error"
    assert exc.structured_error["source"] == "input_reference_resolver"
    assert exc.structured_error["details"]["stage"] == "resolve_inputs"


def test_resolver_unknown_ref_has_structured_error(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    resolver = UploadReferenceResolver(storage)

    with pytest.raises(UploadReferenceResolverError) as exc_info:
        resolver.resolve_inputs(
            {
                "raster_ref": "upl-missing",
            }
        )

    exc = exc_info.value

    assert exc.structured_error["code"] == "input.reference_not_found"
    assert exc.structured_error["category"] == "validation_error"
    assert exc.structured_error["details"]["reference_kind"] == "raster"
    assert exc.structured_error["details"]["upload_id"] == "upl-missing"
    assert exc.structured_error["details"]["stage"] == "read_upload_metadata"


def test_resolver_loader_import_failure_has_structured_error(
    tmp_path: Path,
) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    upload = storage.save_upload(
        filename="image.tif",
        content=b"fake-tiff-bytes",
        content_type="image/tiff",
        kind="raster",
    )

    resolver = UploadReferenceResolver(
        storage,
        UploadReferenceResolverConfig(
            raster_loader_plugin_module="__missing_raster_loader_for_structured_error__",
            vector_loader_plugin_module="fake_vector_loader_plugin",
            allow_adaptive_loader_fallback=False,
        ),
    )

    with pytest.raises(UploadReferenceResolverError) as exc_info:
        resolver.resolve_inputs(
            {
                "raster_ref": upload["upload_id"],
            }
        )

    exc = exc_info.value

    assert exc.structured_error["code"] in {
        "input.resolution_failed",
        "loader.plugin_import_failed",
    }
    assert exc.structured_error["source"] in {
        "input_reference_resolver",
        "loader_plugin_contract",
    }
    assert exc.structured_error["details"]["reference_kind"] == "raster"
    assert exc.structured_error["details"]["stage"] in {
        "loader_plugin_import",
        "plugin_import",
    }
