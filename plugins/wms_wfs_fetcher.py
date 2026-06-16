"""
wms_wfs_fetcher.py

GeoChat SDK Plugin
==================

Plugin ID:
    wms_wfs_fetcher

Purpose:
    Fetch online geospatial data from OGC WFS and WMS services.

Capabilities:
    - fetch_wfs_features:
        Fetch vector features from WFS as VectorOut.

    - fetch_wms_map:
        Fetch WMS map image, save it locally and return RasterOut.

Config-aware behavior:
    The plugin can receive direct service URLs, or use config profiles:

        config/plugins/wms_wfs_fetcher.yaml

Example:
    fetch_wfs_features(service="geoserver_local", layer="roads")
    fetch_wms_map(service="geoserver_local", layer="roads", bbox=[...])
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.types.raster import RasterOut
from geochat_sdk.exceptions import SDKDependencyError

from plugins._shared.plugin_config import load_plugin_config, pick_first, resolve_env_refs


PLUGIN_ID = "wms_wfs_fetcher"

MAX_FEATURE_LIMIT = 100000
MAX_IMAGE_SIZE = 8192

_SAFE_LAYER_RE = re.compile(r"^[A-Za-z0-9_\-:., ]+$")


def _get_requests():
    """
    Lazy import requests.
    """
    try:
        import requests
    except ImportError as exc:
        raise SDKDependencyError(
            "The wms_wfs_fetcher plugin requires 'requests'. "
            "Install it with: pip install requests"
        ) from exc

    return requests


def _load_fetcher_config() -> dict[str, Any]:
    """
    Load config/plugins/wms_wfs_fetcher.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _get_service_config(config: dict[str, Any], service: str | None = None) -> tuple[str | None, dict[str, Any]]:
    """
    Return selected service name and service config.

    If service is None, config['default_service'] is used.
    If no config is available, returns (service, {}).
    """
    if not config:
        return service, {}

    services = config.get("services") or {}
    if not isinstance(services, dict):
        raise ValueError("services in wms_wfs_fetcher config must be a dict.")

    selected = service or config.get("default_service")

    if selected is None:
        return None, {}

    if selected not in services:
        raise ValueError(
            f"Service '{selected}' not found in wms_wfs_fetcher config. "
            f"Available services: {sorted(services.keys())}"
        )

    service_config = services[selected]
    if not isinstance(service_config, dict):
        raise ValueError(f"Service config '{selected}' must be a dict.")

    return str(selected), service_config


def _get_layer_config(service_config: dict[str, Any], layer: str | None) -> dict[str, Any]:
    """
    Return config for friendly layer alias.
    """
    if not layer:
        return {}

    layers = service_config.get("layers") or {}
    if not isinstance(layers, dict):
        raise ValueError("layers in service config must be a dict.")

    layer_config = layers.get(layer)
    if layer_config is None:
        return {}

    if not isinstance(layer_config, dict):
        raise ValueError(f"Layer config '{layer}' must be a dict.")

    return layer_config


def _validate_url(url: str, field_name: str = "base_url") -> str:
    """
    Validate HTTP/HTTPS service URL.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")

    url = url.strip()
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid http/https URL.")

    return url


def _validate_non_empty_string(value: str, field_name: str) -> str:
    """
    Validate required non-empty string.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")

    return value.strip()


def _validate_layer_name(value: str, field_name: str = "layer") -> str:
    """
    Validate WMS/WFS layer/type name.
    """
    value = _validate_non_empty_string(value, field_name)

    if not _SAFE_LAYER_RE.match(value):
        raise ValueError(
            f"Unsafe {field_name}: {value!r}. "
            "Only letters, numbers, underscore, dash, colon, comma, dot and spaces are allowed."
        )

    return value


