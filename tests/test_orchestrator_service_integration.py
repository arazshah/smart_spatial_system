"""
Integration tests for OrchestratorService.

Run:
    pytest tests/test_orchestrator_service_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


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


def _service(tmp_path: Path) -> OrchestratorService:
    return OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )


def test_service_real_user_query_flow(tmp_path: Path) -> None:
    service = _service(tmp_path)

    response = service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-int-001",
        user_context={
            "user_id": "user-001",
            "project_id": "project-001",
        },
    )

    assert response["status"] == "success"
    assert response["request_id"] == "req-service-int-001"
    assert response["answer"]
    assert response["outputs"]
    assert response["confidence"]["score"] is not None
    assert response["audit_ref"]["status"] == "success"

    stored = service.get_request("req-service-int-001")

    assert stored is not None
    assert stored["production_response"]["status"] == "success"
    assert stored["audit_record"]["request_id"] == "req-service-int-001"


def test_service_feedback_to_proposal_to_apply_to_persisted_weights_flow(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    response = service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-int-feedback-001",
    )

    assert response["status"] == "success"

    feedback_payload = service.submit_feedback(
        request_id="req-service-int-feedback-001",
        rating="incorrect",
        issue_types=["route_error"],
        expected_capability="threshold_raster",
    )

    proposals = feedback_payload["proposals"]

    assert proposals

    threshold_proposals = [
        proposal
        for proposal in proposals
        if proposal["target"] == "capability"
        and proposal["name"] == "threshold_raster"
    ]

    assert threshold_proposals

    applied_payload = service.approve_and_apply_proposal(
        threshold_proposals[0],
        save=True,
    )

    assert applied_payload["applied"]["status"] == "applied"
    assert applied_payload["saved"] is True

    expected_weight = threshold_proposals[0]["proposed_weight"]

    assert service.get_weights()["capability_weights"]["threshold_raster"] == expected_weight

    # Simulate service restart.
    restarted_service = _service(tmp_path)

    assert (
        restarted_service.get_weights()["capability_weights"]["threshold_raster"]
        == expected_weight
    )

    weighted_response = restarted_service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-int-weighted-after-restart",
    )

    assert weighted_response["status"] == "success"
    assert weighted_response["metadata"]["weighted_router"] is True


def test_service_can_run_without_weighted_router(tmp_path: Path) -> None:
    service = OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            use_weighted_router=False,
        )
    )

    response = service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-int-no-weighted-router",
    )

    assert response["status"] == "success"
    assert response["metadata"]["weighted_router"] is False
