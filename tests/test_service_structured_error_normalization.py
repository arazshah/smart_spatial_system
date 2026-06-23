from __future__ import annotations

from pathlib import Path

from orchestrator.service import OrchestratorService, OrchestratorServiceConfig


def _service(tmp_path: Path) -> OrchestratorService:
    return OrchestratorService(
        OrchestratorServiceConfig(
            weights_path=tmp_path / "weights" / "router_weights.json",
            outputs_path=tmp_path / "outputs",
            uploads_path=tmp_path / "uploads",
            persist_outputs=False,
            use_weighted_router=True,
            load_persisted_weights=True,
        )
    )


def test_handle_query_failed_response_preserves_input_structured_error(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    response = service.handle_query(
        query="calculate NDVI from raster input",
        inputs={
            "raster_ref": "upl-missing",
        },
        request_id="req-structured-input-error-001",
    )

    assert response["status"] == "failed"
    assert "structured_error" in response

    structured_error = response["structured_error"]

    assert structured_error["code"] == "input.reference_not_found"
    assert structured_error["category"] == "validation_error"
    assert structured_error["source"] == "input_reference_resolver"
    assert structured_error["details"]["reference_kind"] == "raster"
    assert structured_error["details"]["upload_id"] == "upl-missing"
    assert structured_error["details"]["stage"] == "read_upload_metadata"
    assert structured_error["details"]["service_stage"] in {
        "resolve_input_references",
        "handle_query",
    }

    assert response["metadata"]["structured_error"]["code"] == "input.reference_not_found"
    assert response["metadata"]["service_structured_error"]["code"] == (
        "input.reference_not_found"
    )

    record = service.get_request("req-structured-input-error-001")
    assert record is not None
    assert record["production_response"]["structured_error"]["code"] == (
        "input.reference_not_found"
    )
