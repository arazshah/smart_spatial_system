"""
plugins._shared.vector_common

Helpers shared by the vector/raster plugins added from the P0 list
(docs/PLUGIN_ROADMAP_100.md). They reuse the established conventions of
buffer_analysis (feature extraction, metadata) so every plugin reads and
writes the same shapes, and add the guard rails new plugins need because
they can be reached by remote MCP clients: hard size limits that fail with a
clear message instead of exhausting memory/CPU.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, resolve_env_refs
from plugins.buffer_analysis import (
    _build_vector_metadata,
    _extract_features,
    _get_shapely_tools,
    _to_float,
    _to_int,
)

__all__ = [
    "check_limit",
    "extract_features",
    "get_shapely",
    "load_config",
    "make_vector_out",
    "to_float",
    "to_int",
    "utc_now_iso",
    "validate_choice",
]

extract_features = _extract_features
to_float = _to_float
to_int = _to_int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(plugin_id: str) -> dict[str, Any]:
    config = load_plugin_config(plugin_id, required=False)
    return resolve_env_refs(config) if config else {}


def get_shapely(plugin_id: str):
    """Return (shape, mapping, unary_union), or raise a clear dependency error."""
    try:
        return _get_shapely_tools()
    except SDKDependencyError as exc:
        raise SDKDependencyError(f"{plugin_id} requires shapely: pip install shapely") from exc


def check_limit(value: int, limit: int, what: str) -> None:
    if value > limit:
        raise ValueError(f"{what} ({value}) exceeds the configured limit of {limit}.")


def validate_choice(value: Any, choices: set[str], name: str) -> str:
    if not isinstance(value, str) or value.strip().lower() not in choices:
        raise ValueError(f"{name} must be one of {sorted(choices)}.")
    return value.strip().lower()


def make_vector_out(
    features: list[dict[str, Any]],
    *,
    plugin_id: str,
    operation: str,
    source_info: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    user_metadata: dict[str, Any] | None = None,
) -> VectorOut:
    if user_metadata is not None and not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")
    output_metadata = {
        "source": plugin_id,
        "loader": plugin_id,
        "operation": operation,
        "output_feature_count": len(features),
        "created_at": utc_now_iso(),
        "note": (
            "Coordinates are treated as planar coordinates. "
            "No CRS transformation is performed by this plugin."
        ),
        **(source_info or {}),
        **_build_vector_metadata(features),
        **(extra or {}),
        **(user_metadata or {}),
    }
    return VectorOut(features=features, metadata=output_metadata)
