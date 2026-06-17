"""
raster_reclassify.py

GeoChat SDK Plugin
==================

Plugin ID:
    raster_reclassify

Purpose:
    Reclassify raster values using ordered exact, values-list, or range rules.

Capability:
    - reclassify_raster

Supported raster forms:
    - RasterOut-like object with .data and .metadata
    - dict with {"data": ..., "metadata": ...}
    - dict with {"array": ..., "metadata": ...}

Supported raster layout:
    - 2D: data[row][col]
    - 3D band-first: data[band][row][col]

Rule formats:
    Range:
        {"min": 0.0, "max": 0.2, "value": 1, "label": "low"}

    Exact:
        {"equals": 5, "value": 10, "label": "class_5"}

    Values:
        {"values": [1, 2, 3], "value": 100, "label": "group_a"}

Rules are evaluated in order. First match wins.

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


PLUGIN_ID = "raster_reclassify"

VALID_ENGINES = {"python", "auto"}

EPSILON = 1e-12
_MISSING = object()


def _load_reclassify_config() -> dict[str, Any]:
    """
    Load config/plugins/raster_reclassify.yaml if available.
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


def _round_value(value: Any, precision: int | None) -> Any:
    """
    Round float values only.
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


def _get_rule_output_value(rule: dict[str, Any]) -> Any:
    """
    Extract output value from a rule.

    Supported output keys:
        value
        output
        class
        new_value
    """
    for key in ("value", "output", "class", "new_value"):
        if key in rule:
            return rule[key]

    raise ValueError("Each reclass rule must contain one output key: value/output/class/new_value.")


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


def _normalize_rule(rule: Any, index: int) -> dict[str, Any]:
    """
    Normalize one rule.

    Supported forms:
        dict
        [min, max, value]
    """
    if isinstance(rule, (list, tuple)) and len(rule) == 3:
        raw = {
            "min": rule[0],
            "max": rule[1],
            "value": rule[2],
        }
    elif isinstance(rule, dict):
        raw = dict(rule)
    else:
        raise ValueError(f"Rule at index {index} must be dict or [min, max, value].")

    output_value = _get_rule_output_value(raw)

    label = raw.get("label", f"rule_{index}")

    has_equals = "equals" in raw
    has_values = "values" in raw
    has_min = "min" in raw
    has_max = "max" in raw

    rule_type_count = int(has_equals) + int(has_values) + int(has_min or has_max)

    if rule_type_count != 1:
        raise ValueError(
            f"Rule at index {index} must define exactly one matcher: equals, values, or min/max range."
        )

    normalized = {
        "index": index,
        "label": str(label),
        "value": output_value,
        "inclusive_min": _as_bool(raw.get("inclusive_min"), True),
        "inclusive_max": _as_bool(raw.get("inclusive_max"), True),
    }

    if has_equals:
        normalized["type"] = "equals"
        normalized["equals"] = raw.get("equals")
        return normalized

    if has_values:
        values = raw.get("values")
        if not isinstance(values, (list, tuple, set)):
            raise ValueError(f"Rule values at index {index} must be list, tuple, or set.")
        normalized["type"] = "values"
        normalized["values"] = list(values)
        return normalized

    min_value = raw.get("min", None)
    max_value = raw.get("max", None)

    if min_value is None and max_value is None:
        raise ValueError(f"Range rule at index {index} must define min and/or max.")

    if min_value is not None and not _is_number(min_value):
        raise ValueError(f"Range rule min at index {index} must be numeric or null.")

    if max_value is not None and not _is_number(max_value):
        raise ValueError(f"Range rule max at index {index} must be numeric or null.")

    if min_value is not None and max_value is not None and float(min_value) > float(max_value):
        raise ValueError(f"Range rule min must be <= max at index {index}.")

    normalized["type"] = "range"
    normalized["min"] = float(min_value) if min_value is not None else None
    normalized["max"] = float(max_value) if max_value is not None else None

    return normalized


def _normalize_rules(rules: Any) -> list[dict[str, Any]]:
    """
    Normalize list of reclassification rules.
    """
    if rules is None:
        raise ValueError("rules must be provided either as input or config default_rules.")

    if not isinstance(rules, (list, tuple)):
        raise ValueError("rules must be a list or tuple.")

    if not rules:
        raise ValueError("rules must not be empty.")

    return [_normalize_rule(rule, index) for index, rule in enumerate(rules)]


def _values_equal(a: Any, b: Any) -> bool:
    """
    Equality helper with numeric tolerance.
    """
    if _is_number(a) and _is_number(b):
        return abs(float(a) - float(b)) <= EPSILON

    try:
        return a == b
    except Exception:
        return False


def _matches_rule(value: Any, rule: dict[str, Any]) -> bool:
    """
    Check if value matches one normalized rule.
    """
    rule_type = rule["type"]

    if rule_type == "equals":
        return _values_equal(value, rule.get("equals"))

    if rule_type == "values":
        return any(_values_equal(value, candidate) for candidate in rule.get("values", []))

    if rule_type == "range":
        if not _is_number(value):
            return False

        numeric = float(value)

        min_value = rule.get("min")
        max_value = rule.get("max")

        if min_value is not None:
            if rule.get("inclusive_min", True):
                if numeric < float(min_value) - EPSILON:
                    return False
            else:
                if numeric <= float(min_value) + EPSILON:
                    return False

        if max_value is not None:
            if rule.get("inclusive_max", True):
                if numeric > float(max_value) + EPSILON:
                    return False
            else:
                if numeric >= float(max_value) - EPSILON:
                    return False

        return True

    return False


def _find_matching_rule(value: Any, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Return first matching rule.
    """
    for rule in rules:
        if _matches_rule(value, rule):
            return rule

    return None


