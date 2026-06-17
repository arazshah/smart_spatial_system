"""
spectral_indices.py

GeoChat SDK Plugin
==================

Plugin ID:
    spectral_indices

Purpose:
    Calculate common remote-sensing spectral indices from raster bands.

Capability:
    - calculate_spectral_index

Supported indices:
    - NDVI
    - NDWI
    - GNDVI
    - NDBI
    - NDMI
    - MNDWI
    - NBR
    - SAVI
    - EVI

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


PLUGIN_ID = "spectral_indices"

VALID_ENGINES = {"python", "auto"}

SUPPORTED_INDICES = {
    "ndvi": {
        "name": "NDVI",
        "formula": "(NIR - RED) / (NIR + RED)",
        "required_bands": ["nir", "red"],
    },
    "ndwi": {
        "name": "NDWI",
        "formula": "(GREEN - NIR) / (GREEN + NIR)",
        "required_bands": ["green", "nir"],
    },
    "gndvi": {
        "name": "GNDVI",
        "formula": "(NIR - GREEN) / (NIR + GREEN)",
        "required_bands": ["nir", "green"],
    },
    "ndbi": {
        "name": "NDBI",
        "formula": "(SWIR1 - NIR) / (SWIR1 + NIR)",
        "required_bands": ["swir1", "nir"],
    },
    "ndmi": {
        "name": "NDMI",
        "formula": "(NIR - SWIR1) / (NIR + SWIR1)",
        "required_bands": ["nir", "swir1"],
    },
    "mndwi": {
        "name": "MNDWI",
        "formula": "(GREEN - SWIR1) / (GREEN + SWIR1)",
        "required_bands": ["green", "swir1"],
    },
    "nbr": {
        "name": "NBR",
        "formula": "(NIR - SWIR2) / (NIR + SWIR2)",
        "required_bands": ["nir", "swir2"],
    },
    "savi": {
        "name": "SAVI",
        "formula": "((NIR - RED) / (NIR + RED + L)) * (1 + L)",
        "required_bands": ["nir", "red"],
    },
    "evi": {
        "name": "EVI",
        "formula": "G * ((NIR - RED) / (NIR + C1*RED - C2*BLUE + L))",
        "required_bands": ["nir", "red", "blue"],
    },
}


def _load_spectral_indices_config() -> dict[str, Any]:
    """
    Load config/plugins/spectral_indices.yaml if available.
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


def _round_value(value: Any, precision: int | None) -> Any:
    """
    Round floats.
    """
    if value is None:
        return None

    if not isinstance(value, float):
        return value

    if precision is None:
        return value

    return round(value, precision)


def _clip_value(value: float, min_value: float, max_value: float) -> float:
    """
    Clip numeric value.
    """
    return max(float(min_value), min(float(value), float(max_value)))


def _validate_index_name(index_name: Any) -> str:
    """
    Normalize and validate spectral index name.
    """
    if not isinstance(index_name, str) or not index_name.strip():
        raise ValueError("index_name must be a non-empty string.")

    key = index_name.strip().lower()

    if key not in SUPPORTED_INDICES:
        raise ValueError(
            f"Unsupported spectral index '{index_name}'. "
            f"Supported indices: {sorted(SUPPORTED_INDICES.keys())}"
        )

    return key


def _validate_band_index(value: Any, band_count: int, *, band_name: str) -> int:
    """
    Validate 1-based band index.
    """
    if isinstance(value, bool):
        raise ValueError(f"Band '{band_name}' must be a positive integer.")

    try:
        band_index = int(value)
    except Exception as exc:
        raise ValueError(f"Band '{band_name}' must be a positive integer.") from exc

    if band_index <= 0:
        raise ValueError(f"Band '{band_name}' must be >= 1.")

    if band_index > band_count:
        raise ValueError(
            f"Band '{band_name}' index {band_index} is out of range. "
            f"Raster has {band_count} band(s)."
        )

    return band_index


