"""
feature_scoring.py

Feature Scoring and Ranking
===========================

Plugin ID:
    feature_scoring

Purpose:
    Score and rank vector features using a declarative scoring specification.

Capabilities:
    - score_features
    - rank_features

LLM Role:
    LLM may generate the scoring_spec, but this plugin executes it
    deterministically and auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut


PLUGIN_ID = "feature_scoring"


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

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_float(value: Any, default: float | None = None) -> float | None:
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _get_path(obj: Any, path: str, default: Any = None) -> Any:
    """
    Read dotted path from dict.

    For convenience, if path is not found in feature root,
    caller may pass properties dict directly.
    """
    if not path:
        return default

    current = obj
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _get_property(properties: dict[str, Any], field: str, default: Any = None) -> Any:
    """
    Get property by field name. Supports dotted property path.
    """
    if field in properties:
        return properties[field]
    return _get_path(properties, field, default)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if _is_number(value):
        return float(value) != 0.0
    if isinstance(value, str):
        return value.strip().lower() in {
            "true",
            "yes",
            "y",
            "1",
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


def _compare(left: Any, operator: str, right: Any) -> bool:
    left_num = _to_float(left)
    right_num = _to_float(right)

    if operator in {"==", "eq"}:
        return left == right
    if operator in {"!=", "ne"}:
        return left != right

    if left_num is None or right_num is None:
        return False

    if operator in {"<", "lt"}:
        return left_num < right_num
    if operator in {"<=", "lte"}:
        return left_num <= right_num
    if operator in {">", "gt"}:
        return left_num > right_num
    if operator in {">=", "gte"}:
        return left_num >= right_num

    raise ValueError(f"Unsupported threshold operator: {operator}")


def _risk_level_score(value: Any, levels: dict[str, float] | None = None) -> float:
    """
    Higher score means lower/better risk.
    """
    default_levels = {
        "very_low": 1.0,
        "low": 1.0,
        "medium": 0.5,
        "moderate": 0.5,
        "high": 0.15,
        "very_high": 0.0,
        "critical": 0.0,

        # Persian
        "خیلی کم": 1.0,
        "کم": 1.0,
        "متوسط": 0.5,
        "زیاد": 0.15,
        "خیلی زیاد": 0.0,
        "بحرانی": 0.0,
    }

    merged = dict(default_levels)
    if isinstance(levels, dict):
        for key, val in levels.items():
            num = _to_float(val)
            if num is not None:
                merged[str(key).strip().lower()] = _clamp01(num)

    if value is None:
        return 0.0

    if _is_number(value):
        # Numeric risk convention:
        # 0 = best/low risk, 1 = worst/high risk
        return _clamp01(1.0 - float(value))

    key = str(value).strip().lower()
    return _clamp01(float(merged.get(key, 0.0)))


def _score_factor(properties: dict[str, Any], factor: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Return normalized factor score in [0, 1] and detail.
    """
    if not isinstance(factor, dict):
        raise ValueError("Each scoring factor must be a dict.")

    factor_type = str(factor.get("type") or "boolean")
    name = str(factor.get("name") or factor.get("field") or factor_type)
    field = str(factor.get("field") or "")
    value = _get_property(properties, field) if field else None

    score = 0.0

    if factor_type in {"boolean", "boolean_bonus"}:
        score = 1.0 if _truthy(value) else 0.0

    elif factor_type == "inverse_distance":
        distance = _to_float(value)
        max_distance = _to_float(factor.get("max_distance"), None)
        if max_distance is None:
            max_distance = _to_float(factor.get("max_distance_m"), None)
        if distance is None or max_distance is None or max_distance <= 0:
            score = 0.0
        else:
            score = _clamp01(1.0 - (distance / max_distance))

    elif factor_type in {"risk_level", "inverse_level"}:
        levels = factor.get("levels")
        score = _risk_level_score(value, levels if isinstance(levels, dict) else None)

    elif factor_type in {"threshold", "condition"}:
        operator = str(factor.get("operator") or "<=")
        expected = factor.get("value")
        score = 1.0 if _compare(value, operator, expected) else 0.0

    elif factor_type in {"direct", "numeric"}:
        num = _to_float(value, 0.0)
        score = _clamp01(float(num or 0.0))

    elif factor_type in {"inverse_numeric"}:
        num = _to_float(value, 1.0)
        score = _clamp01(1.0 - float(num or 0.0))

    else:
        raise ValueError(f"Unsupported scoring factor type: {factor_type}")

    score = _clamp01(score)

    detail = {
        "name": name,
        "type": factor_type,
        "field": field,
        "value": value,
        "score": score,
        "weight": _to_float(factor.get("weight"), 1.0) or 0.0,
    }

    return score, detail


