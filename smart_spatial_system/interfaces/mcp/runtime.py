"""
smart_spatial_system.interfaces.mcp.runtime

Transport-independent tool runtime: decides which tools exist for a given
policy and executes calls with validation, limits, artifact handling and
audit logging. ``server.py`` is a thin adapter over this class.
"""

from __future__ import annotations

import inspect
import logging
import time
from pathlib import Path
from typing import Any

import anyio

from .artifacts import (
    ArtifactError,
    ArtifactStore,
    summarize_raster,
    summarize_vector,
)
from .catalog import (
    RASTER_LIST_PARAM_NAMES,
    RASTER_PARAM_NAMES,
    VECTOR_LIST_PARAM_NAMES,
    VECTOR_PARAM_NAMES,
    ToolSpec,
    build_tool_specs,
)
from .codec import as_feature_collection, check_input_limits, encode_result, to_jsonable
from .config import McpSettings
from .policy import (
    PolicyViolation,
    check_filename,
    check_path,
    check_url,
    is_tool_allowed,
    safe_error_text,
)

logger = logging.getLogger("smart_spatial_system.mcp")

_ID_SCHEMA = {"type": "string", "pattern": "^art_[A-Za-z0-9_-]{32}$"}


class ToolNotFound(LookupError):
    pass


class ToolCallError(RuntimeError):
    """Execution failed; the message is already safe to show to the client."""


def _is_ref(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"artifact_id"}


def _guess_kind(data: Any, output_kind: str) -> str:
    if output_kind == "raster":
        return "raster"
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        return "vector"
    return "json"


def _default_artifacts_dir() -> Path:
    from orchestrator.runtime_paths import RuntimePaths

    return RuntimePaths.from_env().cache / "mcp_artifacts"


