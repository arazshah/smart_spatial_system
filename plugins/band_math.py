"""
band_math.py

GeoChat SDK Plugin
==================

Plugin ID:
    band_math

Purpose:
    Apply safe mathematical expressions on raster bands and generate
    a derived single-band raster.

Capability:
    - calculate_band_math

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Safe expression examples:
    - "(b4 - b3) / (b4 + b3)"
    - "safe_div(b4 - b3, b4 + b3, 0)"
    - "clip(safe_div(b5 - b4, b5 + b4, 0), -1, 1)"
    - "where(b1 > 100, b1, 0)"

No external dependency is required.
"""

from __future__ import annotations

import ast
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


PLUGIN_ID = "band_math"

VALID_ENGINES = {"python", "auto"}
VALID_OUTPUT_DTYPES = {"float", "int", "bool", "preserve"}

SAFE_FUNCTIONS = {
    "abs",
    "min",
    "max",
    "round",
    "safe_div",
    "clip",
    "where",
}

SAFE_CONSTS = {
    "pi": math.pi,
    "e": math.e,
}


def _load_band_math_config() -> dict[str, Any]:
    """
    Load config/plugins/band_math.yaml if available.
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


def _validate_output_dtype(value: Any) -> str:
    """
    Validate output dtype.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("output_dtype must be a non-empty string.")

    result = value.strip().lower()

    if result not in VALID_OUTPUT_DTYPES:
        raise ValueError(
            f"Unsupported output_dtype '{result}'. Valid values: {sorted(VALID_OUTPUT_DTYPES)}"
        )

    return result


def _is_number(value: Any) -> bool:
    """
    Return True for finite numbers excluding bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_nan(value: Any) -> bool:
    """
    Return True if NaN.
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


def _cast_output_value(value: Any, output_dtype: str, precision: int | None) -> Any:
    """
    Cast expression result to output dtype.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        if output_dtype == "float":
            return 1.0 if value else 0.0
        if output_dtype == "int":
            return 1 if value else 0
        if output_dtype == "bool":
            return value
        return value

    if not _is_number(value):
        return value

    numeric = float(value)

    if output_dtype == "bool":
        return bool(numeric)

    if output_dtype == "int":
        return int(round(numeric))

    if output_dtype in {"float", "preserve"}:
        numeric = round(numeric, precision) if precision is not None else numeric
        return float(numeric)

    return value


def _safe_div(a: Any, b: Any, default: Any = 0) -> Any:
    """
    Safe division helper.
    """
    try:
        if b is None:
            return default
        if _is_number(b) and abs(float(b)) <= 1e-12:
            return default
        return a / b
    except Exception:
        return default


def _clip(value: Any, min_val: Any, max_val: Any) -> Any:
    """
    Clip scalar value between min/max.
    """
    try:
        return max(min_val, min(value, max_val))
    except Exception:
        return value


def _where(condition: Any, a: Any, b: Any) -> Any:
    """
    Conditional scalar selector.
    """
    return a if bool(condition) else b


def _resolve_expression(expression: str | None, preset: str | None, config: dict[str, Any]) -> str:
    """
    Resolve expression from direct expression or named preset.
    """
    presets = config.get("presets") or {}
    if not isinstance(presets, dict):
        raise ValueError("presets in band_math config must be a dict.")

    if expression is not None:
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("expression must be a non-empty string when provided.")
        return expression.strip()

    if preset is not None:
        if not isinstance(preset, str) or not preset.strip():
            raise ValueError("preset must be a non-empty string when provided.")
        key = preset.strip()
        if key not in presets:
            raise ValueError(f"Unknown preset '{key}'. Available presets: {sorted(presets.keys())}")
        expr = presets[key]
        if not isinstance(expr, str) or not expr.strip():
            raise ValueError(f"Preset '{key}' must map to a non-empty expression string.")
        return expr.strip()

    raise ValueError("Either expression or preset must be provided.")


def _validate_ast(node: ast.AST) -> None:
    """
    Validate expression AST for safe evaluation.
    """
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.Call,
        ast.Load,
        ast.Name,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.FloorDiv,
        ast.UAdd,
        ast.USub,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )

    for child in ast.walk(node):
        if not isinstance(child, allowed_nodes):
            raise ValueError(f"Unsupported expression element: {type(child).__name__}")

        if isinstance(child, ast.Call):
            if not isinstance(child.func, ast.Name):
                raise ValueError("Only direct function calls are allowed.")
            if child.func.id not in SAFE_FUNCTIONS:
                raise ValueError(f"Unsupported function '{child.func.id}' in expression.")

        if isinstance(child, ast.Name):
            name = child.id
            if name in SAFE_FUNCTIONS:
                continue
            if name in SAFE_CONSTS:
                continue
            if name.startswith("b") and name[1:].isdigit():
                continue
            raise ValueError(f"Unsupported variable '{name}' in expression.")


def _compile_expression(expression: str) -> Any:
    """
    Compile validated expression.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc

    _validate_ast(tree)
    return compile(tree, "<band_math>", "eval")


