"""
Frontend-facing API surface contract tests.

These tests lock lightweight API endpoints that the frontend depends on:
- root and health
- plugins list/detail/config read
- runtime settings
- weights
- OpenAPI route presence

Run:
    pytest tests/test_frontend_api_surface_contract.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from api.main import create_app  # noqa: E402
from orchestrator.service import (  # noqa: E402
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
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


def _assert_no_secret_values(payload: Any) -> None:
    """
    Runtime settings may expose booleans such as api_key_configured,
    but must not expose raw secret values.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()

            assert lowered not in {
                "api_key",
                "apikey",
                "token",
                "access_token",
                "secret",
                "password",
            }

            _assert_no_secret_values(value)

    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secret_values(item)


def test_frontend_root_health_runtime_and_weights_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)

    root_response = client.get("/")

    assert root_response.status_code == 200

    root = root_response.json()

    assert root["status"] == "ok"
    assert root["service"] == "Smart Spatial System API"
    assert root["docs"] == "/docs"
    assert root["health"] == "/health"

    health_response = client.get("/health")

    assert health_response.status_code == 200

    health = health_response.json()

    assert health["status"] == "ok"
    assert health["service"] == "OrchestratorService"
    assert isinstance(health["plugin_modules"], list)
    assert health["plugin_modules"]
    assert isinstance(health["runtime_paths"], dict)
    assert "weights" in health

    runtime_response = client.get("/settings/runtime")

    assert runtime_response.status_code == 200

    runtime = runtime_response.json()

    assert set(runtime) >= {
        "llm",
        "plugins",
        "runtime",
        "runtime_paths",
    }

    assert isinstance(runtime["llm"]["api_key_configured"], bool)
    assert "api_key" not in runtime["llm"]
    assert "password" not in runtime["llm"]
    assert "secret" not in runtime["llm"]

    assert isinstance(runtime["plugins"]["module_names"], list)
    assert isinstance(runtime["plugins"]["plugin_ids"], list)
    assert isinstance(runtime["plugins"]["capabilities"], list)
    assert isinstance(runtime["plugins"]["capability_count"], int)
    assert runtime["plugins"]["capability_count"] >= 1

    _assert_no_secret_values(runtime)

    weights_response = client.get("/weights")

    assert weights_response.status_code == 200

    weights = weights_response.json()

    assert set(weights) >= {
        "config",
        "capability_weights",
        "plugin_weights",
    }
    assert isinstance(weights["capability_weights"], dict)
    assert isinstance(weights["plugin_weights"], dict)


def test_frontend_plugins_list_detail_and_config_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)

    plugins_response = client.get("/plugins")

    assert plugins_response.status_code == 200

    plugins = plugins_response.json()

    assert isinstance(plugins, list)
    assert plugins

    first = plugins[0]

    assert set(first) >= {
        "plugin_id",
        "enabled",
        "config_path",
        "config_exists",
        "capability_count",
        "capabilities",
        "skipped",
        "skipped_error",
    }

    assert isinstance(first["plugin_id"], str)
    assert isinstance(first["enabled"], bool)
    assert isinstance(first["capability_count"], int)
    assert isinstance(first["capabilities"], list)

    plugin_id = first["plugin_id"]

    detail_response = client.get(f"/plugins/{plugin_id}")

    assert detail_response.status_code == 200

    detail = detail_response.json()

    assert detail["plugin_id"] == plugin_id
    assert detail["enabled"] == first["enabled"]
    assert detail["capability_count"] == first["capability_count"]

    configurable = next(
        (item for item in plugins if item.get("config_exists")),
        None,
    )

    assert configurable is not None

    config_plugin_id = configurable["plugin_id"]

    config_response = client.get(f"/plugins/{config_plugin_id}/config")

    assert config_response.status_code == 200

    config_payload = config_response.json()

    assert isinstance(config_payload, dict)
    assert config_payload


def test_frontend_openapi_contains_critical_routes(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/openapi.json")

    assert response.status_code == 200

    openapi = response.json()
    paths = openapi["paths"]

    expected_paths = {
        "/",
        "/health",
        "/query",
        "/planner/intent",
        "/feedback",
        "/requests",
        "/requests/{request_id}",
        "/requests/{request_id}/map-layers",
        "/requests/{request_id}/outputs",
        "/requests/{request_id}/outputs/save",
        "/requests/{request_id}/outputs/files",
        "/requests/{request_id}/outputs/files/{filename}",
        "/requests/{request_id}/documents/{filename}",
        "/projects",
        "/projects/{project_id}",
        "/projects/{project_id}/data-sources",
        "/uploads/raster",
        "/uploads/vector",
        "/uploads",
        "/uploads/{upload_id}",
        "/uploads/{upload_id}/file",
        "/data-sources/{upload_id}",
        "/data-sources/{upload_id}/preview",
        "/data-sources/csv-table",
        "/data-sources/wms",
        "/data-sources/postgis",
        "/data-sources/wfs",
        "/data-sources/url",
        "/plugins",
        "/plugins/{plugin_id}",
        "/plugins/{plugin_id}/config",
        "/settings/runtime",
        "/settings/llm/smoke-test",
        "/weights",
        "/weights/save",
        "/weights/reload",
        "/weights/proposals/apply",
    }

    missing = expected_paths - set(paths)

    assert missing == set()
