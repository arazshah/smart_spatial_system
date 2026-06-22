"""
orchestrator.error_contract

Provider-agnostic structured error contract helpers.

This module defines a small, stable, public-safe error shape that can be used by
service, planning, kernel execution, capability resolution, and provider/plugin
layers without depending on any specific datasource, UI, language, or case study.

The contract is intentionally additive for Phase 4:
- It does not change existing service behavior by itself.
- Later hardening steps can progressively map existing errors to this contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


CATEGORY_VALIDATION = "validation_error"
CATEGORY_CONFIGURATION = "configuration_error"
CATEGORY_PLANNING = "planning_error"
CATEGORY_CAPABILITY_RESOLUTION = "capability_resolution_error"
CATEGORY_CAPABILITY_CONTRACT = "capability_contract_error"
CATEGORY_KERNEL_EXECUTION = "kernel_execution_error"
CATEGORY_PROVIDER = "provider_error"
CATEGORY_INTERNAL = "internal_error"


KNOWN_CATEGORIES = {
    CATEGORY_VALIDATION,
    CATEGORY_CONFIGURATION,
    CATEGORY_PLANNING,
    CATEGORY_CAPABILITY_RESOLUTION,
    CATEGORY_CAPABILITY_CONTRACT,
    CATEGORY_KERNEL_EXECUTION,
    CATEGORY_PROVIDER,
    CATEGORY_INTERNAL,
}


SENSITIVE_KEY_FRAGMENTS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "dsn",
    "connection_string",
    "conn_string",
}


MAX_STRING_LENGTH = 500
MAX_LIST_ITEMS = 50
MAX_DICT_ITEMS = 100
MAX_DEPTH = 5


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _truncate_string(value: str) -> str:
    if len(value) <= MAX_STRING_LENGTH:
        return value
    return value[:MAX_STRING_LENGTH] + "...<truncated>"


def sanitize_error_detail(value: Any, *, _depth: int = 0) -> Any:
    """
    Return a public-safe version of an error detail value.

    This function recursively:
    - redacts values under sensitive keys
    - truncates long strings
    - limits list/dict sizes
    - limits recursion depth
    - converts unknown objects to safe strings
    """
    if _depth >= MAX_DEPTH:
        return "<max_depth_exceeded>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return _truncate_string(value)

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}

        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_DICT_ITEMS:
                sanitized["<truncated_items>"] = True
                break

            key_str = str(key)

            if _is_sensitive_key(key_str):
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = sanitize_error_detail(
                    item,
                    _depth=_depth + 1,
                )

        return sanitized

    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        sanitized_items = [
            sanitize_error_detail(item, _depth=_depth + 1)
            for item in items[:MAX_LIST_ITEMS]
        ]

        if len(items) > MAX_LIST_ITEMS:
            sanitized_items.append("<truncated_items>")

        return sanitized_items

    try:
        return _truncate_string(str(value))
    except Exception:
        return f"<unserializable:{type(value).__name__}>"


def sanitize_error_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}

    sanitized = sanitize_error_detail(dict(details))
    if isinstance(sanitized, dict):
        return sanitized

    return {"value": sanitized}


def normalize_error_category(category: str | None) -> str:
    if not category:
        return CATEGORY_INTERNAL

    normalized = str(category).strip()

    if normalized in KNOWN_CATEGORIES:
        return normalized

    return CATEGORY_INTERNAL


@dataclass(frozen=True)
class StructuredError:
    """
    Stable structured error object.

    Fields:
      code:
        Machine-readable error code, e.g. "planning.failed".

      message:
        User-safe human-readable message.

      category:
        One of KNOWN_CATEGORIES.

      retryable:
        Whether retrying the same request may succeed.

      details:
        Public-safe details for debugging and clients.

      source:
        Optional subsystem/layer name.

      cause_code:
        Optional machine-readable nested/root cause code.
    """

    code: str
    message: str
    category: str = CATEGORY_INTERNAL
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    cause_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Return the stable public dictionary representation.

        The schema intentionally includes source/cause_code even when None so
        API clients can rely on stable keys.
        """
        return {
            "code": str(self.code),
            "message": str(self.message),
            "category": normalize_error_category(self.category),
            "retryable": bool(self.retryable),
            "details": sanitize_error_details(self.details),
            "source": self.source,
            "cause_code": self.cause_code,
        }


def make_error(
    *,
    code: str,
    message: str,
    category: str = CATEGORY_INTERNAL,
    retryable: bool = False,
    details: Mapping[str, Any] | None = None,
    source: str | None = None,
    cause_code: str | None = None,
) -> StructuredError:
    """
    Build a StructuredError with sanitized details and normalized category.
    """
    return StructuredError(
        code=str(code or "error.unknown"),
        message=str(message or "An error occurred."),
        category=normalize_error_category(category),
        retryable=bool(retryable),
        details=sanitize_error_details(details),
        source=str(source) if source is not None else None,
        cause_code=str(cause_code) if cause_code is not None else None,
    )


def exception_to_error(
    exc: BaseException,
    *,
    code: str = "internal.exception",
    category: str = CATEGORY_INTERNAL,
    retryable: bool = False,
    source: str | None = None,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> StructuredError:
    """
    Convert an exception to a public-safe StructuredError.

    The exception message is used as the public message unless an explicit
    message is provided. The exception type is included as safe metadata.
    """
    merged_details: dict[str, Any] = {
        "exception_type": type(exc).__name__,
    }

    if details:
        merged_details.update(dict(details))

    public_message = message
    if public_message is None:
        public_message = str(exc) or type(exc).__name__

    return make_error(
        code=code,
        message=public_message,
        category=category,
        retryable=retryable,
        details=merged_details,
        source=source,
    )


def normalize_error(value: Any) -> dict[str, Any]:
    """
    Normalize common error representations into the stable structured shape.

    Accepted inputs:
    - StructuredError
    - dict-like objects
    - Exception
    - string/other values
    """
    if isinstance(value, StructuredError):
        return value.to_dict()

    if isinstance(value, BaseException):
        return exception_to_error(value).to_dict()

    if isinstance(value, Mapping):
        code = value.get("code") or value.get("error_code") or "error.unknown"
        message = value.get("message") or value.get("error") or "An error occurred."
        category = value.get("category") or CATEGORY_INTERNAL
        retryable = bool(value.get("retryable", False))
        details = value.get("details")

        if not isinstance(details, Mapping):
            details = {
                key: item
                for key, item in value.items()
                if key
                not in {
                    "code",
                    "error_code",
                    "message",
                    "error",
                    "category",
                    "retryable",
                    "details",
                    "source",
                    "cause_code",
                }
            }

        return make_error(
            code=str(code),
            message=str(message),
            category=str(category),
            retryable=retryable,
            details=details,
            source=value.get("source"),
            cause_code=value.get("cause_code"),
        ).to_dict()

    return make_error(
        code="error.unknown",
        message=str(value),
        category=CATEGORY_INTERNAL,
    ).to_dict()