def _to_int(value: Any, field_name: str) -> int:
    """
    Convert value to int or raise clear error.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")

    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc


def _validate_positive_int(value: Any, field_name: str, max_value: int | None = None) -> int:
    """
    Validate positive integer.
    """
    value = _to_int(value, field_name)

    if value <= 0:
        raise ValueError(f"{field_name} must be positive.")

    if max_value is not None and value > max_value:
        raise ValueError(f"{field_name} is too large. Maximum allowed value is {max_value}.")

    return value


def _validate_non_negative_int(value: Any, field_name: str, max_value: int | None = None) -> int:
    """
    Validate non-negative integer.
    """
    value = _to_int(value, field_name)

    if value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0.")

    if max_value is not None and value > max_value:
        raise ValueError(f"{field_name} is too large. Maximum allowed value is {max_value}.")

    return value


def _bbox_to_string(bbox: str | list[float] | tuple[float, ...]) -> str:
    """
    Convert bbox to comma-separated string.

    Accepts:
        - "minx,miny,maxx,maxy"
        - [minx, miny, maxx, maxy]
        - (minx, miny, maxx, maxy)
    """
    if isinstance(bbox, str):
        cleaned = bbox.strip()
        if not cleaned:
            raise ValueError("bbox must not be empty.")

        parts = [p.strip() for p in cleaned.split(",")]
        if len(parts) != 4:
            raise ValueError("bbox string must contain four comma-separated numbers.")

        try:
            [float(p) for p in parts]
        except Exception as exc:
            raise ValueError("bbox string must contain numeric values.") from exc

        return ",".join(parts)

    if isinstance(bbox, (list, tuple)):
        if len(bbox) != 4:
            raise ValueError("bbox must contain exactly four numbers.")

        try:
            values = [float(x) for x in bbox]
        except Exception as exc:
            raise ValueError("bbox must contain numeric values.") from exc

        return ",".join(str(x) for x in values)

    raise ValueError("bbox must be a string, list or tuple.")


def _bbox_to_dict_or_none(
    bbox: str | list[float] | tuple[float, ...] | None,
) -> dict[str, float] | None:
    """
    Convert bbox to metadata dict.
    """
    if bbox is None:
        return None

    bbox_str = _bbox_to_string(bbox)
    values = [float(x) for x in bbox_str.split(",")]

    return {
        "minx": values[0],
        "miny": values[1],
        "maxx": values[2],
        "maxy": values[3],
    }


def _is_number(value: Any) -> bool:
    """
    Return True if value is int/float but not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox from GeoJSON geometry.
    """
    if not geometry:
        return None

    coords = geometry.get("coordinates")
    if coords is None:
        return None

    xs: list[float] = []
    ys: list[float] = []

    def walk(obj: Any) -> None:
        if (
            isinstance(obj, (list, tuple))
            and len(obj) >= 2
            and _is_number(obj[0])
            and _is_number(obj[1])
        ):
            xs.append(float(obj[0]))
            ys.append(float(obj[1]))
            return

        if isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(coords)

    if not xs or not ys:
        return None

    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_bboxes(bboxes: list[list[float]]) -> dict[str, float] | None:
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


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize GeoJSON Feature.
    """
    if not isinstance(feature, dict):
        raise ValueError(f"Feature at index {index} must be an object.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if properties is None:
        properties = {}

    if not isinstance(properties, dict):
        raise ValueError(f"Feature properties at index {index} must be object or null.")

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": properties,
    }


