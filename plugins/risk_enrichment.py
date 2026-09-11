"""
risk_enrichment.py

Risk Enrichment MVP
===================

Plugin ID:
    risk_enrichment

Capability:
    - enrich_risk

Purpose:
    Add flood/earthquake/fire risk attributes to vector features.

This is an MVP deterministic implementation:
    - Uses default risks.
    - Supports per-feature overrides by id.
    - Supports rule-based mapping from existing properties.
    - Does not call external APIs yet.

Future:
    This capability can later call a real risk API or spatial hazard layers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

PLUGIN_ID = "risk_enrichment"


RISK_FIELDS = ("flood_risk", "earthquake_risk", "fire_risk")

DEFAULT_RISKS = {
    "flood_risk": "low",
    "earthquake_risk": "low",
    "fire_risk": "low",
}


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
# Helpers
# ------------------------------------------------------------------ #

_MISSING = object()


def _get_property(properties: dict[str, Any], path: str, default: Any = _MISSING) -> Any:
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


def _normalize_risk_level(value: Any, default: str = "low") -> str:
    """
    Normalize risk levels to one of:
        very_low, low, medium, high, very_high, critical

    Persian labels are accepted too.
    """
    if value is None:
        return default

    text = str(value).strip().lower()

    mapping = {
        "very_low": "very_low",
        "very low": "very_low",
        "خیلی کم": "very_low",

        "low": "low",
        "کم": "low",

        "medium": "medium",
        "moderate": "medium",
        "متوسط": "medium",

        "high": "high",
        "زیاد": "high",

        "very_high": "very_high",
        "very high": "very_high",
        "خیلی زیاد": "very_high",

        "critical": "critical",
        "بحرانی": "critical",
    }

    if text in mapping:
        return mapping[text]

    # Numeric convention:
    # 0-0.2 very_low, 0.2-0.4 low, 0.4-0.6 medium, 0.6-0.8 high, 0.8+ very_high
    try:
        num = float(text)
    except ValueError:
        return default

    if num <= 0.2:
        return "very_low"
    if num <= 0.4:
        return "low"
    if num <= 0.6:
        return "medium"
    if num <= 0.8:
        return "high"
    return "very_high"


def _normalize_default_risks(default_risks: dict[str, Any] | None) -> dict[str, str]:
    result = dict(DEFAULT_RISKS)

    if isinstance(default_risks, dict):
        for field in RISK_FIELDS:
            if field in default_risks:
                result[field] = _normalize_risk_level(default_risks[field], result[field])

    return result


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


def _apply_mapping_rule(
    properties: dict[str, Any],
    rule: dict[str, Any],
) -> tuple[str, str] | None:
    """
    Apply a rule like:

    {
      "target": "flood_risk",
      "source": "flood_zone",
      "mapping": {
        "A": "low",
        "B": "medium",
        "C": "high"
      },
      "default": "low"
    }
    """
    target = str(rule.get("target") or "")
    source = str(rule.get("source") or "")

    if not target or not source:
        return None

    raw_value = _get_property(properties, source, _MISSING)
    default = rule.get("default", "low")

    if raw_value is _MISSING:
        return target, _normalize_risk_level(default)

    mapping = rule.get("mapping")

    if isinstance(mapping, dict):
        mapped = mapping.get(str(raw_value))
        if mapped is None:
            mapped = mapping.get(str(raw_value).lower())
        if mapped is None:
            mapped = default
        return target, _normalize_risk_level(mapped)

    return target, _normalize_risk_level(raw_value, _normalize_risk_level(default))


def _merge_risk_spec(
    risk_spec: dict[str, Any] | None,
    default_risks: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    rules: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {}

    if risk_spec is not None:
        if not isinstance(risk_spec, dict):
            raise ValueError("risk_spec must be a dict or None.")
        spec.update(risk_spec)

    if default_risks is not None:
        spec["default_risks"] = default_risks
    if overrides is not None:
        spec["overrides"] = overrides
    if rules is not None:
        spec["rules"] = rules

    spec.setdefault("default_risks", DEFAULT_RISKS)
    spec.setdefault("overrides", {})
    spec.setdefault("rules", [])

    if not isinstance(spec["default_risks"], dict):
        raise ValueError("default_risks must be a dict.")

    if not isinstance(spec["overrides"], dict):
        raise ValueError("overrides must be a dict.")

    if not isinstance(spec["rules"], list):
        raise ValueError("rules must be a list.")

    return spec


# ------------------------------------------------------------------ #
# Capability: enrich_risk
# ------------------------------------------------------------------ #

@capability(
    name="enrich_risk",
    keywords=[
        "risk enrichment",
        "hazard risk",
        "flood risk",
        "earthquake risk",
        "fire risk",
        "property risk",
        "real estate risk",
        "ریسک",
        "ریسک سیل",
        "ریسک زلزله",
        "ریسک آتش",
        "خطر سیل",
        "خطر زلزله",
        "خطر آتش‌سوزی",
    ],
    description=(
        "Add deterministic flood/earthquake/fire risk attributes to vector features. "
        "MVP implementation with defaults, overrides and mapping rules."
    ),
    required_inputs=["features"],
    optional_inputs=[
        "risk_spec",
        "default_risks",
        "overrides",
        "rules",
        "id_field",
        "overwrite",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "risk_enrichment",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.risk_enrichment",
    },
)
def enrich_risk(
    features: Any,
    risk_spec: dict[str, Any] | None = None,
    default_risks: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    rules: list[dict[str, Any]] | None = None,
    id_field: str = "id",
    overwrite: bool = False,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Enrich features with risk fields.

    Example:
        enrich_risk(
            features,
            default_risks={
                "flood_risk": "low",
                "earthquake_risk": "medium",
                "fire_risk": "low",
            },
            overrides={
                "p1": {"flood_risk": "high"}
            },
            rules=[
                {
                  "target": "flood_risk",
                  "source": "flood_zone",
                  "mapping": {"A": "low", "B": "medium", "C": "high"}
                }
            ]
        )
    """
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    spec = _merge_risk_spec(
        risk_spec=risk_spec,
        default_risks=default_risks,
        overrides=overrides,
        rules=rules,
    )

    final_default_risks = _normalize_default_risks(spec["default_risks"])
    final_overrides = spec["overrides"]
    final_rules = spec["rules"]

    input_features = _extract_features(features)
    output_features: list[dict[str, Any]] = []

    default_applied_count = 0
    override_applied_count = 0
    rule_applied_count = 0

    for feature in input_features:
        out = dict(feature)
        props = dict(out.get("properties") or {})

        # 1) Defaults
        for field, level in final_default_risks.items():
            if overwrite or _get_property(props, field, _MISSING) is _MISSING:
                _set_property(props, field, level)
                default_applied_count += 1

        # 2) Rule-based mapping from existing properties
        for idx, rule in enumerate(final_rules):
            if not isinstance(rule, dict):
                raise ValueError(f"rules[{idx}] must be a dict.")

            mapped = _apply_mapping_rule(props, rule)
            if mapped is None:
                continue

            target, level = mapped

            if overwrite or _get_property(props, target, _MISSING) is _MISSING:
                _set_property(props, target, level)
                rule_applied_count += 1

        # 3) Per-feature overrides by id
        feature_id = _get_property(props, id_field, _MISSING)
        if feature_id is not _MISSING and feature_id is not None:
            override = final_overrides.get(str(feature_id))
            if isinstance(override, dict):
                for field, value in override.items():
                    _set_property(props, str(field), _normalize_risk_level(value))
                    override_applied_count += 1

        out["properties"] = props
        output_features.append(out)

    output_metadata = _build_vector_metadata(
        output_features,
        {
            "source": PLUGIN_ID,
            "operation": "enrich_risk",
            "default_applied_count": default_applied_count,
            "rule_applied_count": rule_applied_count,
            "override_applied_count": override_applied_count,
            "risk_fields": list(RISK_FIELDS),
            "id_field": id_field,
            "overwrite": bool(overwrite),
            "created_at": _utc_now_iso(),
            **(metadata or {}),
        },
    )

    return VectorOut(features=output_features, metadata=output_metadata)


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Risk Enrichment",
    description="Deterministic MVP risk enrichment for flood, earthquake and fire risk.",
    author="GeoChat Platform Team",
    permissions=[],
)