class ToolRuntime:
    def __init__(
        self, registry: Any, settings: McpSettings, *, store: ArtifactStore | None = None
    ) -> None:
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
        self.store = store
        if self.store is None and settings.artifacts_enabled:
            self.store = ArtifactStore(
                settings.artifacts_dir or _default_artifacts_dir(),
                ttl_seconds=settings.artifact_ttl_seconds,
                max_artifact_bytes=settings.max_artifact_bytes,
                max_total_bytes=settings.max_artifact_total_bytes,
            )
        if self.store is not None:
            self.specs.update(self._builtin_specs())
        self._limiter = anyio.CapacityLimiter(settings.max_concurrency)

    # ------------------------------------------------------------------ builtins
    def _builtin_specs(self) -> dict[str, ToolSpec]:
        store = self.store
        assert store is not None

        def artifact_get(
            artifact_id: str, offset: int = 0, limit: int = 100, include_data: bool = False
        ) -> dict[str, Any]:
            if not (isinstance(offset, int) and isinstance(limit, int)):
                raise ValueError("offset and limit must be integers.")
            if offset < 0 or not 1 <= limit <= 1000:
                raise ValueError("offset must be >= 0 and limit between 1 and 1000.")
            item = store.get(artifact_id)
            out: dict[str, Any] = {
                "artifact_id": item.artifact_id,
                "kind": item.kind,
                "summary": item.summary,
                "size_bytes": item.size_bytes,
            }
            if item.kind == "vector":
                features = item.data.get("features", [])
                out.update(
                    total_features=len(features),
                    offset=offset,
                    features=features[offset : offset + limit],
                )
            elif item.kind == "raster" and not include_data:
                out["data_included"] = False  # pass the handle to a tool instead
            else:
                out["data"] = item.data
            return out

        def artifact_delete(artifact_id: str) -> dict[str, Any]:
            return {"deleted": store.delete(artifact_id)}

        def make(name, description, properties, required, handler, destructive=False):
            return ToolSpec(
                name=name,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                risks=frozenset(),
                handler=handler,
                params=tuple(properties),
                path_params=(),
                url_params=(),
                output_kind="json",
                long_running=False,
                builtin=True,
                destructive=destructive,
            )

        return {
            "artifact_get": make(
                "artifact_get",
                "Read an artifact returned by another tool. Vector artifacts are paged "
                "(offset/limit); raster data is only returned with include_data=true and "
                "when small enough, otherwise pass the handle to a tool.",
                {
                    "artifact_id": _ID_SCHEMA,
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "include_data": {"type": "boolean"},
                },
                ["artifact_id"],
                artifact_get,
            ),
            "artifact_delete": make(
                "artifact_delete",
                "Delete an artifact before it expires.",
                {"artifact_id": _ID_SCHEMA},
                ["artifact_id"],
                artifact_delete,
                destructive=True,
            ),
        }

    # ---------------------------------------------------------------- validation
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
            value = kwargs.get(param)
            if isinstance(value, list):
                kwargs[param] = [check_path(v, self.settings, param=param) for v in value]
            elif value is not None:
                kwargs[param] = check_path(value, self.settings, param=param)
        for param in spec.name_params:
            if kwargs.get(param) is not None:
                kwargs[param] = check_filename(kwargs[param], param=param)
        for param in spec.url_params:
            if kwargs.get(param) is not None:
                kwargs[param] = check_url(kwargs[param], self.settings, param=param)
        return kwargs

    def _resolve_refs(self, spec: ToolSpec, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Replace {"artifact_id": ...} values with the stored data (kind-checked)."""
        if spec.builtin:
            return kwargs
        resolved = dict(kwargs)

        def fetch(param: str, value: dict[str, Any], wanted: str) -> Any:
            if self.store is None:
                raise PolicyViolation(f"'{param}' does not accept an artifact reference.")
            try:
                item = self.store.get(value["artifact_id"])
            except ArtifactError as exc:
                raise ToolCallError(str(exc)) from exc
            if item.kind != wanted:
                raise PolicyViolation(
                    f"'{param}' needs a {wanted} artifact but got a {item.kind} artifact."
                )
            return item.data

        for param, value in kwargs.items():
            list_kind = (
                "raster" if param in RASTER_LIST_PARAM_NAMES
                else "vector" if param in VECTOR_LIST_PARAM_NAMES else None
            )
            if list_kind and isinstance(value, list):
                resolved[param] = [
                    fetch(param, v, list_kind) if _is_ref(v) else v for v in value
                ]
                continue
            if not _is_ref(value):
                continue
            wanted = (
                "raster"
                if param in RASTER_PARAM_NAMES
                else "vector"
                if param in VECTOR_PARAM_NAMES
                else None
            )
            if wanted is None:
                raise PolicyViolation(f"'{param}' does not accept an artifact reference.")
            resolved[param] = fetch(param, value, wanted)
        return resolved

    def _finish(self, spec: ToolSpec, result: Any) -> Any:
        """Serialize a result; store it as an artifact when it is a raster or large."""
        if spec.builtin or self.store is None:
            return encode_result(result, max_bytes=self.settings.max_output_bytes)
        data = as_feature_collection(to_jsonable(result))
        kind = _guess_kind(data, spec.output_kind)
        inline_ok = kind != "raster"
        if inline_ok:
            data_for_size, size = encode_result(
                data, max_bytes=self.settings.max_artifact_bytes
            )
            if size <= min(self.settings.inline_output_bytes, self.settings.max_output_bytes):
                return data_for_size, size
        summary = summarize_raster(data) if kind == "raster" else (
            summarize_vector(data) if kind == "vector" else {}
        )
        try:
            item = self.store.put(kind, data, summary)
        except ArtifactError as exc:
            raise ToolCallError(str(exc)) from exc
        handle = {
            "type": "artifact",
            "artifact_id": item.artifact_id,
            "kind": kind,
            "summary": summary,
            "size_bytes": item.size_bytes,
            "expires_in_seconds": self.settings.artifact_ttl_seconds,
            "hint": (
                'Pass {"artifact_id": "<id>"} as an input of another tool, or read it '
                "with artifact_get."
            ),
        }
        return handle, item.size_bytes

    # ----------------------------------------------------------------- execution
    async def call(self, name: str, arguments: dict[str, Any] | None) -> Any:
        spec = self.specs.get(name)
        if spec is None:
            # Same answer for "does not exist" and "disabled by policy":
            # do not reveal which hidden capabilities are installed.
            raise ToolNotFound(f"Unknown tool: {name}")
        started = time.monotonic()
        status = "ok"
        size = 0
        run = anyio.to_thread.run_sync
        try:
            kwargs = self.validate(spec, dict(arguments or {}))
            try:
                with anyio.fail_after(self.settings.timeout_seconds):
                    kwargs = await run(self._resolve_refs, spec, kwargs, limiter=self._limiter)
                    if inspect.iscoroutinefunction(spec.handler):
                        result = await spec.handler(**kwargs)
                    else:
                        result = await run(
                            lambda: spec.handler(**kwargs),
                            limiter=self._limiter,
                            abandon_on_cancel=True,
                        )
                    data, size = await run(self._finish, spec, result, limiter=self._limiter)
            except TimeoutError as exc:
                status = "timeout"
                raise ToolCallError(
                    f"Tool '{name}' exceeded {self.settings.timeout_seconds}s."
                ) from exc
            except (PolicyViolation, ToolCallError):
                raise
            except Exception as exc:
                status = "error"
                raise ToolCallError(safe_error_text(exc)) from exc
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