def _normalize_band_map(
    *,
    band_map: dict[str, Any] | None,
    config: dict[str, Any],
    band_count: int,
) -> dict[str, int]:
    """
    Merge and validate band map.

    Runtime band_map overrides config default_band_map.
    """
    config_band_map = config.get("default_band_map") or {}

    if not isinstance(config_band_map, dict):
        raise ValueError("default_band_map in spectral_indices config must be a dict.")

    if band_map is not None and not isinstance(band_map, dict):
        raise ValueError("band_map must be a dict or None.")

    merged = {
        str(key).strip().lower(): value
        for key, value in config_band_map.items()
    }

    if band_map:
        for key, value in band_map.items():
            merged[str(key).strip().lower()] = value

    result: dict[str, int] = {}

    for key, value in merged.items():
        if not key:
            raise ValueError("band_map keys must be non-empty strings.")
        result[key] = _validate_band_index(value, band_count, band_name=key)

    return result


def _required_bands(index_name: str) -> list[str]:
    """
    Return required band names for index.
    """
    return list(SUPPORTED_INDICES[index_name]["required_bands"])


def _ensure_required_bands(index_name: str, band_map: dict[str, int]) -> None:
    """
    Ensure all required bands exist in band_map.
    """
    missing = [
        band_name
        for band_name in _required_bands(index_name)
        if band_name not in band_map
    ]

    if missing:
        raise ValueError(
            f"Missing required band(s) for {index_name.upper()}: {missing}. "
            "Provide them through band_map."
        )


def _band_value(data: Any, *, band_index: int, row: int, col: int) -> Any:
    """
    Read 1-based raster band value.
    """
    bands, _height, _width = _array_shape(data)

    if bands == 1:
        if data and isinstance(data[0], list) and (not data[0] or not isinstance(data[0][0], list)):
            return data[row][col]
        return data[0][row][col]

    return data[band_index - 1][row][col]


def _safe_ratio(
    numerator: float,
    denominator: float,
    *,
    division_by_zero_value: Any,
    output_nodata: Any,
) -> tuple[Any, str]:
    """
    Safe division for index formulas.
    """
    if abs(float(denominator)) <= 1e-12:
        if division_by_zero_value is None:
            return output_nodata, "division_by_zero"
        return division_by_zero_value, "division_by_zero"

    return float(numerator) / float(denominator), "success"


def _param(
    params: dict[str, Any],
    config_params: dict[str, Any],
    key: str,
    default: Any,
) -> Any:
    """
    Read calculation parameter from runtime params, config, or default.
    """
    if key in params:
        return params[key]
    if key in config_params:
        return config_params[key]
    return default


