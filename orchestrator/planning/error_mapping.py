"""
orchestrator.planning.error_mapping

Structured error mapping helpers for planning-level exceptions.

This module is intentionally service-friendly and provider-agnostic. It maps
exceptions that happen around QuerySpec generation, validation, planner build,
and planning orchestration into the shared Phase 4 structured error contract.
"""

from __future__ import annotations

from typing import Any, Mapping

from orchestrator.error_contract import (
    CATEGORY_INTERNAL,
    CATEGORY_PLANNING,
    CATEGORY_VALIDATION,
    exception_to_error,
)

from orchestrator.planning.dag_executor import DagExecutionError, DagValidationError
from orchestrator.planning.llm_spec_generator import LLMSpecGenerationError
from orchestrator.planning.planner import PlanningError


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


def _is_retryable_message(message: str) -> bool:
    normalized = message.lower()

    retryable_markers = (
        "timeout",
        "timed out",
        "504",
        "502",
        "503",
        "gateway",
        "temporarily unavailable",
        "temporary",
        "rate limit",
        "too many requests",
        "connection reset",
        "connection aborted",
    )

    return any(marker in normalized for marker in retryable_markers)


def planning_exception_to_structured_error(
    exc: BaseException,
    *,
    source: str = "orchestrator_service",
    stage: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert service/planning exceptions into the shared structured error shape.

    This is used for exceptions that occur before or around DAG execution:
    - LLM QuerySpec generation
    - QuerySpec contract validation
    - deterministic planner build
    - service-level planning orchestration

    The returned dictionary is public-safe through the shared error contract.
    """
    chain = _exception_chain(exc)

    chain_details = [
        {
            "type": type(item).__name__,
            "message": str(item) or type(item).__name__,
        }
        for item in chain
    ]

    combined_message = " | ".join(
        str(item) or type(item).__name__
        for item in chain
    )

    code = "planning.failed"
    category = CATEGORY_PLANNING
    retryable = _is_retryable_message(combined_message)

    if isinstance(exc, LLMSpecGenerationError):
        code = "planning.llm_spec_generation_failed"
        category = CATEGORY_PLANNING

    elif isinstance(exc, PlanningError):
        code = "planning.failed"
        category = CATEGORY_PLANNING

    elif isinstance(exc, DagValidationError):
        code = "dag.validation_failed"
        category = CATEGORY_VALIDATION

    elif isinstance(exc, DagExecutionError):
        code = "dag.execution_failed"
        category = CATEGORY_PLANNING

    elif isinstance(exc, ValueError):
        code = "planning.validation_failed"
        category = CATEGORY_VALIDATION

    elif isinstance(exc, RuntimeError):
        code = "planning.runtime_failed"
        category = CATEGORY_PLANNING

    else:
        code = "planning.unexpected_exception"
        category = CATEGORY_INTERNAL

    merged_details: dict[str, Any] = {
        "exception_chain": chain_details,
    }

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
