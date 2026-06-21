"""
Semantic planning context builder.

Purpose
-------
This module builds a safe, schema-aware planning context for LLM/planner usage.

It bridges:
    natural language user query
        +
    discovered PostGIS schema context
        ↓
    safe semantic layer candidates
        +
    recommended logical operations from OP_CATALOG

Important
---------
This module does NOT execute SQL and does NOT call plugins.
It only prepares structured context so the LLM/planner can avoid guessing:
    - table names
    - column names
    - geometry columns
    - raw SQL predicates

The LLM should prefer the provided semantic layer candidates instead of
inventing PostGIS SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from orchestrator.planning.op_catalog import is_supported
from orchestrator.planning.postgis_semantic_resolver import (
    PostGISSchemaContext,
    SemanticLayerCandidate as PostGISSemanticLayerCandidate,
    infer_semantic_concepts,
    resolve_query_semantic_layers,
)


_PERSIAN_DIGIT_MAP = str.maketrans(
    {
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
    }
)


_NEAREST_TERMS = (
    "نزدیک‌ترین",
    "نزدیک ترین",
    "نزدیکترین",
    "closest",
    "nearest",
    "nearby",
    "nearest neighbor",
    "knn",
)

_DISTANCE_TERMS = (
    "فاصله",
    "distance",
    "متر",
    "meter",
    "metre",
    "meters",
    "metres",
)

_TOP_TERMS = (
    "مورد اول",
    "اول",
    "برتر",
    "top",
    "first",
    "limit",
)

_MAP_TERMS = (
    "نقشه",
    "روی نقشه",
    "نمایش بده",
    "نمایش",
    "map",
    "display",
    "show on map",
)

_TABLE_TERMS = (
    "جدول",
    "جدولی",
    "table",
    "summary",
    "summarize",
    "خلاصه",
    "گزارش",
)

_EXPORT_TERMS = (
    "geojson",
    "ژئوجیسون",
    "جئوجیسون",
    "خروجی فایل",
    "دانلود",
    "export",
    "download",
)


@dataclass(frozen=True)
class SemanticPlanningLayer:
    """
    LLM-safe reference to a semantic PostGIS layer candidate.

    The planner should use `op` and `params` instead of generating raw SQL.
    """

    concept: str
    op: str
    params: dict[str, Any]
    schema: str
    table: str
    geom_col: str
    geometry_type: str
    srid: int | None
    score: float
    used_terms_count: int = 0
    skipped_terms_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "op": self.op,
            "params": dict(self.params),
            "schema": self.schema,
            "table": self.table,
            "geom_col": self.geom_col,
            "geometry_type": self.geometry_type,
            "srid": self.srid,
            "score": self.score,
            "used_terms_count": self.used_terms_count,
            "skipped_terms_count": self.skipped_terms_count,
        }


@dataclass(frozen=True)
class SemanticOperationHint:
    """
    A suggested high-level operation for the planner/LLM.
    """

    op: str
    reason: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "reason": self.reason,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class SemanticPlanningContext:
    """
    Full semantic context passed to a planner/LLM.

    It is intentionally JSON-friendly.
    """

    user_query: str
    detected_concepts: tuple[str, ...] = field(default_factory=tuple)
    semantic_layers: dict[str, tuple[SemanticPlanningLayer, ...]] = field(default_factory=dict)
    recommended_ops: tuple[str, ...] = field(default_factory=tuple)
    operation_hints: tuple[SemanticOperationHint, ...] = field(default_factory=tuple)
    requested_limit: int | None = None
    intents: dict[str, bool] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_query": self.user_query,
            "detected_concepts": list(self.detected_concepts),
            "semantic_layers": {
                concept: [layer.to_dict() for layer in layers]
                for concept, layers in self.semantic_layers.items()
            },
            "recommended_ops": list(self.recommended_ops),
            "operation_hints": [hint.to_dict() for hint in self.operation_hints],
            "requested_limit": self.requested_limit,
            "intents": dict(self.intents),
            "warnings": list(self.warnings),
            "guardrails": {
                "llm_must_not_generate_raw_sql": True,
                "llm_must_not_invent_table_names": True,
                "llm_must_not_invent_column_names": True,
                "use_semantic_layer_candidates_first": True,
            },
        }


def _normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", " ")
    value = value.translate(_PERSIAN_DIGIT_MAP)
    return " ".join(value.split())


def _contains_any(query: str, terms: Iterable[str]) -> bool:
    q = _normalize_text(query)
    return any(_normalize_text(term) in q for term in terms if _normalize_text(term))


def extract_requested_limit(query: str) -> int | None:
    """
    Extract simple requested limit such as:
        20 مورد اول
        ۲۰ مورد اول
        top 10
        first 5

    This is intentionally conservative.
    """
    q = _normalize_text(query)

    patterns = (
        r"\btop\s+(\d{1,5})\b",
        r"\bfirst\s+(\d{1,5})\b",
        r"\blimit\s+(\d{1,5})\b",
        r"\b(\d{1,5})\s+مورد\s+اول\b",
        r"\b(\d{1,5})\s+تا(?:ی)?\s+اول\b",
        r"\b(\d{1,5})\s+اول\b",
    )

    for pattern in patterns:
        match = re.search(pattern, q, flags=re.IGNORECASE)
        if not match:
            continue

        try:
            value = int(match.group(1))
        except Exception:
            continue

        if value > 0:
            return value

    return None


def infer_query_intents(query: str) -> dict[str, bool]:
    requested_limit = extract_requested_limit(query)

    return {
        "nearest": _contains_any(query, _NEAREST_TERMS),
        "distance": _contains_any(query, _DISTANCE_TERMS),
        "top_n": bool(requested_limit) or _contains_any(query, _TOP_TERMS),
        "map": _contains_any(query, _MAP_TERMS),
        "table": _contains_any(query, _TABLE_TERMS),
        "export": _contains_any(query, _EXPORT_TERMS),
    }


def _dedupe_preserve_order(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)

    return tuple(result)


def _candidate_to_layer(
    candidate: PostGISSemanticLayerCandidate,
    *,
    op: str = "query_database",
    default_layer_limit: int = 5000,
    geom_alias: str = "geom",
) -> SemanticPlanningLayer:
    output_srid = candidate.srid if candidate.srid and candidate.srid > 0 else None

    params = candidate.as_query_database_params(
        limit=default_layer_limit,
        output_srid=output_srid,
        geom_alias=geom_alias,
    )

    return SemanticPlanningLayer(
        concept=candidate.concept,
        op=op,
        params=params,
        schema=candidate.schema,
        table=candidate.table,
        geom_col=candidate.geom_col,
        geometry_type=candidate.geometry_type,
        srid=candidate.srid,
        score=candidate.score,
        used_terms_count=len(candidate.used_terms),
        skipped_terms_count=len(candidate.skipped_terms),
    )


def _recommended_ops_for_context(
    *,
    concepts: Iterable[str],
    intents: Mapping[str, bool],
    has_layers: bool,
) -> tuple[str, ...]:
    concepts_list = list(concepts)

    ops: list[str] = []

    if has_layers:
        ops.append("query_database")

    if intents.get("nearest") or (
        intents.get("distance") and len(concepts_list) >= 2
    ):
        ops.append("spatial_nearest")

    if intents.get("top_n"):
        ops.append("top_n")

    if intents.get("map"):
        ops.append("display_vector")

    if intents.get("table"):
        ops.append("summarize_vector")

    if intents.get("export"):
        ops.append("export_geojson")

    # Keep only supported ops and preserve order.
    return _dedupe_preserve_order(op for op in ops if is_supported(op))


def _operation_hints_for_context(
    *,
    concepts: Iterable[str],
    intents: Mapping[str, bool],
    requested_limit: int | None,
) -> tuple[SemanticOperationHint, ...]:
    concepts_list = list(concepts)
    hints: list[SemanticOperationHint] = []

    if intents.get("nearest") and len(concepts_list) >= 2:
        hints.append(
            SemanticOperationHint(
                op="spatial_nearest",
                reason=(
                    "User asks for nearest/closest relationship between at least "
                    "two semantic concepts. Load source and target semantic layers "
                    "first, then run spatial_nearest with k=1."
                ),
                params={
                    "k": 1,
                    "include_target_geometry": True,
                    "source_crs": "EPSG:3857",
                },
            )
        )

    if requested_limit:
        hints.append(
            SemanticOperationHint(
                op="top_n",
                reason=(
                    "User requested a limited/top result set. If previous operation "
                    "is spatial_nearest, rank by _nearest_distance ascending."
                ),
                params={
                    "score_field": "_nearest_distance",
                    "descending": False,
                    "limit": requested_limit,
                },
            )
        )

    if intents.get("map"):
        hints.append(
            SemanticOperationHint(
                op="display_vector",
                reason="User asked to display the result on map.",
                params={},
            )
        )

    if intents.get("table"):
        hints.append(
            SemanticOperationHint(
                op="summarize_vector",
                reason="User asked for table/summary/report-like output.",
                params={},
            )
        )

    return tuple(h for h in hints if is_supported(h.op))


def build_semantic_planning_context(
    query: str,
    schema_context: PostGISSchemaContext,
    *,
    explicit_concepts: Iterable[str] | None = None,
    max_candidates_per_concept: int = 5,
    default_layer_limit: int = 5000,
    rules: Mapping[str, Mapping[str, Any]] | None = None,
) -> SemanticPlanningContext:
    """
    Build schema-aware semantic context for planner/LLM.

    The returned context is safe to serialize to JSON and pass to an LLM prompt.
    """
    concepts = list(explicit_concepts or [])
    if not concepts:
        concepts = infer_semantic_concepts(query, rules=rules)

    concepts = list(_dedupe_preserve_order(concepts))

    resolved = resolve_query_semantic_layers(
        query,
        schema_context,
        explicit_concepts=concepts,
        rules=rules,
        max_candidates_per_concept=max_candidates_per_concept,
    )

    semantic_layers: dict[str, tuple[SemanticPlanningLayer, ...]] = {}
    warnings: list[str] = []

    if not concepts:
        warnings.append("no_semantic_concepts_detected")

    for concept in concepts:
        candidates = resolved.get(concept) or []

        if not candidates:
            warnings.append(f"no_layer_candidates_for_concept:{concept}")
            semantic_layers[concept] = tuple()
            continue

        semantic_layers[concept] = tuple(
            _candidate_to_layer(
                candidate,
                default_layer_limit=default_layer_limit,
            )
            for candidate in candidates
        )

    has_layers = any(bool(layers) for layers in semantic_layers.values())
    requested_limit = extract_requested_limit(query)
    intents = infer_query_intents(query)

    recommended_ops = _recommended_ops_for_context(
        concepts=concepts,
        intents=intents,
        has_layers=has_layers,
    )

    operation_hints = _operation_hints_for_context(
        concepts=concepts,
        intents=intents,
        requested_limit=requested_limit,
    )

    return SemanticPlanningContext(
        user_query=query,
        detected_concepts=tuple(concepts),
        semantic_layers=semantic_layers,
        recommended_ops=recommended_ops,
        operation_hints=operation_hints,
        requested_limit=requested_limit,
        intents=dict(intents),
        warnings=tuple(warnings),
    )


def semantic_planning_context_to_dict(context: SemanticPlanningContext) -> dict[str, Any]:
    return context.to_dict()