def _calculate_index_value(
    *,
    index_name: str,
    values: dict[str, Any],
    nodata: Any,
    output_nodata: Any,
    division_by_zero_value: Any,
    clip_output: bool,
    output_min: float,
    output_max: float,
    precision: int | None,
    params: dict[str, Any],
    config_params: dict[str, Any],
) -> tuple[Any, str]:
    """
    Calculate one spectral index value.

    Returns:
        (value, status)

    Status:
        success
        input_nodata
        invalid_input
        division_by_zero
    """
    for band_name in _required_bands(index_name):
        value = values.get(band_name)
        if _is_nodata(value, nodata):
            return output_nodata, "input_nodata"
        if not _is_number(value):
            return output_nodata, "invalid_input"

    red = float(values.get("red", 0.0))
    nir = float(values.get("nir", 0.0))
    green = float(values.get("green", 0.0))
    blue = float(values.get("blue", 0.0))
    swir1 = float(values.get("swir1", 0.0))
    swir2 = float(values.get("swir2", 0.0))

    if index_name == "ndvi":
        raw, status = _safe_ratio(nir - red, nir + red, division_by_zero_value=division_by_zero_value, output_nodata=output_nodata)
    elif index_name == "ndwi":
        raw, status = _safe_ratio(green - nir, green + nir, division_by_zero_value=division_by_zero_value, output_nodata=output_nodata)
    elif index_name == "gndvi":
        raw, status = _safe_ratio(nir - green, nir + green, division_by_zero_value=division_by_zero_value, output_nodata=output_nodata)
    elif index_name == "ndbi":
        raw, status = _safe_ratio(swir1 - nir, swir1 + nir, division_by_zero_value=division_by_zero_value, output_nodata=output_nodata)
    elif index_name == "ndmi":
        raw, status = _safe_ratio(nir - swir1, nir + swir1, division_by_zero_value=division_by_zero_value, output_nodata=output_nodata)
    elif index_name == "mndwi":
        raw, status = _safe_ratio(green - swir1, green + swir1, division_by_zero_value=division_by_zero_value, output_nodata=output_nodata)
    elif index_name == "nbr":
        raw, status = _safe_ratio(nir - swir2, nir + swir2, division_by_zero_value=division_by_zero_value, output_nodata=output_nodata)
    elif index_name == "savi":
        l_value = float(_param(params, config_params, "savi_l", 0.5))
        ratio, status = _safe_ratio(
            nir - red,
            nir + red + l_value,
            division_by_zero_value=division_by_zero_value,
            output_nodata=output_nodata,
        )
        raw = ratio if status != "success" else float(ratio) * (1.0 + l_value)
    elif index_name == "evi":
        g_value = float(_param(params, config_params, "evi_g", 2.5))
        c1_value = float(_param(params, config_params, "evi_c1", 6.0))
        c2_value = float(_param(params, config_params, "evi_c2", 7.5))
        l_value = float(_param(params, config_params, "evi_l", 1.0))
        ratio, status = _safe_ratio(
            nir - red,
            nir + c1_value * red - c2_value * blue + l_value,
            division_by_zero_value=division_by_zero_value,
            output_nodata=output_nodata,
        )
        raw = ratio if status != "success" else g_value * float(ratio)
    else:
        raise ValueError(f"Unsupported spectral index: {index_name}")

    if status != "success":
        if raw == output_nodata:
            return raw, status
        if not _is_number(raw):
            return raw, status

    if not _is_number(raw):
        return output_nodata, "invalid_input"

    result = float(raw)

    if clip_output:
        result = _clip_value(result, output_min, output_max)

    result = _round_value(result, precision)

    return result, status


