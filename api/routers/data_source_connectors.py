from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from api.support import (
    http_error_detail as _http_error_detail,
    json_safe as _json_safe,
    service as _service,
)
from orchestrator.service import OrchestratorServiceError


router = APIRouter()


def _resolve_service_capability(
    svc: Any,
    candidate_names: tuple[str, ...],
) -> Any:
    """
    Resolve a callable capability through the service registry.

    API routers must not import concrete plugin implementation modules.
    They should go through the service/registry/capability boundary.
    """
    registry = getattr(svc, "registry", None)
    if registry is None:
        raise HTTPException(
            status_code=400,
            detail="Capability registry is not available.",
        )

    last_error: Exception | None = None

    for capability_name in candidate_names:
        try:
            assert_enabled = getattr(svc, "_assert_capability_enabled", None)
            if callable(assert_enabled):
                assert_enabled(capability_name)

            capability = registry.resolve(capability_name)
            if callable(capability):
                return capability

        except Exception as exc:
            last_error = exc
            continue

    detail = "Capability is not available: " + ", ".join(candidate_names)
    if last_error is not None:
        detail = f"{detail}. Last error: {last_error}"

    raise HTTPException(
        status_code=400,
        detail=detail,
    )


@router.post("/data-sources/csv-table")
def register_csv_table_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.register_csv_table_source(payload))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.post("/data-sources/wms")
def register_wms_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    svc = _service(request)

    try:
        return _json_safe(svc.register_wms_source(payload))
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc


