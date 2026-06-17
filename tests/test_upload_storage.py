"""
Tests for UploadStorage.

Run:
    pytest tests/test_upload_storage.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.upload_storage import (  # noqa: E402
    UPLOAD_STORAGE_SCHEMA_VERSION,
    UploadStorage,
    UploadStorageConfig,
    UploadStorageError,
)


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


def test_upload_storage_saves_json_upload(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    metadata = storage.save_upload(
        filename="raster.json",
        content=json.dumps(SAMPLE_RASTER).encode("utf-8"),
        content_type="application/json",
        kind="raster",
    )

    assert metadata["schema_version"] == UPLOAD_STORAGE_SCHEMA_VERSION
    assert metadata["upload_id"].startswith("upl-")
    assert metadata["filename"] == "raster.json"
    assert metadata["parsed_json_available"] is True
    assert metadata["size_bytes"] > 0

    loaded = storage.read_json_content(metadata["upload_id"])

    assert loaded["metadata"]["crs"] == "EPSG:3857"


def test_upload_storage_lists_uploads(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    storage.save_upload(
        filename="raster.json",
        content=json.dumps(SAMPLE_RASTER).encode("utf-8"),
        content_type="application/json",
    )

    items = storage.list_uploads()

    assert len(items) == 1
    assert items[0]["filename"] == "raster.json"


def test_upload_storage_rejects_unsupported_extension(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    with pytest.raises(UploadStorageError, match="Unsupported"):
        storage.save_upload(
            filename="bad.exe",
            content=b"bad",
        )


def test_upload_storage_rejects_large_file(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
            max_size_bytes=2,
        )
    )

    with pytest.raises(UploadStorageError, match="too large"):
        storage.save_upload(
            filename="raster.json",
            content=b"{}{}",
        )


def test_upload_storage_rejects_unknown_upload(tmp_path: Path) -> None:
    storage = UploadStorage(
        UploadStorageConfig(
            root_dir=tmp_path / "uploads",
        )
    )

    with pytest.raises(UploadStorageError, match="Unknown upload_id"):
        storage.read_metadata("upl-missing")


def test_upload_storage_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="max_size_bytes"):
        UploadStorageConfig(max_size_bytes=0)

    with pytest.raises(ValueError, match="indent"):
        UploadStorageConfig(indent=-1)

    with pytest.raises(ValueError, match="allowed_extensions"):
        UploadStorageConfig(allowed_extensions=())
