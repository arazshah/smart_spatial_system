"""
Vector query classifier helpers.

These helpers detect simple vector display and vector summary/inspection
queries before they are routed to capability-backed direct handlers.
"""

from __future__ import annotations

from typing import Any


def is_vector_display_query(
    query: str,
    intent: dict[str, Any] | None = None,
) -> bool:
    """
    Detect simple vector-display queries.

    This is intentionally deterministic and does not depend on LLM.
    """
    q = str(query or "").strip().lower()

    display_tokens = [
        "نمایش",
        "نشان بده",
        "نشان بدهد",
        "روی نقشه",
        "نقشه",
        "display",
        "show",
        "render",
        "draw",
    ]

    vector_tokens = [
        "نقطه",
        "نقاط",
        "عارضه",
        "عوارض",
        "وکتور",
        "برداری",
        "geojson",
        "feature",
        "features",
        "point",
        "points",
        "vector",
        "layer",
        "لایه",
    ]

    has_display = any(token in q for token in display_tokens)
    has_vector = any(token in q for token in vector_tokens)

    if has_display and has_vector:
        return True

    if isinstance(intent, dict):
        name = str(intent.get("intent_name") or "").lower()
        required = intent.get("required_inputs") or {}
        output = intent.get("output_expectation") or {}
        preferred = intent.get("preferred_capabilities") or []

        if (
            name in {"vector_display", "vector_filter", "unknown"}
            and bool(required.get("vector", False))
            and not bool(required.get("raster", False))
            and bool(output.get("map_layer", False))
        ):
            return True

        if (
            bool(required.get("vector", False))
            and not bool(required.get("raster", False))
            and any(
                str(cap) in {"filter_features", "extract_centroids", "export_vector_geojson"}
                for cap in preferred
            )
            and bool(output.get("map_layer", False))
        ):
            return True

    return False


def is_vector_summary_query(
    query: str,
    intent: dict[str, Any] | None = None,
) -> bool:
    """
    Detect vector inspection / feature-count / summary queries.

    Examples:
    - لایه وکتور را بررسی کن و تعداد عارضه‌ها را گزارش بده
    - چند نقطه داخل فایل است؟
    - تعداد عارضه‌های فایل را بگو
    - summarize vector layer
    """
    q = str(query or "").strip().lower()

    summary_tokens = [
        "تعداد",
        "چند",
        "گزارش",
        "گزارش بده",
        "بررسی",
        "خلاصه",
        "آمار",
        "شمارش",
        "بشمار",
        "count",
        "summary",
        "summarize",
        "inspect",
        "report",
        "statistics",
        "stats",
    ]

    vector_tokens = [
        "نقطه",
        "نقاط",
        "عارضه",
        "عوارض",
        "وکتور",
        "برداری",
        "geojson",
        "feature",
        "features",
        "point",
        "points",
        "vector",
        "layer",
        "لایه",
        "فایل",
    ]

    has_summary = any(token in q for token in summary_tokens)
    has_vector = any(token in q for token in vector_tokens)

    if has_summary and has_vector:
        return True

    if isinstance(intent, dict):
        name = str(intent.get("intent_name") or "").lower()
        required = intent.get("required_inputs") or {}
        output = intent.get("output_expectation") or {}

        if (
            name in {"vector_summary", "vector_inspect", "vector_statistics"}
            and bool(required.get("vector", False))
            and not bool(required.get("raster", False))
        ):
            return True

        if (
            bool(required.get("vector", False))
            and not bool(required.get("raster", False))
            and bool(output.get("text", False))
            and not bool(output.get("map_layer", False))
        ):
            return True

    return False
