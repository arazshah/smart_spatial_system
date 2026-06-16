"""
geocoding_resolver.py

GeoChat SDK Plugin
==================

Plugin ID:
    geocoding_resolver

Purpose:
    Resolve place names / addresses to geographic coordinates and optionally
    reverse-resolve coordinates to human-readable locations.

Capabilities:
    - geocode_place:
        Text query -> Point features.

    - reverse_geocode_point:
        lon/lat -> Point feature with address metadata.

Provider-based design:
    This plugin is intentionally not limited to Nominatim.
    Supported provider types:
        - static
        - nominatim
        - generic_http_json

Future providers can be added without changing the public capability contract.

Network behavior:
    Online providers use urllib from the Python standard library.
    No external HTTP dependency is required.

Important:
    Nominatim has usage policies. Use a clear User-Agent and respect rate limits.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "geocoding_resolver"

VALID_PROVIDER_TYPES = {"static", "nominatim", "generic_http_json"}


def _load_geocoding_config() -> dict[str, Any]:
    """
    Load config/plugins/geocoding_resolver.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    """
    Return current UTC timestamp as ISO string.
    """
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any, field_name: str) -> float:
    """
    Convert value to float.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number.")

    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be a number.") from exc


def _to_int(value: Any, field_name: str) -> int:
    """
    Convert value to int.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")

    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc


def _validate_query(query: Any) -> str:
    """
    Validate text query.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string.")
    return query.strip()


def _validate_limit(limit: Any) -> int:
    """
    Validate result limit.
    """
    value = _to_int(limit, "limit")

    if value < 1:
        raise ValueError("limit must be greater than or equal to 1.")

    if value > 50:
        raise ValueError("limit is too large. Maximum allowed value is 50.")

    return value


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return coordinate precision.
    """
    value = config.get("coordinate_precision", 8)

    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("coordinate_precision must be an integer or null.")

    precision = _to_int(value, "coordinate_precision")

    if precision < 0:
        raise ValueError("coordinate_precision must be >= 0.")

    if precision > 15:
        raise ValueError("coordinate_precision is too large. Maximum allowed value is 15.")

    return precision


def _round_coord(value: float, precision: int | None) -> float:
    """
    Round coordinate if precision is configured.
    """
    if precision is None:
        return float(value)
    return round(float(value), precision)


def _normalize_provider_name(provider: Any) -> str:
    """
    Normalize provider name.
    """
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider must be a non-empty string.")
    return provider.strip()


def _get_provider_config(config: dict[str, Any], provider: str) -> dict[str, Any]:
    """
    Return provider config by name.

    Supports implicit defaults for 'nominatim' and 'static'.
    """
    provider = _normalize_provider_name(provider)

    providers = config.get("providers") or {}
    if not isinstance(providers, dict):
        raise ValueError("providers in geocoding_resolver config must be a dict.")

    if provider in providers:
        provider_config = providers[provider]
        if not isinstance(provider_config, dict):
            raise ValueError(f"provider config for '{provider}' must be a dict.")
        result = dict(provider_config)
        result["_provider_name"] = provider
        return result

    if provider == "nominatim":
        return {
            "_provider_name": "nominatim",
            "type": "nominatim",
            "base_url": "https://nominatim.openstreetmap.org",
            "user_agent": "GeoChatPlatform/1.0",
            "email": "",
        }

    if provider == "static":
        return {
            "_provider_name": "static",
            "type": "static",
            "places": {},
        }

    raise ValueError(f"Unknown geocoding provider: {provider}")


def _validate_provider_type(provider_config: dict[str, Any]) -> str:
    """
    Validate provider type.
    """
    provider_type = provider_config.get("type")

    if not isinstance(provider_type, str) or not provider_type.strip():
        raise ValueError("provider.type must be a non-empty string.")

    provider_type = provider_type.strip().lower()

    if provider_type not in VALID_PROVIDER_TYPES:
        raise ValueError(
            f"Unsupported provider type '{provider_type}'. "
            f"Valid types: {sorted(VALID_PROVIDER_TYPES)}"
        )

    return provider_type


