"""
feature_enrichment.py

Feature Enrichment and Property Join
====================================

Plugin ID:
    feature_enrichment

Purpose:
    Enrich vector features by deriving/copying properties and joining attributes.

Capabilities:
    - enrich_feature_properties
    - join_feature_properties

Why this matters:
    Spatial analysis often produces intermediate fields like "distance".
    Before scoring, we need stable semantic fields such as:
        distance_to_poi
        distance_to_road
        inside_buildable_zone
        flood_risk
        earthquake_risk
        fire_risk
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut


PLUGIN_ID = "feature_enrichment"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ #
# Feature extraction
# ------------------------------------------------------------------ #

def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(feature, dict):
        raise ValueError(f"Feature at index {index} must be a dict.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": dict(properties),
    }


def _extract_features(input_data: Any, label: str = "features") -> list[dict[str, Any]]:
    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw = getattr(input_data, "features")
    elif isinstance(input_data, dict):
        gtype = input_data.get("type")
        if gtype == "FeatureCollection":
            raw = input_data.get("features", [])
        elif gtype == "Feature":
            raw = [input_data]
        else:
            raise ValueError(f"{label} dict must be FeatureCollection or Feature.")
    elif isinstance(input_data, list):
        raw = input_data
    else:
        raise ValueError(
            f"{label} must be VectorOut-like, FeatureCollection, Feature, or list[Feature]."
        )

    if not isinstance(raw, list):
        raise ValueError(f"{label} features must be a list.")

    return [_normalize_feature(item, idx) for idx, item in enumerate(raw)]


# ------------------------------------------------------------------ #
# Property helpers
# ------------------------------------------------------------------ #

_MISSING = object()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_float(value: Any, default: Any = _MISSING) -> Any:
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _to_int(value: Any, default: Any = _MISSING) -> Any:
    number = _to_float(value, default)
    if number is _MISSING:
        return default
    try:
        return int(number)
    except Exception:
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if _is_number(value):
        return float(value) != 0.0
    if isinstance(value, str):
        return value.strip().lower() in {
            "true",
            "1",
            "yes",
            "y",
            "ok",
            "pass",
            "passed",
            "inside",
            "within",
            "low",
            "مجاز",
            "داخل",
            "بله",
        }
    return bool(value)


def _get_property(properties: dict[str, Any], path: str, default: Any = _MISSING) -> Any:
    """
    Read property by exact key first, then dotted path.
    """
    if not path:
        return default

    if path in properties:
        return properties[path]

    current: Any = properties
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default

    return current


def _set_property(properties: dict[str, Any], path: str, value: Any) -> None:
    """
    Set property by dotted path.
    """
    if not path:
        raise ValueError("target path must be non-empty.")

    parts = path.split(".")
    current = properties

    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing

    current[parts[-1]] = value


def _apply_transform(value: Any, transform: str | None, default: Any = _MISSING) -> Any:
    if not transform:
        return value

    transform = transform.strip().lower()

    if value is _MISSING:
        return default

    if transform in {"float", "number"}:
        return _to_float(value, default)

    if transform in {"int", "integer"}:
        return _to_int(value, default)

    if transform in {"str", "string"}:
        return str(value)

    if transform in {"bool", "boolean"}:
        return _to_bool(value)

    if transform in {"lower", "lowercase"}:
        return str(value).lower()

    if transform in {"upper", "uppercase"}:
        return str(value).upper()

    raise ValueError(f"Unsupported transform: {transform}")


def _resolve_rule_value(properties: dict[str, Any], rule: dict[str, Any]) -> Any:
    """
    Resolve value for an enrichment rule.

    Supported rule forms:
        {"target": "distance_to_poi", "source": "distance"}
        {"target": "distance_to_poi", "first_existing": ["distance_m", "distance"]}
        {"target": "flood_risk", "value": "low"}
        {"target": "foo", "source": "bar", "default": 0}
    """
    if "value" in rule:
        return rule["value"]

    default = rule.get("default", _MISSING)

    if "source" in rule:
        value = _get_property(properties, str(rule["source"]), _MISSING)
        return value if value is not _MISSING else default

    first_existing = rule.get("first_existing")
    if isinstance(first_existing, list):
        for source in first_existing:
            value = _get_property(properties, str(source), _MISSING)
            if value is not _MISSING:
                return value
        return default

    return default


def _build_vector_metadata(features: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    geometry_types: dict[str, int] = {}

    for feature in features:
        geom = feature.get("geometry")
        if isinstance(geom, dict):
            gt = str(geom.get("type") or "Unknown")
            geometry_types[gt] = geometry_types.get(gt, 0) + 1

    metadata = {
        "feature_count": len(features),
        "geometry_types": geometry_types,
    }
    if extra:
        metadata.update(extra)

    return metadata


# ------------------------------------------------------------------ #
# Capability: enrich_feature_properties
# ------------------------------------------------------------------ #

@capability(
    name="enrich_feature_properties",
    keywords=[
        "enrich features",
        "derive fields",
        "copy field",
        "rename field",
        "add property",
        "attribute enrichment",
        "feature enrichment",
        "غنی سازی",
        "افزودن فیلد",
        "کپی فیلد",
        "تغییر نام فیلد",
        "ویژگی جدید",
    ],
    description=(
        "Derive/copy/rename feature properties using declarative rules. "
        "Useful for preparing stable scoring fields."
    ),
    required_inputs=["features", "rules"],
    optional_inputs=["skip_missing", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "feature_enrichment",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.feature_enrichment",
    },
)
def enrich_feature_properties(
    features: Any,
    rules: list[dict[str, Any]],
    skip_missing: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Enrich features using property derivation rules.

    Example rules:
        [
          {"target": "distance_to_poi", "source": "distance", "transform": "float"},
          {"target": "inside_buildable_zone", "source": "__in_polygon__", "transform": "bool"},
          {"target": "flood_risk", "value": "low"}
        ]
    """
    if not isinstance(rules, list):
        raise ValueError("rules must be a list of dicts.")

    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    input_features = _extract_features(features)
    output_features: list[dict[str, Any]] = []

    applied_count = 0
    skipped_count = 0

    for feature in input_features:
        out = dict(feature)
        props = dict(out.get("properties") or {})

        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValueError(f"rules[{idx}] must be a dict.")

            target = str(rule.get("target") or "")
            if not target:
                raise ValueError(f"rules[{idx}].target is required.")

            value = _resolve_rule_value(props, rule)

            if value is _MISSING:
                if skip_missing:
                    skipped_count += 1
                    continue
                value = None

            transform = rule.get("transform")
            default = rule.get("default", _MISSING)
            value = _apply_transform(value, str(transform) if transform else None, default)

            if value is _MISSING:
                if skip_missing:
                    skipped_count += 1
                    continue
                value = None

            _set_property(props, target, value)
            applied_count += 1

        out["properties"] = props
        output_features.append(out)

    output_metadata = _build_vector_metadata(
        output_features,
        {
            "source": PLUGIN_ID,
            "operation": "enrich_feature_properties",
            "rule_count": len(rules),
            "applied_count": applied_count,
            "skipped_count": skipped_count,
            "created_at": _utc_now_iso(),
            **(metadata or {}),
        },
    )

    return VectorOut(features=output_features, metadata=output_metadata)


