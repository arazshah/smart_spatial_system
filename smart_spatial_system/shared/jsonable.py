"""
smart_spatial_system.shared.jsonable

Conversion of plugin results (SDK objects, numpy values, paths, dataclasses) to plain JSON
data. Shared by the MCP codec and the connector layer.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_DEPTH = 64


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
        attrs = vars(value)
        primary = attrs.get("data")
        return {
            k: to_jsonable(v, _depth + 1)
            for k, v in attrs.items()
            if not k.startswith("_")
            # SDK RasterOut keeps the same array under data/array/payload; send it once.
            and not (primary is not None and k in {"array", "payload"} and v is primary)
        }
    return str(value)


def as_feature_collection(data: Any) -> Any:
    """SDK VectorOut serializes without a GeoJSON ``type``; add it so the output of one
    tool is a valid input of the next."""
    if isinstance(data, dict) and isinstance(data.get("features"), list) and "type" not in data:
        return {"type": "FeatureCollection", **data}
    return data
