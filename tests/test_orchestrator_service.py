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
    service = OrchestratorService(
        OrchestratorServiceConfig(
            weights_path=tmp_path / "weights.json",
            load_persisted_weights=True,
        )
    )

    service.weight_store.set_weight("capability", "threshold_raster", 1.5)

    original_store = service.weight_store
    feedback_store = service.feedback_proposal_service.weight_store

    assert feedback_store is original_store

    saved = service.save_weights()

    assert saved["store"]["capability_weights"]["threshold_raster"] == 1.5

    service.weight_store.set_weight("capability", "threshold_raster", 2.5)

    assert service.get_weights()["capability_weights"]["threshold_raster"] == 2.5
    assert service.feedback_proposal_service.weight_store is original_store

    reloaded = service.reload_weights()

    assert service.weight_store is original_store
    assert service.feedback_proposal_service.weight_store is original_store
    assert reloaded["capability_weights"]["threshold_raster"] == 1.5
    assert service.feedback_proposal_service.weight_store.get_weight(
        "capability",
        "threshold_raster",
    ) == 1.5


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
    service = OrchestratorService(
        OrchestratorServiceConfig(allow_request_kernel_execution=True)
    )

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
    service = _make_service(tmp_path, allow_request_kernel_execution=True)

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


def test_service_planning_opt_in_kernel_execution_metadata_includes_summary_and_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from orchestrator.planning.runner import make_static_planning_runner
    from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec
    from plugins.feature_scoring import rank_features, score_features

    service = _make_service(
        tmp_path,
        allow_request_kernel_execution=True,
    )

    monkeypatch.setattr(service, "_query_spec_planning_enabled", lambda: True)
    monkeypatch.setattr(
        service.query_execution_service,
        "_query_spec_planning_enabled",
        lambda: True,
    )

    class FakeLLMClient:
        pass

    class FakeQuerySpecGenerator:
        def __init__(self, llm_client):
            self.llm_client = llm_client

        def generate(self, query: str, context=None) -> QuerySpec:
            return QuerySpec(
                raw_query=query,
                goal="rank_properties",
                entities=[
                    EntitySpec(ref="properties", kind="vector"),
                ],
                operations=[
                    OperationSpec(
                        op="score_features",
                        inputs={"vector": "properties"},
                        params={
                            "scoring_spec": {
                                "output_field": "investment_score",
                                "scale": 100,
                                "factors": [
                                    {
                                        "name": "near_poi",
                                        "field": "distance_to_poi",
                                        "type": "inverse_distance",
                                        "max_distance": 500,
                                        "weight": 0.7,
                                    },
                                    {
                                        "name": "buildable",
                                        "field": "__in_polygon__",
                                        "type": "boolean",
                                        "weight": 0.3,
                                    },
                                ],
                            }
                        },
                        output="scored",
                    ),
                    OperationSpec(
                        op="rank_features",
                        inputs={"vector": "scored"},
                        params={
                            "score_field": "investment_score",
                            "rank_field": "investment_rank",
                        },
                        output="ranked",
                    ),
                ],
                outputs=[
                    OutputSpec(kind="vector", source="ranked"),
                ],
            )

    def fake_make_registry_planning_runner(registry):
        return make_static_planning_runner(
            {
                "score_features": score_features,
                "rank_features": rank_features,
            }
        )

    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.OpenAICompatibleLLMClient",
        FakeLLMClient,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.LLMQuerySpecGenerator",
        FakeQuerySpecGenerator,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.make_registry_planning_runner",
        fake_make_registry_planning_runner,
    )

    resolved_inputs = {
        "properties": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0, 0]},
                    "properties": {
                        "name": "A",
                        "distance_to_poi": 100,
                        "__in_polygon__": True,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [1, 1]},
                    "properties": {
                        "name": "B",
                        "distance_to_poi": 500,
                        "__in_polygon__": False,
                    },
                },
            ],
        }
    }

    response = service._try_handle_query_with_planning(
        query="املاک را امتیاز بده و رتبه‌بندی کن",
        resolved_inputs=resolved_inputs,
        final_request_id="req_kernel_execution_opt_in",
        final_metadata={},
        metadata={
            "enable_kernel_execution": True,
        },
    )

    assert response is not None
    assert response["status"] == "succeeded"

    metadata = response["metadata"]
    planning_summary = metadata["planning_summary"]

    assert metadata["query_spec_planning_enabled"] is True
    assert metadata["planning_attempted"] is True
    assert metadata["kernel_execution_enabled"] is True
    assert metadata["execution_mode"] == "query_spec_planning_kernel_execution"

    assert planning_summary["success"] is True
    assert planning_summary["kernel_execution_enabled"] is True
    assert planning_summary["kernel_execution_success"] is True

    # Kernel plan summary is available in metadata.
    kernel_plan_summary = planning_summary["kernel_plan"]
    assert kernel_plan_summary is not None
    assert kernel_plan_summary["valid"] is True
    assert kernel_plan_summary["step_count"] == 2
    assert kernel_plan_summary["output_nodes"] == ["ranked"]

    # Kernel execution summary is available in metadata.
    kernel_execution_summary = planning_summary["kernel_execution"]
    assert kernel_execution_summary is not None
    assert kernel_execution_summary["success"] is True
    assert kernel_execution_summary["error"] is None
    assert kernel_execution_summary["artifact_count"] == 2
    assert kernel_execution_summary["output_artifact_count"] == 1
    assert kernel_execution_summary["artifact_ids"] == ["scored", "ranked"]
    assert kernel_execution_summary["output_artifact_ids"] == ["ranked"]

    # Top-level response summary is still available for clients/debug UI.
    assert response["kernel_execution"]["success"] is True
    assert response["kernel_execution"]["artifact_count"] == 2

    # DAG vs Kernel parity is available and successful.
    parity = planning_summary["kernel_execution_parity"]
    assert parity["available"] is True
    assert parity["success"] is True
    assert parity["dag_success"] is True
    assert parity["kernel_success"] is True
    assert parity["matching_output_node_ids"] is True
    assert parity["output_values_match"] is True
    assert parity["dag_output_node_ids"] == ["ranked"]
    assert parity["kernel_output_node_ids"] == ["ranked"]
    assert parity["missing_in_kernel"] == []
    assert parity["extra_in_kernel"] == []
    assert parity["mismatched_outputs"] == []


