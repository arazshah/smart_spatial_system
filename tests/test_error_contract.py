from __future__ import annotations

from orchestrator.error_contract import (
    CATEGORY_CAPABILITY_CONTRACT,
    CATEGORY_INTERNAL,
    CATEGORY_PLANNING,
    CATEGORY_PROVIDER,
    StructuredError,
    exception_to_error,
    make_error,
    normalize_error,
    sanitize_error_details,
)


def test_make_error_returns_stable_public_shape() -> None:
    error = make_error(
        code="planning.failed",
        message="Planning failed.",
        category=CATEGORY_PLANNING,
        retryable=False,
        details={
            "operation": "score_features",
            "missing": ["score_field"],
        },
        source="planning",
        cause_code="capability.missing_property",
    )

    payload = error.to_dict()

    assert payload == {
        "code": "planning.failed",
        "message": "Planning failed.",
        "category": "planning_error",
        "retryable": False,
        "details": {
            "operation": "score_features",
            "missing": ["score_field"],
        },
        "source": "planning",
        "cause_code": "capability.missing_property",
    }


def test_error_details_redact_sensitive_values_recursively() -> None:
    details = {
        "host": "localhost",
        "password": "super-secret",
        "nested": {
            "api_key": "abc-123",
            "token_value": "token-123",
            "safe": "ok",
        },
        "items": [
            {
                "authorization": "Bearer secret",
                "name": "public",
            }
        ],
    }

    sanitized = sanitize_error_details(details)

    assert sanitized["host"] == "localhost"
    assert sanitized["password"] == "<redacted>"
    assert sanitized["nested"]["api_key"] == "<redacted>"
    assert sanitized["nested"]["token_value"] == "<redacted>"
    assert sanitized["nested"]["safe"] == "ok"
    assert sanitized["items"][0]["authorization"] == "<redacted>"
    assert sanitized["items"][0]["name"] == "public"


def test_unknown_category_is_normalized_to_internal_error() -> None:
    payload = make_error(
        code="x",
        message="Unknown category.",
        category="not_a_real_category",
    ).to_dict()

    assert payload["category"] == CATEGORY_INTERNAL


def test_exception_to_error_includes_exception_type_without_raw_object() -> None:
    exc = ValueError("Invalid capability parameters.")

    payload = exception_to_error(
        exc,
        code="capability.contract.invalid",
        category=CATEGORY_CAPABILITY_CONTRACT,
        source="kernel_execution",
        details={
            "password": "must-not-leak",
            "parameter": "score_field",
        },
    ).to_dict()

    assert payload["code"] == "capability.contract.invalid"
    assert payload["message"] == "Invalid capability parameters."
    assert payload["category"] == CATEGORY_CAPABILITY_CONTRACT
    assert payload["retryable"] is False
    assert payload["source"] == "kernel_execution"
    assert payload["details"]["exception_type"] == "ValueError"
    assert payload["details"]["password"] == "<redacted>"
    assert payload["details"]["parameter"] == "score_field"


def test_normalize_error_accepts_structured_error() -> None:
    error = StructuredError(
        code="provider.failed",
        message="Provider failed.",
        category=CATEGORY_PROVIDER,
        retryable=True,
        details={"provider": "sample"},
        source="provider",
    )

    payload = normalize_error(error)

    assert payload["code"] == "provider.failed"
    assert payload["message"] == "Provider failed."
    assert payload["category"] == CATEGORY_PROVIDER
    assert payload["retryable"] is True
    assert payload["details"] == {"provider": "sample"}
    assert payload["source"] == "provider"
    assert payload["cause_code"] is None


def test_normalize_error_accepts_dict_and_redacts_sensitive_details() -> None:
    payload = normalize_error(
        {
            "error_code": "provider.connection_failed",
            "error": "Connection failed.",
            "category": CATEGORY_PROVIDER,
            "retryable": True,
            "details": {
                "database": "sample",
                "dsn": "postgresql://user:password@localhost/db",
            },
        }
    )

    assert payload["code"] == "provider.connection_failed"
    assert payload["message"] == "Connection failed."
    assert payload["category"] == CATEGORY_PROVIDER
    assert payload["retryable"] is True
    assert payload["details"]["database"] == "sample"
    assert payload["details"]["dsn"] == "<redacted>"


def test_normalize_error_accepts_exception() -> None:
    payload = normalize_error(RuntimeError("Something failed."))

    assert payload["code"] == "internal.exception"
    assert payload["message"] == "Something failed."
    assert payload["category"] == CATEGORY_INTERNAL
    assert payload["details"]["exception_type"] == "RuntimeError"


def test_normalize_error_accepts_plain_string() -> None:
    payload = normalize_error("plain error")

    assert payload["code"] == "error.unknown"
    assert payload["message"] == "plain error"
    assert payload["category"] == CATEGORY_INTERNAL
    assert payload["retryable"] is False
    assert payload["details"] == {}
