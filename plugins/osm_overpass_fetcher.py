"""
osm_overpass_fetcher.py

GeoChat SDK Plugin
==================

Plugin ID:
    osm_overpass_fetcher

Capability:
    - fetch_osm_features:
        Download OpenStreetMap features inside a bounding box from an Overpass API server,
        filtered by tags, as GeoJSON points/lines/polygons.

Connector:
    source_type ``osm_overpass`` (vector, network access, no caller credentials: the server
    endpoint comes from config only).

Design decisions (docs/CONNECTOR_REGISTRY_DESIGN.md):
    - **Structured filters only.** Callers give ``tags`` such as ``{"amenity": "hospital"}``;
      the plugin builds the Overpass QL itself and validates every key/value against a strict
      pattern, so a caller can never inject QL. Raw Overpass QL is not accepted.
    - The bbox is mandatory and bounded (``max_bbox_degrees`` per side, ``max_bbox_area``);
      the result is capped (``max_features``); the request goes through the shared network
      policy (public address, no redirects, byte limit, JSON only).
    - The Overpass endpoint is deployer configuration, never a request argument.
    - Output carries the ODbL attribution in metadata.

Limits of this version:
    ways become LineString / Polygon (closed ways with area-like tags); relations (e.g.
    multipolygons) are counted in ``skipped_relations`` and not assembled.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from orchestrator.provider_error_mapping import redact_provider_error_message
from plugins._shared.vector_common import (
    load_config,
    to_float,
    to_int,
    utc_now_iso,
    validate_choice,
)
from smart_spatial_system.shared.net_policy import NetworkPolicyError, fetch_limited

PLUGIN_ID = "osm_overpass_fetcher"

ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"
DEFAULTS = {
    "endpoint": "https://overpass-api.de/api/interpreter",
    "timeout_seconds": 30,
    "max_response_bytes": 50_000_000,
    "max_features": 20_000,
    "max_bbox_degrees": 0.5,
    "max_bbox_area": 0.25,
    "min_interval_seconds": 1.0,
}
ELEMENT_TYPES = {"node", "way"}
_TAG_RE = re.compile(r"^[A-Za-z0-9_:.\- ]{1,64}$")
_VALUE_RE = re.compile(r"^[^\"\\\n\r\x00]{1,128}$")  # any text without quote, backslash, control
_ALTERNATE_RE = re.compile(r"^[A-Za-z0-9_ :-]{1,64}$")  # several values become a regex: plain only
_AREA_KEYS = {"building", "landuse", "natural", "leisure", "amenity", "area", "shop", "tourism",
              "boundary", "place"}
_LINEAR_VALUES = {("natural", "coastline"), ("area", "no")}
_last_call = 0.0
_lock = threading.Lock()


def _bbox(value: Any, config: dict[str, Any]) -> tuple[float, float, float, float]:
    if isinstance(value, dict):
        try:
            value = [value["minx"], value["miny"], value["maxx"], value["maxy"]]
        except KeyError as exc:
            raise ValueError("bbox dict needs minx, miny, maxx, maxy.") from exc
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox is required: [min_lon, min_lat, max_lon, max_lat].")
    west, south, east, north = (to_float(v, "bbox") for v in value)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must be WGS84 [min_lon, min_lat, max_lon, max_lat] with min < max.")
    side_limit = to_float(config.get("max_bbox_degrees", DEFAULTS["max_bbox_degrees"]), "max_bbox_degrees")
    area_limit = to_float(config.get("max_bbox_area", DEFAULTS["max_bbox_area"]), "max_bbox_area")
    if east - west > side_limit or north - south > side_limit:
        raise ValueError(f"bbox is too large: each side may span at most {side_limit} degrees.")
    if (east - west) * (north - south) > area_limit:
        raise ValueError(f"bbox is too large: the area may be at most {area_limit} square degrees.")
    return west, south, east, north


def _filters(tags: Any) -> list[tuple[str, list[str] | None]]:
    """Validate ``tags`` -> [(key, [values] | None for 'any value')]."""
    if not isinstance(tags, dict) or not tags:
        raise ValueError('tags is required, e.g. {"amenity": "hospital"} or {"highway": ["primary", "secondary"]}.')
    if len(tags) > 8:
        raise ValueError("At most 8 tag keys are allowed.")
    filters: list[tuple[str, list[str] | None]] = []
    for key, value in tags.items():
        if not isinstance(key, str) or not _TAG_RE.match(key):
            raise ValueError(f"Invalid tag key: {key!r}.")
        if value is True or value == "*":
            filters.append((key, None))
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        if not values or len(values) > 20:
            raise ValueError(f"Tag '{key}' needs between 1 and 20 values.")
        clean = []
        for item in values:
            if not isinstance(item, str) or not _VALUE_RE.match(item):
                raise ValueError(f"Invalid value for tag '{key}': {item!r}.")
            if len(values) > 1 and not _ALTERNATE_RE.match(item):
                raise ValueError(
                    f"Value {item!r} of tag '{key}' has special characters; "
                    "a list of values may only use letters, digits, space, _ : and -."
                )
            clean.append(item)
        filters.append((key, clean))
    return filters


def build_query(bbox: tuple[float, float, float, float], filters, element_types: list[str],
                limit: int, timeout: int) -> str:
    west, south, east, north = bbox
    area = f"({south:.7f},{west:.7f},{north:.7f},{east:.7f})"
    parts = []
    for key, values in filters:
        if values is None:
            clause = f'["{key}"]'
        elif len(values) == 1:
            clause = f'["{key}"="{values[0]}"]'
        else:
            alternation = "|".join(values)
            clause = f'["{key}"~"^({alternation})$"]'
        for element in element_types:
            parts.append(f"{element}{clause}{area};")
    return f"[out:json][timeout:{timeout}];({''.join(parts)});out geom {limit};"


def _is_area(tags: dict[str, Any]) -> bool:
    if tags.get("area") == "no":
        return False
    return any(key in tags and (key, tags[key]) not in _LINEAR_VALUES for key in _AREA_KEYS) or \
        tags.get("area") == "yes"


def to_features(elements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    features: list[dict[str, Any]] = []
    skipped_relations = 0
    for element in elements:
        kind = element.get("type")
        tags = element.get("tags") or {}
        properties = {**tags, "osm_type": kind, "osm_id": element.get("id")}
        if kind == "node" and "lon" in element and "lat" in element:
            geometry: dict[str, Any] | None = {
                "type": "Point", "coordinates": [element["lon"], element["lat"]]}
        elif kind == "way":
            coords = [[p["lon"], p["lat"]] for p in element.get("geometry") or [] if p]
            if len(coords) < 2:
                continue
            if len(coords) >= 4 and coords[0] == coords[-1] and _is_area(tags):
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else:
                geometry = {"type": "LineString", "coordinates": coords}
        elif kind == "relation":
            skipped_relations += 1
            continue
        else:
            continue
        features.append({"type": "Feature", "geometry": geometry, "properties": properties})
    return features, skipped_relations


def _throttle(seconds: float) -> None:
    """Be a polite client: at most one request per ``seconds`` per process."""
    global _last_call
    if seconds <= 0:
        return
    with _lock:
        wait = _last_call + seconds - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


@capability(
    name="fetch_osm_features",
    keywords=[
        "openstreetmap", "osm", "overpass", "download osm", "osm features", "amenities", "points of interest",
        "poi", "roads from osm", "buildings from osm",
        "اوپن استریت مپ", "داده های osm", "نقاط مورد علاقه", "بیمارستان ها از osm", "خیابان ها از osm",
        "ساختمان ها از osm",
    ],
    description=(
        "Download OpenStreetMap features inside a bounding box filtered by tags "
        "(e.g. amenity=hospital) via an Overpass server. Structured tag filters only; "
        "the bbox size and result size are bounded."
    ),
    required_inputs=["bbox", "tags"],
    optional_inputs=["element_types", "max_features", "timeout", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "connector": {
            "source_type": "osm_overpass",
            "kind": "vector",
            "access": ["network"],
            "credentials": "none",
            "limit_param": "max_features",
        },
        "category": "data_io",
        "data_type": "vector",
        "operation": "fetch_osm_features",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "network_fetch",
        "config_aware": True,
        "routable": True,
    },
)
def fetch_osm_features(
    bbox: list[float] | dict[str, float],
    tags: dict[str, Any],
    element_types: list[str] | None = None,
    max_features: int | None = None,
    timeout: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Fetch OSM features.

    Args:
        bbox: [min_lon, min_lat, max_lon, max_lat] in WGS84 (required, size-bounded).
        tags: ``{key: value | [values] | true}``; ``true`` means any value. All keys are OR-ed
            (an element matching any filter is returned).
        element_types: Subset of ``["node", "way"]`` (default both).
        max_features: Return at most this many features (flagged ``truncated``).
        timeout: Overpass query timeout in seconds (default from config, max 120).
        metadata: Extra output metadata.

    Returns:
        VectorOut (WGS84) with OSM tags as properties plus ``osm_type`` and ``osm_id``.
    """
    config = load_config(PLUGIN_ID)
    box = _bbox(bbox, config)
    filters = _filters(tags)
    types = list(element_types or ["node", "way"])
    types = [validate_choice(t, ELEMENT_TYPES, "element_types") for t in types]
    hard_cap = to_int(config.get("max_features", DEFAULTS["max_features"]), "max_features")
    limit = hard_cap
    if max_features is not None:
        limit = min(hard_cap, to_int(max_features, "max_features"))
        if limit < 1:
            raise ValueError("max_features must be >= 1.")
    seconds = to_int(timeout if timeout is not None else
                     config.get("timeout_seconds", DEFAULTS["timeout_seconds"]), "timeout")
    if not 1 <= seconds <= 120:
        raise ValueError("timeout must be between 1 and 120 seconds.")

    query = build_query(box, filters, types, limit + 1, seconds)  # +1 detects truncation
    endpoint = str(config.get("endpoint", DEFAULTS["endpoint"]))
    _throttle(to_float(config.get("min_interval_seconds", DEFAULTS["min_interval_seconds"]),
                       "min_interval_seconds"))
    try:
        body, _ = fetch_limited(
            endpoint,
            params={"data": query},
            timeout=seconds + 5,
            max_bytes=to_int(config.get("max_response_bytes", DEFAULTS["max_response_bytes"]),
                             "max_response_bytes"),
            allow_private_network=bool(config.get("allow_private_network", False)),
            accept_content_types=["application/json", "application/osm3s+json"],
        )
    except NetworkPolicyError as exc:
        raise ValueError(str(exc)) from None
    except Exception as exc:
        raise RuntimeError(
            redact_provider_error_message(f"Overpass request failed: {type(exc).__name__}")
        ) from None

    import json

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("The Overpass server did not return valid JSON.") from None
    if not isinstance(document, dict) or not isinstance(document.get("elements"), list):
        raise ValueError("Unexpected Overpass response.")
    remark = document.get("remark")
    if isinstance(remark, str) and "error" in remark.lower():
        raise ValueError(f"Overpass reported: {remark[:200]}")

    features, skipped_relations = to_features(document["elements"])
    truncated = len(features) > limit
    features = features[:limit]
    return VectorOut(
        features=features,
        metadata={
            "source": PLUGIN_ID,
            "loader": PLUGIN_ID,
            "operation": "fetch_osm_features",
            "crs": "EPSG:4326",
            "bbox": list(box),
            "tags": {k: v for k, v in filters},
            "element_types": types,
            "feature_count": len(features),
            "truncated": truncated,
            "skipped_relations": skipped_relations,
            "attribution": ATTRIBUTION,
            "fetched_at": utc_now_iso(),
            **(metadata or {}),
        },
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="OSM Overpass Fetcher",
    description="Fetches OpenStreetMap features by bbox and tags via Overpass (structured filters only).",
    author="GeoChat Platform Team",
    permissions=[],
)
