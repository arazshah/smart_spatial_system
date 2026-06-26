"""
Planning execution policy helpers.

This module contains small policy decisions for QuerySpec planning execution.
It intentionally contains no query orchestration and no plugin execution.
"""

from __future__ import annotations

import os
from typing import Any


def is_query_spec_planning_enabled() -> bool:
    """
    Whether QuerySpec-based planning is enabled for /query.

    This is separate from legacy LLM intent planning.
    """
    value = os.getenv("QUERY_SPEC_PLANNING_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_kernel_execution_enabled(
    *,
    config: Any,
    metadata: dict[str, Any] | None = None,
    final_metadata: dict[str, Any] | None = None,
) -> bool:
    """
    Return whether experimental kernel execution should be enabled for
    QuerySpec planning.

    Precedence:

      1. Request-level explicit disable always disables.
      2. Request-level explicit enable is honored only when
         config.allow_request_kernel_execution is true.
      3. Deployment environment override is honored.
      4. Service config default is honored.
      5. Safe default is False.
    """

    truthy = {"true", "1", "yes", "y", "on", "enabled"}
    falsy = {"false", "0", "no", "n", "off", "disabled", ""}

    def _coerce(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value

        if value is None:
            return None

        if isinstance(value, (int, float)):
            return bool(value)

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in truthy:
                return True
            if normalized in falsy:
                return False

        return None

    def _request_flag() -> bool | None:
        for source in (metadata, final_metadata):
            if not isinstance(source, dict):
                continue

            for key in (
                "enable_kernel_execution",
                "kernel_execution",
                "use_kernel_execution",
            ):
                parsed = _coerce(source.get(key))
                if parsed is not None:
                    return parsed

            planning_options = source.get("planning")
            if isinstance(planning_options, dict):
                for key in (
                    "enable_kernel_execution",
                    "kernel_execution",
                    "use_kernel_execution",
                ):
                    parsed = _coerce(planning_options.get(key))
                    if parsed is not None:
                        return parsed

        return None

    allow_request_enable = bool(
        getattr(config, "allow_request_kernel_execution", False)
    )

    request_flag = _request_flag()

    if request_flag is not None:
        if request_flag is False:
            return False

        if allow_request_enable:
            return True

        # Request tried to enable but request-level enabling is not allowed.
        # Fall through to deployment/config defaults.

    for env_name in (
        "SMART_SPATIAL_ENABLE_KERNEL_EXECUTION",
        "ENABLE_KERNEL_EXECUTION",
    ):
        parsed = _coerce(os.getenv(env_name))
        if parsed is not None:
            return parsed

    parsed = _coerce(getattr(config, "enable_kernel_execution", None))
    if parsed is not None:
        return parsed

    return False
