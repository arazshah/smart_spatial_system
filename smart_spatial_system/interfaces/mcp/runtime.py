"""
smart_spatial_system.interfaces.mcp.runtime

Transport-independent tool runtime: decides which tools exist for a given
policy and executes calls with validation, limits and audit logging.
``server.py`` is a thin adapter over this class.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anyio

from .catalog import ToolSpec, build_tool_specs
from .codec import check_input_limits, encode_result
from .config import McpSettings
from .policy import PolicyViolation, check_path, check_url, is_tool_allowed, safe_error_text

logger = logging.getLogger("smart_spatial_system.mcp")


class ToolNotFound(LookupError):
    pass


class ToolCallError(RuntimeError):
    """Execution failed; the message is already safe to show to the client."""


class ToolRuntime:
    def __init__(self, registry: Any, settings: McpSettings) -> None:
        self.settings = settings
        all_specs = build_tool_specs(registry)
        self.specs: dict[str, ToolSpec] = {
            name: spec
            for name, spec in all_specs.items()
            if is_tool_allowed(name, spec.risks, settings)
        }
        self.hidden: dict[str, ToolSpec] = {
            name: spec for name, spec in all_specs.items() if name not in self.specs
        }
        self._limiter = anyio.CapacityLimiter(settings.max_concurrency)

    def validate(self, spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply limits and path/URL policy; returns the kwargs for the plugin."""
        check_input_limits(arguments, max_bytes=self.settings.max_input_bytes)
        unknown = set(arguments) - set(spec.params)
        if unknown:
            raise PolicyViolation(f"Unknown argument(s): {sorted(unknown)}.")
        missing = [p for p in spec.input_schema["required"] if p not in arguments]
        if missing:
            raise PolicyViolation(f"Missing required argument(s): {missing}.")
        kwargs = dict(arguments)
        for param in spec.path_params:
            if kwargs.get(param) is not None:
                kwargs[param] = check_path(kwargs[param], self.settings, param=param)
        for param in spec.url_params:
            if kwargs.get(param) is not None:
                kwargs[param] = check_url(kwargs[param], self.settings, param=param)
        return kwargs

    async def call(self, name: str, arguments: dict[str, Any] | None) -> Any:
        spec = self.specs.get(name)
        if spec is None:
            # Same answer for "does not exist" and "disabled by policy":
            # do not reveal which hidden capabilities are installed.
            raise ToolNotFound(f"Unknown tool: {name}")
        started = time.monotonic()
        status = "ok"
        size = 0
        try:
            kwargs = self.validate(spec, dict(arguments or {}))
            try:
                with anyio.fail_after(self.settings.timeout_seconds):
                    result = await anyio.to_thread.run_sync(
                        lambda: spec.handler(**kwargs),
                        limiter=self._limiter,
                        abandon_on_cancel=True,
                    )
            except TimeoutError as exc:
                status = "timeout"
                raise ToolCallError(
                    f"Tool '{name}' exceeded {self.settings.timeout_seconds}s."
                ) from exc
            except PolicyViolation:
                raise
            except Exception as exc:
                status = "error"
                raise ToolCallError(safe_error_text(exc)) from exc
            data, size = encode_result(result, max_bytes=self.settings.max_output_bytes)
            return data
        except PolicyViolation:
            status = "denied"
            raise
        finally:
            logger.info(
                "mcp_tool_call tool=%s status=%s ms=%d out_bytes=%d",
                name,
                status,
                int((time.monotonic() - started) * 1000),
                size,
            )
