from __future__ import annotations

from orchestrator.provider_error_mapping import (
    ProviderExecutionError,
    make_provider_execution_error,
    provider_exception_to_structured_error,
    redact_provider_error_message,
)


def test_provider_error_mapping_classifies_connection_failure_as_retryable() -> None:
    payload = provider_exception_to_structured_error(
        RuntimeError("connection refused while connecting to host"),
        provider="postgis",
        operation="execute_query",
        source="postgis_connector",
    )

    assert payload["code"] == "provider.connection_failed"
    assert payload["category"] == "provider_error"
    assert payload["retryable"] is True
    assert payload["source"] == "postgis_connector"
    assert payload["details"]["provider"] == "postgis"
    assert payload["details"]["operation"] == "execute_query"
    assert payload["details"]["exception_type"] == "RuntimeError"


def test_provider_error_mapping_classifies_query_failure() -> None:
    payload = provider_exception_to_structured_error(
        RuntimeError("syntax error at or near SELECT"),
        provider="postgis",
        operation="execute_query",
        source="postgis_connector",
    )

    assert payload["code"] == "provider.query_failed"
    assert payload["category"] == "provider_error"
    assert payload["retryable"] is False
    assert payload["source"] == "postgis_connector"


def test_provider_error_mapping_classifies_configuration_failure() -> None:
    payload = provider_exception_to_structured_error(
        ValueError("Either dsn or host must be provided."),
        provider="postgis",
        operation="build_connection",
        source="postgis_connector",
    )

    assert payload["code"] == "provider.configuration_invalid"
    assert payload["category"] == "configuration_error"
    assert payload["retryable"] is False


def test_provider_error_mapping_redacts_secret_message_and_details() -> None:
    payload = provider_exception_to_structured_error(
        RuntimeError("connection failed password=super-secret"),
        provider="postgis",
        operation="execute_query",
        source="postgis_connector",
        details={
            "dsn": "postgresql://user:secret@localhost/db",
            "password": "super-secret",
            "safe": "visible",
        },
    )

    assert "super-secret" not in payload["message"]
    assert "password=<redacted>" in payload["message"]
    assert payload["details"]["dsn"] == "<redacted>"
    assert payload["details"]["password"] == "<redacted>"
    assert payload["details"]["safe"] == "visible"


def test_make_provider_execution_error_preserves_value_error_compatibility() -> None:
    exc = RuntimeError("connection refused")
    wrapped = make_provider_execution_error(
        exc,
        provider="postgis",
        operation="execute_query",
        source="postgis_connector",
        message="Failed to execute PostGIS query. Error: connection refused",
    )

    assert isinstance(wrapped, ValueError)
    assert isinstance(wrapped, ProviderExecutionError)
    assert wrapped.structured_error["code"] == "provider.connection_failed"


def test_redact_provider_error_message_redacts_url_credentials() -> None:
    message = redact_provider_error_message(
        "failed for postgresql://user:secret-password@localhost/db"
    )

    assert "secret-password" not in message
    assert "postgresql://user:<redacted>@localhost/db" in message
