"""
raster_threshold.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_threshold

Purpose:
    Create a binary/class mask from raster values using threshold conditions.

Capability:
    - threshold_raster

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Supported operators:
    - gt / >
    - gte / >=
    - lt / <
    - lte / <=
    - eq / ==
    - neq / !=
    - between
    - outside

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


PLUGIN_ID = "raster_threshold"

VALID_ENGINES = {"python", "auto"}

OPERATOR_ALIASES = {
    ">": "gt",
    "gt": "gt",
    "greater": "gt",
    "greater_than": "gt",

    ">=": "gte",
    "gte": "gte",
    "ge": "gte",
    "greater_equal": "gte",
    "greater_than_or_equal": "gte",

    "<": "lt",
    "lt": "lt",
    "less": "lt",
    "less_than": "lt",

    "<=": "lte",
    "lte": "lte",
    "le": "lte",
    "less_equal": "lte",
    "less_than_or_equal": "lte",

    "==": "eq",
    "=": "eq",
    "eq": "eq",
    "equal": "eq",
    "equals": "eq",

    "!=": "neq",
    "<>": "neq",
    "neq": "neq",
    "ne": "neq",
    "not_equal": "neq",

    "between": "between",
    "range": "between",
    "inside": "between",

    "outside": "outside",
    "not_between": "outside",
}


EPSILON = 1e-12


def _load_threshold_config() -> dict[str, Any]:
    """
    Load config/plugins/raster_threshold.yaml if available.
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

    value = engine.strip().lower()

    if value not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Valid engines: {sorted(VALID_ENGINES)}")

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
    Round float output values.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return value

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


def _validate_operator(operator: Any) -> str:
    """
    Normalize and validate threshold operator.
    """
    if not isinstance(operator, str) or not operator.strip():
        raise ValueError("operator must be a non-empty string.")

    key = operator.strip().lower()

    if key not in OPERATOR_ALIASES:
        raise ValueError(
            f"Unsupported operator '{operator}'. "
            f"Valid operators: {sorted(set(OPERATOR_ALIASES.values()))}"
        )

    return OPERATOR_ALIASES[key]


def _validate_numeric_or_none(value: Any, *, name: str) -> float | None:
    """
    Validate numeric parameter or None.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric or None.")

    try:
        numeric = float(value)
    except Exception as exc:
        raise ValueError(f"{name} must be numeric or None.") from exc

    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite.")

    return numeric


def _as_bool(value: Any, default: bool) -> bool:
    """
    Convert common bool-like values.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False

    return bool(value)


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


def _validate_threshold_params(
    *,
    operator: str,
    threshold: Any,
    min_value: Any,
    max_value: Any,
) -> tuple[float | None, float | None, float | None]:
    """
    Validate threshold/min/max depending on operator.
    """
    final_threshold = _validate_numeric_or_none(threshold, name="threshold")
    final_min = _validate_numeric_or_none(min_value, name="min_value")
    final_max = _validate_numeric_or_none(max_value, name="max_value")

    if operator in {"gt", "gte", "lt", "lte", "eq", "neq"}:
        if final_threshold is None:
            raise ValueError(f"threshold is required for operator '{operator}'.")
        return final_threshold, final_min, final_max

    if operator in {"between", "outside"}:
        if final_min is None or final_max is None:
            raise ValueError(f"min_value and max_value are required for operator '{operator}'.")

        if final_min > final_max:
            raise ValueError("min_value must be <= max_value.")

        return final_threshold, final_min, final_max

    raise ValueError(f"Unsupported operator '{operator}'.")


