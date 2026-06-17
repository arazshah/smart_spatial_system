"""
Tests for project/session API.

Run:
    pytest tests/test_api_projects.py -v
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


def test_api_can_create_list_and_get_project(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/projects",
        json={
            "name": "Vegetation Project",
            "description": "Test project",
            "metadata": {
                "owner": "tester",
            },
        },
    )

    assert create_response.status_code == 200

    project = create_response.json()
    project_id = project["project_id"]

    assert project_id.startswith("prj-")
    assert project["name"] == "Vegetation Project"

    list_response = client.get("/projects")

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    get_response = client.get(f"/projects/{project_id}")

    assert get_response.status_code == 200
    assert get_response.json()["project_id"] == project_id


def test_api_upload_can_attach_to_project(tmp_path: Path) -> None:
    client = _client(tmp_path)

    project = client.post(
        "/projects",
        json={
            "name": "Upload Project",
        },
    ).json()

    project_id = project["project_id"]

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

    assert upload["project_id"] == project_id

    project_after = client.get(f"/projects/{project_id}").json()

    assert len(project_after["uploads"]) == 1
    assert project_after["uploads"][0] == upload["upload_id"]
