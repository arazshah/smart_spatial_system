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

    # Both languages are matched. The English tokens are not decoration:
    # without them an English query like "rank these properties by distance
    # to metro, malls and main roads" matched real_estate_tokens (via
    # "properties") but no analysis token, so it fell through to the legacy
    # routing-aware planner and failed with an unrelated capability error.
    analysis_tokens = [
        # fa
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
        # en
        "metro",
        "subway",
        "underground station",
        "shopping",
        "mall",
        "main road",
        "risk",
        "flood",
        "earthquake",
        "fire",
        "score",
        "scoring",
        "rank",
        "ranking",
        "report",
        "near",
        "distance",
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

    # All three lists match both languages. Two English gaps used to make
    # this return False for otherwise obvious English queries: "property"
    # is not a substring of "properties", and constraint_terms had no
    # English entries at all - so "rank these properties by distance to
    # metro, malls and main roads" fell through to the legacy planner.
    property_terms = [
        "ملک",
        "املاک",
        "زمین",
        "آپارتمان",
        "ویلا",
        "property",
        "properties",
        "real estate",
        "apartment",
        "housing",
    ]
    ranking_terms = [
        "رتبه",
        "رتبه‌بندی",
        "رتبه بندی",
        "امتیاز",
        "score",
        "scoring",
        "rank",
        "ranking",
        "گزارش",
        "report",
    ]
    constraint_terms = [
        # fa
        "مترو",
        "مرکز خرید",
        "خیابان اصلی",
        "ریسک",
        "سیل",
        "زلزله",
        "آتش",
        "۵۰۰",
        "متر",
        # en
        "metro",
        "subway",
        "underground station",
        "shopping",
        "mall",
        "main road",
        "risk",
        "flood",
        "earthquake",
        "fire",
        "distance",
        "near",
        "500",
    ]

    return (
        any(term in q for term in property_terms)
        and any(term in q for term in ranking_terms)
        and any(term in q for term in constraint_terms)
    )