def _compare_value(
    value: Any,
    *,
    operator: str,
    threshold: float | None,
    min_value: float | None,
    max_value: float | None,
    inclusive_min: bool,
    inclusive_max: bool,
) -> bool:
    """
    Evaluate threshold condition for one numeric value.
    """
    if not _is_number(value):
        return False

    numeric = float(value)

    if operator == "gt":
        return numeric > float(threshold) + EPSILON

    if operator == "gte":
        return numeric >= float(threshold) - EPSILON

    if operator == "lt":
        return numeric < float(threshold) - EPSILON

    if operator == "lte":
        return numeric <= float(threshold) + EPSILON

    if operator == "eq":
        return abs(numeric - float(threshold)) <= EPSILON

    if operator == "neq":
        return abs(numeric - float(threshold)) > EPSILON

    if operator == "between":
        assert min_value is not None
        assert max_value is not None

        if inclusive_min:
            lower_ok = numeric >= min_value - EPSILON
        else:
            lower_ok = numeric > min_value + EPSILON

        if inclusive_max:
            upper_ok = numeric <= max_value + EPSILON
        else:
            upper_ok = numeric < max_value - EPSILON

        return lower_ok and upper_ok

    if operator == "outside":
        assert min_value is not None
        assert max_value is not None

        if inclusive_min:
            lower_outside = numeric < min_value - EPSILON
        else:
            lower_outside = numeric <= min_value + EPSILON

        if inclusive_max:
            upper_outside = numeric > max_value + EPSILON
        else:
            upper_outside = numeric >= max_value - EPSILON

        return lower_outside or upper_outside

    return False


def _threshold_value(
    value: Any,
    *,
    operator: str,
    threshold: float | None,
    min_value: float | None,
    max_value: float | None,
    inclusive_min: bool,
    inclusive_max: bool,
    nodata: Any,
    output_nodata: Any,
    true_value: Any,
    false_value: Any,
    precision: int | None,
) -> tuple[Any, str]:
    """
    Threshold one pixel.

    Returns:
        (output_value, status)

    Status:
        true
        false
        input_nodata
        invalid_input
    """
    if _is_nodata(value, nodata):
        return output_nodata, "input_nodata"

    if not _is_number(value):
        return output_nodata, "invalid_input"

    matched = _compare_value(
        value,
        operator=operator,
        threshold=threshold,
        min_value=min_value,
        max_value=max_value,
        inclusive_min=inclusive_min,
        inclusive_max=inclusive_max,
    )

    if matched:
        return _round_value(true_value, precision), "true"

    return _round_value(false_value, precision), "false"


def _increment_count(mapping: dict[str, int], key: Any) -> None:
    """
    Increment count using repr key.
    """
    text = repr(key)
    mapping[text] = mapping.get(text, 0) + 1