def test_orchestrator_service_kernel_execution_can_be_enabled_from_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path, enable_kernel_execution=True)

    assert service.config.enable_kernel_execution is True
    assert service._kernel_execution_enabled() is True

    # Per-request metadata can still explicitly disable it.
    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": False}
    ) is False

    default_service = _make_service(tmp_path)
    assert default_service.config.enable_kernel_execution is False
    assert default_service._kernel_execution_enabled() is False

    # Env remains a deployment-level override.
    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "1")
    assert default_service._kernel_execution_enabled() is True

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "0")
    assert service._kernel_execution_enabled() is False


def test_service_planning_uses_config_kernel_execution_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec

    service = _make_service(tmp_path, enable_kernel_execution=True)

    monkeypatch.setattr(service, "_query_spec_planning_enabled", lambda: True)
    monkeypatch.setattr(
        service.query_execution_service,
        "_query_spec_planning_enabled",
        lambda: True,
    )

    class FakeLLMClient:
        pass

    class FakeQuerySpecGenerator:
        def __init__(self, llm_client):
            self.llm_client = llm_client

        def generate(self, query: str, context=None) -> QuerySpec:
            return QuerySpec(
                raw_query=query,
                goal="rank_properties",
                entities=[
                    EntitySpec(ref="properties", kind="vector"),
                ],
                operations=[
                    OperationSpec(
                        op="score_features",
                        inputs={"vector": "properties"},
                        params={
                            "scoring_spec": {
                                "output_field": "investment_score",
                            }
                        },
                        output="scored",
                    ),
                    OperationSpec(
                        op="rank_features",
                        inputs={"vector": "scored"},
                        params={
                            "score_field": "investment_score",
                            "rank_field": "investment_rank",
                        },
                        output="ranked",
                    ),
                ],
                outputs=[
                    OutputSpec(kind="vector", source="ranked"),
                ],
            )

    calls = {
        "run": 0,
        "run_with_kernel_execution": 0,
    }

    class FakePlanningResult:
        success = True
        error = None
        output_nodes = {}
        trace = []
        kernel_plan = None
        kernel_execution = None

    class FakeRunner:
        def run(self, query_spec, *, initial_inputs=None, fail_fast=True):
            calls["run"] += 1
            return FakePlanningResult()

        def run_with_kernel_execution(
            self,
            query_spec,
            *,
            initial_inputs=None,
            fail_fast=True,
            raise_on_kernel_error=False,
        ):
            calls["run_with_kernel_execution"] += 1
            return FakePlanningResult()

    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.OpenAICompatibleLLMClient",
        FakeLLMClient,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.LLMQuerySpecGenerator",
        FakeQuerySpecGenerator,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.make_registry_planning_runner",
        lambda registry: FakeRunner(),
    )

    response = service._try_handle_query_with_planning(
        query="املاک را امتیاز بده و رتبه‌بندی کن",
        resolved_inputs={
            "properties": {
                "type": "FeatureCollection",
                "features": [],
            }
        },
        final_request_id="req_kernel_execution_from_config",
        final_metadata={},
        metadata={},
    )

    assert response is not None
    assert response["status"] == "succeeded"

    assert calls["run"] == 0
    assert calls["run_with_kernel_execution"] == 1

    metadata = response["metadata"]

    assert metadata["kernel_execution_enabled"] is True
    assert metadata["execution_mode"] == "query_spec_planning_kernel_execution"
    assert metadata["planning_summary"]["kernel_execution_enabled"] is True


