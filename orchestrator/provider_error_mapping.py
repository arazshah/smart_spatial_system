"""
orchestrator.provider_error_mapping

Structured error mapping helpers for provider/plugin/connector failures.

This module is provider-agnostic and can be used by datasource connectors such
as PostGIS, geocoding providers, file loaders, and future plugin-backed data
providers.

The mapping is intentionally additive:
- legacy exception messages remain available
- exceptions can carry `.structured_error`
- callers that do not inspect structured_error keep working
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from orchestrator.error_contract import (
    CATEGORY_CONFIGURATION,
    CATEGORY_PROVIDER,
    exception_to_error,
)


_PASSWORD_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|api_key|apikey|secret)=(\S+)"
)
_URL_CREDENTIALS_RE = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^:/@\s]+):([^@\s]+)@"
)


class ProviderExecutionError(ValueError):
    """
    Provider/plugin execution error carrying the shared structured error payload.

    It intentionally subclasses ValueError for compatibility with existing
    provider/plugin tests and callers that already catch ValueError.
    """

    def __init__(
        self,
        message: str,
        *,
        structured_error: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.structured_error = structured_error


def redact_provider_error_message(message: str) -> str:
    """Redact common secret patterns from provider error messages."""
    safe = str(message)
    safe = _PASSWORD_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=<redacted>", safe)
    safe = _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}:<redacted>@", safe)
    return safe


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)

        if isinstance(cause, BaseException):
            current = cause
        elif isinstance(context, BaseException):
            current = context
        else:
            current = None

    return chain


def _is_retryable_provider_message(message: str) -> bool:
    normalized = message.lower()

    retryable_markers = (
        "timeout",
        "timed out",
        "connection refused",
        "connection reset",
        "connection aborted",
        "server closed the connection",
        "temporarily unavailable",
        "temporary failure",
        "could not connect",
        "network is unreachable",
        "too many connections",
        "rate limit",
        "too many requests",
        "502",
        "503",
        "504",
    )

    non_retryable_markers = (
        "password authentication failed",
        "authentication failed",
        "permission denied",
        "access denied",
        "role does not exist",
        "database does not exist",
        "relation does not exist",
        "column does not exist",
        "syntax error",
        "unsafe token",
        "only read-only",
    )

    if any(marker in normalized for marker in non_retryable_markers):
        return False

    return any(marker in normalized for marker in retryable_markers)


def provider_exception_to_structured_error(
    exc: BaseException,
    *,
    provider: str,
    operation: str | None = None,
    source: str | None = None,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert provider/plugin exceptions into the shared structured error shape.
    """
    existing = getattr(exc, "structured_error", None)
    if isinstance(existing, dict):
        return existing

    chain = _exception_chain(exc)

    raw_message = message if message is not None else str(exc)
    public_message = redact_provider_error_message(raw_message)

    combined_message = " | ".join(
        str(item) or type(item).__name__
        for item in chain
    ).lower()

    type_names = {type(item).__name__ for item in chain}

    code = "provider.failed"
    category = CATEGORY_PROVIDER

    if (
        "configuration" in combined_message
        or "config" in combined_message
        or "profile" in combined_message
        or "must be provided" in combined_message
        or "required" in combined_message
    ):
        code = "provider.configuration_invalid"
        category = CATEGORY_CONFIGURATION

    elif (
        "connection" in combined_message
        or "connect" in combined_message
        or "timeout" in combined_message
        or "timed out" in combined_message
        or "authentication" in combined_message
        or "password" in combined_message
        or "operationalerror" in {name.lower() for name in type_names}
    ):
        code = "provider.connection_failed"
        category = CATEGORY_PROVIDER

    elif (
        "sql" in combined_message
        or "query" in combined_message
        or "syntax error" in combined_message
        or "relation does not exist" in combined_message
        or "column does not exist" in combined_message
        or "postgis" in combined_message
    ):
        code = "provider.query_failed"
        category = CATEGORY_PROVIDER

    merged_details: dict[str, Any] = {
        "provider": provider,
        "exception_chain": [
            {
                "type": type(item).__name__,
                "message": redact_provider_error_message(str(item) or type(item).__name__),
            }
            for item in chain
        ],
    }

    if operation is not None:
        merged_details["operation"] = operation

    if details:
        merged_details.update(dict(details))

    return exception_to_error(
        exc,
        code=code,
        category=category,
        retryable=_is_retryable_provider_message(combined_message),
        source=source or provider,
        message=public_message,
        details=merged_details,
    ).to_dict()


def make_provider_execution_error(
    exc: BaseException,
    *,
    provider: str,
    operation: str | None = None,
    source: str | None = None,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> ProviderExecutionError:
    """
    Build a compatibility-friendly provider exception carrying structured_error.
    """
    public_message = redact_provider_error_message(message if message is not None else str(exc))

    structured_error = provider_exception_to_structured_error(
        exc,
        provider=provider,
        operation=operation,
        source=source,
        message=public_message,
        details=details,
    )

    return ProviderExecutionError(
        public_message,
        structured_error=structured_error,
    )
