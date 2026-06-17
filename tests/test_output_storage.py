"""
Tests for OutputStorage.

Run:
    pytest tests/test_output_storage.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.output_storage import (  # noqa: E402
    OUTPUT_STORAGE_SCHEMA_VERSION,
    OutputStorage,
    OutputStorageConfig,
    OutputStorageError,
)


def test_output_storage_saves_manifest_and_geojson(tmp_path: Path) -> None:
    storage = OutputStorage(
        OutputStorageConfig(
            root_dir=tmp_path / "outputs",
        )
    )

    record = {
        "request_id": "req-output-001",
        "query": "NDVI",
        "band_map": {
            "red": 1,
            "nir": 2,
        },
        "production_response": {
            "status": "success",
            "query_hash": "hash-001",
            "outputs": {
                "summary": {
                    "vegetation_polygons": {
                        "kind": "vector",
                        "feature_count": 1,
                    }
                }
            },
        },
        "audit_record": {
            "status": "success",
        },
        "run_result": {},
    }

    map_layers = {
        "request_id": "req-output-001",
        "layers": [
            {
                "name": "vegetation_polygons",
                "kind": "vector",
                "crs": "EPSG:4326",
                "feature_count": 1,
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "id": 1,
                            },
                            "geometry": {
                                "type": "Point",
                                "coordinates": [51, 35],
                            },
                        }
                    ],
                },
            }
        ],
    }

    manifest = storage.save_request_record(
        record,
        map_layers_payload=map_layers,
    )

    assert manifest["schema_version"] == OUTPUT_STORAGE_SCHEMA_VERSION
    assert manifest["request_id"] == "req-output-001"

    request_dir = tmp_path / "outputs" / "req-output-001"

    assert (request_dir / "manifest.json").exists()
    assert (request_dir / "production_response.json").exists()
    assert (request_dir / "audit_record.json").exists()
    assert (request_dir / "outputs_summary.json").exists()
    assert (request_dir / "map_layers.json").exists()
    assert (request_dir / "vegetation_polygons.geojson").exists()

    geojson = json.loads(
        (request_dir / "vegetation_polygons.geojson").read_text(
            encoding="utf-8"
        )
    )

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1


def test_output_storage_reads_manifest_and_lists_files(tmp_path: Path) -> None:
    storage = OutputStorage(
        OutputStorageConfig(
            root_dir=tmp_path / "outputs",
        )
    )

    record = {
        "request_id": "req-output-002",
        "production_response": {
            "status": "success",
            "outputs": {
                "summary": {},
            },
        },
        "audit_record": {},
        "run_result": {},
    }

    storage.save_request_record(record)

    manifest = storage.read_manifest("req-output-002")
    files = storage.list_files("req-output-002")

    assert manifest["request_id"] == "req-output-002"
    assert files
    assert any(item["filename"] == "manifest.json" for item in files)


def test_output_storage_rejects_missing_manifest(tmp_path: Path) -> None:
    storage = OutputStorage(
        OutputStorageConfig(
            root_dir=tmp_path / "outputs",
        )
    )

    with pytest.raises(OutputStorageError, match="manifest"):
        storage.read_manifest("missing")


def test_output_storage_prevents_path_traversal(tmp_path: Path) -> None:
    storage = OutputStorage(
        OutputStorageConfig(
            root_dir=tmp_path / "outputs",
        )
    )

    record = {
        "request_id": "req-output-003",
        "production_response": {
            "status": "success",
        },
        "audit_record": {},
        "run_result": {},
    }

    storage.save_request_record(record)

    with pytest.raises(OutputStorageError, match="Invalid"):
        storage.get_file_path(
            "req-output-003",
            "../secret.txt",
        )


def test_output_storage_config_rejects_invalid_indent() -> None:
    with pytest.raises(ValueError, match="indent"):
        OutputStorageConfig(indent=-1)
