"""
Tests for OrchestratorService.

Run:
    pytest tests/test_orchestrator_service.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.service import (  # noqa: E402
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
    OrchestratorServiceError,
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


def _make_service(tmp_path: Path, **kwargs) -> OrchestratorService:
    return OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            **kwargs,
        )
    )


def test_service_health_payload(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    health = service.get_health()

    assert health["status"] == "ok"
    assert health["service"] == "OrchestratorService"
    assert health["plugin_modules"]
    assert health["use_weighted_router"] is True
    assert "weights" in health


def test_service_handle_query_returns_production_response(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    payload = service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-unit-001",
        user_context={
            "user_id": "u-001",
            "project_id": "p-001",
        },
    )

    assert payload["status"] == "success"
    assert payload["request_id"] == "req-service-unit-001"
    assert payload["answer"]
    assert payload["confidence"]["score"] is not None
    assert payload["audit_ref"]["plan_steps"] >= 1

    stored = service.get_request("req-service-unit-001")

    assert stored is not None
    assert stored["request_id"] == "req-service-unit-001"
    assert stored["audit_record"]["status"] == "success"


def test_service_generates_request_id_when_missing(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    payload = service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
    )

    assert payload["request_id"]
    assert payload["request_id"].startswith("req-")
    assert service.get_request(payload["request_id"]) is not None


def test_service_handles_failure_as_production_response(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    payload = service.handle_query(
        query=NDVI_QUERY,
        inputs={},  # missing raster
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-failed-001",
    )

    assert payload["status"] == "failed"
    assert payload["request_id"] == "req-service-failed-001"
    assert payload["warnings"]
    assert payload["next_actions"]

    stored = service.get_request("req-service-failed-001")

    assert stored is not None
    assert "error" in stored


def test_service_submit_feedback_builds_signals_and_proposals(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-feedback-001",
    )

    payload = service.submit_feedback(
        request_id="req-service-feedback-001",
        rating="incorrect",
        issue_types=["route_error"],
        expected_capability="threshold_raster",
    )

    assert payload["request_id"] == "req-service-feedback-001"
    assert "feedback" in payload
    assert isinstance(payload["signals"], list)
    assert isinstance(payload["proposals"], list)
    assert payload["proposals"]

    summary = payload["proposal_summary"]

    assert summary["total_proposals"] >= len(payload["proposals"])


def test_service_submit_feedback_rejects_unknown_request(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    with pytest.raises(OrchestratorServiceError, match="Unknown request_id"):
        service.submit_feedback(
            request_id="missing",
            rating="correct",
        )


def test_service_can_save_and_reload_weights(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    service.weight_store.set_weight(
        "capability",
        "threshold_raster",
        1.25,
    )

    saved = service.save_weights()

    assert saved["store"]["capability_weights"]["threshold_raster"] == 1.25

    service.weight_store.set_weight(
        "capability",
        "threshold_raster",
        0.75,
    )

    reloaded = service.reload_weights()

    assert reloaded["capability_weights"]["threshold_raster"] == 1.25


def test_service_list_requests(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-list-001",
    )

    items = service.list_requests()

    assert len(items) == 1
    assert items[0]["request_id"] == "req-service-list-001"
    assert items[0]["status"] == "success"


def test_service_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="plugin_modules"):
        OrchestratorServiceConfig(plugin_modules=[])

    with pytest.raises(ValueError, match="min_score"):
        OrchestratorServiceConfig(min_score=-1)

    with pytest.raises(ValueError, match="max_history_items"):
        OrchestratorServiceConfig(max_history_items=-1)

    with pytest.raises(ValueError, match="default_weight"):
        OrchestratorServiceConfig(default_weight=-1)

    with pytest.raises(ValueError, match="max_weight"):
        OrchestratorServiceConfig(min_weight=2, max_weight=1)

    with pytest.raises(ValueError, match="response_language"):
        OrchestratorServiceConfig(response_language="bad")


def test_service_history_can_be_disabled(tmp_path: Path) -> None:
    service = _make_service(
        tmp_path,
        keep_history=False,
    )

    payload = service.handle_query(
        query=NDVI_QUERY,
        inputs={
            "raster": SATELLITE_RASTER_2BAND,
        },
        band_map={
            "red": 1,
            "nir": 2,
        },
        request_id="req-service-no-history",
    )

    assert payload["status"] == "success"
    assert service.get_request("req-service-no-history") is None


def test_orchestrator_service_kernel_execution_flag_is_opt_in(monkeypatch) -> None:
    service = OrchestratorService()

    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    assert service._kernel_execution_enabled() is False

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": True}
    ) is True

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": "true"}
    ) is True

    assert service._kernel_execution_enabled(
        metadata={"planning": {"kernel_execution": "on"}}
    ) is True

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": "false"}
    ) is False

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "1")
    assert service._kernel_execution_enabled() is True

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "0")
    assert service._kernel_execution_enabled() is False


def test_orchestrator_service_kernel_execution_flag_is_opt_in(tmp_path: Path, monkeypatch) -> None:
    service = _make_service(tmp_path)

    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    assert service._kernel_execution_enabled() is False

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": True}
    ) is True

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": "true"}
    ) is True

    assert service._kernel_execution_enabled(
        metadata={"planning": {"kernel_execution": "on"}}
    ) is True

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": "false"}
    ) is False

    assert service._kernel_execution_enabled(
        final_metadata={"use_kernel_execution": "yes"}
    ) is True

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "1")
    assert service._kernel_execution_enabled() is True

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "0")
    assert service._kernel_execution_enabled() is False