def _normalize_scoring_spec(
    scoring_spec: dict[str, Any] | None,
    factors: list[dict[str, Any]] | None,
    output_field: str | None,
    scale: float | None,
    normalize_weights: bool | None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {}

    if scoring_spec:
        if not isinstance(scoring_spec, dict):
            raise ValueError("scoring_spec must be a dict.")
        # Accept either {"scoring": {...}} or direct spec.
        inner = scoring_spec.get("scoring")
        if isinstance(inner, dict):
            spec.update(inner)
        spec.update({k: v for k, v in scoring_spec.items() if k != "scoring"})

    if factors is not None:
        spec["factors"] = factors
    if output_field is not None:
        spec["output_field"] = output_field
    if scale is not None:
        spec["scale"] = scale
    if normalize_weights is not None:
        spec["normalize_weights"] = normalize_weights

    if "factors" not in spec:
        raise ValueError("scoring_spec must contain factors.")

    if not isinstance(spec["factors"], list):
        raise ValueError("scoring factors must be a list.")

    spec.setdefault("output_field", "score")
    spec.setdefault("details_field", "__score_details__")
    spec.setdefault("scale", 100.0)
    spec.setdefault("normalize_weights", True)

    return spec


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
# Capability: score_features
# ------------------------------------------------------------------ #

@capability(
    name="score_features",
    keywords=[
        "score",
        "scoring",
        "rank score",
        "investment score",
        "weighted score",
        "multi criteria",
        "امتیاز",
        "امتیازدهی",
        "امتیاز سرمایه گذاری",
        "چند معیاره",
        "رتبه بندی املاک",
    ],
    description="Score vector features using a declarative weighted scoring specification.",
    required_inputs=["features"],
    optional_inputs=[
        "scoring_spec",
        "factors",
        "output_field",
        "scale",
        "normalize_weights",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "feature_scoring",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.feature_scoring",
    },
)
def score_features(
    features: Any,
    scoring_spec: dict[str, Any] | None = None,
    factors: list[dict[str, Any]] | None = None,
    output_field: str | None = None,
    scale: float | None = None,
    normalize_weights: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Score features using weighted factor specification.

    Each factor produces a normalized score [0, 1].
    Final score is weighted sum, optionally normalized by total weight,
    then multiplied by scale.

    Supported factor types:
        - boolean
        - inverse_distance
        - risk_level
        - threshold
        - direct
        - inverse_numeric
    """
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    spec = _normalize_scoring_spec(
        scoring_spec=scoring_spec,
        factors=factors,
        output_field=output_field,
        scale=scale,
        normalize_weights=normalize_weights,
    )

    final_output_field = str(spec["output_field"])
    details_field = str(spec.get("details_field") or "__score_details__")
    final_scale = _to_float(spec.get("scale"), 100.0) or 100.0
    final_normalize_weights = bool(spec.get("normalize_weights", True))
    final_factors = spec["factors"]

    output_features: list[dict[str, Any]] = []

    for feature in _extract_features(features):
        out = dict(feature)
        props = dict(out.get("properties") or {})

        weighted_sum = 0.0
        total_weight = 0.0
        details: list[dict[str, Any]] = []

        for factor in final_factors:
            factor_score, detail = _score_factor(props, factor)
            weight = _to_float(factor.get("weight"), 1.0) or 0.0

            weighted_sum += factor_score * weight
            if weight > 0:
                total_weight += weight

            detail["weighted_score"] = factor_score * weight
            details.append(detail)

        if final_normalize_weights:
            normalized = weighted_sum / total_weight if total_weight > 0 else 0.0
        else:
            normalized = weighted_sum

        final_score = round(_clamp01(normalized) * final_scale, 6)

        props[final_output_field] = final_score
        props[details_field] = details

        out["properties"] = props
        output_features.append(out)

    output_metadata = _build_vector_metadata(
        output_features,
        {
            "source": "feature_scoring",
            "operation": "score_features",
            "output_field": final_output_field,
            "details_field": details_field,
            "factor_count": len(final_factors),
            "scale": final_scale,
            "normalize_weights": final_normalize_weights,
            "created_at": _utc_now_iso(),
            **(metadata or {}),
        },
    )

    return VectorOut(features=output_features, metadata=output_metadata)


# ------------------------------------------------------------------ #
# Capability: rank_features
# ------------------------------------------------------------------ #

@capability(
    name="rank_features",
    keywords=[
        "rank",
        "ranking",
        "sort by score",
        "top features",
        "best properties",
        "رتبه",
        "رتبه بندی",
        "بهترین گزینه",
        "بهترین املاک",
        "مرتب سازی امتیاز",
    ],
    description="Rank vector features by a numeric score field.",
    required_inputs=["features"],
    optional_inputs=[
        "score_field",
        "rank_field",
        "descending",
        "limit",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "feature_ranking",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.feature_scoring",
    },
)
def rank_features(
    features: Any,
    score_field: str = "score",
    rank_field: str = "rank",
    descending: bool = True,
    limit: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Rank features by score field.
    """
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    if limit is not None:
        if isinstance(limit, bool):
            raise ValueError("limit must be int or None.")
        limit = int(limit)
        if limit < 0:
            raise ValueError("limit must be non-negative.")

    input_features = _extract_features(features)

    def sort_key(feature: dict[str, Any]) -> tuple[int, float]:
        props = feature.get("properties") or {}
        value = _to_float(_get_property(props, score_field), None)
        # Missing values go last.
        if value is None:
            return (1, 0.0)
        return (0, value)

    ranked = sorted(
        input_features,
        key=sort_key,
        reverse=bool(descending),
    )

    # Fix missing values last even when descending=True.
    ranked = sorted(
        ranked,
        key=lambda f: _to_float((f.get("properties") or {}).get(score_field), None) is None,
    )
    if descending:
        with_score = [f for f in ranked if _to_float((f.get("properties") or {}).get(score_field), None) is not None]
        no_score = [f for f in ranked if _to_float((f.get("properties") or {}).get(score_field), None) is None]
        with_score = sorted(
            with_score,
            key=lambda f: _to_float((f.get("properties") or {}).get(score_field), 0.0) or 0.0,
            reverse=True,
        )
        ranked = with_score + no_score
    else:
        with_score = [f for f in ranked if _to_float((f.get("properties") or {}).get(score_field), None) is not None]
        no_score = [f for f in ranked if _to_float((f.get("properties") or {}).get(score_field), None) is None]
        with_score = sorted(
            with_score,
            key=lambda f: _to_float((f.get("properties") or {}).get(score_field), 0.0) or 0.0,
        )
        ranked = with_score + no_score

    if limit is not None:
        ranked = ranked[:limit]

    output_features: list[dict[str, Any]] = []
    for idx, feature in enumerate(ranked, start=1):
        out = dict(feature)
        props = dict(out.get("properties") or {})
        props[rank_field] = idx
        out["properties"] = props
        output_features.append(out)

    output_metadata = _build_vector_metadata(
        output_features,
        {
            "source": "feature_scoring",
            "operation": "rank_features",
            "score_field": score_field,
            "rank_field": rank_field,
            "descending": bool(descending),
            "limit": limit,
            "created_at": _utc_now_iso(),
            **(metadata or {}),
        },
    )

    return VectorOut(features=output_features, metadata=output_metadata)


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Feature Scoring",
    description="Weighted feature scoring and ranking for multi-criteria spatial analysis.",
    author="GeoChat Platform Team",
    permissions=[],
)
