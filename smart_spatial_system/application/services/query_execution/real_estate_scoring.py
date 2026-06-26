from __future__ import annotations

from typing import Any

from smart_spatial_system.application.services.real_estate_spatial_helpers import (
    normalize_risk_level,
    to_float_or_none,
)


def score_real_estate_property(props: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    metro = to_float_or_none(props.get("distance_to_metro_m"))
    mall = to_float_or_none(props.get("distance_to_mall_m"))
    road = to_float_or_none(props.get("distance_to_main_road_m"))
    price = to_float_or_none(props.get("price"))

    poi_distances = [d for d in [metro, mall] if d is not None]
    best_poi = min(poi_distances) if poi_distances else None

    flood = normalize_risk_level(props.get("flood_risk"))
    earthquake = normalize_risk_level(props.get("earthquake_risk"))
    fire = normalize_risk_level(props.get("fire_risk"))

    risks = [flood, earthquake, fire]
    risk_penalty = 0.0
    for risk in risks:
        if risk == "medium":
            risk_penalty += 12.0
        elif risk == "high":
            risk_penalty += 35.0

    score = 100.0

    # نزدیکی به مترو/مرکز خرید؛ هرچه کمتر بهتر.
    if best_poi is None:
        score -= 20.0
    else:
        score -= min(best_poi, 1500.0) / 500.0 * 10.0

    # نزدیکی به خیابان اصلی؛ هرچه کمتر بهتر.
    if road is None:
        score -= 10.0
    else:
        score -= min(road, 500.0) / 150.0 * 6.0

    score -= risk_penalty

    # اگر خارج از محدوده مجاز باشد، جریمه سنگین می‌گیرد.
    in_allowed_zone = props.get("in_allowed_zone")
    if in_allowed_zone is None:
        in_allowed_zone = props.get("build_zone_allowed")
    if in_allowed_zone is None:
        in_allowed_zone = props.get("construction_allowed", True)

    if in_allowed_zone is False:
        score -= 30.0

    # قیمت را خیلی کم وارد می‌کنیم، چون در این query معیار اصلی مکانی/ریسک است.
    if price is not None:
        score -= min(price / 10_000_000_000.0, 5.0) * 0.8

    kind = str(props.get("kind") or props.get("property_type") or "").lower()
    if "villa" in kind or "ویلا" in kind:
        score += 1.0

    score = max(0.0, min(100.0, score))

    details = {
        "best_poi_distance_m": best_poi,
        "distance_to_metro_m": metro,
        "distance_to_mall_m": mall,
        "distance_to_main_road_m": road,
        "risk_levels": {
            "flood": flood,
            "earthquake": earthquake,
            "fire": fire,
        },
        "risk_penalty": round(risk_penalty, 2),
        "in_allowed_zone": bool(in_allowed_zone),
    }

    return round(score, 1), details


def evaluate_real_estate_eligibility(
    props: dict[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:
    metro = to_float_or_none(props.get("distance_to_metro_m"))
    mall = to_float_or_none(props.get("distance_to_mall_m"))
    road = to_float_or_none(props.get("distance_to_main_road_m"))

    poi_distances = [d for d in [metro, mall] if d is not None]
    best_poi = min(poi_distances) if poi_distances else None

    flood = normalize_risk_level(props.get("flood_risk"))
    earthquake = normalize_risk_level(props.get("earthquake_risk"))
    fire = normalize_risk_level(props.get("fire_risk"))

    in_allowed_zone = props.get("in_allowed_zone")
    if in_allowed_zone is None:
        in_allowed_zone = props.get("build_zone_allowed")
    if in_allowed_zone is None:
        in_allowed_zone = props.get("construction_allowed", True)

    reasons: list[str] = []

    # شرط اصلی کاربر: کمتر از ۵۰۰ متر به مترو یا مرکز خرید
    if best_poi is None:
        reasons.append("distance_to_metro_or_mall_missing")
    elif best_poi > 500:
        reasons.append("farther_than_500m_from_metro_or_mall")

    # نزدیکی به خیابان اصلی؛ برای MVP آستانه ۱۵۰ متر می‌گذاریم.
    if road is None:
        reasons.append("distance_to_main_road_missing")
    elif road > 150:
        reasons.append("far_from_main_road")

    # برای MVP فقط high را حذف می‌کنیم و medium را با جریمه امتیازی نگه می‌داریم.
    if flood == "high":
        reasons.append("high_flood_risk")
    if earthquake == "high":
        reasons.append("high_earthquake_risk")
    if fire == "high":
        reasons.append("high_fire_risk")

    if in_allowed_zone is False:
        reasons.append("outside_allowed_construction_zone")

    eligible = len(reasons) == 0

    metrics = {
        "best_poi_distance_m": best_poi,
        "distance_to_metro_m": metro,
        "distance_to_mall_m": mall,
        "distance_to_main_road_m": road,
        "risk_levels": {
            "flood": flood,
            "earthquake": earthquake,
            "fire": fire,
        },
        "in_allowed_zone": bool(in_allowed_zone),
    }

    return eligible, reasons, metrics
