"""
Tests for FastAPI MVP API.

Run:
    pytest tests/test_api_mvp.py -v
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


def _query_payload(request_id: str = "req-api-query-001") -> dict:
    return {
        "query": NDVI_QUERY,
        "inputs": {
            "raster": SATELLITE_RASTER_2BAND,
        },
        "band_map": {
            "red": 1,
            "nir": 2,
        },
        "request_id": request_id,
        "user_context": {
            "user_id": "u-api-001",
            "project_id": "p-api-001",
        },
    }


def test_api_health(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "ok"
    assert payload["service"] == "OrchestratorService"
    assert payload["plugin_modules"]


def test_api_query_endpoint_returns_production_response(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/query",
        json=_query_payload("req-api-query-001"),
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "success"
    assert payload["request_id"] == "req-api-query-001"
    assert payload["answer"]
    assert payload["confidence"]["score"] is not None
    assert payload["audit_ref"]["status"] == "success"


def test_api_query_endpoint_rejects_invalid_body(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/query",
        json={
            "inputs": {},
        },
    )

    assert response.status_code == 400
    assert "query" in response.json()["detail"]


def test_api_requests_endpoints(tmp_path: Path) -> None:
    client = _client(tmp_path)

    query_response = client.post(
        "/query",
        json=_query_payload("req-api-requests-001"),
    )

    assert query_response.status_code == 200

    list_response = client.get("/requests")

    assert list_response.status_code == 200

    items = list_response.json()

    assert len(items) == 1
    assert items[0]["request_id"] == "req-api-requests-001"

    detail_response = client.get("/requests/req-api-requests-001")

    assert detail_response.status_code == 200

    detail = detail_response.json()

    assert detail["request_id"] == "req-api-requests-001"
    assert detail["production_response"]["status"] == "success"


def test_api_get_unknown_request_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/requests/missing-request")

    assert response.status_code == 404


def test_api_feedback_endpoint_builds_proposals(tmp_path: Path) -> None:
    client = _client(tmp_path)

    query_response = client.post(
        "/query",
        json=_query_payload("req-api-feedback-001"),
    )

    assert query_response.status_code == 200

    feedback_response = client.post(
        "/feedback",
        json={
            "request_id": "req-api-feedback-001",
            "rating": "incorrect",
            "issue_types": [
                "route_error",
            ],
            "expected_capability": "threshold_raster",
            "comment": "Expected thresholding step to be stronger.",
        },
    )

    assert feedback_response.status_code == 200

    payload = feedback_response.json()

    assert payload["request_id"] == "req-api-feedback-001"
    assert payload["feedback"]
    assert isinstance(payload["signals"], list)
    assert isinstance(payload["proposals"], list)
    assert payload["proposals"]


def test_api_feedback_unknown_request_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/feedback",
        json={
            "request_id": "missing",
            "rating": "incorrect",
        },
    )

    assert response.status_code == 404


def test_api_weights_endpoints(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/weights")

    assert response.status_code == 200

    weights = response.json()

    assert "capability_weights" in weights
    assert "plugin_weights" in weights

    save_response = client.post("/weights/save")

    assert save_response.status_code == 200
    assert save_response.json()["schema_version"] == "1.0.0"

    reload_response = client.post("/weights/reload")

    assert reload_response.status_code == 200
    assert "capability_weights" in reload_response.json()


def test_api_apply_weight_proposal_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)

    query_response = client.post(
        "/query",
        json=_query_payload("req-api-apply-proposal-001"),
    )

    assert query_response.status_code == 200

    feedback_response = client.post(
        "/feedback",
        json={
            "request_id": "req-api-apply-proposal-001",
            "rating": "incorrect",
            "issue_types": [
                "route_error",
            ],
            "expected_capability": "threshold_raster",
        },
    )

    assert feedback_response.status_code == 200

    proposals = feedback_response.json()["proposals"]

    proposal = [
        item
        for item in proposals
        if item["target"] == "capability"
        and item["name"] == "threshold_raster"
    ][0]

    apply_response = client.post(
        "/weights/proposals/apply",
        json={
            "proposal": proposal,
            "save": True,
        },
    )

    assert apply_response.status_code == 200

    payload = apply_response.json()

    assert payload["applied"]["status"] == "applied"
    assert payload["saved"] is True

    weights_response = client.get("/weights")

    assert weights_response.status_code == 200
    assert (
        weights_response.json()["capability_weights"]["threshold_raster"]
        == proposal["proposed_weight"]
    )


def test_api_apply_weight_proposal_rejects_invalid_body(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/weights/proposals/apply",
        json={
            "proposal": "bad",
        },
    )

    assert response.status_code == 400