@router.post("/data-sources/postgis")
def register_postgis_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Connect to PostGIS, fetch a spatial table and register as a data source.

    Expected body:
        {
            "project_id": "optional",
            "display_name": "optional",
            "table": "roads",
            "schema": "public",
            "geom_col": "geom",
            "where": "optional SQL filter",
            "limit": 1000,
            "output_srid": 4326,
            "dsn": "postgresql://user:pass@host/db",
            "host": "localhost",
            "port": 5432,
            "database": "gis",
            "user": "postgres",
            "password": "secret",
            "profile": "optional config profile"
        }
    """
    svc = _service(request)

    table = payload.get("table")
    if not isinstance(table, str) or not table.strip():
        raise HTTPException(
            status_code=400,
            detail="'table' must be a non-empty string.",
        )

    try:
        fetch_postgis_layer = _resolve_service_capability(
            svc,
            (
                "fetch_postgis_layer",
                "query_database_postgis",
                "load_postgis_layer",
            ),
        )

        result = fetch_postgis_layer(
            table=str(table).strip(),
            profile=payload.get("profile"),
            dsn=payload.get("dsn"),
            schema=payload.get("schema"),
            geom_col=payload.get("geom_col"),
            where=payload.get("where"),
            limit=payload.get("limit"),
            output_srid=payload.get("output_srid"),
            host=payload.get("host"),
            port=payload.get("port"),
            database=payload.get("database"),
            user=payload.get("user"),
            password=payload.get("password"),
            connect_timeout=payload.get("connect_timeout"),
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"PostGIS fetch failed: {exc}",
        ) from exc

    import json as _json

    features = getattr(result, "features", None) or []
    metadata = getattr(result, "metadata", None) or {}

    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": metadata,
    }

    display_name = (
        payload.get("display_name")
        or f"{payload.get('schema', 'public')}.{table}"
    )

    content = _json.dumps(geojson, ensure_ascii=False).encode("utf-8")

    try:
        upload = svc.save_upload(
            filename=f"{table}.geojson",
            content=content,
            content_type="application/geo+json",
            kind="vector",
            user_context={
                "source": "postgis",
                "source_type": "postgis",
                "display_name": display_name,
                "table": table,
                "schema": payload.get("schema", "public"),
                "postgis_metadata": metadata,
            },
            project_id=payload.get("project_id"),
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc

    return _json_safe({
        **upload,
        "source_type": "postgis",
        "feature_count": len(features),
        "postgis_metadata": metadata,
    })


@router.post("/data-sources/wfs")
def register_wfs_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Fetch features from WFS service and register as a data source.

    Expected body:
        {
            "project_id": "optional",
            "display_name": "optional",
            "base_url": "https://...",
            "type_name": "layer:name",
            "layer": "alias from config",
            "service": "config profile name",
            "version": "2.0.0",
            "output_format": "application/json",
            "srs_name": "EPSG:4326",
            "bbox": [minx, miny, maxx, maxy],
            "max_features": 1000,
            "timeout": 30
        }
    """
    svc = _service(request)

    base_url = payload.get("base_url")
    type_name = payload.get("type_name") or payload.get("layer")

    if not payload.get("service") and (
        not isinstance(base_url, str) or not base_url.strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="'base_url' or 'service' must be provided.",
        )

    if not payload.get("service") and (
        not isinstance(type_name, str) or not str(type_name).strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="'type_name' or 'layer' must be provided.",
        )

    try:
        fetch_wfs_features = _resolve_service_capability(
            svc,
            (
                "fetch_wfs_features",
                "load_wfs_features",
                "load_wfs_layer",
            ),
        )

        result = fetch_wfs_features(
            service=payload.get("service"),
            base_url=base_url,
            type_name=payload.get("type_name"),
            layer=payload.get("layer"),
            version=payload.get("version"),
            output_format=payload.get("output_format"),
            srs_name=payload.get("srs_name"),
            bbox=payload.get("bbox"),
            max_features=payload.get("max_features"),
            property_name=payload.get("property_name"),
            timeout=payload.get("timeout"),
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"WFS fetch failed: {exc}",
        ) from exc

    import json as _json

    features = getattr(result, "features", None) or []
    metadata = getattr(result, "metadata", None) or {}

    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": metadata,
    }

    display_name = (
        payload.get("display_name")
        or payload.get("type_name")
        or payload.get("layer")
        or "wfs_layer"
    )

    safe_name = str(display_name).replace(":", "_").replace("/", "_")
    content = _json.dumps(geojson, ensure_ascii=False).encode("utf-8")

    try:
        upload = svc.save_upload(
            filename=f"{safe_name}.geojson",
            content=content,
            content_type="application/geo+json",
            kind="vector",
            user_context={
                "source": "wfs",
                "source_type": "wfs",
                "display_name": display_name,
                "base_url": base_url,
                "type_name": type_name,
                "wfs_metadata": metadata,
            },
            project_id=payload.get("project_id"),
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc

    return _json_safe({
        **upload,
        "source_type": "wfs",
        "feature_count": len(features),
        "wfs_metadata": metadata,
    })


@router.post("/data-sources/url")
def register_url_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Fetch GeoJSON from a URL and register as a data source.

    Expected body:
        {
            "project_id": "optional",
            "display_name": "optional",
            "url": "https://example.com/data.geojson",
            "kind": "vector",
            "timeout": 30,
            "headers": {"Authorization": "Bearer ..."}
        }
    """
    svc = _service(request)

    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(
            status_code=400,
            detail="'url' must be a non-empty string.",
        )

    url = url.strip()
    timeout = int(payload.get("timeout") or 30)
    kind = str(payload.get("kind") or "vector")
    extra_headers = payload.get("headers") or {}

    try:
        import httpx
        response = httpx.get(
            url,
            timeout=timeout,
            headers=extra_headers,
            follow_redirects=True,
        )
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "application/json")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"URL fetch failed: {exc}",
        ) from exc

    import json as _json
    from urllib.parse import urlparse as _urlparse

    try:
        parsed_json = _json.loads(content)
        if not isinstance(parsed_json, dict):
            raise ValueError("Response is not a JSON object.")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Response is not valid JSON: {exc}",
        ) from exc

    url_path = _urlparse(url).path
    raw_filename = url_path.split("/")[-1] or "remote_data.geojson"
    display_name = payload.get("display_name") or raw_filename

    feature_count = 0
    if parsed_json.get("type") == "FeatureCollection":
        feature_count = len(parsed_json.get("features") or [])
    elif parsed_json.get("type") == "Feature":
        feature_count = 1

    try:
        upload = svc.save_upload(
            filename=raw_filename,
            content=content,
            content_type=content_type,
            kind=kind,
            user_context={
                "source": "url",
                "source_type": "url",
                "display_name": display_name,
                "original_url": url,
                "feature_count": feature_count,
            },
            project_id=payload.get("project_id"),
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=_http_error_detail(exc),
        ) from exc

    return _json_safe({
        **upload,
        "source_type": "url",
        "feature_count": feature_count,
        "original_url": url,
    })

