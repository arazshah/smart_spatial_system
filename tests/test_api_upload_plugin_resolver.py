"""
Tests for plugin-based upload resolver through API.

Run:
    pytest tests/test_api_upload_plugin_resolver.py -v
"""

from __future__ import annotations

import json
import sys
import types
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


NDVI_QUERY = (
    "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است "
    "را به پلیگون تبدیل کن"
)


def _client(
    tmp_path: Path,
    *,
    raster_loader_plugin_module: str = "plugins.local_raster_loader",
    vector_loader_plugin_module: str = "plugins.local_vector_loader",
) -> TestClient:
    service = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            outputs_path=tmp_path / "outputs",
            uploads_path=tmp_path / "uploads",
            persist_outputs=True,
            use_weighted_router=True,
            load_persisted_weights=True,
            raster_loader_plugin_module=raster_loader_plugin_module,
            vector_loader_plugin_module=vector_loader_plugin_module,
        )
    )

    app = create_app(service=service)
    return TestClient(app)


def test_api_upload_vector_geojson(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/uploads/vector",
        files={
            "file": (
                "points.geojson",
                json.dumps(SAMPLE_VECTOR).encode("utf-8"),
                "application/geo+json",
            )
        },
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["kind"] == "vector"
    assert payload["filename"] == "points.geojson"
    assert payload["parsed_json_available"] is True

    list_response = client.get("/uploads")

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_api_query_raster_tiff_ref_uses_loader_plugin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = types.ModuleType("fake_api_raster_loader_plugin")

    def load_local_raster(path: str):
        assert path.endswith(".tif")
        return SAMPLE_RASTER

    module.load_local_raster = load_local_raster
    monkeypatch.setitem(sys.modules, "fake_api_raster_loader_plugin", module)

    client = _client(
        tmp_path,
        raster_loader_plugin_module="fake_api_raster_loader_plugin",
    )

    upload_response = client.post(
        "/uploads/raster",
        files={
            "file": (
                "image.tif",
                b"fake-tiff-bytes",
                "image/tiff",
            )
        },
    )

    assert upload_response.status_code == 200

    upload_id = upload_response.json()["upload_id"]

    query_response = client.post(
        "/query",
        json={
            "query": NDVI_QUERY,
            "inputs": {
                "raster_ref": upload_id,
            },
            "band_map": {
                "red": 1,
                "nir": 2,
            },
            "request_id": "req-api-plugin-raster-001",
        },
    )

    assert query_response.status_code == 200

    payload = query_response.json()

    assert payload["status"] == "success"
    assert payload["request_id"] == "req-api-plugin-raster-001"

    map_response = client.get(
        "/requests/req-api-plugin-raster-001/map-layers"
    )

    assert map_response.status_code == 200
    assert map_response.json()["layers"][0]["feature_count"] == 3
