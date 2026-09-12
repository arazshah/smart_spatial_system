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

    # Both languages are matched, and the English side is deliberately as
    # wide as the Persian one: "نمایش نقاط روی نقشه" was handled here while
    # its literal translation "display the sites on the map" was not, so an
    # English display query fell through to the legacy routing-aware
    # planner and failed asking for raster capabilities it never needed.
    display_tokens = [
        # fa
        "نمایش",
        "نشان بده",
        "نشان بدهد",
        "روی نقشه",
        "نقشه",
        # en
        "display",
        "show",
        "render",
        "draw",
        "map",
        "plot",
        "visualize",
        "visualise",
    ]

    # Kept to nouns that are unambiguously vector: a token that also reads
    # naturally in a raster question (area, zone, region) would route
    # raster work into the vector display handler.
    vector_tokens = [
        # fa
        "نقطه",
        "نقاط",
        "عارضه",
        "عوارض",
        "وکتور",
        "برداری",
        "لایه",
        # en
        "geojson",
        "feature",
        "features",
        "point",
        "points",
        "vector",
        "layer",
        "site",
        "sites",
        "location",
        "locations",
        "polygon",
        "polygons",
        "geometry",
        "geometries",
        "parcel",
        "parcels",
        "shapefile",
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
        # fa
        "تعداد",
        "چند",
        "گزارش",
        "گزارش بده",
        "بررسی",
        "خلاصه",
        "آمار",
        "شمارش",
        "بشمار",
        # en
        "count",
        "how many",
        "number of",
        "summary",
        "summarize",
        "summarise",
        "inspect",
        "report",
        "statistics",
        "stats",
    ]

    vector_tokens = [
        # fa
        "نقطه",
        "نقاط",
        "عارضه",
        "عوارض",
        "وکتور",
        "برداری",
        "لایه",
        "فایل",
        # en
        "geojson",
        "feature",
        "features",
        "point",
        "points",
        "vector",
        "layer",
        "site",
        "sites",
        "location",
        "locations",
        "polygon",
        "polygons",
        "geometry",
        "geometries",
        "parcel",
        "parcels",
        "shapefile",
        "file",
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
