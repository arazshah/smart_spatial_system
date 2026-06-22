"""
orchestrator.plugin_error_mapping

Structured error mapping helpers for plugin loading and capability registry
registration failures.

This layer covers errors that happen before capability execution:
- plugin module import failures
- invalid plugin manifests/contracts
- duplicate capability registrations
- missing capability callables
- registry registration failures

The mapping is additive and does not change registry behavior by itself.
"""

from __future__ import annotations

from typing import Any, Mapping

from orchestrator.error_contract import (
    CATEGORY_CAPABILITY_CONTRACT,
    CATEGORY_CAPABILITY_RESOLUTION,
    CATEGORY_CONFIGURATION,
    CATEGORY_INTERNAL,
    exception_to_error,
)


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


def plugin_exception_to_structured_error(
    exc: BaseException,
    *,
    module_name: str | None = None,
    plugin_id: str | None = None,
    capability_name: str | None = None,
    stage: str | None = None,
    source: str = "capability_registry",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert plugin loading / registry registration exceptions into the shared
    structured error contract.
    """
    existing = getattr(exc, "structured_error", None)
    if isinstance(existing, dict):
        return existing

    chain = _exception_chain(exc)
    combined_message = " | ".join(
        str(item) or type(item).__name__
        for item in chain
    ).lower()
    type_names = {type(item).__name__ for item in chain}

    code = "plugin.registration_failed"
    category = CATEGORY_CONFIGURATION
    retryable = False

    if (
        isinstance(exc, (ModuleNotFoundError, ImportError))
        or "ModuleNotFoundError" in type_names
        or "ImportError" in type_names
        or "no module named" in combined_message
        or "cannot import" in combined_message
    ):
        code = "plugin.import_failed"
        category = CATEGORY_CONFIGURATION

    elif (
        "duplicate capability" in combined_message
        or "already registered" in combined_message
    ):
        code = "plugin.duplicate_capability"
        category = CATEGORY_CONFIGURATION

    elif (
        "does not define plugin" in combined_message
        or "has no manifest" in combined_message
        or "manifest" in combined_message and "no id" in combined_message
        or "has no registered capabilities" in combined_message
        or "invalid capability registration" in combined_message
    ):
        code = "plugin.contract_invalid"
        category = CATEGORY_CAPABILITY_CONTRACT

    elif (
        "no callable" in combined_message
        or "callable with the same name" in combined_message
        or "function" in combined_message and "registered by plugin" in combined_message
    ):
        code = "plugin.capability_callable_missing"
        category = CATEGORY_CAPABILITY_RESOLUTION

    elif isinstance(exc, ValueError):
        code = "plugin.registration_failed"
        category = CATEGORY_CONFIGURATION

    else:
        code = "plugin.unexpected_exception"
        category = CATEGORY_INTERNAL

    merged_details: dict[str, Any] = {
        "exception_chain": [
            {
                "type": type(item).__name__,
                "message": str(item) or type(item).__name__,
            }
            for item in chain
        ],
    }

    if module_name is not None:
        merged_details["module"] = module_name

    if plugin_id is not None:
        merged_details["plugin_id"] = plugin_id

    if capability_name is not None:
        merged_details["capability_name"] = capability_name

    if stage is not None:
        merged_details["stage"] = stage

    if details:
        merged_details.update(dict(details))

    return exception_to_error(
        exc,
        code=code,
        category=category,
        retryable=retryable,
        source=source,
        details=merged_details,
    ).to_dict()
