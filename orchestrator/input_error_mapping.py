"""
orchestrator.input_error_mapping

Structured error mapping helpers for input/upload reference resolution and
loader plugin contract failures.

Covers:
- invalid input reference payloads
- missing/unsupported upload references
- loader plugin import/call failures
- invalid loader outputs
- loader contract violations

The mapping is additive: legacy exception messages remain unchanged, while
exceptions may carry `.structured_error`.
"""

from __future__ import annotations

from typing import Any, Mapping

from orchestrator.error_contract import (
    CATEGORY_CAPABILITY_CONTRACT,
    CATEGORY_CONFIGURATION,
    CATEGORY_INTERNAL,
    CATEGORY_PROVIDER,
    CATEGORY_VALIDATION,
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


def input_exception_to_structured_error(
    exc: BaseException,
    *,
    reference_kind: str | None = None,
    upload_id: str | None = None,
    stage: str | None = None,
    source: str = "input_reference_resolver",
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert input/upload reference resolution errors to the shared structured
    error contract.
    """
    existing = getattr(exc, "structured_error", None)
    if isinstance(existing, dict):
        payload = dict(existing)
        payload_details = dict(payload.get("details") or {})

        if reference_kind is not None:
            payload_details.setdefault("reference_kind", reference_kind)
        if upload_id is not None:
            payload_details.setdefault("upload_id", upload_id)
        if stage is not None:
            payload_details.setdefault("stage", stage)
        if details:
            payload_details.update(dict(details))

        payload["details"] = payload_details
        return payload

    chain = _exception_chain(exc)
    raw_message = message if message is not None else str(exc)
    combined_message = (
        str(raw_message)
        + " | "
        + " | ".join(str(item) or type(item).__name__ for item in chain)
    ).lower()

    code = "input.resolution_failed"
    category = CATEGORY_VALIDATION
    retryable = False

    if (
        "inputs must be a dict" in combined_message
        or "must be a dict" in combined_message
        or "must be an object" in combined_message
    ):
        code = "input.invalid_payload"
        category = CATEGORY_VALIDATION

    elif (
        "could not import" in combined_message
        or "loader plugin" in combined_message and "no module named" in combined_message
        or stage == "loader_plugin_import"
    ):
        code = "input.resolution_failed"
        category = CATEGORY_VALIDATION

    elif (
        "not found" in combined_message
        or "missing" in combined_message
        or "unknown upload" in combined_message
        or "unknown upload_id" in combined_message
        or "no such file" in combined_message
        or "does not exist" in combined_message
    ):
        code = "input.reference_not_found"
        category = CATEGORY_VALIDATION

    elif (
        "unsupported reference kind" in combined_message
        or "unsupported" in combined_message
        or "plugin loading disabled" in combined_message
        or "fallback unavailable" in combined_message
        or "cannot resolve upload" in combined_message
    ):
        code = "input.reference_unsupported"
        category = CATEGORY_VALIDATION

    merged_details: dict[str, Any] = {
        "exception_chain": [
            {
                "type": type(item).__name__,
                "message": str(item) or type(item).__name__,
            }
            for item in chain
        ],
    }

    if reference_kind is not None:
        merged_details["reference_kind"] = reference_kind

    if upload_id is not None:
        merged_details["upload_id"] = upload_id

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
        message=raw_message,
        details=merged_details,
    ).to_dict()


def loader_exception_to_structured_error(
    exc: BaseException,
    *,
    module_name: str | None = None,
    kind: str | None = None,
    function_name: str | None = None,
    stage: str | None = None,
    source: str = "loader_plugin_contract",
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert loader plugin contract/execution errors to the shared structured
    error contract.
    """
    existing = getattr(exc, "structured_error", None)
    if isinstance(existing, dict):
        return existing

    chain = _exception_chain(exc)
    raw_message = message if message is not None else str(exc)
    combined_message = (
        str(raw_message)
        + " | "
        + " | ".join(str(item) or type(item).__name__ for item in chain)
    ).lower()
    type_names = {type(item).__name__ for item in chain}

    code = "loader.contract_invalid"
    category = CATEGORY_CAPABILITY_CONTRACT
    retryable = False

    if (
        isinstance(exc, (ModuleNotFoundError, ImportError))
        or "ModuleNotFoundError" in type_names
        or "ImportError" in type_names
        or "could not import loader plugin" in combined_message
        or "no module named" in combined_message
    ):
        code = "loader.plugin_import_failed"
        category = CATEGORY_CONFIGURATION

    elif (
        "unsupported loader kind" in combined_message
        or "module_name must not be empty" in combined_message
    ):
        code = "loader.contract_invalid"
        category = CATEGORY_CAPABILITY_CONTRACT

    elif (
        "must define callable" in combined_message
        or "no compatible callable" in combined_message
        or "could not be called with the standard contract" in combined_message
    ):
        code = "loader.contract_invalid"
        category = CATEGORY_CAPABILITY_CONTRACT

    elif (
        "loader output" in combined_message
        or "featurecollection" in combined_message
        or "features" in combined_message
        or "metadata" in combined_message
        or "data" in combined_message
        or "dict-like object" in combined_message
    ):
        code = "loader.output_invalid"
        category = CATEGORY_CAPABILITY_CONTRACT

    elif (
        "loader" in combined_message
        and "failed" in combined_message
    ):
        code = "loader.execution_failed"
        category = CATEGORY_PROVIDER

    elif not isinstance(exc, (RuntimeError, ValueError, TypeError)):
        code = "loader.unexpected_exception"
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

    if kind is not None:
        merged_details["kind"] = kind

    if function_name is not None:
        merged_details["function_name"] = function_name

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
        message=raw_message,
        details=merged_details,
    ).to_dict()
