"""
attribute_prep.py

GeoChat SDK Plugin
==================

Plugin ID:
    attribute_prep

Capabilities:
    - deduplicate_features:
        Remove (or flag) features with identical attributes and/or geometry.
    - map_schema:
        Rename, cast, drop, keep and default attribute columns in one step.
    - normalize_persian_text:
        Clean Persian/Arabic text attributes: Arabic yeh/kaf -> Persian, digit style,
        diacritics, spaces and zero-width characters.
    - sample_features:
        Reproducible random (optionally stratified) or systematic sample of features.
    - detect_outliers:
        Flag unusual values of a numeric attribute (IQR fences, z-score or robust modified z-score).
    - classify_breaks:
        Class breaks for a numeric attribute (equal interval, quantile, natural breaks,
        standard deviation or manual) and a class number/label per feature - the basis of
        choropleth styling.

Config-aware behavior:
    Reads config/plugins/attribute_prep.yaml (size limits).
"""

from __future__ import annotations

import bisect
import math
import re
import statistics
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    get_shapely,
    load_config,
    make_vector_out,
    to_int,
    validate_choice,
)
from plugins.csv_xy_loader import parse_number

PLUGIN_ID = "attribute_prep"

DEFAULTS = {"max_features": 500_000, "max_jenks_values": 1000}
_META = {
    "category": "analysis",
    "data_type": "vector",
    "returns": "VectorOut",
    "artifact_kind": "features",
    "access_scope": "attribute_analysis",
    "config_aware": True,
    "routable": True,
}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _names(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ValueError(f"{name} must be a string or a list of strings.")


# ------------------------------------------------------------------ deduplicate
@capability(
    name="deduplicate_features",
    keywords=[
        "deduplicate", "remove duplicates", "unique features", "duplicate records", "dedupe",
        "حذف تکراری", "رکوردهای تکراری", "حذف موارد تکراری", "یکتا سازی",
    ],
    description=(
        "Remove (or flag) duplicate features, comparing chosen attributes, the geometry, or both."
    ),
    required_inputs=["features"],
    optional_inputs=["by", "fields", "keep", "mark_only", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "deduplicate"},
)
def deduplicate_features(
    features: Any,
    by: str = "properties",
    fields: list[str] | str | None = None,
    keep: str = "first",
    mark_only: bool = False,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Remove duplicates.

    Args:
        features: Input features.
        by: ``properties`` (all, or ``fields``), ``geometry`` (normalized, so ring/vertex order
            does not matter) or ``both``.
        fields: Property names to compare when ``by`` includes properties (default: all).
        keep: ``first`` or ``last`` occurrence survives.
        mark_only: Keep every feature and add ``is_duplicate`` instead of dropping.
        metadata: Extra output metadata.

    Returns:
        VectorOut without the duplicates; metadata has ``duplicates_removed``.
    """
    config = load_config(PLUGIN_ID)
    mode = validate_choice(by, {"properties", "geometry", "both"}, "by")
    survivor = validate_choice(keep, {"first", "last"}, "keep")
    wanted = _names(fields, "fields")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")
    shape = get_shapely(PLUGIN_ID)[0] if mode != "properties" else None

    def key_of(feature: dict[str, Any]) -> tuple:
        parts: list[Any] = []
        if mode in {"properties", "both"}:
            props = feature.get("properties") or {}
            names = wanted or sorted(props)
            parts.append(tuple((n, repr(props.get(n))) for n in names))
        if shape is not None:
            geometry = feature.get("geometry")
            parts.append(shape(geometry).normalize().wkb_hex if geometry else None)
        return tuple(parts)

    order = range(len(input_features)) if survivor == "first" else range(len(input_features) - 1, -1, -1)
    seen: set[tuple] = set()
    duplicate_ids: set[int] = set()
    for index in order:
        key = key_of(input_features[index])
        if key in seen:
            duplicate_ids.add(index)
        else:
            seen.add(key)

    output = []
    for index, feature in enumerate(input_features):
        if mark_only:
            props = dict(feature.get("properties") or {})
            props["is_duplicate"] = index in duplicate_ids
            output.append({**feature, "properties": props})
        elif index not in duplicate_ids:
            output.append(feature)
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="deduplicate", source_info=source_info,
        extra={"by": mode, "fields": wanted or None, "keep": survivor,
               "duplicates_removed": 0 if mark_only else len(duplicate_ids),
               "duplicates_found": len(duplicate_ids)},
        user_metadata=metadata,
    )


# ------------------------------------------------------------------- map_schema
_TRUE = {"true", "yes", "y", "1", "t", "بله", "آری", "درست"}
_FALSE = {"false", "no", "n", "0", "f", "خیر", "نه", "نادرست"}


def _cast(value: Any, kind: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if kind == "str":
        return str(value)
    if kind in {"int", "float"}:
        number = parse_number(value) if not isinstance(value, (int, float)) or isinstance(value, bool) \
            else float(value)
        if number is None or isinstance(value, bool):
            raise ValueError(f"cannot convert {value!r} to {kind}")
        if kind == "float":
            return number
        if not float(number).is_integer():
            raise ValueError(f"{value!r} is not a whole number")
        return int(number)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"cannot convert {value!r} to bool")
    raise ValueError(f"unknown type {kind!r}")


@capability(
    name="map_schema",
    keywords=[
        "rename columns", "cast columns", "change types", "drop columns", "select columns",
        "schema mapping", "rename fields", "default values",
        "تغییر نام ستون", "تبدیل نوع", "حذف ستون", "انتخاب ستون ها", "نگاشت ستون", "مقدار پیش فرض",
    ],
    description=(
        "Reshape attribute columns: rename (mapping), convert types (cast: int/float/str/bool), "
        "fill missing values (defaults), drop or keep columns."
    ),
    required_inputs=["features"],
    optional_inputs=["mapping", "cast", "defaults", "drop", "keep_only", "on_error", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "map_schema"},
)
def map_schema(
    features: Any,
    mapping: dict[str, str] | None = None,
    cast: dict[str, str] | None = None,
    defaults: dict[str, Any] | None = None,
    drop: list[str] | str | None = None,
    keep_only: list[str] | str | None = None,
    on_error: str = "null",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Reshape attributes. Order of operations: rename -> cast -> defaults -> drop / keep_only
    (all names refer to the *new* names after renaming).

    Args:
        features: Input features.
        mapping: ``{old_name: new_name}``. Renaming onto an existing column is refused.
        cast: ``{column: "int" | "float" | "str" | "bool"}``; Persian digits are understood.
        defaults: ``{column: value}`` used where the value is missing/null.
        drop: Columns to remove.
        keep_only: If given, only these columns survive.
        on_error: ``null`` stores null where a cast fails (counted); ``raise`` aborts.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the reshaped properties and the geometry untouched.
    """
    config = load_config(PLUGIN_ID)
    policy = validate_choice(on_error, {"null", "raise"}, "on_error")
    mapping = mapping or {}
    cast = cast or {}
    defaults = defaults or {}
    if not all(isinstance(d, dict) for d in (mapping, cast, defaults)):
        raise ValueError("mapping, cast and defaults must be objects.")
    if len(set(mapping.values())) != len(mapping) or any(
        not isinstance(v, str) or not v.strip() for v in mapping.values()
    ):
        raise ValueError("mapping must map to distinct, non-empty column names.")
    kinds = {c: validate_choice(k, {"int", "float", "str", "bool"}, f"cast['{c}']") for c, k in cast.items()}
    dropped = set(_names(drop, "drop"))
    kept = _names(keep_only, "keep_only")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")

    cast_errors = 0
    output = []
    for index, feature in enumerate(input_features):
        props = dict(feature.get("properties") or {})
        for old, new in mapping.items():
            if old in props:
                if new in props and new != old:
                    raise ValueError(f"Cannot rename '{old}' to '{new}': the column already exists.")
                props[new] = props.pop(old)
        for column, kind in kinds.items():
            if column in props:
                try:
                    props[column] = _cast(props[column], kind)
                except ValueError as exc:
                    if policy == "raise":
                        raise ValueError(f"Feature {index}, column '{column}': {exc}.") from None
                    cast_errors += 1
                    props[column] = None
        for column, value in defaults.items():
            if props.get(column) is None:
                props[column] = value
        for column in dropped:
            props.pop(column, None)
        if kept:
            props = {c: props.get(c) for c in kept}
        output.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="map_schema", source_info=source_info,
        extra={"renamed": mapping, "cast": kinds, "cast_errors": cast_errors,
               "dropped": sorted(dropped), "kept_only": kept or None},
        user_metadata=metadata,
    )


# ------------------------------------------------------------ Persian normalizer
_TO_PERSIAN_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"})
_TO_PERSIAN_DIGITS = str.maketrans("0123456789٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹۰۱۲۳۴۵۶۷۸۹")
_TO_ENGLISH_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DIACRITICS_RE = re.compile("[ً-ٰٟۖ-ۭـ]")  # harakat, dagger alef, tatweel
_SPACES_RE = re.compile(r"[ \t  -  ]+")
_ZW_RE = re.compile("[​‍‎‏⁦-⁩﻿]")  # all but ZWNJ (U+200C)


def normalize_text(text: str, *, letters: bool, digits: str, diacritics: bool, spaces: bool,
                   zero_width: bool) -> str:
    if letters:
        text = text.translate(_TO_PERSIAN_LETTERS)
    if digits == "persian":
        text = text.translate(_TO_PERSIAN_DIGITS)
    elif digits == "english":
        text = text.translate(_TO_ENGLISH_DIGITS)
    if diacritics:
        text = _DIACRITICS_RE.sub("", text)
    if zero_width:
        text = _ZW_RE.sub("", text)
    if spaces:
        text = _SPACES_RE.sub(" ", text).strip()
        text = re.sub(r"\s*‌\s*", "‌", text) if "‌" in text else text
    return text


@capability(
    name="normalize_persian_text",
    keywords=[
        "normalize persian", "persian text", "fix arabic letters", "persian digits", "clean names",
        "yeh kaf", "نرمال سازی متن فارسی", "اصلاح ی و ک", "ارقام فارسی", "پاکسازی نام ها",
        "یکسان سازی حروف", "فاصله و نیم فاصله",
    ],
    description=(
        "Normalize Persian/Arabic text attributes: Arabic yeh/kaf to Persian, digit style "
        "(persian/english), diacritics, repeated spaces and zero-width characters."
    ),
    required_inputs=["features"],
    optional_inputs=["fields", "digits", "arabic_to_persian", "remove_diacritics", "clean_spaces",
                     "remove_zero_width", "suffix", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "normalize_persian_text"},
)
def normalize_persian_text(
    features: Any,
    fields: list[str] | str | None = None,
    digits: str = "keep",
    arabic_to_persian: bool = True,
    remove_diacritics: bool = True,
    clean_spaces: bool = True,
    remove_zero_width: bool = True,
    suffix: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Normalize text properties.

    Args:
        features: Input features.
        fields: Text properties to process (default: every string property).
        digits: ``keep`` | ``persian`` (۱۲۳) | ``english`` (123).
        arabic_to_persian: Map ي/ى -> ی, ك -> ک, ة/ۀ -> ه.
        remove_diacritics: Strip harakat and tatweel.
        clean_spaces: Collapse repeated spaces, trim, tidy spaces around ZWNJ (the half-space
            ZWNJ itself is kept: it is meaningful in Persian).
        remove_zero_width: Remove other invisible characters (ZWSP, ZWJ, bidi marks, BOM).
        suffix: Write results to ``<field><suffix>`` and keep the original column.
        metadata: Extra output metadata.

    Returns:
        VectorOut; metadata has ``values_changed``.
    """
    config = load_config(PLUGIN_ID)
    digit_mode = validate_choice(digits, {"keep", "persian", "english"}, "digits")
    wanted = _names(fields, "fields")
    if suffix is not None and (not isinstance(suffix, str) or not suffix.strip()):
        raise ValueError("suffix must be a non-empty string.")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")

    changed = 0
    output = []
    for feature in input_features:
        props = dict(feature.get("properties") or {})
        for name in (wanted or [k for k, v in props.items() if isinstance(v, str)]):
            value = props.get(name)
            if not isinstance(value, str):
                continue
            cleaned = normalize_text(
                value, letters=arabic_to_persian, digits=digit_mode, diacritics=remove_diacritics,
                spaces=clean_spaces, zero_width=remove_zero_width)
            if cleaned != value:
                changed += 1
            props[f"{name}{suffix}" if suffix else name] = cleaned
        output.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="normalize_persian_text", source_info=source_info,
        extra={"fields": wanted or "all text properties", "digits": digit_mode,
               "values_changed": changed},
        user_metadata=metadata,
    )


# --------------------------------------------------------------- sampling
@capability(
    name="sample_features",
    keywords=[
        "random sample", "sample features", "subset randomly", "stratified sample", "systematic sample",
        "pick random rows", "survey sample",
        "نمونه گیری", "نمونه تصادفی", "نمونه گیری طبقه ای", "انتخاب تصادفی", "نمونه سیستماتیک",
    ],
    description=(
        "Reproducible sample of features: random, stratified by an attribute (proportional or equal "
        "per stratum) or systematic (every k-th); a seed makes it repeatable."
    ),
    required_inputs=["features"],
    optional_inputs=["size", "fraction", "method", "stratify_by", "per_stratum", "seed", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "sample_features"},
)
def sample_features(
    features: Any,
    size: int | None = None,
    fraction: float | None = None,
    method: str = "random",
    stratify_by: str | None = None,
    per_stratum: bool = False,
    seed: int = 0,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Sample features.

    Args:
        features: Population.
        size: Number of features wanted (or per stratum with ``per_stratum``).
        fraction: Share of the population (0 < fraction <= 1), instead of ``size``.
        method: ``random`` (without replacement) or ``systematic`` (every k-th after a seeded start).
        stratify_by: Property to stratify on. By default the sample size is split between strata in
            proportion to their size (every stratum keeps at least one if the sample allows).
        per_stratum: Interpret ``size`` as a count for EACH stratum (equal allocation).
        seed: Seed; the same input and seed always give the same sample.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the sampled features in their original order; metadata has the population
        and sample sizes and the per-stratum counts.
    """
    import random

    config = load_config(PLUGIN_ID)
    chosen = validate_choice(method, {"random", "systematic"}, "method")
    if (size is None) == (fraction is None):
        raise ValueError("Provide exactly one of size or fraction.")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")
    total = len(input_features)
    if total == 0:
        raise ValueError("There are no features to sample.")
    if fraction is not None:
        share = float(fraction)
        if not 0 < share <= 1:
            raise ValueError("fraction must be in (0, 1].")
        wanted = max(1, round(total * share))
    else:
        wanted = to_int(size, "size")
        if wanted < 1:
            raise ValueError("size must be >= 1.")
    rng = random.Random(to_int(seed, "seed"))

    strata: dict[str, list[int]] = {}
    for index, feature in enumerate(input_features):
        key = repr((feature.get("properties") or {}).get(stratify_by)) if stratify_by else "all"
        strata.setdefault(key, []).append(index)

    if per_stratum:
        if not stratify_by:
            raise ValueError("per_stratum needs stratify_by.")
        quota = {k: min(wanted, len(v)) for k, v in strata.items()}
    else:
        wanted = min(wanted, total)
        raw = {k: wanted * len(v) / total for k, v in strata.items()}
        quota = {k: int(raw[k]) for k in strata}
        for key in sorted(strata, key=lambda k: raw[k] - quota[k], reverse=True)[: wanted - sum(quota.values())]:
            quota[key] += 1
        if stratify_by and wanted >= len(strata):  # keep every stratum represented
            for key in strata:
                if quota[key] == 0:
                    donor = max(quota, key=lambda k: quota[k])
                    if quota[donor] > 1:
                        quota[donor] -= 1
                        quota[key] = 1

    picked: set[int] = set()
    for key, members in strata.items():
        count = min(quota[key], len(members))
        if count <= 0:
            continue
        if chosen == "random":
            picked.update(rng.sample(members, count))
        else:
            step = len(members) / count
            start = rng.random() * step
            picked.update(members[min(len(members) - 1, int(start + i * step))] for i in range(count))
    output = [feature for index, feature in enumerate(input_features) if index in picked]
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="sample_features", source_info=source_info,
        extra={"population_size": total, "sample_size": len(output), "method": chosen, "seed": seed,
               "stratify_by": stratify_by,
               "stratum_counts": {k: sum(1 for i in v if i in picked) for k, v in strata.items()}
               if stratify_by else None},
        user_metadata=metadata,
    )


# -------------------------------------------------------------- outliers
@capability(
    name="detect_outliers",
    keywords=[
        "outliers", "detect outliers", "anomalies", "unusual values", "iqr", "z score", "data errors",
        "extreme values",
        "داده پرت", "تشخیص ناهنجاری", "مقادیر غیرعادی", "مقادیر حدی", "خطاهای داده", "پرت",
    ],
    description=(
        "Flag outliers in a numeric attribute with IQR fences, z-score or the robust modified z-score "
        "(MAD), optionally within groups."
    ),
    required_inputs=["features", "field"],
    optional_inputs=["method", "threshold", "group_by", "outlier_field", "score_field", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "detect_outliers"},
)
def detect_outliers(
    features: Any,
    field: str,
    method: str = "iqr",
    threshold: float | None = None,
    group_by: str | None = None,
    outlier_field: str = "is_outlier",
    score_field: str = "outlier_score",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Outlier detection.

    Args:
        features: Input features.
        field: Numeric attribute (Persian digits understood; non-numeric values stay unflagged).
        method: ``iqr`` (outside Q1 - t*IQR .. Q3 + t*IQR, default t=1.5), ``zscore`` (|z| > t,
            default 3) or ``modified_zscore`` (0.6745*|x - median| / MAD > t, default 3.5).
        threshold: Override the method's default ``t``.
        group_by: Compute the statistics separately per value of this property.
        outlier_field / score_field: Output property names (the score is distance beyond the fence
            for IQR, |z| otherwise).
        metadata: Extra output metadata.

    Returns:
        VectorOut with a boolean ``is_outlier`` and a numeric score; metadata has the fences / limits
        per group and the outlier count.
    """
    config = load_config(PLUGIN_ID)
    chosen = validate_choice(method, {"iqr", "zscore", "modified_zscore"}, "method")
    limit = {"iqr": 1.5, "zscore": 3.0, "modified_zscore": 3.5}[chosen] if threshold is None else float(threshold)
    if limit <= 0 or not math.isfinite(limit):
        raise ValueError("threshold must be a positive number.")
    if not isinstance(field, str) or not field.strip():
        raise ValueError("field must be a property name.")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")

    values: dict[str, list[tuple[int, float]]] = {}
    for index, feature in enumerate(input_features):
        props = feature.get("properties") or {}
        number = parse_number(props.get(field))
        if number is not None:
            values.setdefault(repr(props.get(group_by)) if group_by else "all", []).append((index, number))
    if not values:
        raise ValueError(f"No numeric values found in '{field}'.")

    flags: dict[int, tuple[bool, float]] = {}
    summary: dict[str, Any] = {}
    for key, pairs in values.items():
        data = sorted(v for _, v in pairs)
        if chosen == "iqr":
            q1, q3 = _quantile(data, 0.25), _quantile(data, 0.75)
            spread = q3 - q1
            low, high = q1 - limit * spread, q3 + limit * spread
            summary[key] = {"lower_fence": low, "upper_fence": high, "q1": q1, "q3": q3, "n": len(data)}
            for index, value in pairs:
                beyond = max(low - value, value - high, 0.0)
                flags[index] = (value < low or value > high, beyond)
        elif chosen == "zscore":
            mean = statistics.fmean(data)
            sd = statistics.pstdev(data)
            summary[key] = {"mean": mean, "std": sd, "n": len(data)}
            for index, value in pairs:
                z = abs(value - mean) / sd if sd > 0 else 0.0
                flags[index] = (z > limit, z)
        else:
            median = statistics.median(data)
            mad = statistics.median([abs(v - median) for v in data])
            summary[key] = {"median": median, "mad": mad, "n": len(data)}
            for index, value in pairs:
                score = 0.6745 * abs(value - median) / mad if mad > 0 else (0.0 if value == median else math.inf)
                flags[index] = (score > limit, score if math.isfinite(score) else 1e12)
    output = []
    for index, feature in enumerate(input_features):
        props = dict(feature.get("properties") or {})
        flag = flags.get(index)
        props[outlier_field] = None if flag is None else flag[0]
        props[score_field] = None if flag is None else flag[1]
        output.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="detect_outliers", source_info=source_info,
        extra={"field": field, "method": chosen, "threshold": limit, "group_by": group_by,
               "outlier_count": sum(1 for f in flags.values() if f[0]), "statistics": summary,
               "unscored": len(input_features) - len(flags)},
        user_metadata=metadata,
    )


# ------------------------------------------------------------ classification
def jenks_breaks(values: list[float], classes: int, max_values: int) -> list[float]:
    """Fisher-Jenks natural breaks; very large inputs are reduced to quantile representatives."""
    data = sorted(values)
    if len(data) > max_values:
        data = [data[round(i * (len(data) - 1) / (max_values - 1))] for i in range(max_values)]
    n = len(data)
    if classes >= n:
        return sorted(set(data))
    lower = [[0] * (classes + 1) for _ in range(n + 1)]
    variance = [[math.inf] * (classes + 1) for _ in range(n + 1)]
    for j in range(1, classes + 1):
        lower[1][j] = 1
        variance[1][j] = 0.0
    for length in range(2, n + 1):
        s1 = s2 = w = 0.0
        for m in range(1, length + 1):
            lower_index = length - m + 1
            value = data[lower_index - 1]
            w += 1
            s1 += value
            s2 += value * value
            var = s2 - s1 * s1 / w
            previous = lower_index - 1
            if previous:
                for j in range(2, classes + 1):
                    if variance[length][j] >= var + variance[previous][j - 1]:
                        lower[length][j] = lower_index
                        variance[length][j] = var + variance[previous][j - 1]
        lower[length][1] = 1
        variance[length][1] = s2 - s1 * s1 / w
    breaks = [0.0] * (classes + 1)
    breaks[classes] = data[-1]
    breaks[0] = data[0]
    k, count = n, classes
    while count > 1:
        index = lower[k][count] - 2
        breaks[count - 1] = data[max(index, 0)]
        k = lower[k][count] - 1
        count -= 1
    return breaks


def _quantile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


@capability(
    name="classify_breaks",
    keywords=[
        "classification", "class breaks", "natural breaks", "jenks", "quantile", "equal interval",
        "choropleth classes", "std dev classes", "bin values",
        "طبقه بندی", "شکست های طبیعی", "کوانتایل", "فاصله مساوی", "طبقات نقشه", "دسته بندی مقادیر",
    ],
    description=(
        "Compute class breaks for a numeric attribute (equal_interval, quantile, natural_breaks, "
        "std_dev or manual) and give every feature its class number and label."
    ),
    required_inputs=["features", "field"],
    optional_inputs=["method", "classes", "breaks", "class_field", "label_field", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "operation": "classify_breaks"},
)
def classify_breaks(
    features: Any,
    field: str,
    method: str = "natural_breaks",
    classes: int = 5,
    breaks: list[float] | None = None,
    class_field: str = "class",
    label_field: str = "class_label",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Classify a numeric attribute.

    Args:
        features: Input features.
        field: Numeric property to classify (non-numeric values get null classes).
        method: equal_interval | quantile | natural_breaks | std_dev | manual.
        classes: Number of classes (2-12; ignored for std_dev and manual).
        breaks: For ``manual``: ascending upper-bound list *including* min and max
            ([0, 10, 50, 200] makes three classes).
        class_field: Property receiving the class number (1 = lowest).
        label_field: Property receiving a label such as ``10 – 50``.
        metadata: Extra output metadata.

    Returns:
        VectorOut; metadata has ``breaks`` and ``class_counts``.
    """
    config = load_config(PLUGIN_ID)
    chosen = validate_choice(method, {"equal_interval", "quantile", "natural_breaks", "std_dev", "manual"},
                             "method")
    if not isinstance(field, str) or not field.strip():
        raise ValueError("field must be a property name.")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")
    numbers: list[float | None] = []
    for feature in input_features:
        numbers.append(parse_number((feature.get("properties") or {}).get(field)))
    values = sorted(n for n in numbers if n is not None)
    if not values:
        raise ValueError(f"No numeric values found in '{field}'.")
    lo, hi = values[0], values[-1]

    if chosen == "manual":
        if not isinstance(breaks, (list, tuple)) or len(breaks) < 2:
            raise ValueError("manual classification needs breaks=[min, ..., max] (at least 2 numbers).")
        edges = [float(b) for b in breaks]
        if any(b >= edges[i + 1] for i, b in enumerate(edges[:-1])):
            raise ValueError("breaks must be strictly ascending.")
    elif chosen == "std_dev":
        if lo == hi:
            edges = [lo, hi]
        else:
            mean, sd = statistics.fmean(values), statistics.pstdev(values)
            edges = sorted({max(lo, min(hi, mean + k * sd)) for k in (-2, -1, 0, 1, 2)} | {lo, hi})
    else:
        count = to_int(classes, "classes")
        if not 2 <= count <= 12:
            raise ValueError("classes must be between 2 and 12.")
        if lo == hi:
            edges = [lo, hi]
        elif chosen == "equal_interval":
            edges = [lo + (hi - lo) * i / count for i in range(count + 1)]
        elif chosen == "quantile":
            edges = sorted({_quantile(values, i / count) for i in range(count + 1)})
        else:
            edges = sorted(set(jenks_breaks(values, count, _limit(config, "max_jenks_values"))))
    if len(edges) < 2:
        edges = [lo, hi]

    def class_of(value: float) -> int:
        if value <= edges[0]:
            return 1
        return min(len(edges) - 1, max(1, bisect.bisect_left(edges, value)))

    def fmt(x: float) -> str:
        return f"{x:.6g}"

    labels = {i + 1: f"{fmt(edges[i])} – {fmt(edges[i + 1])}" for i in range(len(edges) - 1)}
    counts = {i: 0 for i in labels}
    output = []
    for feature, number in zip(input_features, numbers, strict=True):
        props = dict(feature.get("properties") or {})
        if number is None or (chosen == "manual" and not edges[0] <= number <= edges[-1]):
            props[class_field], props[label_field] = None, None
        else:
            klass = class_of(number)
            props[class_field], props[label_field] = klass, labels[klass]
            counts[klass] += 1
        output.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="classify_breaks", source_info=source_info,
        extra={"field": field, "method": chosen, "breaks": edges,
               "class_counts": {labels[k]: v for k, v in counts.items()},
               "unclassified": sum(1 for n in numbers if n is None)},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Attribute Preparation",
    description=(
        "Deduplicate features, reshape columns, normalize Persian text and compute class breaks."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