def test_orchestrator_service_kernel_execution_can_be_enabled_from_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path, enable_kernel_execution=True)

    assert service.config.enable_kernel_execution is True
    assert service._kernel_execution_enabled() is True

    # Per-request metadata can still explicitly disable config-level default.
    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": False}
    ) is False

    default_service = _make_service(tmp_path)

    assert default_service.config.enable_kernel_execution is False
    assert default_service._kernel_execution_enabled() is False

    # Env remains a deployment-level override.
    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "1")
    assert default_service._kernel_execution_enabled() is True

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "0")
    assert service._kernel_execution_enabled() is False


def test_service_planning_uses_config_kernel_execution_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec

    service = _make_service(tmp_path, enable_kernel_execution=True)

    monkeypatch.setattr(service, "_query_spec_planning_enabled", lambda: True)
    monkeypatch.setattr(
        service.query_execution_service,
        "_query_spec_planning_enabled",
        lambda: True,
    )

    class FakeLLMClient:
        pass

    class FakeQuerySpecGenerator:
        def __init__(self, llm_client):
            self.llm_client = llm_client

        def generate(self, query: str, context=None) -> QuerySpec:
            return QuerySpec(
                raw_query=query,
                goal="rank_properties",
                entities=[
                    EntitySpec(ref="properties", kind="vector"),
                ],
                operations=[
                    OperationSpec(
                        op="score_features",
                        inputs={"vector": "properties"},
                        params={
                            "scoring_spec": {
                                "output_field": "investment_score",
                            }
                        },
                        output="scored",
                    ),
                    OperationSpec(
                        op="rank_features",
                        inputs={"vector": "scored"},
                        params={
                            "score_field": "investment_score",
                            "rank_field": "investment_rank",
                        },
                        output="ranked",
                    ),
                ],
                outputs=[
                    OutputSpec(kind="vector", source="ranked"),
                ],
            )

    calls = {
        "run": 0,
        "run_with_kernel_execution": 0,
    }

    class FakePlanningResult:
        success = True
        error = None
        output_nodes = {}
        trace = []
        kernel_plan = None
        kernel_execution = None

    class FakeRunner:
        def run(self, query_spec, *, initial_inputs=None, fail_fast=True):
            calls["run"] += 1
            return FakePlanningResult()

        def run_with_kernel_execution(
            self,
            query_spec,
            *,
            initial_inputs=None,
            fail_fast=True,
            raise_on_kernel_error=False,
        ):
            calls["run_with_kernel_execution"] += 1
            return FakePlanningResult()

    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.OpenAICompatibleLLMClient",
        FakeLLMClient,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.LLMQuerySpecGenerator",
        FakeQuerySpecGenerator,
    )
    monkeypatch.setattr(
        "smart_spatial_system.application.services.query_execution_service.make_registry_planning_runner",
        lambda registry: FakeRunner(),
    )

    response = service._try_handle_query_with_planning(
        query="املاک را امتیاز بده و رتبه‌بندی کن",
        resolved_inputs={
            "properties": {
                "type": "FeatureCollection",
                "features": [],
            }
        },
        final_request_id="req_kernel_execution_from_config",
        final_metadata={},
        metadata={},
    )

    assert response is not None
    assert response["status"] == "succeeded"

    assert calls["run"] == 0
    assert calls["run_with_kernel_execution"] == 1

    metadata = response["metadata"]

    assert metadata["kernel_execution_enabled"] is True
    assert metadata["execution_mode"] == "query_spec_planning_kernel_execution"
    assert metadata["planning_summary"]["kernel_execution_enabled"] is True


