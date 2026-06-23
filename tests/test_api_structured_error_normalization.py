from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from orchestrator.service import OrchestratorService, OrchestratorServiceConfig


def _client(tmp_path: Path) -> TestClient:
    service = OrchestratorService(
        OrchestratorServiceConfig(
            weights_path=tmp_path / "weights" / "router_weights.json",
            outputs_path=tmp_path / "outputs",
            uploads_path=tmp_path / "uploads",
            persist_outputs=False,
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )

    return TestClient(create_app(service=service))


def test_api_query_failed_response_exposes_structured_error(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/query",
        json={
            "query": "calculate NDVI from raster input",
            "inputs": {
                "raster_ref": "upl-missing",
            },
            "request_id": "req-api-structured-input-error-001",
        },
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "failed"
    assert "structured_error" in payload

    structured_error = payload["structured_error"]

    assert structured_error["code"] == "input.reference_not_found"
    assert structured_error["category"] == "validation_error"
    assert structured_error["source"] == "input_reference_resolver"
    assert structured_error["details"]["reference_kind"] == "raster"
    assert structured_error["details"]["upload_id"] == "upl-missing"

    assert payload["metadata"]["structured_error"]["code"] == "input.reference_not_found"
