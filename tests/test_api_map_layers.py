"""
Tests for map layers API.

Run:
    pytest tests/test_api_map_layers.py -v
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
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )

    app = create_app(service=service)

    return TestClient(app)


def test_api_map_layers_returns_leaflet_ready_geojson(tmp_path: Path) -> None:
    client = _client(tmp_path)

    query_response = client.post(
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
            "request_id": "req-api-map-layers-001",
        },
    )

    assert query_response.status_code == 200
    assert query_response.json()["status"] == "success"

    map_response = client.get(
        "/requests/req-api-map-layers-001/map-layers"
    )

    assert map_response.status_code == 200

    payload = map_response.json()

    assert payload["request_id"] == "req-api-map-layers-001"
    assert payload["layer_count"] >= 1

    layer = payload["layers"][0]

    assert layer["name"] == "vegetation_polygons"
    assert layer["kind"] == "vector"
    assert layer["crs"] == "EPSG:4326"
    assert layer["feature_count"] == 3
    assert layer["geojson"]["type"] == "FeatureCollection"
    assert len(layer["geojson"]["features"]) == 3


def test_api_map_layers_unknown_request_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/requests/missing/map-layers")

    assert response.status_code == 404
