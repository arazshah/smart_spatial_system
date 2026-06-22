from __future__ import annotations

from orchestrator.input_error_mapping import (
    input_exception_to_structured_error,
    loader_exception_to_structured_error,
)


def test_input_error_mapping_classifies_invalid_payload() -> None:
    payload = input_exception_to_structured_error(
        RuntimeError("inputs must be a dict."),
        stage="resolve_inputs",
    )

    assert payload["code"] == "input.invalid_payload"
    assert payload["category"] == "validation_error"
    assert payload["source"] == "input_reference_resolver"
    assert payload["details"]["stage"] == "resolve_inputs"


def test_input_error_mapping_classifies_missing_reference() -> None:
    payload = input_exception_to_structured_error(
        RuntimeError("Unknown upload_id: upl-missing"),
        reference_kind="raster",
        upload_id="upl-missing",
        stage="read_upload_metadata",
    )

    assert payload["code"] == "input.reference_not_found"
    assert payload["category"] == "validation_error"
    assert payload["details"]["reference_kind"] == "raster"
    assert payload["details"]["upload_id"] == "upl-missing"


def test_input_error_mapping_classifies_unsupported_reference() -> None:
    payload = input_exception_to_structured_error(
        RuntimeError("Unsupported reference kind: table"),
        reference_kind="table",
        stage="validate_reference_kind",
    )

    assert payload["code"] == "input.reference_unsupported"
    assert payload["category"] == "validation_error"


def test_loader_error_mapping_classifies_import_failure() -> None:
    payload = loader_exception_to_structured_error(
        ModuleNotFoundError("No module named 'missing_loader'"),
        module_name="missing_loader",
        kind="raster",
        stage="plugin_import",
    )

    assert payload["code"] == "loader.plugin_import_failed"
    assert payload["category"] == "configuration_error"
    assert payload["source"] == "loader_plugin_contract"
    assert payload["details"]["module"] == "missing_loader"
    assert payload["details"]["kind"] == "raster"


def test_loader_error_mapping_classifies_invalid_output() -> None:
    payload = loader_exception_to_structured_error(
        RuntimeError("Raster loader output must contain 'data' as a list."),
        module_name="fake_loader",
        kind="raster",
        stage="normalize_output",
    )

    assert payload["code"] == "loader.output_invalid"
    assert payload["category"] == "capability_contract_error"


def test_loader_error_mapping_classifies_execution_failure() -> None:
    payload = loader_exception_to_structured_error(
        RuntimeError("Loader 'fake.load_local_raster' failed: boom"),
        module_name="fake",
        kind="raster",
        function_name="load_local_raster",
        stage="loader_execution",
    )

    assert payload["code"] == "loader.execution_failed"
    assert payload["category"] == "provider_error"
    assert payload["details"]["function_name"] == "load_local_raster"
