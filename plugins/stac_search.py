"""
stac_search.py

GeoChat SDK Plugin
==================

Plugin ID:
    stac_search

Capability:
    - search_stac:
        Search a STAC API (for example Earth Search / Planetary Computer style catalogues) for
        satellite scenes covering a bounding box and date range, and return the scene footprints
        as polygons with their key properties (date, cloud cover, platform) and asset links.

Connector:
    source_type ``stac_catalog`` (vector, network access, no caller credentials: the catalogue
    endpoint is deployer configuration).

Scope:
    This finds scenes; it does not download imagery. The ``assets`` property holds the links the
    caller asked for (``include_assets``), which a later step or the user can fetch.

Safety:
    The search is a GET request to the configured endpoint through the shared network policy
    (public address, no redirects, byte limit, JSON only). bbox, datetime, collection ids and limits
    are validated; one page only (``limit`` <= max_items) - ``has_more`` tells if there is more.
"""

from __future__ import annotations

import json
import re
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from orchestrator.provider_error_mapping import redact_provider_error_message
from plugins._shared.vector_common import (
    load_config,
    make_vector_out,
    to_float,
    to_int,
    utc_now_iso,
)
from smart_spatial_system.shared.net_policy import NetworkPolicyError, fetch_limited

PLUGIN_ID = "stac_search"

DEFAULTS = {
    "endpoint": "https://earth-search.aws.element84.com/v1/search",
    "timeout_seconds": 30,
    "max_response_bytes": 20_000_000,
    "max_items": 200,
    "max_bbox_degrees": 5.0,
}
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-:]{0,63}$")
_DATE = r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?"
_DATETIME_RE = re.compile(rf"^(?:{_DATE}|{_DATE}/{_DATE}|\.\.\/{_DATE}|{_DATE}/\.\.)$")


