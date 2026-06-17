"""
ndvi_calculator.py

GeoChat SDK Plugin
==================

Plugin ID:
    ndvi_calculator

Purpose:
    Calculate NDVI from RED and NIR raster bands.

Capability:
    - calculate_ndvi

Formula:
    NDVI = (NIR - RED) / (NIR + RED)

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Band indices:
    - 1-based

No external dependency is required.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs
from plugins.raster_clip_mask import (
    _array_shape,
    _extract_raster,
    _is_geographic_crs,
    _make_raster_out,
)


PLUGIN_ID = "ndvi_calculator"

VALID_ENGINES = {"python", "auto"}


def _load_ndvi_config() -> dict[str, Any]:
    """
    Load config/plugins/ndvi_calculator.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    """
    Return current UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def _validate_engine(engine: str) -> str:
    """
    Validate execution engine.
    """
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a non-empty string.")

    engine = engine.strip().lower()

    if engine not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

    return engine


def _validate_precision(value: Any) -> int | None:
    """
    Validate float precision.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("precision must be an integer or None.")

    try:
        precision = int(value)
    except Exception as exc:
        raise ValueError("precision must be an integer or None.") from exc

    if precision < 0 or precision > 15:
        raise ValueError("precision must be between 0 and 15.")

    return precision


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return configured precision.
    """
    return _validate_precision(config.get("coordinate_precision", 6))


def _is_number(value: Any) -> bool:
    """
    Return True for finite numbers excluding bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_nan(value: Any) -> bool:
    """
    Return True if value is NaN.
    """
    try:
        return isinstance(value, float) and math.isnan(value)
    except Exception:
        return False


def _is_nodata(value: Any, nodata: Any) -> bool:
    """
    Check whether value should be treated as nodata.
    """
    if value is None:
        return True

    if _is_nan(value):
        return True

    if nodata is None:
        return False

    if _is_nan(nodata):
        return _is_nan(value)

    try:
        return value == nodata
    except Exception:
        return False