def _normalize_geojson_response(
    data: Any,
    max_features: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Normalize WFS GeoJSON response.
    """
    if not isinstance(data, dict):
        raise ValueError("WFS response must be a JSON object.")

    geojson_type = data.get("type")

    if geojson_type == "FeatureCollection":
        raw_features = data.get("features", [])
        if not isinstance(raw_features, list):
            raise ValueError("FeatureCollection.features must be a list.")
    elif geojson_type == "Feature":
        raw_features = [data]
    else:
        raise ValueError("Unsupported WFS response. Expected GeoJSON FeatureCollection or Feature.")

    raw_features = raw_features[:max_features]

    features: list[dict[str, Any]] = []
    geometry_types: dict[str, int] = {}
    bboxes: list[list[float]] = []

    for idx, raw in enumerate(raw_features):
        feature = _normalize_feature(raw, idx)
        features.append(feature)

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

    metadata = {
        "geojson_type": geojson_type,
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": _merge_bboxes(bboxes),
    }

    return features, metadata


def _http_get_json(base_url: str, params: dict[str, Any], timeout: int) -> Any:
    """
    HTTP GET and parse JSON.
    """
    requests = _get_requests()

    try:
        response = requests.get(base_url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch JSON from WFS service. Error: {exc}") from exc


def _http_get_bytes(base_url: str, params: dict[str, Any], timeout: int) -> tuple[bytes, dict[str, Any]]:
    """
    HTTP GET and return bytes + response metadata.
    """
    requests = _get_requests()

    try:
        response = requests.get(base_url, params=params, timeout=timeout)
        response.raise_for_status()

        content = response.content
        if not isinstance(content, (bytes, bytearray)) or len(content) == 0:
            raise RuntimeError("Empty binary response from WMS service.")

        headers = dict(getattr(response, "headers", {}) or {})
        content_type = headers.get("Content-Type") or headers.get("content-type")

        prefix = bytes(content[:200]).lower()
        if b"serviceexception" in prefix or b"exceptionreport" in prefix:
            raise RuntimeError("WMS service returned an OGC exception response.")

        return bytes(content), {
            "content_type": content_type,
            "url": getattr(response, "url", None),
            "headers": headers,
        }

    except Exception as exc:
        raise RuntimeError(f"Failed to fetch bytes from WMS service. Error: {exc}") from exc


def _build_wfs_params(
    *,
    type_name: str,
    version: str,
    output_format: str,
    srs_name: str | None,
    bbox: str | list[float] | tuple[float, ...] | None,
    max_features: int,
    property_name: str | None,
    extra_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build WFS GetFeature parameters.
    """
    type_name = _validate_layer_name(type_name, "type_name")
    max_features = _validate_non_negative_int(max_features, "max_features", MAX_FEATURE_LIMIT)

    params: dict[str, Any] = {
        "service": "WFS",
        "request": "GetFeature",
        "version": version,
        "outputFormat": output_format,
    }

    if str(version).startswith("2."):
        params["typeNames"] = type_name
        params["count"] = max_features
    else:
        params["typeName"] = type_name
        params["maxFeatures"] = max_features

    if srs_name:
        params["srsName"] = srs_name

    if bbox is not None:
        params["bbox"] = _bbox_to_string(bbox)

    if property_name:
        params["propertyName"] = property_name

    if extra_params:
        if not isinstance(extra_params, dict):
            raise ValueError("extra_params must be a dict or None.")
        params.update(extra_params)

    return params


def _build_wms_params(
    *,
    layers: str,
    bbox: str | list[float] | tuple[float, ...],
    width: int,
    height: int,
    crs: str,
    version: str,
    styles: str,
    image_format: str,
    transparent: bool,
    extra_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build WMS GetMap parameters.
    """
    layers = _validate_layer_name(layers, "layers")
    width = _validate_positive_int(width, "width", MAX_IMAGE_SIZE)
    height = _validate_positive_int(height, "height", MAX_IMAGE_SIZE)

    params: dict[str, Any] = {
        "service": "WMS",
        "request": "GetMap",
        "version": version,
        "layers": layers,
        "styles": styles,
        "bbox": _bbox_to_string(bbox),
        "width": width,
        "height": height,
        "format": image_format,
        "transparent": "TRUE" if transparent else "FALSE",
    }

    if str(version).startswith("1.3"):
        params["crs"] = crs
    else:
        params["srs"] = crs

    if extra_params:
        if not isinstance(extra_params, dict):
            raise ValueError("extra_params must be a dict or None.")
        params.update(extra_params)

    return params


def _extension_from_image_format(image_format: str) -> str:
    """
    Choose output file extension from WMS image format.
    """
    fmt = image_format.lower()

    if "geotiff" in fmt or "tiff" in fmt:
        return ".tif"
    if "png" in fmt:
        return ".png"
    if "jpeg" in fmt or "jpg" in fmt:
        return ".jpg"

    return ".bin"


def _safe_filename_part(value: str) -> str:
    """
    Sanitize layer name for file names.
    """
    value = value.strip().replace(":", "_").replace(",", "_").replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9_\-\.]+", "_", value)
    return value[:80] or "layer"


@capability(
    name="fetch_wfs_features",
    keywords=[
        "wfs",
        "web feature service",
        "ogc wfs",
        "online vector",
        "fetch wfs",
        "load wfs",
        "read wfs",
        "remote vector",
        "سرویس عارضه",
        "دبلیو اف اس",
        "لایه آنلاین برداری",
        "داده برداری آنلاین",
        "واکشی wfs",
        "خواندن wfs",
    ],
    description="Fetch vector features from an OGC WFS service and return VectorOut.",
    required_inputs=[],
    optional_inputs=[
        "service",
        "base_url",
        "type_name",
        "layer",
        "version",
        "output_format",
        "srs_name",
        "bbox",
        "max_features",
        "property_name",
        "extra_params",
        "timeout",
    ],
    output_kind="vector",
    permissions=["network"],
    metadata={
        "category": "data_io",
        "data_type": "vector",
        "source_type": "wfs",
        "source_priority": 3,
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "read_online_vector_service",
        "config_aware": True,
        "supports_services": True,
        "routable": True,
    },
)
def fetch_wfs_features(
    service: str | None = None,
    base_url: str | None = None,
    type_name: str | None = None,
    layer: str | None = None,
    version: str | None = None,
    output_format: str | None = None,
    srs_name: str | None = None,
    bbox: str | list[float] | tuple[float, ...] | None = None,
    max_features: int | None = None,
    property_name: str | None = None,
    extra_params: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> VectorOut:
    """
    Fetch GeoJSON features from WFS.

    Can be used with direct base_url/type_name or config service/layer alias.
    """
    config = _load_fetcher_config()
    selected_service, service_config = _get_service_config(config, service)
    layer_config = _get_layer_config(service_config, layer)

    final_base_url = pick_first(
        base_url,
        service_config.get("wfs_url"),
        default=None,
    )

    if not final_base_url:
        raise ValueError("base_url or config service.wfs_url must be provided.")

    final_type_name = pick_first(
        type_name,
        layer_config.get("type_name"),
        layer,
        default=None,
    )

    if not final_type_name:
        raise ValueError("type_name or layer must be provided.")

    final_version = pick_first(
        version,
        service_config.get("default_wfs_version"),
        default="2.0.0",
    )

    final_output_format = pick_first(
        output_format,
        service_config.get("default_wfs_output_format"),
        default="application/json",
    )

    final_srs_name = pick_first(
        srs_name,
        service_config.get("default_crs"),
        default="EPSG:4326",
    )

    final_max_features = pick_first(
        max_features,
        service_config.get("default_max_features"),
        default=1000,
    )

    final_timeout = pick_first(
        timeout,
        service_config.get("timeout"),
        default=30,
    )

    final_base_url = _validate_url(str(final_base_url))
    final_timeout = _validate_positive_int(final_timeout, "timeout", 3600)
    final_max_features = _validate_non_negative_int(final_max_features, "max_features", MAX_FEATURE_LIMIT)

    params = _build_wfs_params(
        type_name=str(final_type_name),
        version=str(final_version),
        output_format=str(final_output_format),
        srs_name=str(final_srs_name) if final_srs_name else None,
        bbox=bbox,
        max_features=final_max_features,
        property_name=property_name,
        extra_params=extra_params,
    )

    data = _http_get_json(final_base_url, params=params, timeout=final_timeout)
    features, extracted = _normalize_geojson_response(data, max_features=final_max_features)

    metadata: dict[str, Any] = {
        "source": "wfs",
        "loader": PLUGIN_ID,
        "format": "geojson_features",
        "service": selected_service,
        "base_url": final_base_url,
        "type_name": str(final_type_name),
        "layer_alias": layer,
        "version": str(final_version),
        "output_format": str(final_output_format),
        "srs_name": str(final_srs_name) if final_srs_name else None,
        "bbox_filter": _bbox_to_dict_or_none(bbox),
        "max_features": final_max_features,
        **extracted,
    }

    return VectorOut(features=features, metadata=metadata)


@capability(
    name="fetch_wms_map",
    keywords=[
        "wms",
        "web map service",
        "ogc wms",
        "online raster",
        "online map",
        "fetch wms",
        "load wms",
        "read wms",
        "remote map",
        "سرویس نقشه",
        "دبلیو ام اس",
        "لایه آنلاین رستری",
        "نقشه آنلاین",
        "واکشی wms",
        "خواندن wms",
    ],
    description="Fetch a map image from an OGC WMS service, save it locally and return RasterOut.",
    required_inputs=["bbox"],
    optional_inputs=[
        "service",
        "base_url",
        "layer",
        "layers",
        "output_path",
        "width",
        "height",
        "crs",
        "version",
        "styles",
        "image_format",
        "transparent",
        "extra_params",
        "timeout",
    ],
    output_kind="raster",
    permissions=["network", "filesystem"],
    metadata={
        "category": "data_io",
        "data_type": "raster",
        "source_type": "wms",
        "source_priority": 3,
        "returns": "RasterOut",
        "artifact_kind": "raster_ref",
        "access_scope": "read_online_map_service",
        "config_aware": True,
        "supports_services": True,
        "routable": True,
    },
)
def fetch_wms_map(
    bbox: str | list[float] | tuple[float, ...],
    service: str | None = None,
    base_url: str | None = None,
    layer: str | None = None,
    layers: str | None = None,
    output_path: str | None = None,
    width: int | None = None,
    height: int | None = None,
    crs: str | None = None,
    version: str | None = None,
    styles: str | None = None,
    image_format: str | None = None,
    transparent: bool = True,
    extra_params: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> RasterOut:
    """
    Fetch map image from WMS and save it as a local file.
    """
    config = _load_fetcher_config()
    selected_service, service_config = _get_service_config(config, service)
    layer_config = _get_layer_config(service_config, layer)

    final_base_url = pick_first(
        base_url,
        service_config.get("wms_url"),
        default=None,
    )

    if not final_base_url:
        raise ValueError("base_url or config service.wms_url must be provided.")

    final_layers = pick_first(
        layers,
        layer_config.get("wms_layer"),
        layer_config.get("type_name"),
        layer,
        default=None,
    )

    if not final_layers:
        raise ValueError("layers or layer must be provided.")

    final_width = pick_first(
        width,
        service_config.get("default_width"),
        default=1024,
    )

    final_height = pick_first(
        height,
        service_config.get("default_height"),
        default=1024,
    )

    final_crs = pick_first(
        crs,
        service_config.get("default_crs"),
        default="EPSG:4326",
    )

    final_version = pick_first(
        version,
        service_config.get("default_wms_version"),
        default="1.3.0",
    )

    final_styles = pick_first(
        styles,
        layer_config.get("style"),
        service_config.get("default_style"),
        default="",
    )

    final_image_format = pick_first(
        image_format,
        service_config.get("default_wms_image_format"),
        default="image/geotiff",
    )

    final_timeout = pick_first(
        timeout,
        service_config.get("timeout"),
        default=30,
    )

    final_base_url = _validate_url(str(final_base_url))
    final_timeout = _validate_positive_int(final_timeout, "timeout", 3600)

    params = _build_wms_params(
        layers=str(final_layers),
        bbox=bbox,
        width=final_width,
        height=final_height,
        crs=str(final_crs),
        version=str(final_version),
        styles=str(final_styles),
        image_format=str(final_image_format),
        transparent=transparent,
        extra_params=extra_params,
    )

    content, response_meta = _http_get_bytes(final_base_url, params=params, timeout=final_timeout)

    if output_path is None:
        output_dir = service_config.get("output_dir") or "output/plugins/wms_wfs_fetcher"
        ext = _extension_from_image_format(str(final_image_format))
        file_name = f"wms_{_safe_filename_part(str(final_layers))}{ext}"
        output_path = str(Path(output_dir) / file_name)

    out_path = Path(output_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)

    metadata: dict[str, Any] = {
        "source": "wms",
        "loader": PLUGIN_ID,
        "format": str(final_image_format),
        "service": selected_service,
        "base_url": final_base_url,
        "layer_alias": layer,
        "layers": str(final_layers),
        "bbox": _bbox_to_dict_or_none(bbox),
        "width": int(final_width),
        "height": int(final_height),
        "crs": str(final_crs),
        "version": str(final_version),
        "styles": str(final_styles),
        "transparent": bool(transparent),
        "file_size_bytes": int(out_path.stat().st_size),
        "content_type": response_meta.get("content_type"),
        "response_url": response_meta.get("url"),
    }

    return RasterOut(path=str(out_path), metadata=metadata)


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="WMS/WFS Fetcher",
    description=(
        "Fetches online geospatial data from OGC WFS and WMS services and exposes "
        "them as VectorOut or RasterOut artifacts. Supports config services and layer aliases."
    ),
    author="GeoChat Platform Team",
    permissions=["network", "filesystem"],
)