def test_kernel_execution_default_is_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path)

    assert service.config.enable_kernel_execution is False
    assert service.config.allow_request_kernel_execution is False
    assert service._kernel_execution_enabled() is False


def test_request_cannot_enable_kernel_execution_without_permission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path)

    assert service.config.allow_request_kernel_execution is False

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": True}
    ) is False

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": "true"}
    ) is False

    assert service._kernel_execution_enabled(
        metadata={"planning": {"kernel_execution": "on"}}
    ) is False

    assert service._kernel_execution_enabled(
        final_metadata={"use_kernel_execution": "yes"}
    ) is False


def test_request_can_enable_kernel_execution_when_permission_granted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(
        tmp_path,
        allow_request_kernel_execution=True,
    )

    assert service.config.enable_kernel_execution is False
    assert service.config.allow_request_kernel_execution is True

    assert service._kernel_execution_enabled() is False

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": True}
    ) is True

    assert service._kernel_execution_enabled(
        metadata={"planning": {"kernel_execution": "on"}}
    ) is True

    assert service._kernel_execution_enabled(
        final_metadata={"use_kernel_execution": "yes"}
    ) is True


def test_request_can_always_disable_kernel_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path, enable_kernel_execution=True)

    assert service.config.enable_kernel_execution is True
    assert service._kernel_execution_enabled() is True

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": False}
    ) is False

    assert service._kernel_execution_enabled(
        metadata={"planning": {"kernel_execution": "off"}}
    ) is False


def test_kernel_execution_config_default_enables_globally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path, enable_kernel_execution=True)

    assert service._kernel_execution_enabled() is True


def test_kernel_execution_env_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path)

    assert service._kernel_execution_enabled() is False

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "1")
    assert service._kernel_execution_enabled() is True

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "0")
    assert service._kernel_execution_enabled() is False


def test_request_disable_takes_priority_over_env_enable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", raising=False)
    monkeypatch.delenv("ENABLE_KERNEL_EXECUTION", raising=False)

    service = _make_service(tmp_path)

    monkeypatch.setenv("SMART_SPATIAL_ENABLE_KERNEL_EXECUTION", "1")

    assert service._kernel_execution_enabled(
        metadata={"enable_kernel_execution": False}
    ) is False
