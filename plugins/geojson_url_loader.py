"""
geojson_url_loader.py

GeoChat SDK Plugin
==================

Plugin ID:
    geojson_url_loader

Capability:
    - load_geojson_url:
        Download a GeoJSON document from an http(s) URL and return its features.

Connector:
    source_type ``geojson_url`` (vector, network access, credentials from profiles only).

Security (all of it comes from smart_spatial_system.shared.net_policy):
    - http(s) only, no credentials in the URL, only publicly routable addresses (SSRF guard),
      optional ``allowed_hosts`` allow-list;
    - redirects are never followed (a redirect could point at an internal address);
    - the response is streamed and aborted past ``max_response_bytes``;
    - the content type must be a JSON/GeoJSON type;
    - request headers (e.g. an Authorization token) come only from a server-side ``profile``,
      never from the caller;
    - ``allow_private_network`` is read from the plugin config only (deployer-trusted), never
      from arguments.

Config-aware behavior:
    Reads config/plugins/geojson_url_loader.yaml.
"""

from __future__ import annotations

from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from orchestrator.provider_error_mapping import redact_provider_error_message
from plugins._shared.plugin_config import get_profile_config
from plugins._shared.vector_common import load_config, to_float, to_int, utc_now_iso
from smart_spatial_system.shared.net_policy import (
    NetworkPolicyError,
    fetch_limited,
    redact_url,
)

PLUGIN_ID = "geojson_url_loader"

DEFAULT_CONTENT_TYPES = [
    "application/json", "application/geo+json", "application/vnd.geo+json", "text/json",
    "text/plain",
]
DEFAULTS = {"default_timeout_seconds": 30, "max_response_bytes": 50_000_000, "max_features": 200_000}
_GEOMETRY_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon",
    "GeometryCollection",
}


def _features_from(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("The response is not a GeoJSON object.")
    kind = document.get("type")
    if kind == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list):
            raise ValueError("FeatureCollection.features must be a list.")
        return [f for f in features if isinstance(f, dict)]
    if kind == "Feature":
        return [document]
    if kind in _GEOMETRY_TYPES:
        return [{"type": "Feature", "properties": {}, "geometry": document}]
    raise ValueError("The response is not GeoJSON (expected FeatureCollection, Feature or geometry).")


@capability(
    name="load_geojson_url",
    keywords=[
        "geojson url", "load from url", "download geojson", "remote geojson", "web geojson",
        "open data url",
        "آدرس geojson", "بارگذاری از url", "دانلود geojson", "داده از اینترنت", "لایه آنلاین",
    ],
    description=(
        "Download a GeoJSON document from an http(s) URL (public hosts only, no redirects, size "
        "limited) and return its features."
    ),
    required_inputs=["url"],
    optional_inputs=["profile", "timeout", "max_features", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "connector": {
            "source_type": "geojson_url",
            "kind": "vector",
            "access": ["network"],
            "credentials": "profile",
            "limit_param": "max_features",
        },
        "category": "data_io",
        "data_type": "vector",
        "operation": "load_geojson_url",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "network_fetch",
        "config_aware": True,
        "routable": True,
    },
)
def load_geojson_url(
    url: str,
    profile: str | None = None,
    timeout: float | None = None,
    max_features: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Load features from a GeoJSON URL.

    Args:
        url: http(s) URL of a GeoJSON document.
        profile: Name of a server-side profile (config ``profiles``) providing request headers.
        timeout: Seconds (default from config, max 120).
        max_features: Keep at most this many features (flagged ``truncated``). The configured
            ``max_features`` is a hard ceiling: bigger documents are refused.
        metadata: Extra output metadata.

    Returns:
        VectorOut (coordinates assumed WGS84 per the GeoJSON spec).
    """
    config = load_config(PLUGIN_ID)
    headers: dict[str, str] = {}
    if profile:
        profile_config = get_profile_config(PLUGIN_ID, profile, required=True)
        headers = {str(k): str(v) for k, v in (profile_config.get("headers") or {}).items()}

    seconds = to_float(timeout if timeout is not None else
                       config.get("default_timeout_seconds", DEFAULTS["default_timeout_seconds"]),
                       "timeout")
    if not 0 < seconds <= 120:
        raise ValueError("timeout must be between 0 and 120 seconds.")
    max_bytes = to_int(config.get("max_response_bytes", DEFAULTS["max_response_bytes"]),
                       "max_response_bytes")
    hard_cap = to_int(config.get("max_features", DEFAULTS["max_features"]), "max_features")
    keep = None
    if max_features is not None:
        keep = to_int(max_features, "max_features")
        if keep < 1:
            raise ValueError("max_features must be >= 1.")

    safe_url = redact_url(url) if isinstance(url, str) else "<url>"
    try:
        body, response = fetch_limited(
            url,
            headers=headers,
            timeout=seconds,
            max_bytes=max_bytes,
            allow_private_network=bool(config.get("allow_private_network", False)),
            allowed_hosts=config.get("allowed_hosts") or None,
            accept_content_types=config.get("accepted_content_types") or DEFAULT_CONTENT_TYPES,
        )
    except NetworkPolicyError as exc:
        raise ValueError(str(exc)) from None
    except Exception as exc:
        raise RuntimeError(
            redact_provider_error_message(f"Failed to fetch GeoJSON from {safe_url}: {type(exc).__name__}")
        ) from None

    import json

    try:
        features = _features_from(json.loads(body.decode("utf-8-sig")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"The response is not valid JSON: {exc.__class__.__name__}.") from None
    if len(features) > hard_cap:
        raise ValueError(f"The document has {len(features)} features; the limit is {hard_cap}.")
    truncated = keep is not None and len(features) > keep
    if truncated:
        features = features[:keep]

    return VectorOut(
        features=features,
        metadata={
            "source": PLUGIN_ID,
            "loader": PLUGIN_ID,
            "operation": "load_geojson_url",
            "url": safe_url,
            "crs": "EPSG:4326",
            "http_content_type": response["content_type"],
            "bytes": len(body),
            "feature_count": len(features),
            "truncated": truncated,
            "fetched_at": utc_now_iso(),
            **(metadata or {}),
        },
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="GeoJSON URL Loader",
    description="Loads GeoJSON from an http(s) URL under the shared outbound network policy.",
    author="GeoChat Platform Team",
    permissions=[],
)