def _evaluate_expression(compiled_expr: Any, variables: dict[str, Any]) -> Any:
    """
    Evaluate compiled expression using restricted globals.
    """
    safe_globals = {
        "__builtins__": {},
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
        "safe_div": _safe_div,
        "clip": _clip,
        "where": _where,
        **SAFE_CONSTS,
    }

    return eval(compiled_expr, safe_globals, variables)


def _build_output_array(height: int, width: int, fill_value: Any = None) -> list[list[Any]]:
    """
    Build empty 2D raster.
    """
    return [[fill_value for _ in range(width)] for _ in range(height)]


@capability(
    name="calculate_band_math",
    keywords=[
        "band math",
        "raster formula",
        "spectral index",
        "ndvi",
        "ndwi",
        "ndbi",
        "raster calculation",
        "band calculator",
        "محاسبات باند",
        "فرمول رستری",
        "شاخص طیفی",
        "محاسبه ndvi",
        "محاسبه ndwi",
        "محاسبه ndbi",
    ],
    description="Apply safe mathematical expressions on raster bands.",
    required_inputs=["raster"],
    optional_inputs=[
        "expression",
        "preset",
        "output_dtype",
        "nodata",
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
        "operation": "band_math",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "raster_analysis",
        "config_aware": True,
        "spectral_index_supported": True,
        "safe_expression_eval": True,
        "routable": True,
    },
)
def calculate_band_math(
    raster: Any,
    expression: str | None = None,
    preset: str | None = None,
    output_dtype: str | None = None,
    nodata: Any = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Calculate a derived single-band raster from input raster bands.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        expression:
            Safe mathematical expression using b1, b2, ...
        preset:
            Optional preset name resolved from config.
        output_dtype:
            float | int | bool | preserve
        nodata:
            If any referenced band pixel is nodata, output becomes nodata.
        engine:
            python | auto
        precision:
            Float rounding precision.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        RasterOut-like object produced via SDK-compatible helper.
    """
    config = _load_band_math_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    final_output_dtype = _validate_output_dtype(
        str(pick_first(output_dtype, config.get("default_output_dtype"), default="float"))
    )

    final_precision = _configured_precision(config) if precision is None else _validate_precision(precision)

    preserve_metadata = bool(config.get("preserve_metadata", True))

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_expression = _resolve_expression(expression, preset, config)
    compiled_expr = _compile_expression(final_expression)

    final_nodata = pick_first(nodata, input_metadata.get("nodata"), config.get("default_nodata"), default=None)

    final_source_crs = pick_first(source_crs, input_metadata.get("crs"), config.get("source_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", False))

    output = _build_output_array(height, width, fill_value=final_nodata)

    valid_pixel_count = 0
    nodata_pixel_count = 0
    error_pixel_count = 0

    for row in range(height):
        for col in range(width):
            variables: dict[str, Any] = {}
            pixel_has_nodata = False

            for band_idx in range(1, band_count + 1):
                value = _band_value(data, band_index=band_idx, row=row, col=col)
                variables[f"b{band_idx}"] = value

                if _is_nodata(value, final_nodata):
                    pixel_has_nodata = True

            if pixel_has_nodata:
                output[row][col] = final_nodata
                nodata_pixel_count += 1
                continue

            try:
                result = _evaluate_expression(compiled_expr, variables)
                casted = _cast_output_value(result, final_output_dtype, final_precision)
                output[row][col] = casted
                valid_pixel_count += 1
            except Exception:
                output[row][col] = final_nodata
                error_pixel_count += 1

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Band math is being evaluated on a geographic CRS. "
            "The formula is fine, but metric interpretation may require reprojection."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    output_metadata = {
        **base_metadata,
        "source": "band_math",
        "loader": PLUGIN_ID,
        "operation": "band_math",
        "engine_requested": final_engine,
        "engine_used": "python",
        "expression": final_expression,
        "preset": preset,
        "input_band_count": band_count,
        "output_band_count": 1,
        "width": width,
        "height": height,
        "output_dtype": final_output_dtype,
        "nodata": final_nodata,
        "valid_pixel_count": valid_pixel_count,
        "nodata_pixel_count": nodata_pixel_count,
        "error_pixel_count": error_pixel_count,
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
    name="Band Math",
    description=(
        "Applies safe mathematical expressions on raster bands and generates "
        "a derived raster output."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
