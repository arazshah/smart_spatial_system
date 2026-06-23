"""
End-to-end API contract smoke test for the frontend-facing flow.

This test locks the main product flow documented in Phase 5:

1. create project
2. upload raster attached to project
3. run query using raster_ref and project_id
4. fetch request detail
5. fetch map layers
6. fetch outputs manifest
7. list output files
8. download GeoJSON output file

Run:
    PYTHONPATH=. pytest tests/test_api_contract_smoke.py -q
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
            projects_path=tmp_path / "projects",
            persist_outputs=True,
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )

    app = create_app(service=service)
    return TestClient(app)


def test_frontend_main_api_contract_smoke_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    # 1. Create project.
    project_response = client.post(
        "/projects",
        json={
            "name": "Frontend Smoke Project",
            "description": "End-to-end frontend-facing contract smoke test.",
            "metadata": {
                "owner": "contract-test",
            },
        },
    )

    assert project_response.status_code == 200

    project = project_response.json()
    project_id = project["project_id"]

    assert project_id.startswith("prj-")
    assert project["name"] == "Frontend Smoke Project"

    # 2. Upload raster and attach it to project.
    upload_response = client.post(
        "/uploads/raster",
        files={
            "file": (
                "sample_raster.json",
                json.dumps(SAMPLE_RASTER).encode("utf-8"),
                "application/json",
            ),
            "project_id": (None, project_id),
        },
    )

    assert upload_response.status_code == 200

    upload = upload_response.json()
    upload_id = upload["upload_id"]

    assert upload_id.startswith("upl-")
    assert upload["kind"] == "raster"
    assert upload["project_id"] == project_id
    assert upload["parsed_json_available"] is True

    # 3. Confirm project now contains the upload.
    project_after_upload_response = client.get(f"/projects/{project_id}")

    assert project_after_upload_response.status_code == 200

    project_after_upload = project_after_upload_response.json()

    assert upload_id in project_after_upload["uploads"]

    # 4. Execute query using upload reference and project_id.
    request_id = "req-api-contract-smoke-001"

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
            "request_id": request_id,
            "project_id": project_id,
            "metadata": {
                "test_name": "frontend_main_api_contract_smoke_flow",
            },
        },
    )

    assert query_response.status_code == 200

    query_payload = query_response.json()

    assert query_payload["status"] in {"success", "succeeded"}
    assert query_payload["request_id"] == request_id
    assert query_payload["answer"]
    assert "structured_error" not in query_payload

    # 5. Fetch request detail.
    request_detail_response = client.get(f"/requests/{request_id}")

    assert request_detail_response.status_code == 200

    request_detail = request_detail_response.json()

    assert request_detail["request_id"] == request_id
    assert request_detail["production_response"]["request_id"] == request_id
    assert request_detail["production_response"]["status"] in {
        "success",
        "succeeded",
    }

    # 6. Fetch Leaflet-ready map layers.
    map_layers_response = client.get(f"/requests/{request_id}/map-layers")

    assert map_layers_response.status_code == 200

    map_layers = map_layers_response.json()

    assert map_layers["request_id"] == request_id
    assert map_layers["layer_count"] >= 1
    assert map_layers["layers"]

    first_layer = map_layers["layers"][0]

    assert first_layer["name"] == "vegetation_polygons"
    assert first_layer["kind"] == "vector"
    assert first_layer["crs"] == "EPSG:4326"
    assert first_layer["feature_count"] == 3
    assert first_layer["geojson"]["type"] == "FeatureCollection"
    assert len(first_layer["geojson"]["features"]) == 3

    # 7. Fetch output manifest.
    outputs_response = client.get(f"/requests/{request_id}/outputs")

    assert outputs_response.status_code == 200

    manifest = outputs_response.json()

    assert manifest["request_id"] == request_id
    assert manifest["schema_version"] == "1.0.0"

    manifest_filenames = {
        item["filename"]
        for item in manifest["files"]
    }

    assert "manifest.json" in manifest_filenames
    assert "production_response.json" in manifest_filenames
    assert "audit_record.json" in manifest_filenames
    assert "outputs_summary.json" in manifest_filenames
    assert "map_layers.json" in manifest_filenames
    assert "vegetation_polygons.geojson" in manifest_filenames

    # 8. List output files.
    output_files_response = client.get(f"/requests/{request_id}/outputs/files")

    assert output_files_response.status_code == 200

    output_files = output_files_response.json()

    output_file_names = {
        item["filename"]
        for item in output_files
    }

    assert "vegetation_polygons.geojson" in output_file_names

    # 9. Download GeoJSON output file.
    geojson_response = client.get(
        f"/requests/{request_id}/outputs/files/vegetation_polygons.geojson"
    )

    assert geojson_response.status_code == 200

    geojson = geojson_response.json()

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 3
