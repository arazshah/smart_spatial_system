"""
Tests for Upload API and raster_ref query flow.

Run:
    pytest tests/test_api_uploads.py -v
"""

from __future__ import annotations

import json
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
            uploads_path=tmp_path / "uploads",
            persist_outputs=True,
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )

    app = create_app(service=service)
    return TestClient(app)


def test_api_upload_raster_json_and_query_by_ref(tmp_path: Path) -> None:
    client = _client(tmp_path)

    upload_response = client.post(
        "/uploads/raster",
        files={
            "file": (
                "sample_raster.json",
                json.dumps(SAMPLE_RASTER).encode("utf-8"),
                "application/json",
            )
        },
    )

    assert upload_response.status_code == 200

    upload = upload_response.json()

    assert upload["upload_id"].startswith("upl-")
    assert upload["parsed_json_available"] is True

    query_response = client.post(
        "/query",
        json={
            "query": NDVI_QUERY,
            "inputs": {
                "raster_ref": upload["upload_id"],
            },
            "band_map": {
                "red": 1,
                "nir": 2,
            },
            "request_id": "req-api-upload-ref-001",
        },
    )

    assert query_response.status_code == 200

    payload = query_response.json()

    assert payload["status"] == "success"
    assert payload["request_id"] == "req-api-upload-ref-001"
    assert "3 عارضه" in payload["answer"] or "3" in payload["answer"]

    map_response = client.get(
        "/requests/req-api-upload-ref-001/map-layers"
    )

    assert map_response.status_code == 200
    assert map_response.json()["layers"][0]["feature_count"] == 3


def test_api_uploads_list_and_metadata_and_file(tmp_path: Path) -> None:
    client = _client(tmp_path)

    upload_response = client.post(
        "/uploads/raster",
        files={
            "file": (
                "sample_raster.json",
                json.dumps(SAMPLE_RASTER).encode("utf-8"),
                "application/json",
            )
        },
    )

    assert upload_response.status_code == 200

    upload = upload_response.json()
    upload_id = upload["upload_id"]

    list_response = client.get("/uploads")

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    metadata_response = client.get(f"/uploads/{upload_id}")

    assert metadata_response.status_code == 200
    assert metadata_response.json()["upload_id"] == upload_id

    file_response = client.get(f"/uploads/{upload_id}/file")

    assert file_response.status_code == 200
    assert file_response.json()["metadata"]["crs"] == "EPSG:3857"


def test_api_upload_rejects_bad_extension(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/uploads/raster",
        files={
            "file": (
                "bad.exe",
                b"bad",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 400


def test_api_query_with_unknown_raster_ref_returns_failed_response(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/query",
        json={
            "query": NDVI_QUERY,
            "inputs": {
                "raster_ref": "upl-missing",
            },
            "band_map": {
                "red": 1,
                "nir": 2,
            },
            "request_id": "req-api-upload-missing-ref",
        },
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "failed"
    assert "raster_ref" in payload["answer"] or payload["warnings"]
