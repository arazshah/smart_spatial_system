"""
slope_aspect.py

GeoChat SDK Plugin
==================

Plugin ID:
    slope_aspect

Purpose:
    Calculate slope and aspect from DEM raster using Horn 3x3 method.

Capability:
    - calculate_slope_aspect

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Slope units:
    - degree
    - radian
    - percent

Aspect convention:
    - degrees clockwise from north
    - 0   = north
    - 90  = east
    - 180 = south
    - 270 = west

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


PLUGIN_ID = "slope_aspect"

VALID_ENGINES = {"python", "auto"}
VALID_OUTPUTS = {"slope", "aspect", "both"}
VALID_SLOPE_UNITS = {"degree", "radian", "percent"}

EPSILON = 1e-12


def _load_slope_aspect_config() -> dict[str, Any]:
    """
    Load config/plugins/slope_aspect.yaml if available.
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


def _validate_output(output: str) -> str:
    """
    Validate requested output.
    """
    if not isinstance(output, str) or not output.strip():
        raise ValueError("output must be a non-empty string.")

    value = output.strip().lower()

    if value not in VALID_OUTPUTS:
        raise ValueError(f"Unsupported output '{output}'. Valid outputs: {sorted(VALID_OUTPUTS)}")

    return value


def _validate_slope_unit(unit: str) -> str:
    """
    Validate slope unit.
    """
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("slope_unit must be a non-empty string.")

    value = unit.strip().lower()

    if value not in VALID_SLOPE_UNITS:
        raise ValueError(
            f"Unsupported slope_unit '{unit}'. Valid slope units: {sorted(VALID_SLOPE_UNITS)}"
        )

    return value


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


def _round_value(value: Any, precision: int | None) -> Any:
    """
    Round float values only.
    """
    if value is None:
        return None

    if not isinstance(value, float):
        return value

    if precision is None:
        return value

    return round(value, precision)


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
    Check nodata logic.
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


def _validate_band_index(value: Any, band_count: int) -> int:
    """
    Validate 1-based band index.
    """
    if isinstance(value, bool):
        raise ValueError("band_index must be a positive integer.")

    try:
        band_index = int(value)
    except Exception as exc:
        raise ValueError("band_index must be a positive integer.") from exc

    if band_index <= 0:
        raise ValueError("band_index must be >= 1.")

    if band_index > band_count:
        raise ValueError(f"band_index {band_index} is out of range. Raster has {band_count} band(s).")

    return band_index


