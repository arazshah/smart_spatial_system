"""
smart_spatial_system.interfaces.mcp.codec

Input limits and output serialization. Plugin results are SDK objects
(``VectorOut``, ``RasterOut``...) or plain dicts; clients get JSON only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from smart_spatial_system.shared.jsonable import (
    MAX_DEPTH,
    as_feature_collection,
    to_jsonable,
)

from .policy import PolicyViolation


def check_input_limits(arguments: Mapping[str, Any], *, max_bytes: int) -> None:
    """Reject oversized or absurdly nested arguments before any plugin sees them."""

    def walk(value: Any, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise PolicyViolation("Arguments are nested too deeply.")
        if isinstance(value, Mapping):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, depth + 1)

    walk(arguments, 0)
    try:
        size = len(json.dumps(arguments, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise PolicyViolation("Arguments are not JSON serializable.") from exc
    if size > max_bytes:
        raise PolicyViolation(f"Arguments are {size} bytes; the limit is {max_bytes}.")


def encode_result(value: Any, *, max_bytes: int) -> tuple[Any, int]:
    """JSON-ify a result and enforce the response size limit. Returns (data, size)."""
    data = as_feature_collection(to_jsonable(value))
    size = len(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    if size > max_bytes:
        raise PolicyViolation(
            f"Result is {size} bytes; the limit is {max_bytes}. "
            "Filter or aggregate the input first."
        )
    return data, size