def _reclassify_value(
    value: Any,
    *,
    rules: list[dict[str, Any]],
    nodata: Any,
    output_nodata: Any,
    keep_unmatched: bool,
    unmatched_value: Any,
    precision: int | None,
) -> tuple[Any, str, dict[str, Any] | None]:
    """
    Reclassify one pixel.

    Returns:
        (output_value, status, matched_rule)

    Status:
        input_nodata
        matched
        unmatched_kept
        unmatched_default
    """
    if _is_nodata(value, nodata):
        return output_nodata, "input_nodata", None

    matched_rule = _find_matching_rule(value, rules)

    if matched_rule is not None:
        output_value = _round_value(matched_rule["value"], precision)
        return output_value, "matched", matched_rule

    if keep_unmatched:
        return _round_value(value, precision), "unmatched_kept", None

    return _round_value(unmatched_value, precision), "unmatched_default", None


def _increment_count(mapping: dict[str, int], key: Any) -> None:
    """
    Increment count using string key.
    """
    text = str(key)
    mapping[text] = mapping.get(text, 0) + 1


@capability(
    name="reclassify_raster",
    keywords=[
        "raster reclassify",
        "reclassify raster",
        "raster classification",
        "classify raster",
        "ndvi classes",
        "vegetation class",
        "dem classes",
        "threshold raster",
        "بازطبقه بندی رستر",
        "طبقه بندی رستر",
        "کلاس بندی رستر",
        "کلاس بندی ndvi",
        "آستانه گذاری رستر",
    ],
    description="Reclassify raster values using ordered exact, values-list, or range rules.",
    required_inputs=["raster", "rules"],
    optional_inputs=[
        "band_index",
        "nodata",
        "output_nodata",
        "keep_unmatched",
        "unmatched_value",
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
        "operation": "raster_reclassify",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "raster_analysis",
        "config_aware": True,
        "classification_supported": True,
        "threshold_supported": True,
        "routable": True,
    },
)
def reclassify_raster(
    raster: Any,
    rules: list[dict[str, Any]] | list[list[Any]] | None = None,
    band_index: int | None = None,
    nodata: Any = None,
    output_nodata: Any = None,
    keep_unmatched: bool | None = None,
    unmatched_value: Any = None,
    engine: str | None = None,
    precision: int | None = None,
    source_crs: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Reclassify raster values.

    Args:
        raster:
            RasterOut-like object or dict with data/array and metadata.
        rules:
            Ordered reclassification rules. First match wins.
        band_index:
            1-based input band index. For 2D raster, use 1.
        nodata:
            Input nodata value. If omitted, raster metadata/config nodata is used.
        output_nodata:
            Output value for input nodata pixels.
        keep_unmatched:
            If True, unmatched pixels keep original value.
            If False, unmatched pixels become unmatched_value.
        unmatched_value:
            Output value for unmatched pixels when keep_unmatched=False.
        engine:
            python | auto.
        precision:
            Rounding precision for float output values.
        source_crs:
            Optional CRS hint.
        metadata:
            Optional metadata to merge.

    Returns:
        RasterOut-like object with single-band 2D reclassified data.
    """
    config = _load_reclassify_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="python"))
    )

    data, input_metadata, source_info = _extract_raster(raster)
    band_count, height, width = _array_shape(data)

    final_band_index = _validate_band_index(
        pick_first(band_index, config.get("default_band_index"), default=1),
        band_count=band_count,
    )

    rules_candidate = rules
    if rules_candidate is None:
        rules_candidate = config.get("default_rules")

    normalized_rules = _normalize_rules(rules_candidate)

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

    final_keep_unmatched = bool(
        pick_first(keep_unmatched, config.get("default_keep_unmatched"), default=True)
    )

    final_unmatched_value = pick_first(
        unmatched_value,
        config.get("default_unmatched_value"),
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

    matched_pixel_count = 0
    input_nodata_pixel_count = 0
    unmatched_pixel_count = 0
    unmatched_kept_pixel_count = 0
    unmatched_default_pixel_count = 0

    rule_match_counts: dict[str, int] = {}
    output_value_counts: dict[str, int] = {}

    for row in range(height):
        output_row: list[Any] = []

        for col in range(width):
            value = _band_value(data, band_index=final_band_index, row=row, col=col)

            output_value, status, matched_rule = _reclassify_value(
                value,
                rules=normalized_rules,
                nodata=final_nodata,
                output_nodata=final_output_nodata,
                keep_unmatched=final_keep_unmatched,
                unmatched_value=final_unmatched_value,
                precision=final_precision,
            )

            output_row.append(output_value)
            _increment_count(output_value_counts, repr(output_value))

            if status == "input_nodata":
                input_nodata_pixel_count += 1
            elif status == "matched":
                matched_pixel_count += 1
                if matched_rule is not None:
                    _increment_count(rule_match_counts, matched_rule["label"])
            elif status == "unmatched_kept":
                unmatched_pixel_count += 1
                unmatched_kept_pixel_count += 1
            elif status == "unmatched_default":
                unmatched_pixel_count += 1
                unmatched_default_pixel_count += 1

        output.append(output_row)

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Raster reclassification is being evaluated on a geographic CRS. "
            "The operation is value-based, but downstream metric workflows may require reprojection."
        )

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    base_metadata = deepcopy(input_metadata) if preserve_metadata else {}

    serialized_rules = [
        {
            "index": rule["index"],
            "type": rule["type"],
            "label": rule["label"],
            "value": rule["value"],
            **({"equals": rule["equals"]} if rule["type"] == "equals" else {}),
            **({"values": rule["values"]} if rule["type"] == "values" else {}),
            **(
                {
                    "min": rule.get("min"),
                    "max": rule.get("max"),
                    "inclusive_min": rule.get("inclusive_min", True),
                    "inclusive_max": rule.get("inclusive_max", True),
                }
                if rule["type"] == "range"
                else {}
            ),
        }
        for rule in normalized_rules
    ]

    output_metadata = {
        **base_metadata,
        "source": "raster_reclassify",
        "loader": PLUGIN_ID,
        "operation": "raster_reclassify",
        "engine_requested": final_engine,
        "engine_used": "python",
        "input_band_count": band_count,
        "selected_band_index": final_band_index,
        "output_band_count": 1,
        "width": width,
        "height": height,
        "rules": serialized_rules,
        "rule_count": len(serialized_rules),
        "nodata": final_nodata,
        "output_nodata": final_output_nodata,
        "keep_unmatched": final_keep_unmatched,
        "unmatched_value": final_unmatched_value,
        "matched_pixel_count": matched_pixel_count,
        "input_nodata_pixel_count": input_nodata_pixel_count,
        "unmatched_pixel_count": unmatched_pixel_count,
        "unmatched_kept_pixel_count": unmatched_kept_pixel_count,
        "unmatched_default_pixel_count": unmatched_default_pixel_count,
        "rule_match_counts": rule_match_counts,
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
    name="Raster Reclassify",
    description=(
        "Reclassifies raster values using ordered exact, values-list, or range rules. "
        "Useful for NDVI classes, DEM classes, risk classes, and threshold maps."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