def _basic_output_stats(data: list[list[Any]], nodata: Any, precision: int | None) -> dict[str, Any]:
    """
    Calculate simple output statistics.
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
        "output_min_value": _round_value(min(values), precision),
        "output_max_value": _round_value(max(values), precision),
        "output_mean_value": _round_value(sum(values) / len(values), precision),
    }


@capability(
    name="calculate_spectral_index",
    keywords=[
        "spectral index",
        "spectral indices",
        "remote sensing index",
        "ndvi",
        "ndwi",
        "ndbi",
        "ndmi",
        "mndwi",
        "gndvi",
        "savi",
        "evi",
        "nbr",
        "vegetation index",
        "water index",
        "burn index",
        "شاخص طیفی",
        "شاخص سنجش از دور",
        "محاسبه ndvi",
        "محاسبه ndwi",
        "شاخص پوشش گیاهی",
        "شاخص آب",
    ],
    description="Calculate common remote-sensing spectral indices from raster bands.",
    required_inputs=["raster", "index_name"],
    optional_inputs=[
        "band_map",
        "params",
        "nodata",
        "output_nodata",
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
        "operation": "spectral_index",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "remote_sensing",
        "config_aware": True,
        "spectral_indices_supported": sorted(SUPPORTED_INDICES.keys()),
        "raster_analysis": True,
        "routable": True,
    },
)
def calculate_spectral_index(
    raster: Any,
    index_name: str | None = None,
    band_map: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    nodata: Any = None,
    output_nodata: Any = None,
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
    Calculate a spectral index raster.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        index_name:
            One of: ndvi, ndwi, gndvi, ndbi, ndmi, mndwi, nbr, savi, evi.
        band_map:
            Mapping from semantic band names to 1-based band indices.
            Example:
                {"blue": 1, "green": 2, "red": 3, "nir": 4, "swir1": 5, "swir2": 6}
        params:
            Optional parameters:
                savi_l, evi_g, evi_c1, evi_c2, evi_l
        nodata:
            Input nodata value.
        output_nodata:
            Output nodata value.
        division_by_zero_value:
            Value used when denominator is zero. If None, output_nodata is used.
        clip_output:
            If True, clips output to [output_min, output_max].
        output_min:
            Minimum output value.
        output_max:
            Maximum output value.
        engine:
            python | auto.
        precision:
            Rounding precision.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        RasterOut-like object with single-band 2D index data.
    """
    config = _load_spectral_indices_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    final_index_name = _validate_index_name(
        pick_first(index_name, config.get("default_index"), default="ndvi")
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_band_map = _normalize_band_map(
        band_map=band_map,
        config=config,
        band_count=band_count,
    )
    _ensure_required_bands(final_index_name, final_band_map)

    final_nodata = pick_first(
        nodata,
        input_metadata.get("nodata"),
        config.get("default_nodata"),
        default=None,
    )

    final_output_nodata = pick_first(
        output_nodata,
        config.get("default_output_nodata"),
        final_nodata,
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

    runtime_params = params or {}
    if not isinstance(runtime_params, dict):
        raise ValueError("params must be a dict or None.")

    config_params = config.get("default_params") or {}
    if not isinstance(config_params, dict):
        raise ValueError("default_params in spectral_indices config must be a dict.")

    preserve_metadata = bool(config.get("preserve_metadata", True))

    final_source_crs = pick_first(
        source_crs,
        input_metadata.get("crs"),
        config.get("source_crs"),
        default=None,
    )

    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    output: list[list[Any]] = []

    success_pixel_count = 0
    input_nodata_pixel_count = 0
    invalid_input_pixel_count = 0
    division_by_zero_pixel_count = 0

    required = _required_bands(final_index_name)

    for row in range(height):
        out_row: list[Any] = []

        for col in range(width):
            values: dict[str, Any] = {}

            for band_name in required:
                values[band_name] = _band_value(
                    data,
                    band_index=final_band_map[band_name],
                    row=row,
                    col=col,
                )

            index_value, status = _calculate_index_value(
                index_name=final_index_name,
                values=values,
                nodata=final_nodata,
                output_nodata=final_output_nodata,
                division_by_zero_value=final_division_by_zero_value,
                clip_output=final_clip_output,
                output_min=final_output_min,
                output_max=final_output_max,
                precision=final_precision,
                params=runtime_params,
                config_params=config_params,
            )

            out_row.append(index_value)

            if status == "success":
                success_pixel_count += 1
            elif status == "input_nodata":
                input_nodata_pixel_count += 1
            elif status == "invalid_input":
                invalid_input_pixel_count += 1
            elif status == "division_by_zero":
                division_by_zero_pixel_count += 1

        output.append(out_row)

    output_stats = _basic_output_stats(output, final_output_nodata, final_precision)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Spectral index is being evaluated on a geographic CRS. "
            "The index formula is valid, but downstream metric workflows may require reprojection."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    index_info = SUPPORTED_INDICES[final_index_name]

    output_metadata = {
        **base_metadata,
        "source": "spectral_indices",
        "loader": PLUGIN_ID,
        "operation": "spectral_index",
        "engine_requested": final_engine,
        "engine_used": "python",
        "index_name": final_index_name,
        "index_display_name": index_info["name"],
        "formula": index_info["formula"],
        "required_bands": required,
        "band_map": final_band_map,
        "input_band_count": band_count,
        "output_band_count": 1,
        "width": width,
        "height": height,
        "nodata": final_nodata,
        "output_nodata": final_output_nodata,
        "division_by_zero_value": final_division_by_zero_value,
        "clip_output": final_clip_output,
        "output_min": final_output_min,
        "output_max": final_output_max,
        "params": {
            **config_params,
            **runtime_params,
        },
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
        **output_stats,
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
    name="Spectral Indices",
    description=(
        "Calculates common remote-sensing spectral indices such as NDVI, NDWI, "
        "GNDVI, NDBI, NDMI, MNDWI, NBR, SAVI, and EVI."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