@capability(
    name="search_stac",
    keywords=[
        "stac", "satellite scenes", "sentinel", "landsat", "search imagery", "find satellite images",
        "earth observation catalog", "scene search",
        "جستجوی تصاویر ماهواره ای", "سنتینل", "لندست", "کاتالوگ تصاویر", "جستجوی صحنه", "استک",
    ],
    description=(
        "Search a STAC catalogue for satellite scenes over a bounding box and date range; returns "
        "footprints with date, cloud cover, platform and selected asset links."
    ),
    required_inputs=["bbox", "collections"],
    optional_inputs=["datetime", "max_cloud_cover", "limit", "include_assets", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "connector": {
            "source_type": "stac_catalog",
            "kind": "vector",
            "access": ["network"],
            "credentials": "none",
            "limit_param": "limit",
        },
        "category": "data_io",
        "data_type": "vector",
        "operation": "search_stac",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "network_fetch",
        "config_aware": True,
        "routable": True,
    },
)
def search_stac(
    bbox: list[float] | dict[str, float],
    collections: list[str] | str,
    datetime: str | None = None,
    max_cloud_cover: float | None = None,
    limit: int = 20,
    include_assets: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Search a STAC API.

    Args:
        bbox: [min_lon, min_lat, max_lon, max_lat] WGS84 (each side at most max_bbox_degrees).
        collections: Collection id(s), e.g. ``sentinel-2-l2a``, ``landsat-c2-l2``.
        datetime: RFC 3339 date/time or interval, e.g. ``2024-06-01/2024-06-30`` or
            ``2024-06-01T00:00:00Z/..``.
        max_cloud_cover: Keep scenes with ``eo:cloud_cover`` <= this percentage (scenes without the
            property are kept). Applied after the search, so fewer than ``limit`` scenes can come
            back even when ``has_more`` is true.
        limit: Maximum scenes to return (1..max_items); newest first as returned by the catalogue.
        include_assets: Asset keys whose links to include (e.g. ["visual", "thumbnail"]).
        metadata: Extra output metadata.

    Returns:
        VectorOut of scene footprints with ``id``, ``collection``, ``datetime``, ``cloud_cover``,
        ``platform``, ``assets`` ({key: href}) and ``asset_keys``; metadata has ``has_more``.
    """
    config = load_config(PLUGIN_ID)
    if isinstance(bbox, dict):
        try:
            bbox = [bbox["minx"], bbox["miny"], bbox["maxx"], bbox["maxy"]]
        except KeyError as exc:
            raise ValueError("bbox dict needs minx, miny, maxx, maxy.") from exc
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat].")
    west, south, east, north = (to_float(v, "bbox") for v in bbox)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must be WGS84 [min_lon, min_lat, max_lon, max_lat] with min < max.")
    side_limit = to_float(config.get("max_bbox_degrees", DEFAULTS["max_bbox_degrees"]), "max_bbox_degrees")
    if east - west > side_limit or north - south > side_limit:
        raise ValueError(f"bbox is too large: each side may span at most {side_limit} degrees.")

    ids = [collections] if isinstance(collections, str) else list(collections or [])
    if not ids or len(ids) > 10 or not all(isinstance(c, str) and _COLLECTION_RE.match(c) for c in ids):
        raise ValueError("collections must be 1-10 collection ids such as 'sentinel-2-l2a'.")
    if datetime is not None and (not isinstance(datetime, str) or not _DATETIME_RE.match(datetime)):
        raise ValueError("datetime must be RFC 3339, e.g. '2024-06-01' or '2024-06-01/2024-06-30'.")
    cloud = None
    if max_cloud_cover is not None:
        cloud = to_float(max_cloud_cover, "max_cloud_cover")
        if not 0 <= cloud <= 100:
            raise ValueError("max_cloud_cover must be between 0 and 100.")
    cap = to_int(config.get("max_items", DEFAULTS["max_items"]), "max_items")
    count = to_int(limit, "limit")
    if not 1 <= count <= cap:
        raise ValueError(f"limit must be between 1 and {cap}.")
    wanted_assets = list(include_assets or [])
    if len(wanted_assets) > 20 or not all(isinstance(a, str) and _ASSET_RE.match(a) for a in wanted_assets):
        raise ValueError("include_assets must be up to 20 asset keys such as 'visual'.")

    params = {"bbox": f"{west},{south},{east},{north}", "collections": ",".join(ids),
              # ask for one extra item so has_more is known without a second request
              "limit": count + 1}
    if datetime:
        params["datetime"] = datetime
    try:
        body, _ = fetch_limited(
            str(config.get("endpoint", DEFAULTS["endpoint"])),
            params=params,
            timeout=to_float(config.get("timeout_seconds", DEFAULTS["timeout_seconds"]), "timeout_seconds"),
            max_bytes=to_int(config.get("max_response_bytes", DEFAULTS["max_response_bytes"]), "max_response_bytes"),
            allow_private_network=bool(config.get("allow_private_network", False)),
            accept_content_types=["application/json", "application/geo+json"],
        )
    except NetworkPolicyError as exc:
        raise ValueError(str(exc)) from None
    except Exception as exc:
        raise RuntimeError(
            redact_provider_error_message(f"STAC search failed: {type(exc).__name__}")
        ) from None
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("The STAC server did not return valid JSON.") from None
    items = document.get("features") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise ValueError("Unexpected STAC response (no features list).")

    features: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("geometry"), dict):
            continue
        props = item.get("properties") or {}
        cloud_value = props.get("eo:cloud_cover")
        if cloud is not None and isinstance(cloud_value, (int, float)) and cloud_value > cloud:
            continue
        assets = item.get("assets") if isinstance(item.get("assets"), dict) else {}
        links = {k: assets[k].get("href") for k in wanted_assets
                 if k in assets and isinstance(assets[k], dict) and isinstance(assets[k].get("href"), str)}
        features.append({
            "type": "Feature",
            "geometry": item["geometry"],
            "properties": {
                "id": item.get("id"),
                "collection": item.get("collection"),
                "datetime": props.get("datetime"),
                "cloud_cover": cloud_value,
                "platform": props.get("platform"),
                "constellation": props.get("constellation"),
                "gsd": props.get("gsd"),
                "assets": links,
                "asset_keys": sorted(assets),
            },
        })
    has_more = len(items) > count  # the server returned the extra probe item
    features = features[:count]
    return make_vector_out(
        features, plugin_id=PLUGIN_ID, operation="search_stac",
        extra={"crs": "EPSG:4326", "bbox": [west, south, east, north], "collections": ids,
               "datetime": datetime, "max_cloud_cover": cloud, "has_more": has_more,
               "scene_count": len(features), "fetched_at": utc_now_iso()},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="STAC Search",
    description="Finds satellite scenes in a STAC catalogue and returns their footprints and links.",
    author="GeoChat Platform Team",
    permissions=[],
)
