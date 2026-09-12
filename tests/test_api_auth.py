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


from api.main import APIConfig, create_app, warn_if_unauthenticated  # noqa: E402
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


# ---------------------------------------------------------------------------
# Startup warning when no key is configured
# ---------------------------------------------------------------------------


def test_warn_if_unauthenticated_is_silent_when_a_key_is_set() -> None:
    assert warn_if_unauthenticated("some-key", env={}) is None


def test_warn_if_unauthenticated_warns_when_no_key_is_set() -> None:
    message = warn_if_unauthenticated(None, env={})

    assert message is not None
    assert "SMART_SPATIAL_API_KEY is not set" in message
    assert "unauthenticated" in message


def test_warn_if_unauthenticated_escalates_when_an_llm_key_is_configured() -> None:
    """
    An open instance that also holds an LLM key is not just an access
    problem - anyone who reaches it can spend the deployer's credit. The
    warning has to say so, since that is the expensive failure mode.
    """
    message = warn_if_unauthenticated(None, env={"OPENAI_API_KEY": "sk-test"})

    assert message is not None
    assert "OPENAI_API_KEY" in message
    assert "spend that credit" in message


def test_warn_if_unauthenticated_lists_every_configured_llm_key_variable() -> None:
    message = warn_if_unauthenticated(
        None,
        env={"LLM_API_KEY": "a", "AVALAI_API_KEY": "b"},
    )

    assert message is not None
    assert "LLM_API_KEY" in message
    assert "AVALAI_API_KEY" in message


def test_create_app_logs_the_warning_when_no_key_is_configured(caplog, tmp_path) -> None:
    service = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
        )
    )

    with caplog.at_level("WARNING", logger="api.main"):
        create_app(service=service, api_config=APIConfig(api_key=None))

    assert any(
        "SMART_SPATIAL_API_KEY is not set" in record.message for record in caplog.records
    )
