"""
smart_spatial_system.interfaces.mcp.catalog

Builds MCP tool specs from the capability registry. The JSON schema of each
tool is derived from the capability's declared inputs and Python signature;
nothing is hand-written per tool, so a new plugin becomes a tool by being
registered. Only *declared* inputs that also exist on the callable are
exposed, and ``policy.NEVER_EXPOSED`` names are always dropped.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from .policy import (
    NAME_PARAMS,
    NEVER_EXPOSED,
    PATH_PARAMS,
    URL_PARAMS,
    classify,
)

# Plugins take GeoJSON-like values under these names (annotation is just Any).
VECTOR_PARAM_NAMES = frozenset(
    {
        "features",
        "vector",
        "points",
        "polygons",
        "zones",
        "source_features",
        "target_features",
        "left_features",
        "right_features",
        "metro",
        "malls",
        "roads",
        "stations",
    }
)
RASTER_PARAM_NAMES = frozenset({"raster"})
RASTER_LIST_PARAM_NAMES = frozenset({"rasters"})

ARTIFACT_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"artifact_id": {"type": "string", "pattern": "^art_[A-Za-z0-9_-]{32}$"}},
    "required": ["artifact_id"],
    "additionalProperties": False,
}
_VECTOR_SCHEMA: dict[str, Any] = {
    "description": (
        "GeoJSON FeatureCollection, a single Feature, or an array of Features; or an "
        "artifact reference {\"artifact_id\": ...} returned by an earlier tool call."
    ),
    "anyOf": [
        ARTIFACT_REF_SCHEMA,
        {"type": "object"},
        {"type": "array", "items": {"type": "object"}},
    ],
}
_RASTER_SCHEMA: dict[str, Any] = {
    "description": (
        "Raster object (array data plus metadata such as transform and nodata), or an "
        "artifact reference {\"artifact_id\": ...} returned by an earlier tool call."
    ),
    "anyOf": [ARTIFACT_REF_SCHEMA, {"type": "object"}],
}
_RASTER_LIST_SCHEMA: dict[str, Any] = {
    "description": "List of rasters; each item is a raster object or an artifact reference.",
    "type": "array",
    "items": _RASTER_SCHEMA,
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risks: frozenset[str]
    handler: Callable[..., Any]
    params: tuple[str, ...]
    path_params: tuple[str, ...]
    url_params: tuple[str, ...]
    output_kind: str
    long_running: bool
    builtin: bool = False
    destructive: bool = False
    name_params: tuple[str, ...] = ()


def _param_schema(name: str, annotation: Any) -> dict[str, Any]:
    if name in VECTOR_PARAM_NAMES:
        return dict(_VECTOR_SCHEMA)
    if name in RASTER_PARAM_NAMES:
        return dict(_RASTER_SCHEMA)
    if name in RASTER_LIST_PARAM_NAMES:
        return dict(_RASTER_LIST_SCHEMA)
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}
    try:
        schema = TypeAdapter(annotation).json_schema()
    except Exception:  # custom SDK types, forward refs: leave the value unconstrained
        return {}
    schema.pop("title", None)
    return schema


def build_spec(name: str, descriptor: Any, handler: Callable[..., Any]) -> ToolSpec:
    signature = inspect.signature(handler)
    try:
        hints = typing.get_type_hints(handler)
    except Exception:
        hints = {}

    declared = list(getattr(descriptor, "required_inputs", []) or []) + list(
        getattr(descriptor, "optional_inputs", []) or []
    )
    output_kind = str(getattr(descriptor, "output_kind", "") or "")
    # Classify on *all* signature parameters, not just exposed ones, so an
    # undeclared `output_path` still marks the tool as filesystem-touching.
    risks = classify(name, signature.parameters, output_kind)
    # A connector block (docs/CONNECTOR_REGISTRY_DESIGN.md) declares its access explicitly;
    # it can only add to what the parameter-name heuristics found, never remove.
    connector = (getattr(descriptor, "metadata", None) or {}).get("connector")
    if isinstance(connector, dict):
        risks = risks | frozenset(connector.get("access") or ())

    exposed = tuple(
        param
        for param in dict.fromkeys(declared)
        if param in signature.parameters and param not in NEVER_EXPOSED
    )
    required_declared = set(getattr(descriptor, "required_inputs", []) or [])
    properties = {
        param: _param_schema(param, hints.get(param, signature.parameters[param].annotation))
        for param in exposed
    }
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": [param for param in exposed if param in required_declared],
        "additionalProperties": False,
    }
    description = str(getattr(descriptor, "description", "") or name)
    return ToolSpec(
        name=name,
        description=description,
        input_schema=schema,
        risks=risks,
        handler=handler,
        params=exposed,
        path_params=tuple(p for p in exposed if p in PATH_PARAMS),
        name_params=tuple(p for p in exposed if p in NAME_PARAMS),
        url_params=tuple(p for p in exposed if p in URL_PARAMS),
        output_kind=output_kind,
        long_running=bool(getattr(descriptor, "long_running", False)),
    )


def build_tool_specs(registry: Any) -> dict[str, ToolSpec]:
    specs: dict[str, ToolSpec] = {}
    for name in sorted(registry.registered_capability_names()):
        binding = registry.resolve(name)
        specs[name] = build_spec(name, registry.descriptor_for(name), binding.callable)
    return specs