@capability(
    name="threshold_raster",
    keywords=[
        "raster threshold",
        "threshold raster",
        "binary mask",
        "raster mask",
        "ndvi mask",
        "ndwi mask",
        "slope threshold",
        "elevation threshold",
        "vegetation mask",
        "water mask",
        "built-up mask",
        "آستانه گذاری رستر",
        "ماسک رستر",
        "ماسک باینری",
        "ماسک ndvi",
        "ماسک آب",
        "ماسک پوشش گیاهی",
    ],
    description="Create a binary/class mask from raster values using threshold conditions.",
    required_inputs=["raster"],
    optional_inputs=[
        "band_index",
        "operator",
        "threshold",
        "min_value",
        "max_value",
        "inclusive_min",
        "inclusive_max",
        "true_value",
        "false_value",
        "nodata",
        "output_nodata",
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
        "operation": "raster_threshold",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "raster_analysis",
        "config_aware": True,
        "binary_mask_supported": True,
        "range_threshold_supported": True,
        "routable": True,
    },
)
def threshold_raster(
    raster: Any,
    band_index: int | None = None,
    operator: str | None = None,
    threshold: float | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    inclusive_min: bool | None = None,
    inclusive_max: bool | None = None,
    true_value: Any = None,
    false_value: Any = None,
    nodata: Any = None,
    output_nodata: Any = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Threshold a raster and return a single-band 2D mask.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        band_index:
            1-based band index.
        operator:
            gt/gte/lt/lte/eq/neq/between/outside or aliases.
        threshold:
            Numeric threshold for gt/gte/lt/lte/eq/neq.
        min_value:
            Range lower bound for between/outside.
        max_value:
            Range upper bound for between/outside.
        inclusive_min:
            Whether min bound is inclusive for range operators.
        inclusive_max:
            Whether max bound is inclusive for range operators.
        true_value:
            Output value when condition is true.
        false_value:
            Output value when condition is false.
        nodata:
            Input nodata value.
        output_nodata:
            Output value for nodata/invalid pixels.
        engine:
            python | auto.
        precision:
            Rounding precision for float true/false values.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        RasterOut-like object with threshold mask.
    """
    config = _load_threshold_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_band_index = _validate_band_index(
        pick_first(band_index, config.get("default_band_index"), default=1),
        band_count=band_count,
    )

    final_operator = _validate_operator(
        pick_first(operator, config.get("default_operator"), default="gt")
    )

    final_threshold, final_min_value, final_max_value = _validate_threshold_params(
        operator=final_operator,
        threshold=pick_first(threshold, config.get("default_threshold"), default=None),
        min_value=pick_first(min_value, config.get("default_min_value"), default=None),
        max_value=pick_first(max_value, config.get("default_max_value"), default=None),
    )

    final_inclusive_min = _as_bool(
        pick_first(inclusive_min, config.get("inclusive_min"), default=True),
        default=True,
    )

    final_inclusive_max = _as_bool(
        pick_first(inclusive_max, config.get("inclusive_max"), default=True),
        default=True,
    )

    final_true_value = pick_first(true_value, config.get("true_value"), default=1)
    final_false_value = pick_first(false_value, config.get("false_value"), default=0)

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

    preserve_metadata = bool(config.get("preserve_metadata", True))

    final_source_crs = pick_first(
        source_crs,
        input_metadata.get("crs"),
        config.get("source_crs"),
        default=None,
    )

    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    output: list[list[Any]] = []

    true_pixel_count = 0
    false_pixel_count = 0
    input_nodata_pixel_count = 0
    invalid_input_pixel_count = 0

    output_value_counts: dict[str, int] = {}

    for row in range(height):
        output_row: list[Any] = []

        for col in range(width):
            value = _band_value(data, band_index=final_band_index, row=row, col=col)

            output_value, status = _threshold_value(
                value,
                operator=final_operator,
                threshold=final_threshold,
                min_value=final_min_value,
                max_value=final_max_value,
                inclusive_min=final_inclusive_min,
                inclusive_max=final_inclusive_max,
                nodata=final_nodata,
                output_nodata=final_output_nodata,
                true_value=final_true_value,
                false_value=final_false_value,
                precision=final_precision,
            )

            output_row.append(output_value)
            _increment_count(output_value_counts, output_value)

            if status == "true":
                true_pixel_count += 1
            elif status == "false":
                false_pixel_count += 1
            elif status == "input_nodata":
                input_nodata_pixel_count += 1
            elif status == "invalid_input":
                invalid_input_pixel_count += 1

        output.append(output_row)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Raster threshold is being evaluated on a geographic CRS. "
            "The operation is value-based, but downstream metric workflows may require reprojection."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    output_metadata = {
        **base_metadata,
        "source": "raster_threshold",
        "loader": PLUGIN_ID,
        "operation": "raster_threshold",
        "engine_requested": final_engine,
        "engine_used": "python",
        "input_band_count": band_count,
        "selected_band_index": final_band_index,
        "output_band_count": 1,
        "width": width,
        "height": height,
        "operator": final_operator,
        "threshold": final_threshold,
        "min_value": final_min_value,
        "max_value": final_max_value,
        "inclusive_min": final_inclusive_min,
        "inclusive_max": final_inclusive_max,
        "true_value": final_true_value,
        "false_value": final_false_value,
        "nodata": final_nodata,
        "output_nodata": final_output_nodata,
        "true_pixel_count": true_pixel_count,
        "false_pixel_count": false_pixel_count,
        "valid_pixel_count": true_pixel_count + false_pixel_count,
        "input_nodata_pixel_count": input_nodata_pixel_count,
        "invalid_input_pixel_count": invalid_input_pixel_count,
        "nodata_pixel_count": input_nodata_pixel_count + invalid_input_pixel_count,
        "output_value_counts": output_value_counts,
        "coordinate_precision": final_precision,
        "source_crs": final_source_crs,
        "warning": geographic_warning,
        "created_at": _utc_now_iso(),
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
    name="Raster Threshold",
    description=(
        "Creates binary or class masks from raster values using threshold operators. "
        "Useful for NDVI vegetation masks, NDWI water masks, slope risk masks, and DEM thresholds."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
