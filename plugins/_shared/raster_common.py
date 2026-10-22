"""
plugins._shared.raster_common

Raster helpers for the P0 raster plugins: read the repository's in-memory raster
convention (dict or RasterOut with ``data``/``array`` and ``metadata`` holding a
6-number affine ``transform`` [a, b, c, d, e, f]) into numpy, and build RasterOut
results. Only non-rotated north-up transforms are supported, as elsewhere.
"""

from __future__ import annotations

import math
from typing import Any

from geochat_sdk.exceptions import SDKDependencyError

from plugins.raster_clip_mask import (
    _extract_raster,
    _make_raster_out,
    _normalize_transform,
)

DEFAULT_NODATA = -9999.0


def numpy_module():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - numpy is a base dependency
        raise SDKDependencyError("This plugin requires numpy: pip install numpy") from exc
    return np


def read_raster(raster: Any, *, max_cells: int) -> tuple[Any, dict[str, Any], list[float]]:
    """Return (float array [bands, rows, cols] with NaN for nodata, metadata, transform)."""
    np = numpy_module()
    data, metadata, _ = _extract_raster(raster)
    array = np.asarray(data, dtype=np.float64) if not hasattr(data, "shape") else data.astype(np.float64)
    if array.ndim == 2:
        array = array[None, :, :]
    if array.ndim != 3 or array.size == 0:
        raise ValueError("raster data must be a non-empty 2D or [band, row, col] array.")
    if array.size > max_cells:
        raise ValueError(f"raster has {array.size} cells; the limit is {max_cells}.")
    nodata = metadata.get("nodata")
    if nodata is not None:
        try:
            array = np.where(array == float(nodata), np.nan, array)
        except (TypeError, ValueError) as exc:
            raise ValueError("raster nodata must be a number or null.") from exc
    transform = get_transform(metadata)
    return array, metadata, transform


def get_transform(metadata: dict[str, Any]) -> list[float]:
    candidate = metadata.get("transform", metadata.get("affine_transform"))
    if candidate is None:
        raise ValueError("raster metadata needs a 'transform' (affine [a, b, c, d, e, f]).")
    a, b, c, d, e, f = (float(v) for v in _normalize_transform(candidate))
    if b != 0 or d != 0 or a <= 0 or e >= 0 or not all(map(math.isfinite, (a, c, e, f))):
        raise ValueError("Only non-rotated north-up transforms (a > 0, e < 0, b = d = 0) are supported.")
    return [a, b, c, d, e, f]


def make_raster_out(
    array: Any, metadata: dict[str, Any], *, nodata: float | None = None
):
    """Build RasterOut from a numpy array or nested list; NaN becomes ``nodata``."""
    np = numpy_module()
    meta = dict(metadata)
    if hasattr(array, "shape"):
        values = np.asarray(array, dtype=np.float64)
        if np.isnan(values).any():
            fill = DEFAULT_NODATA if nodata is None else nodata
            values = np.where(np.isnan(values), fill, values)
            meta["nodata"] = fill
        elif nodata is not None:
            meta["nodata"] = nodata
        array = values.tolist()
    meta.setdefault("band_count", 1 if not isinstance(array[0][0], list) else len(array))
    return _make_raster_out(array, meta)
