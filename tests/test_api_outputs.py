"""
Tests for output file API.

Run:
    pytest tests/test_api_outputs.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from api.main import create_app  # noqa: E402
from orchestrator.service import (  # noqa: E402
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
)


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


NDVI_QUERY = (
    "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است "
    "را به پلیگون تبدیل کن"
)


def _client(tmp_path: Path) -> TestClient:
    service = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            outputs_path=tmp_path / "outputs",
            persist_outputs=True,
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )

    app = create_app(service=service)

    return TestClient(app)


def _run_query(client: TestClient, request_id: str) -> None:
    response = client.post(
        "/query",
        json={
            "query": NDVI_QUERY,
            "inputs": {
                "raster": SATELLITE_RASTER_2BAND,
            },
            "band_map": {
                "red": 1,
                "nir": 2,
            },
            "request_id": request_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_outputs_manifest_and_files(tmp_path: Path) -> None:
    client = _client(tmp_path)

    _run_query(
        client,
        "req-api-outputs-001",
    )

    manifest_response = client.get(
        "/requests/req-api-outputs-001/outputs"
    )

    assert manifest_response.status_code == 200

    manifest = manifest_response.json()

    assert manifest["request_id"] == "req-api-outputs-001"
    assert manifest["schema_version"] == "1.0.0"

    filenames = {
        item["filename"]
        for item in manifest["files"]
    }

    assert "manifest.json" in filenames
    assert "production_response.json" in filenames
    assert "audit_record.json" in filenames
    assert "outputs_summary.json" in filenames
    assert "map_layers.json" in filenames
    assert "vegetation_polygons.geojson" in filenames

    files_response = client.get(
        "/requests/req-api-outputs-001/outputs/files"
    )

    assert files_response.status_code == 200
    assert len(files_response.json()) >= 1


def test_api_download_geojson_file(tmp_path: Path) -> None:
    client = _client(tmp_path)

    _run_query(
        client,
        "req-api-outputs-geojson-001",
    )

    response = client.get(
        "/requests/req-api-outputs-geojson-001/outputs/files/vegetation_polygons.geojson"
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 3


def test_api_save_outputs_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)

    _run_query(
        client,
        "req-api-outputs-save-001",
    )

    response = client.post(
        "/requests/req-api-outputs-save-001/outputs/save"
    )

    assert response.status_code == 200
    assert response.json()["request_id"] == "req-api-outputs-save-001"


def test_api_outputs_unknown_request_or_file_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get(
        "/requests/missing/outputs"
    )

    assert response.status_code == 404

    _run_query(
        client,
        "req-api-outputs-missing-file-001",
    )

    file_response = client.get(
        "/requests/req-api-outputs-missing-file-001/outputs/files/missing.geojson"
    )

    assert file_response.status_code == 404
