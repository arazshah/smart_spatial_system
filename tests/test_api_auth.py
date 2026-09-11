"""
Tests for the optional shared-secret API key (api/auth.py).

Run:
    pytest tests/test_api_auth.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from api.main import APIConfig, create_app  # noqa: E402
from orchestrator.service import (  # noqa: E402
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
)


def _service(tmp_path: Path) -> OrchestratorService:
    return OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )


def test_api_open_by_default_when_no_key_configured(tmp_path: Path) -> None:
    app = create_app(service=_service(tmp_path))
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/plugins").status_code == 200


def test_protected_routes_require_key_when_configured(tmp_path: Path) -> None:
    app = create_app(
        service=_service(tmp_path),
        api_config=APIConfig(api_key="test-secret-key"),
    )
    client = TestClient(app)

    # No key at all.
    response = client.get("/plugins")
    assert response.status_code == 401

    # Wrong key.
    response = client.get("/plugins", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401

    # Correct key.
    response = client.get("/plugins", headers={"X-API-Key": "test-secret-key"})
    assert response.status_code == 200


def test_health_and_root_stay_open_when_key_configured(tmp_path: Path) -> None:
    app = create_app(
        service=_service(tmp_path),
        api_config=APIConfig(api_key="test-secret-key"),
    )
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_query_endpoint_requires_key_when_configured(tmp_path: Path) -> None:
    app = create_app(
        service=_service(tmp_path),
        api_config=APIConfig(api_key="test-secret-key"),
    )
    client = TestClient(app)

    response = client.post("/query", json={"query": "test"})
    assert response.status_code == 401

    response = client.post(
        "/query",
        json={"query": "test"},
        headers={"X-API-Key": "test-secret-key"},
    )
    assert response.status_code != 401