# ------------------------------------------------------------------ #
# Capability: join_feature_properties
# ------------------------------------------------------------------ #

@capability(
    name="join_feature_properties",
    keywords=[
        "join attributes",
        "join properties",
        "attribute join",
        "merge properties",
        "join by id",
        "اتصال جدولی",
        "اتصال ویژگی",
        "ترکیب فیلدها",
        "ادغام ویژگی‌ها",
    ],
    description="Join properties from right features into left features by matching property keys.",
    required_inputs=["left_features", "right_features"],
    optional_inputs=[
        "left_key",
        "right_key",
        "fields",
        "prefix",
        "overwrite",
        "unmatched",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "feature_join",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.feature_enrichment",
    },
)
def join_feature_properties(
    left_features: Any,
    right_features: Any,
    left_key: str = "id",
    right_key: str = "id",
    fields: list[str] | dict[str, str] | None = None,
    prefix: str = "",
    overwrite: bool = False,
    unmatched: str = "keep",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Join right feature properties into left features.

    Args:
        fields:
            None:
                copy all right properties except right_key.
            list[str]:
                copy listed right fields. Target name is prefix + field.
            dict[str, str]:
                mapping of right_field -> target_field.
        unmatched:
            keep | drop
    """
    if unmatched not in {"keep", "drop"}:
        raise ValueError("unmatched must be 'keep' or 'drop'.")

    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    left = _extract_features(left_features, label="left_features")
    right = _extract_features(right_features, label="right_features")

    right_index: dict[str, dict[str, Any]] = {}
    for feature in right:
        props = feature.get("properties") or {}
        key_value = _get_property(props, right_key, _MISSING)
        if key_value is _MISSING or key_value is None:
            continue
        right_index[str(key_value)] = props

    output_features: list[dict[str, Any]] = []
    matched_count = 0
    unmatched_count = 0
    copied_field_count = 0

    for feature in left:
        left_props = dict(feature.get("properties") or {})
        key_value = _get_property(left_props, left_key, _MISSING)

        right_props = None
        if key_value is not _MISSING and key_value is not None:
            right_props = right_index.get(str(key_value))

        if right_props is None:
            unmatched_count += 1
            if unmatched == "drop":
                continue
            out = dict(feature)
            out["properties"] = left_props
            output_features.append(out)
            continue

        matched_count += 1

        if fields is None:
            copy_map = {
                str(k): f"{prefix}{k}"
                for k in right_props.keys()
                if str(k) != right_key
            }
        elif isinstance(fields, list):
            copy_map = {
                str(field): f"{prefix}{field}"
                for field in fields
            }
        elif isinstance(fields, dict):
            copy_map = {
                str(src): str(dst)
                for src, dst in fields.items()
            }
        else:
            raise ValueError("fields must be None, list[str], or dict[str, str].")

        for src, dst in copy_map.items():
            value = _get_property(right_props, src, _MISSING)
            if value is _MISSING:
                continue
            if not overwrite and _get_property(left_props, dst, _MISSING) is not _MISSING:
                continue
            _set_property(left_props, dst, value)
            copied_field_count += 1

        out = dict(feature)
        out["properties"] = left_props
        output_features.append(out)

    output_metadata = _build_vector_metadata(
        output_features,
        {
            "source": PLUGIN_ID,
            "operation": "join_feature_properties",
            "left_count": len(left),
            "right_count": len(right),
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "copied_field_count": copied_field_count,
            "left_key": left_key,
            "right_key": right_key,
            "unmatched": unmatched,
            "created_at": _utc_now_iso(),
            **(metadata or {}),
        },
    )

    return VectorOut(features=output_features, metadata=output_metadata)


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Feature Enrichment",
    description="Feature property enrichment, derivation and attribute joining.",
    author="GeoChat Platform Team",
    permissions=[],
)
