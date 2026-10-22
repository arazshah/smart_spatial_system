"""
smart_spatial_system.interfaces.mcp.codec

Input limits and output serialization. Plugin results are SDK objects
(``VectorOut``, ``RasterOut``...) or plain dicts; clients get JSON only.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .policy import PolicyViolation

MAX_DEPTH = 64


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


def to_jsonable(value: Any, _depth: int = 0) -> Any:
    """Convert a plugin result to plain JSON data (NaN/inf become null)."""
    if _depth > MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v, _depth + 1) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.name  # never reveal server directory layout
    if hasattr(value, "tolist"):  # numpy arrays and scalars
        return to_jsonable(value.tolist(), _depth + 1)
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(), _depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value), _depth + 1)
    if hasattr(value, "__dict__"):
        return {
            k: to_jsonable(v, _depth + 1)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return str(value)


def encode_result(value: Any, *, max_bytes: int) -> tuple[Any, int]:
    """JSON-ify a result and enforce the response size limit. Returns (data, size)."""
    data = to_jsonable(value)
    size = len(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    if size > max_bytes:
        raise PolicyViolation(
            f"Result is {size} bytes; the limit is {max_bytes}. "
            "Filter or aggregate the input first."
        )
    return data, size