def _validate_resolution(value: Any, *, name: str) -> float:
    """
    Validate positive pixel resolution.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive number.")

    try:
        result = float(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a positive number.") from exc

    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number.")

    return result


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


def _extract_resolution_from_transform(
    metadata: dict[str, Any],
    *,
    default_x_resolution: Any,
    default_y_resolution: Any,
) -> tuple[float, float, str]:
    """
    Extract pixel resolution from affine-like transform if available.

    Expected transform:
        [a, b, c, d, e, f]

    Pixel size:
        x = abs(a)
        y = abs(e)

    Returns:
        (x_resolution, y_resolution, source)
    """
    transform = metadata.get("transform")

    if isinstance(transform, (list, tuple)) and len(transform) >= 6:
        try:
            x_resolution = abs(float(transform[0]))
            y_resolution = abs(float(transform[4]))

            if x_resolution > 0 and y_resolution > 0:
                return x_resolution, y_resolution, "transform"
        except Exception:
            pass

    return (
        _validate_resolution(default_x_resolution, name="x_resolution"),
        _validate_resolution(default_y_resolution, name="y_resolution"),
        "default",
    )


def _window_values(
    data: Any,
    *,
    band_index: int,
    row: int,
    col: int,
) -> list[list[Any]]:
    """
    Read 3x3 window around target cell.
    """
    return [
        [
            _band_value(data, band_index=band_index, row=row - 1, col=col - 1),
            _band_value(data, band_index=band_index, row=row - 1, col=col),
            _band_value(data, band_index=band_index, row=row - 1, col=col + 1),
        ],
        [
            _band_value(data, band_index=band_index, row=row, col=col - 1),
            _band_value(data, band_index=band_index, row=row, col=col),
            _band_value(data, band_index=band_index, row=row, col=col + 1),
        ],
        [
            _band_value(data, band_index=band_index, row=row + 1, col=col - 1),
            _band_value(data, band_index=band_index, row=row + 1, col=col),
            _band_value(data, band_index=band_index, row=row + 1, col=col + 1),
        ],
    ]


def _window_has_invalid_value(window: list[list[Any]], nodata: Any) -> bool:
    """
    Return True if any 3x3 value is nodata or non-numeric.
    """
    for row in window:
        for value in row:
            if _is_nodata(value, nodata):
                return True
            if not _is_number(value):
                return True
    return False


def _horn_derivatives(
    window: list[list[Any]],
    *,
    x_resolution: float,
    y_resolution: float,
) -> tuple[float, float]:
    """
    Calculate Horn dz/dx and dz/dy.

    Window positions:
        z1 z2 z3
        z4 z5 z6
        z7 z8 z9

    dzdx:
        positive means elevation increases eastward.

    dzdy:
        positive means elevation increases southward,
        because raster row index increases downward.
    """
    z1 = float(window[0][0])
    z2 = float(window[0][1])
    z3 = float(window[0][2])
    z4 = float(window[1][0])
    z6 = float(window[1][2])
    z7 = float(window[2][0])
    z8 = float(window[2][1])
    z9 = float(window[2][2])

    dzdx = ((z3 + 2.0 * z6 + z9) - (z1 + 2.0 * z4 + z7)) / (8.0 * x_resolution)
    dzdy = ((z7 + 2.0 * z8 + z9) - (z1 + 2.0 * z2 + z3)) / (8.0 * y_resolution)

    return dzdx, dzdy


def _slope_from_gradient(
    dzdx: float,
    dzdy: float,
    *,
    slope_unit: str,
) -> float:
    """
    Convert gradient to slope.
    """
    gradient = math.sqrt(dzdx * dzdx + dzdy * dzdy)

    if slope_unit == "percent":
        return gradient * 100.0

    slope_radian = math.atan(gradient)

    if slope_unit == "radian":
        return slope_radian

    return math.degrees(slope_radian)


def _aspect_from_gradient(
    dzdx: float,
    dzdy: float,
    *,
    flat_aspect_value: Any,
) -> Any:
    """
    Calculate downslope aspect in degrees clockwise from north.

    Coordinate interpretation:
        dzdx > 0 means elevation increases eastward.
        dzdy > 0 means elevation increases southward.

    Downslope vector:
        east component  = -dzdx
        north component = dzdy

    Aspect:
        atan2(east, north), converted to 0..360.
    """
    if abs(dzdx) <= EPSILON and abs(dzdy) <= EPSILON:
        return flat_aspect_value

    aspect = math.degrees(math.atan2(-dzdx, dzdy))

    if aspect < 0:
        aspect += 360.0

    if aspect >= 360.0:
        aspect -= 360.0

    return aspect


def _calculate_slope_aspect_cell(
    window: list[list[Any]],
    *,
    nodata: Any,
    output_nodata: Any,
    x_resolution: float,
    y_resolution: float,
    slope_unit: str,
    flat_aspect_value: Any,
    precision: int | None,
) -> tuple[Any, Any, str]:
    """
    Calculate slope/aspect for one 3x3 cell.

    Returns:
        (slope_value, aspect_value, status)

    Status:
        success
        input_nodata
        invalid_input
    """
    if _window_has_invalid_value(window, nodata):
        return output_nodata, output_nodata, "input_nodata"

    dzdx, dzdy = _horn_derivatives(
        window,
        x_resolution=x_resolution,
        y_resolution=y_resolution,
    )

    slope_value = _slope_from_gradient(
        dzdx,
        dzdy,
        slope_unit=slope_unit,
    )

    aspect_value = _aspect_from_gradient(
        dzdx,
        dzdy,
        flat_aspect_value=flat_aspect_value,
    )

    slope_value = _round_value(float(slope_value), precision)

    if _is_number(aspect_value):
        aspect_value = _round_value(float(aspect_value), precision)

    return slope_value, aspect_value, "success"


def _build_output_array(height: int, width: int, fill_value: Any) -> list[list[Any]]:
    """
    Build 2D raster.
    """
    return [[fill_value for _ in range(width)] for _ in range(height)]


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
            "min_value": None,
            "max_value": None,
            "mean_value": None,
        }

    return {
        "min_value": _round_value(min(values), precision),
        "max_value": _round_value(max(values), precision),
        "mean_value": _round_value(sum(values) / len(values), precision),
    }


@capability(
    name="calculate_slope_aspect",
    keywords=[
        "slope",
        "aspect",
        "dem slope",
        "terrain slope",
        "terrain aspect",
        "hill terrain",
        "elevation slope",
        "شیب",
        "جهت شیب",
        "شیب زمین",
        "مدل ارتفاعی",
        "dem",
        "تحلیل ارتفاع",
    ],
    description="Calculate slope and aspect from DEM raster using Horn 3x3 method.",
    required_inputs=["raster"],
    optional_inputs=[
        "band_index",
        "output",
        "slope_unit",
        "nodata",
        "output_nodata",
        "x_resolution",
        "y_resolution",
        "flat_aspect_value",
        "engine",
        "precision",
        "source_crs",
        "metadata",
    ],
    output_kind="json",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "raster",
        "operation": "slope_aspect",
        "returns": "dict",
        "artifact_kind": "raster_collection",
        "access_scope": "terrain_analysis",
        "config_aware": True,
        "terrain_analysis": True,
        "dem_required": True,
        "routable": True,
    },
)
def calculate_slope_aspect(
    raster: Any,
    band_index: int | None = None,
    output: str | None = None,
    slope_unit: str | None = None,
    nodata: Any = None,
    output_nodata: Any = None,
    x_resolution: float | None = None,
    y_resolution: float | None = None,
    flat_aspect_value: Any = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Calculate slope and/or aspect from DEM raster.

    Args:
        raster:
            DEM raster as RasterOut-like object or dict.
        band_index:
            1-based DEM band index.
        output:
            slope | aspect | both.
        slope_unit:
            degree | radian | percent.
        nodata:
            Input nodata value.
        output_nodata:
            Output nodata value.
        x_resolution:
            Pixel width. If omitted, transform/config is used.
        y_resolution:
            Pixel height. If omitted, transform/config is used.
        flat_aspect_value:
            Aspect value for flat cells. If None, output_nodata is used.
        engine:
            python | auto.
        precision:
            Float rounding precision.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        {
            "slope": RasterOut | None,
            "aspect": RasterOut | None,
            "metadata": {...}
        }
    """
    config = _load_slope_aspect_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    final_output = _validate_output(
        str(pick_first(output, config.get("default_output"), default="both"))
    )

    final_slope_unit = _validate_slope_unit(
        str(pick_first(slope_unit, config.get("default_slope_unit"), default="degree"))
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_band_index = _validate_band_index(
        pick_first(band_index, config.get("default_band_index"), default=1),
        band_count=band_count,
    )

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

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    default_x = pick_first(x_resolution, config.get("default_x_resolution"), default=1.0)
    default_y = pick_first(y_resolution, config.get("default_y_resolution"), default=1.0)

    x_res, y_res, resolution_source = _extract_resolution_from_transform(
        input_metadata,
        default_x_resolution=default_x,
        default_y_resolution=default_y,
    )

    if x_resolution is not None:
        x_res = _validate_resolution(x_resolution, name="x_resolution")
        resolution_source = "runtime"

    if y_resolution is not None:
        y_res = _validate_resolution(y_resolution, name="y_resolution")
        resolution_source = "runtime"

    final_flat_aspect_value = pick_first(
        flat_aspect_value,
        config.get("flat_aspect_value"),
        final_output_nodata,
        default=final_output_nodata,
    )

    preserve_metadata = bool(config.get("preserve_metadata", True))

    final_source_crs = pick_first(
        source_crs,
        input_metadata.get("crs"),
        config.get("source_crs"),
        default=None,
    )

    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    slope_data = _build_output_array(height, width, final_output_nodata)
    aspect_data = _build_output_array(height, width, final_output_nodata)

    success_pixel_count = 0
    edge_pixel_count = 0
    input_nodata_pixel_count = 0

    if height < 3 or width < 3:
        edge_pixel_count = height * width
    else:
        for row in range(height):
            for col in range(width):
                if row == 0 or col == 0 or row == height - 1 or col == width - 1:
                    edge_pixel_count += 1
                    continue

                window = _window_values(
                    data,
                    band_index=final_band_index,
                    row=row,
                    col=col,
                )

                slope_value, aspect_value, status = _calculate_slope_aspect_cell(
                    window,
                    nodata=final_nodata,
                    output_nodata=final_output_nodata,
                    x_resolution=x_res,
                    y_resolution=y_res,
                    slope_unit=final_slope_unit,
                    flat_aspect_value=final_flat_aspect_value,
                    precision=final_precision,
                )

                slope_data[row][col] = slope_value
                aspect_data[row][col] = aspect_value

                if status == "success":
                    success_pixel_count += 1
                else:
                    input_nodata_pixel_count += 1

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Slope/aspect is being evaluated on a geographic CRS. "
            "For meaningful terrain derivatives, DEM should usually be projected "
            "to a metric CRS before calculation."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    common_metadata = {
        **base_metadata,
        "source": "slope_aspect",
        "loader": PLUGIN_ID,
        "operation": "slope_aspect",
        "engine_requested": final_engine,
        "engine_used": "python",
        "method": "Horn 3x3",
        "input_band_count": band_count,
        "selected_band_index": final_band_index,
        "width": width,
        "height": height,
        "output": final_output,
        "slope_unit": final_slope_unit,
        "nodata": final_nodata,
        "output_nodata": final_output_nodata,
        "x_resolution": x_res,
        "y_resolution": y_res,
        "resolution_source": resolution_source,
        "flat_aspect_value": final_flat_aspect_value,
        "success_pixel_count": success_pixel_count,
        "valid_pixel_count": success_pixel_count,
        "edge_pixel_count": edge_pixel_count,
        "input_nodata_pixel_count": input_nodata_pixel_count,
        "nodata_pixel_count": edge_pixel_count + input_nodata_pixel_count,
        "coordinate_precision": final_precision,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "created_at": _utc_now_iso(),
        **source_info,
        **user_metadata,
    }

    slope_raster = None
    aspect_raster = None

    if final_output in {"slope", "both"}:
        slope_metadata = {
            **common_metadata,
            "artifact": "slope",
            "output_band_count": 1,
            **{
                f"slope_{key}": value
                for key, value in _basic_output_stats(
                    slope_data,
                    final_output_nodata,
                    final_precision,
                ).items()
            },
        }

        slope_raster = _make_raster_out(
            data=slope_data,
            metadata=slope_metadata,
        )

    if final_output in {"aspect", "both"}:
        aspect_metadata = {
            **common_metadata,
            "artifact": "aspect",
            "output_band_count": 1,
            "aspect_unit": "degree_clockwise_from_north",
            **{
                f"aspect_{key}": value
                for key, value in _basic_output_stats(
                    aspect_data,
                    final_output_nodata,
                    final_precision,
                ).items()
            },
        }

        aspect_raster = _make_raster_out(
            data=aspect_data,
            metadata=aspect_metadata,
        )

    collection_metadata = {
        **common_metadata,
        "artifact": "slope_aspect_collection",
        "has_slope": slope_raster is not None,
        "has_aspect": aspect_raster is not None,
    }

    return {
        "slope": slope_raster,
        "aspect": aspect_raster,
        "metadata": collection_metadata,
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Slope Aspect",
    description=(
        "Calculates slope and aspect from DEM raster using Horn 3x3 method. "
        "Useful for terrain analysis, flood risk, erosion risk, and geomorphology."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