def _provider_chain_from_config(
    *,
    config: dict[str, Any],
    provider: str | None,
    provider_chain: list[str] | None,
) -> list[str]:
    """
    Resolve provider chain.

    Priority:
        explicit provider_chain > explicit provider > config.default_provider_chain > config.default_provider
    """
    if provider_chain is not None:
        if not isinstance(provider_chain, list) or not provider_chain:
            raise ValueError("provider_chain must be a non-empty list of provider names.")
        return [_normalize_provider_name(item) for item in provider_chain]

    if provider is not None:
        return [_normalize_provider_name(provider)]

    configured_chain = config.get("default_provider_chain")
    if configured_chain:
        if not isinstance(configured_chain, list):
            raise ValueError("default_provider_chain must be a list.")
        return [_normalize_provider_name(item) for item in configured_chain]

    return [_normalize_provider_name(config.get("default_provider", "nominatim"))]


def _http_get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 10,
) -> Any:
    """
    HTTP GET JSON helper.

    Kept as a small function so tests can monkeypatch urllib.request.urlopen.
    """
    request = urllib.request.Request(
        url,
        headers=headers or {},
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read()

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    return json.loads(raw)


def _build_feature(
    *,
    lon: float,
    lat: float,
    display_name: str,
    provider: str,
    precision: int | None,
    properties: dict[str, Any] | None = None,
    bbox: list[float] | None = None,
) -> dict[str, Any]:
    """
    Build a GeoJSON Point feature from geocoding result.
    """
    props = dict(properties or {})
    props.setdefault("display_name", display_name)
    props.setdefault("provider", provider)

    if bbox is not None:
        props["bbox"] = bbox

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [
                _round_coord(lon, precision),
                _round_coord(lat, precision),
            ],
        },
        "properties": props,
    }


def _parse_bbox(value: Any) -> list[float] | None:
    """
    Parse bbox-like values.

    Nominatim boundingbox format:
        [south, north, west, east]

    Output format:
        [minx, miny, maxx, maxy]
    """
    if not isinstance(value, list) or len(value) != 4:
        return None

    try:
        south = float(value[0])
        north = float(value[1])
        west = float(value[2])
        east = float(value[3])
    except Exception:
        return None

    return [west, south, east, north]


