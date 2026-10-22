"""
decision_analysis.py

GeoChat SDK Plugin
==================

Plugin ID:
    decision_analysis

Capabilities:
    - calculate_ahp_weights:
        Criteria weights from pairwise comparisons (Saaty's Analytic Hierarchy Process), with the
        consistency ratio that tells whether the judgements contradict each other.
    - rank_topsis:
        Rank alternatives (features) over several weighted criteria with TOPSIS: closest to the
        ideal solution and farthest from the worst.

They compose: feed the AHP weights into rank_topsis, or into weighted_overlay (raster_tools).

Config-aware behavior:
    Reads config/plugins/decision_analysis.yaml (size limits).
"""

from __future__ import annotations

import math
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    load_config,
    make_vector_out,
    to_float,
    to_int,
    utc_now_iso,
    validate_choice,
)
from plugins.csv_xy_loader import parse_number

PLUGIN_ID = "decision_analysis"

DEFAULTS = {"max_features": 200_000, "max_criteria": 10}
# Saaty's random consistency index for n = 1..10
RANDOM_INDEX = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49}
_META = {"category": "analysis", "access_scope": "attribute_analysis", "config_aware": True, "routable": True}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


@capability(
    name="calculate_ahp_weights",
    keywords=[
        "ahp", "analytic hierarchy process", "criteria weights", "pairwise comparison", "weights from comparisons",
        "consistency ratio", "saaty",
        "فرایند تحلیل سلسله مراتبی", "وزن معیارها", "مقایسه زوجی", "نرخ ناسازگاری", "ای اچ پی",
    ],
    description=(
        "Criteria weights by the Analytic Hierarchy Process from pairwise comparisons (Saaty 1-9 "
        "scale) with the consistency ratio."
    ),
    required_inputs=["criteria", "comparisons"],
    optional_inputs=["metadata"],
    output_kind="json",
    permissions=[],
    metadata={**_META, "data_type": "json", "operation": "ahp_weights", "returns": "dict",
              "artifact_kind": "json"},
)
def calculate_ahp_weights(
    criteria: list[str],
    comparisons: list[Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute AHP weights.

    Args:
        criteria: Criterion names (2-10).
        comparisons: Either a full n x n matrix (list of rows), or a list of judgements
            ``{"a": "cost", "b": "distance", "value": 3}`` meaning *a is 3 times as important as b*
            (value 1/9..9; the reciprocal is filled in; every pair must be given exactly once).
        metadata: Extra output metadata.

    Returns:
        dict with ``weights`` {criterion: weight} (sum 1), ``ranking``, ``lambda_max``,
        ``consistency_index``, ``consistency_ratio`` and ``consistent`` (CR < 0.10).
    """
    config = load_config(PLUGIN_ID)
    if not isinstance(criteria, (list, tuple)) or not all(isinstance(c, str) and c.strip() for c in criteria):
        raise ValueError("criteria must be a list of non-empty names.")
    names = [c.strip() for c in criteria]
    n = len(names)
    if len(set(names)) != n:
        raise ValueError("criteria names must be unique.")
    if not 2 <= n <= min(10, _limit(config, "max_criteria")):
        raise ValueError(f"AHP needs between 2 and {min(10, _limit(config, 'max_criteria'))} criteria.")

    matrix = [[1.0] * n for _ in range(n)]
    if comparisons and isinstance(comparisons[0], dict):
        seen: set[frozenset] = set()
        position = {name: i for i, name in enumerate(names)}
        for item in comparisons:
            a, b, value = item.get("a"), item.get("b"), item.get("value")
            if a not in position or b not in position or a == b:
                raise ValueError(f"Each comparison needs two different criteria from {names}: {item!r}.")
            pair = frozenset((a, b))
            if pair in seen:
                raise ValueError(f"The pair {a!r}/{b!r} is compared more than once.")
            seen.add(pair)
            v = to_float(value, "comparison value")
            if not (1 / 9 - 1e-9 <= v <= 9 + 1e-9) or v <= 0:
                raise ValueError("comparison values must be between 1/9 and 9.")
            matrix[position[a]][position[b]] = v
            matrix[position[b]][position[a]] = 1.0 / v
        if len(seen) != n * (n - 1) // 2:
            raise ValueError(f"All {n * (n - 1) // 2} pairs must be compared; got {len(seen)}.")
    else:
        if not isinstance(comparisons, (list, tuple)) or len(comparisons) != n or any(
                not isinstance(r, (list, tuple)) or len(r) != n for r in comparisons):
            raise ValueError(f"comparisons must be an {n} x {n} matrix or a list of pair judgements.")
        matrix = [[to_float(v, "matrix entry") for v in row] for row in comparisons]
        for i in range(n):
            if abs(matrix[i][i] - 1.0) > 1e-9:
                raise ValueError("The diagonal of the comparison matrix must be 1.")
            for j in range(n):
                if matrix[i][j] <= 0 or abs(matrix[i][j] * matrix[j][i] - 1.0) > 1e-6:
                    raise ValueError("The matrix must be positive and reciprocal (a[j][i] = 1 / a[i][j]).")

    # principal eigenvector by power iteration (positive reciprocal matrices converge)
    vector = [1.0 / n] * n
    for _ in range(1000):
        product = [sum(matrix[i][j] * vector[j] for j in range(n)) for i in range(n)]
        total = sum(product)
        updated = [p / total for p in product]
        if max(abs(u - v) for u, v in zip(updated, vector, strict=True)) < 1e-12:
            vector = updated
            break
        vector = updated
    product = [sum(matrix[i][j] * vector[j] for j in range(n)) for i in range(n)]
    lambda_max = sum(product[i] / vector[i] for i in range(n)) / n
    ci = (lambda_max - n) / (n - 1)
    ri = RANDOM_INDEX[n]
    cr = ci / ri if ri > 0 else 0.0
    weights = {name: vector[i] for i, name in enumerate(names)}
    return {
        "type": "ahp_weights",
        "weights": weights,
        "ranking": sorted(names, key=lambda c: -weights[c]),
        "lambda_max": lambda_max,
        "consistency_index": ci,
        "consistency_ratio": cr,
        "consistent": cr < 0.10,
        "matrix": matrix,
        "note": "CR < 0.10 is the usual acceptance threshold; revisit the judgements otherwise.",
        "metadata": {"source": PLUGIN_ID, "operation": "ahp_weights", "created_at": utc_now_iso(),
                     **(metadata or {})},
    }


@capability(
    name="rank_topsis",
    keywords=[
        "topsis", "multi criteria ranking", "rank alternatives", "best option", "weighted ranking",
        "site ranking", "mcdm",
        "تاپسیس", "رتبه بندی چند معیاره", "بهترین گزینه", "رتبه بندی گزینه ها", "تصمیم گیری چند معیاره",
    ],
    description=(
        "TOPSIS multi-criteria ranking of features: each criterion has a field, a weight and a "
        "direction (benefit or cost); adds a closeness score and a rank."
    ),
    required_inputs=["features", "criteria"],
    optional_inputs=["score_field", "rank_field", "missing", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={**_META, "data_type": "vector", "operation": "topsis", "returns": "VectorOut",
              "artifact_kind": "features"},
)
def rank_topsis(
    features: Any,
    criteria: list[dict[str, Any]],
    score_field: str = "topsis_score",
    rank_field: str = "topsis_rank",
    missing: str = "skip",
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Rank features with TOPSIS.

    Args:
        features: The alternatives.
        criteria: ``[{"field": "price", "weight": 3, "direction": "cost"}, ...]``; direction is
            ``benefit`` (higher is better, default) or ``cost``. Weights are relative.
        score_field: Property for the closeness score (0 worst .. 1 best).
        rank_field: Property for the rank (1 = best; equal scores share a rank).
        missing: ``skip`` leaves features with a missing criterion unscored (null score and rank);
            ``raise`` fails.
        metadata: Extra output metadata.

    Returns:
        VectorOut with score and rank; metadata lists the normalised weights and the ideal and
        anti-ideal values per criterion.
    """
    config = load_config(PLUGIN_ID)
    policy = validate_choice(missing, {"skip", "raise"}, "missing")
    if not isinstance(criteria, (list, tuple)) or not 1 <= len(criteria) <= _limit(config, "max_criteria"):
        raise ValueError("criteria must be a list of 1-10 objects with field, weight and direction.")
    specs = []
    for item in criteria:
        if not isinstance(item, dict) or not isinstance(item.get("field"), str) or not item["field"].strip():
            raise ValueError("each criterion needs a 'field'.")
        weight = to_float(item.get("weight", 1.0), "weight")
        if weight < 0 or not math.isfinite(weight):
            raise ValueError("weights must be non-negative numbers.")
        specs.append((item["field"], weight, validate_choice(item.get("direction", "benefit"),
                                                              {"benefit", "cost"}, "direction")))
    if len({f for f, _, _ in specs}) != len(specs):
        raise ValueError("a field can appear only once in criteria.")
    total_weight = sum(w for _, w, _ in specs)
    if total_weight <= 0:
        raise ValueError("at least one weight must be positive.")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_features"), "feature count")

    rows: list[tuple[int, list[float]]] = []
    for index, feature in enumerate(input_features):
        props = feature.get("properties") or {}
        values = [parse_number(props.get(field)) for field, _, _ in specs]
        if any(v is None for v in values):
            if policy == "raise":
                raise ValueError(f"Feature {index} has a missing or non-numeric criterion value.")
            continue
        rows.append((index, values))
    if len(rows) < 2:
        raise ValueError("TOPSIS needs at least 2 features with complete criteria.")

    weights = [w / total_weight for _, w, _ in specs]
    norms = [math.sqrt(sum(values[j] ** 2 for _, values in rows)) for j in range(len(specs))]
    weighted = [[(values[j] / norms[j] if norms[j] else 0.0) * weights[j] for j in range(len(specs))]
                for _, values in rows]
    best, worst = [], []
    for j, (_, _, direction) in enumerate(specs):
        column = [row[j] for row in weighted]
        high, low = max(column), min(column)
        best.append(high if direction == "benefit" else low)
        worst.append(low if direction == "benefit" else high)
    scores: dict[int, float] = {}
    for (index, _), row in zip(rows, weighted, strict=True):
        d_best = math.sqrt(sum((row[j] - best[j]) ** 2 for j in range(len(specs))))
        d_worst = math.sqrt(sum((row[j] - worst[j]) ** 2 for j in range(len(specs))))
        scores[index] = d_worst / (d_best + d_worst) if d_best + d_worst > 0 else 0.5

    ordered = sorted(scores.values(), reverse=True)
    rank_of = {score: ordered.index(score) + 1 for score in set(ordered)}  # competition ranking
    output = []
    for index, feature in enumerate(input_features):
        props = dict(feature.get("properties") or {})
        if index in scores:
            props[score_field], props[rank_field] = scores[index], rank_of[scores[index]]
        else:
            props[score_field], props[rank_field] = None, None
        output.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
    return make_vector_out(
        output, plugin_id=PLUGIN_ID, operation="topsis", source_info=source_info,
        extra={"criteria": [{"field": f, "weight": w, "direction": d}
                            for (f, _, d), w in zip(specs, weights, strict=True)],
               "ideal": dict(zip((f for f, _, _ in specs), best, strict=True)),
               "anti_ideal": dict(zip((f for f, _, _ in specs), worst, strict=True)),
               "ranked_features": len(rows), "skipped_features": len(input_features) - len(rows)},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Decision Analysis",
    description="AHP criteria weights and TOPSIS multi-criteria ranking.",
    author="GeoChat Platform Team",
    permissions=[],
)