def _validate_band_index(value: Any, band_count: int, *, name: str) -> int:
    """
    Validate 1-based band index.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer.")

    try:
        band_index = int(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc

    if band_index <= 0:
        raise ValueError(f"{name} must be >= 1.")

    if band_index > band_count:
        raise ValueError(f"{name} {band_index} is out of range. Raster has {band_count} band(s).")

    return band_index


def _band_value(data: Any, *, band_index: int, row: int, col: int) -> Any:
    """
    Read 1-based raster band value.
    """
    bands, _height, _width = _array_shape(data)

    if bands == 1:
        # 2D raster
        if data and isinstance(data[0], list) and (not data[0] or not isinstance(data[0][0], list)):
            return data[row][col]

        # 3D single-band raster
        return data[0][row][col]

    return data[band_index - 1][row][col]


def _clip_value(value: float, min_value: float, max_value: float) -> float:
    """
    Clip numeric value.
    """
    return max(float(min_value), min(float(value), float(max_value)))


def _round_value(value: Any, precision: int | None) -> Any:
    """
    Round float value.
    """
    if value is None:
        return None

    if not isinstance(value, float):
        return value

    if precision is None:
        return value

    return round(value, precision)


def _calculate_ndvi_value(
    *,
    red: Any,
    nir: Any,
    nodata: Any,
    division_by_zero_value: Any,
    clip_output: bool,
    output_min: float,
    output_max: float,
    precision: int | None,
) -> tuple[Any, str]:
    """
    Calculate NDVI for one pixel.

    Returns:
        (value, status)

    Status:
        success
        input_nodata
        division_by_zero
        invalid_input
    """
    if _is_nodata(red, nodata) or _is_nodata(nir, nodata):
        return nodata, "input_nodata"

    if not _is_number(red) or not _is_number(nir):
        return nodata, "invalid_input"

    red_f = float(red)
    nir_f = float(nir)

    denominator = nir_f + red_f

    if abs(denominator) <= 1e-12:
        if division_by_zero_value is None:
            return nodata, "division_by_zero"

        if _is_number(division_by_zero_value):
            value = float(division_by_zero_value)
        else:
            value = division_by_zero_value

        if isinstance(value, float):
            if clip_output:
                value = _clip_value(value, output_min, output_max)
            value = _round_value(value, precision)

        return value, "division_by_zero"

    ndvi = (nir_f - red_f) / denominator

    if clip_output:
        ndvi = _clip_value(ndvi, output_min, output_max)

    ndvi = _round_value(float(ndvi), precision)

    return ndvi, "success"


def _basic_raster_stats(data: list[list[Any]], nodata: Any) -> dict[str, Any]:
    """
    Calculate simple output raster stats.
    """
    values: list[float] = []

    for row in data:
        for value in row:
            if _is_nodata(value, nodata):
                continue
            if _is_number(value):
                values.append(float(value))

    if not values:
        return {
            "output_min_value": None,
            "output_max_value": None,
            "output_mean_value": None,
        }

    return {
        "output_min_value": min(values),
        "output_max_value": max(values),
        "output_mean_value": sum(values) / len(values),
    }


@capability(
    name="calculate_ndvi",
    keywords=[
        "ndvi",
        "calculate ndvi",
        "vegetation index",
        "normalized difference vegetation index",
        "nir red index",
        "plant health",
        "vegetation health",
        "شاخص پوشش گیاهی",
        "محاسبه ndvi",
        "ان دی وی آی",
        "سلامت گیاه",
        "پوشش گیاهی",
    ],
    description="Calculate NDVI raster from RED and NIR bands.",
    required_inputs=["raster"],
    optional_inputs=[
        "red_band",
        "nir_band",
        "nodata",
        "division_by_zero_value",
        "clip_output",
        "output_min",
        "output_max",
        "engine",
        "precision",
        "source_crs",
        "metadata",
    ],
    output_kind="raster",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster",
        "operation": "ndvi",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "remote_sensing",
        "config_aware": True,
        "spectral_index": "NDVI",
        "formula": "(NIR - RED) / (NIR + RED)",
        "raster_analysis": True,
        "routable": True,
    },
)
def calculate_ndvi(
    raster: Any,
    red_band: int | None = None,
    nir_band: int | None = None,
    nodata: Any = None,
    division_by_zero_value: Any = None,
    clip_output: bool | None = None,
    output_min: float | None = None,
    output_max: float | None = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Calculate NDVI from RED and NIR bands.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        red_band:
            1-based RED band index.
        nir_band:
            1-based NIR band index.
        nodata:
            Nodata value. If omitted, raster metadata/config nodata is used.
        division_by_zero_value:
            Value used when NIR + RED == 0.
            If None, output nodata for that pixel.
        clip_output:
            If True, clips output to [output_min, output_max].
        output_min:
            Minimum output value, default -1.
        output_max:
            Maximum output value, default 1.
        engine:
            python | auto.
        precision:
            Rounding precision.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        RasterOut-like object produced via SDK-compatible helper.
    """
    config = _load_ndvi_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_red_band = _validate_band_index(
        pick_first(red_band, config.get("default_red_band"), default=1),
        band_count,
        name="red_band",
    )

    final_nir_band = _validate_band_index(
        pick_first(nir_band, config.get("default_nir_band"), default=2),
        band_count,
        name="nir_band",
    )

    final_nodata = pick_first(
        nodata,
        input_metadata.get("nodata"),
        config.get("default_nodata"),
        default=None,
    )

    final_division_by_zero_value = pick_first(
        division_by_zero_value,
        config.get("division_by_zero_value"),
        default=None,
    )

    final_clip_output = bool(
        pick_first(clip_output, config.get("clip_output"), default=True)
    )

    final_output_min = float(
        pick_first(output_min, config.get("output_min"), default=-1.0)
    )

    final_output_max = float(
        pick_first(output_max, config.get("output_max"), default=1.0)
    )

    if final_output_min > final_output_max:
        raise ValueError("output_min must be <= output_max.")

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    preserve_metadata = bool(config.get("preserve_metadata", True))

    final_source_crs = pick_first(source_crs, input_metadata.get("crs"), config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    output: list[list[Any]] = []

    success_pixel_count = 0
    input_nodata_pixel_count = 0
    invalid_input_pixel_count = 0
    division_by_zero_pixel_count = 0

    for row in range(height):
        out_row: list[Any] = []

        for col in range(width):
            red_value = _band_value(data, band_index=final_red_band, row=row, col=col)
            nir_value = _band_value(data, band_index=final_nir_band, row=row, col=col)

            ndvi_value, status = _calculate_ndvi_value(
                red=red_value,
                nir=nir_value,
                nodata=final_nodata,
                division_by_zero_value=final_division_by_zero_value,
                clip_output=final_clip_output,
                output_min=final_output_min,
                output_max=final_output_max,
                precision=final_precision,
            )

            out_row.append(ndvi_value)

            if status == "success":
                success_pixel_count += 1
            elif status == "input_nodata":
                input_nodata_pixel_count += 1
            elif status == "division_by_zero":
                division_by_zero_pixel_count += 1
            elif status == "invalid_input":
                invalid_input_pixel_count += 1

        output.append(out_row)

    stats = _basic_raster_stats(output, final_nodata)
    stats = {key: _round_value(value, final_precision) for key, value in stats.items()}

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "NDVI is being evaluated on a geographic CRS. "
            "The index formula is valid, but downstream metric workflows may require reprojection."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    output_metadata = {
        **base_metadata,
        "source": "ndvi_calculator",
        "loader": PLUGIN_ID,
        "operation": "ndvi",
        "engine_requested": final_engine,
        "engine_used": "python",
        "formula": "(NIR - RED) / (NIR + RED)",
        "red_band": final_red_band,
        "nir_band": final_nir_band,
        "input_band_count": band_count,
        "output_band_count": 1,
        "width": width,
        "height": height,
        "nodata": final_nodata,
        "division_by_zero_value": final_division_by_zero_value,
        "clip_output": final_clip_output,
        "output_min": final_output_min,
        "output_max": final_output_max,
        "success_pixel_count": success_pixel_count,
        "valid_pixel_count": success_pixel_count,
        "input_nodata_pixel_count": input_nodata_pixel_count,
        "invalid_input_pixel_count": invalid_input_pixel_count,
        "division_by_zero_pixel_count": division_by_zero_pixel_count,
        "nodata_pixel_count": input_nodata_pixel_count + invalid_input_pixel_count + (
            division_by_zero_pixel_count if final_division_by_zero_value is None else 0
        ),
        "coordinate_precision": final_precision,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "created_at": _utc_now_iso(),
        **stats,
        **source_info,
        **user_metadata,
    }

    return _make_raster_out(
        data=output,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="NDVI Calculator",
    description=(
        "Calculates NDVI from RED and NIR raster bands using a safe pure-python implementation."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
