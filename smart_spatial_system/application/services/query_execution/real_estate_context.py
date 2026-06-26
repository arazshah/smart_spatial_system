from __future__ import annotations

from collections.abc import Callable
from typing import Any


def extract_property_feature_collection_from_inputs(
    inputs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(inputs, dict):
        return None

    def _property_only_feature_collection(fc: dict[str, Any]) -> dict[str, Any]:
        features = fc.get("features") or []
        if not isinstance(features, list):
            features = []

        property_features: list[dict[str, Any]] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            if not isinstance(props, dict):
                props = {}

            layer = str(props.get("layer") or "").strip().lower()
            property_type = str(props.get("property_type") or props.get("kind") or "").strip().lower()
            has_property_identity = bool(
                props.get("property_id")
                or str(props.get("id") or "").startswith("prop-")
                or layer == "property"
                or property_type in {"apartment", "villa", "land", "house", "ملک", "آپارتمان", "ویلا", "زمین"}
            )

            if has_property_identity:
                property_features.append(feature)

        # اگر featureهای property پیدا شد، فقط همان‌ها را برای ranking برگردان.
        if property_features:
            out = dict(fc)
            out["features"] = property_features
            return out

        return fc

    candidate_keys = [
        "properties",
        "property_layer",
        "propertyLayer",
        "real_estate_properties",
        "realEstateProperties",
        "parcels",
        "assets",
        "vector",
        "geojson",
    ]

    for key in candidate_keys:
        value = inputs.get(key)
        if isinstance(value, dict) and value.get("type") == "FeatureCollection":
            return _property_only_feature_collection(value)

        if isinstance(value, dict):
            nested = value.get("geojson") or value.get("data") or value.get("feature_collection")
            if isinstance(nested, dict) and nested.get("type") == "FeatureCollection":
                return _property_only_feature_collection(nested)

    return None


def extract_real_estate_spatial_context_from_inputs(
    inputs: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Extract optional spatial context layers for real-estate ranking.

    Supported layer groups:
    - metro: point features
    - malls: point/polygon features
    - main_roads: line features
    - allowed_zones: polygon features

    The extractor is deliberately permissive and can work with:
    - inputs["geojson"] as one mixed FeatureCollection
    - inputs["metro_layer"], inputs["main_roads"], ...
    - nested {"geojson": FeatureCollection} wrappers
    """
    context: dict[str, list[dict[str, Any]]] = {
        "metro": [],
        "malls": [],
        "main_roads": [],
        "allowed_zones": [],
    }

    if not isinstance(inputs, dict):
        return context

    seen: set[int] = set()

    def _iter_feature_collections(value: Any, hint: str = "", depth: int = 0):
        if depth > 6:
            return

        if isinstance(value, dict):
            obj_id = id(value)
            if obj_id in seen:
                return
            seen.add(obj_id)

            if value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list):
                yield value, hint

            for key, nested in value.items():
                if key in {"features", "geometry", "properties"}:
                    continue
                next_hint = f"{hint}.{key}" if hint else str(key)
                if isinstance(nested, (dict, list)):
                    yield from _iter_feature_collections(nested, next_hint, depth + 1)

        elif isinstance(value, list):
            for index, item in enumerate(value):
                next_hint = f"{hint}[{index}]"
                if isinstance(item, (dict, list)):
                    yield from _iter_feature_collections(item, next_hint, depth + 1)

    def _search_text(feature: dict[str, Any], source_hint: str) -> str:
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            props = {}

        parts = [
            source_hint,
            props.get("layer"),
            props.get("category"),
            props.get("kind"),
            props.get("type"),
            props.get("feature_type"),
            props.get("role"),
            props.get("class"),
            props.get("name"),
            props.get("title"),
            props.get("description"),
        ]
        return " ".join(str(part or "") for part in parts).strip().lower()

    def _classify_feature(feature: dict[str, Any], source_hint: str) -> str | None:
        geom = feature.get("geometry") or {}
        geom_type = str(geom.get("type") or "").lower()
        text = _search_text(feature, source_hint)

        # Property features are handled by extract_property_feature_collection_from_inputs.
        property_terms = [
            "property",
            "properties",
            "real_estate",
            "real-estate",
            "parcel",
            "apartment",
            "villa",
            "house",
            "land",
            "ملک",
            "آپارتمان",
            "ویلا",
            "زمین",
        ]
        if any(term in text for term in property_terms):
            return "property"

        metro_terms = [
            "metro",
            "subway",
            "station",
            "metro_station",
            "ایستگاه مترو",
            "مترو",
        ]
        if any(term in text for term in metro_terms):
            return "metro"

        mall_terms = [
            "mall",
            "shopping",
            "shopping_center",
            "commercial_center",
            "مرکز خرید",
            "خرید",
            "مال",
        ]
        if any(term in text for term in mall_terms):
            return "malls"

        road_terms = [
            "main_road",
            "main-road",
            "road",
            "street",
            "highway",
            "primary",
            "خیابان اصلی",
            "جاده اصلی",
            "خیابان",
            "جاده",
        ]
        if any(term in text for term in road_terms):
            return "main_roads"

        allowed_zone_terms = [
            "allowed_zone",
            "allowed-zone",
            "construction_zone",
            "construction-zone",
            "build_zone",
            "build-zone",
            "zoning",
            "allowed construction",
            "محدوده مجاز",
            "محدوده ساخت",
            "ساخت‌وساز مجاز",
            "ساخت و ساز مجاز",
        ]
        if any(term in text for term in allowed_zone_terms):
            return "allowed_zones"

        # Geometry-based conservative hints from source key.
        if "metro" in text and geom_type == "point":
            return "metro"
        if ("mall" in text or "shopping" in text) and geom_type in {"point", "polygon", "multipolygon"}:
            return "malls"
        if ("road" in text or "street" in text or "highway" in text) and geom_type in {"linestring", "multilinestring"}:
            return "main_roads"
        if ("zone" in text or "zoning" in text) and geom_type in {"polygon", "multipolygon"}:
            return "allowed_zones"

        return None

    for fc, source_hint in _iter_feature_collections(inputs):
        for feature in fc.get("features") or []:
            if not isinstance(feature, dict):
                continue
            group = _classify_feature(feature, source_hint)
            if group in context:
                context[group].append(feature)

    return context


def enrich_property_feature_collection_with_spatial_context(
    feature_collection: dict[str, Any],
    spatial_context: dict[str, list[dict[str, Any]]] | None,
    *,
    feature_point_lonlat: Callable[[dict[str, Any]], tuple[float, float] | None],
    has_metric_value: Callable[[dict[str, Any], str], bool],
    nearest_distance_to_features_m: Callable[[tuple[float, float], list[dict[str, Any]]], float | None],
    has_bool_like_value: Callable[[dict[str, Any], str, str, str], bool],
    point_in_polygon_feature_lonlat: Callable[[tuple[float, float], dict[str, Any]], bool],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Fill missing real-estate ranking metrics from optional spatial layers.

    This is an additive fallback:
    - existing property distance/risk fields are preserved
    - only missing distance_to_* and in_allowed_zone aliases are computed
    """
    context = spatial_context or {}
    metro_features = context.get("metro") or []
    mall_features = context.get("malls") or []
    road_features = context.get("main_roads") or []
    allowed_zone_features = context.get("allowed_zones") or []

    features = feature_collection.get("features") or []
    if not isinstance(features, list):
        features = []

    enriched_features: list[dict[str, Any]] = []
    updated_distance_count = 0
    updated_allowed_zone_count = 0
    touched_property_count = 0

    for feature in features:
        if not isinstance(feature, dict):
            continue

        props = dict(feature.get("properties") or {})
        point = feature_point_lonlat(feature)

        touched = False

        if point is not None:
            if not has_metric_value(props, "distance_to_metro_m") and metro_features:
                distance = nearest_distance_to_features_m(point, metro_features)
                if distance is not None:
                    props["distance_to_metro_m"] = int(round(distance))
                    updated_distance_count += 1
                    touched = True

            if not has_metric_value(props, "distance_to_mall_m") and mall_features:
                distance = nearest_distance_to_features_m(point, mall_features)
                if distance is not None:
                    props["distance_to_mall_m"] = int(round(distance))
                    updated_distance_count += 1
                    touched = True

            if not has_metric_value(props, "distance_to_main_road_m") and road_features:
                distance = nearest_distance_to_features_m(point, road_features)
                if distance is not None:
                    props["distance_to_main_road_m"] = int(round(distance))
                    updated_distance_count += 1
                    touched = True

            if (
                not has_bool_like_value(
                    props,
                    "in_allowed_zone",
                    "build_zone_allowed",
                    "construction_allowed",
                )
                and allowed_zone_features
            ):
                in_zone = any(
                    point_in_polygon_feature_lonlat(point, zone_feature)
                    for zone_feature in allowed_zone_features
                    if isinstance(zone_feature, dict)
                )
                props["in_allowed_zone"] = bool(in_zone)
                props["build_zone_allowed"] = bool(in_zone)
                props["construction_allowed"] = bool(in_zone)
                updated_allowed_zone_count += 1
                touched = True

        if touched:
            touched_property_count += 1
            props["spatial_enrichment_applied"] = True

        enriched_features.append(
            {
                **feature,
                "properties": props,
            }
        )

    enriched_fc = dict(feature_collection)
    enriched_fc["features"] = enriched_features

    summary = {
        "applied": touched_property_count > 0,
        "property_count": len(enriched_features),
        "touched_property_count": touched_property_count,
        "updated_distance_count": updated_distance_count,
        "updated_allowed_zone_count": updated_allowed_zone_count,
        "context_counts": {
            "metro": len(metro_features),
            "malls": len(mall_features),
            "main_roads": len(road_features),
            "allowed_zones": len(allowed_zone_features),
        },
    }

    return enriched_fc, summary
