from __future__ import annotations

from orchestrator.plugin_error_mapping import plugin_exception_to_structured_error


def test_plugin_error_mapping_classifies_import_failure() -> None:
    payload = plugin_exception_to_structured_error(
        ModuleNotFoundError("No module named 'plugins.missing_plugin'"),
        module_name="plugins.missing_plugin",
        stage="plugin_import",
    )

    assert payload["code"] == "plugin.import_failed"
    assert payload["category"] == "configuration_error"
    assert payload["retryable"] is False
    assert payload["source"] == "capability_registry"
    assert payload["details"]["module"] == "plugins.missing_plugin"
    assert payload["details"]["stage"] == "plugin_import"
    assert payload["details"]["exception_type"] == "ModuleNotFoundError"


def test_plugin_error_mapping_classifies_invalid_plugin_contract() -> None:
    payload = plugin_exception_to_structured_error(
        ValueError("Module 'plugins.bad' does not define PLUGIN."),
        module_name="plugins.bad",
        stage="plugin_registration",
    )

    assert payload["code"] == "plugin.contract_invalid"
    assert payload["category"] == "capability_contract_error"
    assert payload["source"] == "capability_registry"
    assert payload["details"]["module"] == "plugins.bad"


def test_plugin_error_mapping_classifies_duplicate_capability() -> None:
    payload = plugin_exception_to_structured_error(
        ValueError(
            "Duplicate capability 'buffer_analysis' found in plugin 'plugin_b'. "
            "Already registered by plugin 'plugin_a'."
        ),
        module_name="plugins.plugin_b",
        plugin_id="plugin_b",
        capability_name="buffer_analysis",
        stage="plugin_registration",
    )

    assert payload["code"] == "plugin.duplicate_capability"
    assert payload["category"] == "configuration_error"
    assert payload["details"]["module"] == "plugins.plugin_b"
    assert payload["details"]["plugin_id"] == "plugin_b"
    assert payload["details"]["capability_name"] == "buffer_analysis"


def test_plugin_error_mapping_classifies_missing_callable() -> None:
    payload = plugin_exception_to_structured_error(
        ValueError(
            "Capability function 'missing_func' was registered by plugin "
            "'bad_plugin' but no callable with the same name exists in module "
            "'plugins.bad_plugin'."
        ),
        module_name="plugins.bad_plugin",
        plugin_id="bad_plugin",
        capability_name="missing_func",
        stage="plugin_registration",
    )

    assert payload["code"] == "plugin.capability_callable_missing"
    assert payload["category"] == "capability_resolution_error"
    assert payload["details"]["module"] == "plugins.bad_plugin"
    assert payload["details"]["plugin_id"] == "bad_plugin"
    assert payload["details"]["capability_name"] == "missing_func"


def test_plugin_error_mapping_classifies_unexpected_exception() -> None:
    payload = plugin_exception_to_structured_error(
        RuntimeError("unexpected plugin loader crash"),
        module_name="plugins.crashy",
        stage="plugin_registration",
    )

    assert payload["code"] == "plugin.unexpected_exception"
    assert payload["category"] == "internal_error"
    assert payload["source"] == "capability_registry"
    assert payload["details"]["module"] == "plugins.crashy"
