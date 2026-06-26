from __future__ import annotations

from typing import Any


def is_real_estate_analysis_query(
    query: str,
    llm_intent: Any | None = None,
) -> bool:
    text = str(query or "").strip().lower()

    if not text:
        return False

    intent_name = None

    if isinstance(llm_intent, dict):
        intent_name = str(llm_intent.get("intent_name") or "").lower()
    else:
        intent_name = str(getattr(llm_intent, "intent_name", "") or "").lower()

    real_estate_tokens = [
        "ملک",
        "املاک",
        "آپارتمان",
        "ویلا",
        "زمین",
        "ساخت و ساز",
        "ساخت‌وساز",
        "real estate",
        "property",
        "properties",
    ]

    analysis_tokens = [
        "مترو",
        "مرکز خرید",
        "خیابان اصلی",
        "ریسک",
        "سیل",
        "زلزله",
        "آتش",
        "امتیاز",
        "رتبه",
        "رتبه‌بندی",
        "گزارش",
        "نزدیک",
        "۵۰۰",
        "500",
    ]

    if intent_name in {
        "real_estate_ranking",
        "property_ranking",
        "vector_filter",
        "investment_analysis",
    }:
        return any(token in text for token in real_estate_tokens)

    return (
        any(token in text for token in real_estate_tokens)
        and any(token in text for token in analysis_tokens)
    )


def has_any_real_estate_payload(
    resolved_inputs: dict[str, Any],
) -> bool:
    if not isinstance(resolved_inputs, dict) or not resolved_inputs:
        return False

    useful_keys = {
        "vector",
        "vectors",
        "properties",
        "property_layer",
        "real_estate",
        "pois",
        "poi",
        "metro",
        "shopping_centers",
        "roads",
        "main_roads",
        "risk_layers",
        "flood_risk",
        "earthquake_risk",
        "fire_risk",
        "zoning",
        "landuse",
        "land_use",
    }

    if any(key in resolved_inputs and resolved_inputs.get(key) not in (None, {}, []) for key in useful_keys):
        return True

    vector = resolved_inputs.get("vector")

    if isinstance(vector, dict):
        features = vector.get("features")
        if isinstance(features, list) and features:
            return True

    vectors = resolved_inputs.get("vectors")

    if isinstance(vectors, list) and vectors:
        return True

    return False


def looks_like_real_estate_ranking_query(query: str) -> bool:
    q = (query or "").lower()

    property_terms = [
        "ملک",
        "املاک",
        "زمین",
        "آپارتمان",
        "ویلا",
        "property",
        "real estate",
    ]
    ranking_terms = [
        "رتبه",
        "رتبه‌بندی",
        "رتبه بندی",
        "امتیاز",
        "score",
        "rank",
        "ranking",
        "گزارش",
        "report",
    ]
    constraint_terms = [
        "مترو",
        "مرکز خرید",
        "خیابان اصلی",
        "ریسک",
        "سیل",
        "زلزله",
        "آتش",
        "۵۰۰",
        "500",
        "متر",
    ]

    return (
        any(term in q for term in property_terms)
        and any(term in q for term in ranking_terms)
        and any(term in q for term in constraint_terms)
    )