def _static_geocode(
    *,
    query: str,
    provider_name: str,
    provider_config: dict[str, Any],
    limit: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Geocode using static configured places.
    """
    places = provider_config.get("places") or {}

    if not isinstance(places, dict):
        raise ValueError("static provider places must be a dict.")

    q = query.strip().lower()
    features: list[dict[str, Any]] = []

    for key, item in places.items():
        if not isinstance(item, dict):
            continue

        key_text = str(key).lower()
        display_name = str(item.get("display_name") or key)

        searchable = " ".join([
            key_text,
            display_name.lower(),
            str(item.get("country") or "").lower(),
            str(item.get("city") or "").lower(),
            str(item.get("province") or "").lower(),
        ])

        if q not in searchable:
            continue

        lon = _to_float(item.get("lon"), "lon")
        lat = _to_float(item.get("lat"), "lat")

        props = {
            "display_name": display_name,
            "provider": provider_name,
            "provider_type": "static",
            "matched_key": key,
        }

        for prop_key, prop_val in item.items():
            if prop_key not in {"lon", "lat"}:
                props[prop_key] = prop_val

        features.append(
            _build_feature(
                lon=lon,
                lat=lat,
                display_name=display_name,
                provider=provider_name,
                precision=precision,
                properties=props,
            )
        )

        if len(features) >= limit:
            break

    return features


def _static_reverse_geocode(
    *,
    lon: float,
    lat: float,
    provider_name: str,
    provider_config: dict[str, Any],
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Reverse geocode using nearest static place.
    """
    places = provider_config.get("places") or {}

    if not isinstance(places, dict):
        raise ValueError("static provider places must be a dict.")

    best_key = None
    best_item = None
    best_dist2 = None

    for key, item in places.items():
        if not isinstance(item, dict):
            continue

        item_lon = _to_float(item.get("lon"), "lon")
        item_lat = _to_float(item.get("lat"), "lat")

        dist2 = (item_lon - lon) ** 2 + (item_lat - lat) ** 2

        if best_dist2 is None or dist2 < best_dist2:
            best_key = key
            best_item = item
            best_dist2 = dist2

    if best_item is None:
        return []

    display_name = str(best_item.get("display_name") or best_key)

    props = {
        "display_name": display_name,
        "provider": provider_name,
        "provider_type": "static",
        "matched_key": best_key,
        "distance_degrees_squared": best_dist2,
    }

    for prop_key, prop_val in best_item.items():
        if prop_key not in {"lon", "lat"}:
            props[prop_key] = prop_val

    return [
        _build_feature(
            lon=lon,
            lat=lat,
            display_name=display_name,
            provider=provider_name,
            precision=precision,
            properties=props,
        )
    ]


def _nominatim_headers(provider_config: dict[str, Any]) -> dict[str, str]:
    """
    Build Nominatim headers.
    """
    user_agent = str(provider_config.get("user_agent") or "GeoChatPlatform/1.0")
    return {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }


def _nominatim_base_url(provider_config: dict[str, Any]) -> str:
    """
    Return normalized Nominatim base URL.
    """
    base_url = str(provider_config.get("base_url") or "https://nominatim.openstreetmap.org")
    return base_url.rstrip("/")


def _nominatim_geocode(
    *,
    query: str,
    provider_name: str,
    provider_config: dict[str, Any],
    limit: int,
    language: str | None,
    country_codes: str | None,
    timeout_seconds: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Geocode using Nominatim.
    """
    base_url = _nominatim_base_url(provider_config)

    params: dict[str, Any] = {
        "q": query,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": limit,
    }

    if language:
        params["accept-language"] = language

    if country_codes:
        params["countrycodes"] = country_codes

    email = provider_config.get("email")
    if isinstance(email, str) and email.strip():
        params["email"] = email.strip()

    viewbox = provider_config.get("viewbox")
    if viewbox:
        params["viewbox"] = str(viewbox)

    if bool(provider_config.get("bounded", False)):
        params["bounded"] = 1

    url = f"{base_url}/search?{urllib.parse.urlencode(params)}"

    data = _http_get_json(
        url=url,
        headers=_nominatim_headers(provider_config),
        timeout_seconds=timeout_seconds,
    )

    if not isinstance(data, list):
        raise ValueError("Nominatim search response must be a list.")

    features: list[dict[str, Any]] = []

    for item in data[:limit]:
        if not isinstance(item, dict):
            continue

        lon = _to_float(item.get("lon"), "lon")
        lat = _to_float(item.get("lat"), "lat")
        display_name = str(item.get("display_name") or query)

        props = {
            "display_name": display_name,
            "provider": provider_name,
            "provider_type": "nominatim",
            "osm_type": item.get("osm_type"),
            "osm_id": item.get("osm_id"),
            "class": item.get("class"),
            "type": item.get("type"),
            "importance": item.get("importance"),
            "address": item.get("address"),
            "raw": item,
        }

        features.append(
            _build_feature(
                lon=lon,
                lat=lat,
                display_name=display_name,
                provider=provider_name,
                precision=precision,
                properties=props,
                bbox=_parse_bbox(item.get("boundingbox")),
            )
        )

    return features


def _nominatim_reverse_geocode(
    *,
    lon: float,
    lat: float,
    provider_name: str,
    provider_config: dict[str, Any],
    language: str | None,
    timeout_seconds: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Reverse geocode using Nominatim.
    """
    base_url = _nominatim_base_url(provider_config)

    params: dict[str, Any] = {
        "lon": lon,
        "lat": lat,
        "format": "jsonv2",
        "addressdetails": 1,
    }

    if language:
        params["accept-language"] = language

    email = provider_config.get("email")
    if isinstance(email, str) and email.strip():
        params["email"] = email.strip()

    url = f"{base_url}/reverse?{urllib.parse.urlencode(params)}"

    item = _http_get_json(
        url=url,
        headers=_nominatim_headers(provider_config),
        timeout_seconds=timeout_seconds,
    )

    if not isinstance(item, dict):
        raise ValueError("Nominatim reverse response must be a dict.")

    display_name = str(item.get("display_name") or "")

    props = {
        "display_name": display_name,
        "provider": provider_name,
        "provider_type": "nominatim",
        "osm_type": item.get("osm_type"),
        "osm_id": item.get("osm_id"),
        "class": item.get("class"),
        "type": item.get("type"),
        "address": item.get("address"),
        "raw": item,
    }

    return [
        _build_feature(
            lon=lon,
            lat=lat,
            display_name=display_name,
            provider=provider_name,
            precision=precision,
            properties=props,
            bbox=_parse_bbox(item.get("boundingbox")),
        )
    ]


def _get_path(obj: Any, path: str | None, default: Any = None) -> Any:
    """
    Read dot-separated path from dict/list structures.

    Examples:
        results.0.lon
        geometry.coordinates.0
    """
    if not path:
        return default

    current = obj

    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part, default)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except Exception:
                return default
        else:
            return default

    return current


def _generic_http_geocode(
    *,
    query: str,
    provider_name: str,
    provider_config: dict[str, Any],
    limit: int,
    language: str | None,
    timeout_seconds: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Generic HTTP JSON geocoder.

    Expected configurable fields:
        endpoint_url
        query_param
        limit_param
        language_param
        api_key_param
        api_key
        results_path
        lon_path
        lat_path
        display_name_path
    """
    endpoint_url = provider_config.get("endpoint_url")
    if not isinstance(endpoint_url, str) or not endpoint_url.strip():
        raise ValueError("generic_http_json provider requires endpoint_url.")

    query_param = str(provider_config.get("query_param") or "q")
    limit_param = str(provider_config.get("limit_param") or "limit")

    params: dict[str, Any] = {
        query_param: query,
        limit_param: limit,
    }

    language_param = provider_config.get("language_param")
    if language_param and language:
        params[str(language_param)] = language

    api_key = provider_config.get("api_key")
    api_key_param = provider_config.get("api_key_param")
    if api_key and api_key_param:
        params[str(api_key_param)] = api_key

    separator = "&" if "?" in endpoint_url else "?"
    url = f"{endpoint_url}{separator}{urllib.parse.urlencode(params)}"

    headers = {
        "Accept": "application/json",
        "User-Agent": str(provider_config.get("user_agent") or "GeoChatPlatform/1.0"),
    }

    api_key_header = provider_config.get("api_key_header")
    if api_key and api_key_header:
        headers[str(api_key_header)] = str(api_key)

    data = _http_get_json(
        url=url,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )

    results = _get_path(data, str(provider_config.get("results_path") or ""), default=data)

    if isinstance(results, dict):
        results = [results]

    if not isinstance(results, list):
        raise ValueError("generic_http_json results must be a list or dict.")

    lon_path = str(provider_config.get("lon_path") or "lon")
    lat_path = str(provider_config.get("lat_path") or "lat")
    display_name_path = str(provider_config.get("display_name_path") or "display_name")

    features: list[dict[str, Any]] = []

    for item in results[:limit]:
        if not isinstance(item, dict):
            continue

        lon = _to_float(_get_path(item, lon_path), "lon")
        lat = _to_float(_get_path(item, lat_path), "lat")
        display_name = str(_get_path(item, display_name_path, query))

        props = {
            "display_name": display_name,
            "provider": provider_name,
            "provider_type": "generic_http_json",
            "raw": item,
        }

        features.append(
            _build_feature(
                lon=lon,
                lat=lat,
                display_name=display_name,
                provider=provider_name,
                precision=precision,
                properties=props,
            )
        )

    return features


def _generic_http_reverse_geocode(
    *,
    lon: float,
    lat: float,
    provider_name: str,
    provider_config: dict[str, Any],
    language: str | None,
    timeout_seconds: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Generic HTTP JSON reverse geocoder.
    """
    endpoint_url = provider_config.get("reverse_endpoint_url") or provider_config.get("endpoint_url")
    if not isinstance(endpoint_url, str) or not endpoint_url.strip():
        raise ValueError("generic_http_json provider requires reverse_endpoint_url or endpoint_url.")

    lon_param = str(provider_config.get("lon_param") or "lon")
    lat_param = str(provider_config.get("lat_param") or "lat")

    params: dict[str, Any] = {
        lon_param: lon,
        lat_param: lat,
    }

    language_param = provider_config.get("language_param")
    if language_param and language:
        params[str(language_param)] = language

    api_key = provider_config.get("api_key")
    api_key_param = provider_config.get("api_key_param")
    if api_key and api_key_param:
        params[str(api_key_param)] = api_key

    separator = "&" if "?" in endpoint_url else "?"
    url = f"{endpoint_url}{separator}{urllib.parse.urlencode(params)}"

    headers = {
        "Accept": "application/json",
        "User-Agent": str(provider_config.get("user_agent") or "GeoChatPlatform/1.0"),
    }

    api_key_header = provider_config.get("api_key_header")
    if api_key and api_key_header:
        headers[str(api_key_header)] = str(api_key)

    data = _http_get_json(
        url=url,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )

    display_name = str(_get_path(data, str(provider_config.get("display_name_path") or "display_name"), ""))

    props = {
        "display_name": display_name,
        "provider": provider_name,
        "provider_type": "generic_http_json",
        "raw": data,
    }

    return [
        _build_feature(
            lon=lon,
            lat=lat,
            display_name=display_name,
            provider=provider_name,
            precision=precision,
            properties=props,
        )
    ]


def _geocode_with_provider(
    *,
    query: str,
    provider_name: str,
    provider_config: dict[str, Any],
    limit: int,
    language: str | None,
    country_codes: str | None,
    timeout_seconds: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Dispatch geocode to provider implementation.
    """
    provider_type = _validate_provider_type(provider_config)

    if provider_type == "static":
        return _static_geocode(
            query=query,
            provider_name=provider_name,
            provider_config=provider_config,
            limit=limit,
            precision=precision,
        )

    if provider_type == "nominatim":
        return _nominatim_geocode(
            query=query,
            provider_name=provider_name,
            provider_config=provider_config,
            limit=limit,
            language=language,
            country_codes=country_codes,
            timeout_seconds=timeout_seconds,
            precision=precision,
        )

    if provider_type == "generic_http_json":
        return _generic_http_geocode(
            query=query,
            provider_name=provider_name,
            provider_config=provider_config,
            limit=limit,
            language=language,
            timeout_seconds=timeout_seconds,
            precision=precision,
        )

    raise ValueError(f"Unsupported provider type: {provider_type}")


def _reverse_with_provider(
    *,
    lon: float,
    lat: float,
    provider_name: str,
    provider_config: dict[str, Any],
    language: str | None,
    timeout_seconds: int,
    precision: int | None,
) -> list[dict[str, Any]]:
    """
    Dispatch reverse geocode to provider implementation.
    """
    provider_type = _validate_provider_type(provider_config)

    if provider_type == "static":
        return _static_reverse_geocode(
            lon=lon,
            lat=lat,
            provider_name=provider_name,
            provider_config=provider_config,
            precision=precision,
        )

    if provider_type == "nominatim":
        return _nominatim_reverse_geocode(
            lon=lon,
            lat=lat,
            provider_name=provider_name,
            provider_config=provider_config,
            language=language,
            timeout_seconds=timeout_seconds,
            precision=precision,
        )

    if provider_type == "generic_http_json":
        return _generic_http_reverse_geocode(
            lon=lon,
            lat=lat,
            provider_name=provider_name,
            provider_config=provider_config,
            language=language,
            timeout_seconds=timeout_seconds,
            precision=precision,
        )

    raise ValueError(f"Unsupported provider type: {provider_type}")


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox for Point geometry.
    """
    if not geometry:
        return None

    if geometry.get("type") != "Point":
        return None

    coords = geometry.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return None

    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except Exception:
        return None

    return [lon, lat, lon, lat]


def _merge_bbox_arrays(bboxes: list[list[float]]) -> dict[str, float] | None:
    """
    Merge bbox arrays.
    """
    valid = [b for b in bboxes if b and len(b) == 4]
    if not valid:
        return None

    return {
        "minx": min(b[0] for b in valid),
        "miny": min(b[1] for b in valid),
        "maxx": max(b[2] for b in valid),
        "maxy": max(b[3] for b in valid),
    }


def _build_vector_metadata(features: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build VectorOut metadata.
    """
    geometry_types: dict[str, int] = {}
    bboxes: list[list[float]] = []

    for feature in features:
        geometry = feature.get("geometry")

        if isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "Unknown")
            bbox = _geometry_bbox(geometry)
            if bbox is not None:
                bboxes.append(bbox)
        elif geometry is None:
            gtype = "Null"
        else:
            gtype = "Invalid"

        geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

    return {
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": _merge_bbox_arrays(bboxes),
    }


@capability(
    name="geocode_place",
    keywords=[
        "geocode",
        "geocoding",
        "resolve place",
        "place to coordinate",
        "address to coordinate",
        "find location",
        "location resolver",
        "تبدیل آدرس به مختصات",
        "ژئوکدینگ",
        "کدگذاری مکانی",
        "پیدا کردن مکان",
        "مختصات مکان",
        "آدرس به نقطه",
        "نام مکان به مختصات",
    ],
    description="Resolve a place name or address query to geographic point features.",
    required_inputs=["query"],
    optional_inputs=[
        "provider",
        "provider_chain",
        "limit",
        "language",
        "country_codes",
        "timeout_seconds",
        "metadata",
    ],
    output_kind="vector",
    permissions=["network"],
    metadata={
        "category": "data_source",
        "data_type": "vector",
        "operation": "geocode",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "external_geocoding",
        "config_aware": True,
        "provider_based": True,
        "routable": True,
    },
)
def geocode_place(
    query: str,
    provider: str | None = None,
    provider_chain: list[str] | None = None,
    limit: int | None = None,
    language: str | None = None,
    country_codes: str | None = None,
    timeout_seconds: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Resolve place name/address to Point features.

    Args:
        query:
            User text query, e.g. "Tehran", "میدان آزادی".
        provider:
            Single provider name.
        provider_chain:
            Ordered list of providers. First provider returning results wins.
        limit:
            Maximum number of results.
        language:
            Preferred response language.
        country_codes:
            Optional provider-specific country filter, e.g. "ir".
        timeout_seconds:
            HTTP timeout for online providers.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut containing Point features.
    """
    config = _load_geocoding_config()

    final_query = _validate_query(query)
    final_limit = _validate_limit(pick_first(limit, config.get("default_limit"), default=5))
    final_language = pick_first(language, config.get("default_language"), default=None)

    final_timeout = _validate_limit(
        pick_first(timeout_seconds, config.get("default_timeout_seconds"), default=10)
    )

    precision = _configured_precision(config)

    chain = _provider_chain_from_config(
        config=config,
        provider=provider,
        provider_chain=provider_chain,
    )

    continue_on_error = bool(config.get("continue_on_provider_error", True))

    output_features: list[dict[str, Any]] = []
    providers_tried: list[str] = []
    provider_errors: list[dict[str, str]] = []
    provider_used: str | None = None

    for provider_name in chain:
        providers_tried.append(provider_name)
        provider_config = _get_provider_config(config, provider_name)

        try:
            features = _geocode_with_provider(
                query=final_query,
                provider_name=provider_name,
                provider_config=provider_config,
                limit=final_limit,
                language=final_language,
                country_codes=country_codes,
                timeout_seconds=final_timeout,
                precision=precision,
            )
        except Exception as exc:
            provider_errors.append({
                "provider": provider_name,
                "error": str(exc),
            })

            if not continue_on_error or len(chain) == 1:
                raise

            continue

        if features:
            output_features = features
            provider_used = provider_name
            break

    stats = _build_vector_metadata(output_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "geocoding_resolver",
        "loader": PLUGIN_ID,
        "operation": "geocode",
        "query": final_query,
        "provider_used": provider_used,
        "providers_tried": providers_tried,
        "provider_errors": provider_errors,
        "limit": final_limit,
        "language": final_language,
        "country_codes": country_codes,
        "coordinate_precision": precision,
        "result_count": len(output_features),
        "created_at": _utc_now_iso(),
        **stats,
        **user_metadata,
    }

    return VectorOut(
        features=output_features,
        metadata=output_metadata,
    )


@capability(
    name="reverse_geocode_point",
    keywords=[
        "reverse geocode",
        "coordinate to address",
        "point to address",
        "resolve coordinate",
        "reverse location",
        "تبدیل مختصات به آدرس",
        "ژئوکدینگ معکوس",
        "مختصات به مکان",
        "نقطه به آدرس",
    ],
    description="Resolve longitude/latitude coordinates to a human-readable place/address.",
    required_inputs=["lon", "lat"],
    optional_inputs=[
        "provider",
        "provider_chain",
        "language",
        "timeout_seconds",
        "metadata",
    ],
    output_kind="vector",
    permissions=["network"],
    metadata={
        "category": "data_source",
        "data_type": "vector",
        "operation": "reverse_geocode",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "external_geocoding",
        "config_aware": True,
        "provider_based": True,
        "routable": True,
    },
)
def reverse_geocode_point(
    lon: float,
    lat: float,
    provider: str | None = None,
    provider_chain: list[str] | None = None,
    language: str | None = None,
    timeout_seconds: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Resolve lon/lat to address/place feature.

    Args:
        lon:
            Longitude.
        lat:
            Latitude.
        provider:
            Single provider name.
        provider_chain:
            Ordered list of providers. First provider returning results wins.
        language:
            Preferred response language.
        timeout_seconds:
            HTTP timeout for online providers.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut containing one Point feature if resolved.
    """
    config = _load_geocoding_config()

    final_lon = _to_float(lon, "lon")
    final_lat = _to_float(lat, "lat")

    if final_lon < -180 or final_lon > 180:
        raise ValueError("lon must be between -180 and 180.")

    if final_lat < -90 or final_lat > 90:
        raise ValueError("lat must be between -90 and 90.")

    final_language = pick_first(language, config.get("default_language"), default=None)

    final_timeout = _validate_limit(
        pick_first(timeout_seconds, config.get("default_timeout_seconds"), default=10)
    )

    precision = _configured_precision(config)

    chain = _provider_chain_from_config(
        config=config,
        provider=provider,
        provider_chain=provider_chain,
    )

    continue_on_error = bool(config.get("continue_on_provider_error", True))

    output_features: list[dict[str, Any]] = []
    providers_tried: list[str] = []
    provider_errors: list[dict[str, str]] = []
    provider_used: str | None = None

    for provider_name in chain:
        providers_tried.append(provider_name)
        provider_config = _get_provider_config(config, provider_name)

        try:
            features = _reverse_with_provider(
                lon=final_lon,
                lat=final_lat,
                provider_name=provider_name,
                provider_config=provider_config,
                language=final_language,
                timeout_seconds=final_timeout,
                precision=precision,
            )
        except Exception as exc:
            provider_errors.append({
                "provider": provider_name,
                "error": str(exc),
            })

            if not continue_on_error or len(chain) == 1:
                raise

            continue

        if features:
            output_features = features
            provider_used = provider_name
            break

    stats = _build_vector_metadata(output_features)

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "geocoding_resolver",
        "loader": PLUGIN_ID,
        "operation": "reverse_geocode",
        "lon": final_lon,
        "lat": final_lat,
        "provider_used": provider_used,
        "providers_tried": providers_tried,
        "provider_errors": provider_errors,
        "language": final_language,
        "coordinate_precision": precision,
        "result_count": len(output_features),
        "created_at": _utc_now_iso(),
        **stats,
        **user_metadata,
    }

    return VectorOut(
        features=output_features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Geocoding Resolver",
    description=(
        "Resolves place names and addresses to coordinates and supports reverse "
        "geocoding through provider-based backends such as static, Nominatim, "
        "and generic HTTP JSON APIs."
    ),
    author="GeoChat Platform Team",
    permissions=["network"],
)
