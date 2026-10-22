from __future__ import annotations

import json as _json
import os
from typing import Any

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Request,
)

from api.support import (
    http_error_detail as _http_error_detail,
)
from api.support import (
    json_safe as _json_safe,
)
from api.support import (
    service as _service,
)
from orchestrator.plugin_config_store import read_plugin_config
from orchestrator.service import OrchestratorServiceError
from smart_spatial_system.application.connectors import (
    ConnectorError,
    ConnectorRegistry,
    ConnectorResult,
    ConnectorSpec,
)

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

            binding = registry.resolve(capability_name)
            capability = getattr(binding, "callable", binding)
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


# ---------------------------------------------------------------------------
# Connector registry integration (docs/CONNECTOR_REGISTRY_DESIGN.md)
# ---------------------------------------------------------------------------

_DEFAULT_HTTP_ACCESS = "network,database"
_STATUS_BY_CODE = {
    "policy": 403,
    "unknown_connector": 404,
    "invalid_result": 502,
}


def _http_access() -> frozenset[str]:
    """
    Access classes of connectors that may be driven over HTTP. Filesystem and raw-SQL
    connectors are excluded by default: a caller must not choose server paths or SQL
    (uploads are the file route). Override with SMART_SPATIAL_CONNECTOR_ACCESS.
    """
    raw = os.environ.get("SMART_SPATIAL_CONNECTOR_ACCESS", _DEFAULT_HTTP_ACCESS)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _allow_client(spec: ConnectorSpec) -> bool:
    """Per-plugin opt-in (`allow_client_connection: true` in its config); default False."""
    try:
        config = read_plugin_config(spec.plugin_id).get("parsed") or {}
    except Exception:
        return False
    return config.get("allow_client_connection") is True


def _connectors(request: Request) -> ConnectorRegistry:
    svc = _service(request)
    cached = getattr(request.app.state, "connector_registry", None)
    if cached is not None and cached[0] is svc.registry:
        return cached[1]
    registry = ConnectorRegistry.from_capability_registry(
        svc.registry, allow_client_connection=lambda spec: _allow_client(spec)
    )
    request.app.state.connector_registry = (svc.registry, registry)
    return registry


def _fail(exc: ConnectorError) -> HTTPException:
    return HTTPException(status_code=_STATUS_BY_CODE.get(exc.code, 400), detail=str(exc))


def _run_connector(
    request: Request,
    capability: str,
    params: dict[str, Any],
    *,
    row_limit: int | None = None,
) -> ConnectorResult:
    svc = _service(request)
    registry = _connectors(request)
    try:
        spec = registry.get(capability)
        if not spec.access <= _http_access():
            raise ConnectorError(
                f"Connector '{capability}' is not available over the API "
                f"(needs access: {sorted(spec.access)}).",
                code="policy",
            )
        assert_enabled = getattr(svc, "_assert_capability_enabled", None)
        if callable(assert_enabled):
            try:
                assert_enabled(capability)
            except OrchestratorServiceError as exc:
                raise ConnectorError(str(exc), code="policy") from None
        return registry.fetch(capability, params, row_limit=row_limit)
    except ConnectorError as exc:
        raise _fail(exc) from None


def _snapshot(
    request: Request,
    result: ConnectorResult,
    *,
    filename: str,
    display_name: str,
    project_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Store the fetched layer as a project upload (a reproducible snapshot)."""
    if result.kind != "vector":
        raise HTTPException(
            status_code=400,
            detail=f"Only vector connectors can be registered as data sources (got {result.kind}).",
        )
    svc = _service(request)
    plugin_metadata = getattr(result.data, "metadata", None) or {}
    document = result.to_dict(include_data=True)["data"]
    document["metadata"] = _json_safe(plugin_metadata)
    content = _json.dumps(document, ensure_ascii=False).encode("utf-8")
    try:
        upload = svc.save_upload(
            filename=filename,
            content=content,
            content_type="application/geo+json",
            kind="vector",
            user_context={
                "source": result.source_type,
                "source_type": result.source_type,
                "display_name": display_name,
                "connector": result.metadata,
                **context,
            },
            project_id=project_id,
        )
    except OrchestratorServiceError as exc:
        raise HTTPException(status_code=400, detail=_http_error_detail(exc)) from exc
    return upload


def _safe_filename(name: str, default: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name or "")).strip("._")
    return cleaned[:100] or default


@router.get("/connectors")
def list_connectors(request: Request) -> dict[str, Any]:
    """Registered data connectors and whether this API may drive them."""
    allowed = _http_access()
    items = []
    for spec in _connectors(request).list():
        item = spec.describe()
        item["available"] = spec.access <= allowed
        items.append(item)
    return {"connectors": items, "http_access": sorted(allowed)}


@router.post("/connectors/preview")
def preview_connector(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Bounded sample (<= `limit` rows) plus schema, without registering anything."""
    capability = str(payload.get("capability") or "")
    params = payload.get("params") or {}
    limit = payload.get("limit", 20)
    if not capability:
        raise HTTPException(status_code=400, detail="'capability' is required.")
    registry = _connectors(request)
    try:
        spec = registry.get(capability)
        if not spec.supports_preview:
            raise ConnectorError(f"{capability} does not support preview.", code="invalid_params")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ConnectorError("limit must be an integer between 1 and 1000.", code="invalid_params")
        result = _run_connector(request, capability, params, row_limit=limit)
    except ConnectorError as exc:
        raise _fail(exc) from None
    return _json_safe(result.to_dict(include_data=True, max_features=limit))


@router.post("/data-sources/connect")
def connect_data_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Fetch a layer through any registered connector and register it as a data source.

    Body: {"capability": "fetch_wfs_features", "params": {...}, "project_id": "optional",
    "display_name": "optional"}. ``source_type`` may replace ``capability`` when it names a
    single connector. Connection details come from server-side profiles (``params.profile``).
    """
    registry = _connectors(request)
    capability = payload.get("capability")
    source_type = payload.get("source_type")
    if not capability:
        candidates = registry.by_source_type(str(source_type or ""))
        if len(candidates) != 1:
            raise HTTPException(
                status_code=400,
                detail="Provide 'capability' (or a 'source_type' that names exactly one connector).",
            )
        capability = candidates[0].capability
    params = payload.get("params") or {}
    result = _run_connector(request, str(capability), params)
    display_name = str(payload.get("display_name") or f"{result.source_type}_layer")
    upload = _snapshot(
        request, result,
        filename=f"{_safe_filename(display_name, result.source_type)}.geojson",
        display_name=display_name,
        project_id=payload.get("project_id"),
        context={},
    )
    return _json_safe({
        **upload,
        "source_type": result.source_type,
        "feature_count": result.metadata.get("feature_count", 0),
        "connector": result.metadata,
    })


@router.post("/data-sources/postgis")
def register_postgis_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Fetch a spatial table from PostGIS and register it as a data source.

    Connection details come from a server-side ``profile`` (config/plugins/postgis_connector.yaml).
    ``dsn``/``host``/``port``/``database``/``user``/``password`` in the body are refused unless the
    deployer set ``allow_client_connection: true`` in that config.

    Body: {"project_id", "display_name", "table", "schema", "geom_col", "where", "limit",
    "output_srid", "profile", "connect_timeout"}.
    """
    table = payload.get("table")
    if not isinstance(table, str) or not table.strip():
        raise HTTPException(status_code=400, detail="'table' must be a non-empty string.")
    names = ("profile", "schema", "geom_col", "where", "limit", "output_srid", "connect_timeout",
             "dsn", "host", "port", "database", "user", "password")
    params = {"table": table.strip(), **{k: payload[k] for k in names if payload.get(k) is not None}}
    result = _run_connector(request, "fetch_postgis_layer", params)
    schema = payload.get("schema", "public")
    display_name = payload.get("display_name") or f"{schema}.{table}"
    plugin_metadata = _json_safe(getattr(result.data, "metadata", None) or {})
    upload = _snapshot(
        request, result,
        filename=f"{_safe_filename(table, 'postgis')}.geojson",
        display_name=display_name,
        project_id=payload.get("project_id"),
        context={"table": table, "schema": schema, "postgis_metadata": plugin_metadata},
    )
    return _json_safe({
        **upload,
        "source_type": "postgis",
        "feature_count": result.metadata.get("feature_count", 0),
        "postgis_metadata": plugin_metadata,
    })


@router.post("/data-sources/wfs")
def register_wfs_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Fetch features from a WFS service and register them as a data source.

    Body: {"project_id", "display_name", "base_url" | "service", "type_name" | "layer",
    "version", "output_format", "srs_name", "bbox", "max_features", "property_name", "timeout"}.
    """
    base_url = payload.get("base_url")
    type_name = payload.get("type_name") or payload.get("layer")
    if not payload.get("service") and (not isinstance(base_url, str) or not base_url.strip()):
        raise HTTPException(status_code=400, detail="'base_url' or 'service' must be provided.")
    if not payload.get("service") and (not isinstance(type_name, str) or not str(type_name).strip()):
        raise HTTPException(status_code=400, detail="'type_name' or 'layer' must be provided.")
    names = ("service", "base_url", "type_name", "layer", "version", "output_format", "srs_name",
             "bbox", "max_features", "property_name", "timeout")
    params = {k: payload[k] for k in names if payload.get(k) is not None}
    result = _run_connector(request, "fetch_wfs_features", params)
    display_name = payload.get("display_name") or type_name or payload.get("layer") or "wfs_layer"
    plugin_metadata = _json_safe(getattr(result.data, "metadata", None) or {})
    upload = _snapshot(
        request, result,
        filename=f"{_safe_filename(str(display_name).replace(':', '_'), 'wfs_layer')}.geojson",
        display_name=str(display_name),
        project_id=payload.get("project_id"),
        context={"base_url": result.metadata["source_ref"].get("base_url"), "type_name": type_name,
                 "wfs_metadata": plugin_metadata},
    )
    return _json_safe({
        **upload,
        "source_type": "wfs",
        "feature_count": result.metadata.get("feature_count", 0),
        "wfs_metadata": plugin_metadata,
    })


@router.post("/data-sources/url")
def register_url_source(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    Fetch GeoJSON from a URL and register it as a data source.

    Public http(s) hosts only, no redirects, size limited (config/plugins/geojson_url_loader.yaml).
    Request headers are not accepted; use a server-side ``profile`` for authenticated sources.

    Body: {"project_id", "display_name", "url", "profile", "timeout"}.
    """
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(status_code=400, detail="'url' must be a non-empty string.")
    if payload.get("headers"):
        raise HTTPException(
            status_code=400,
            detail="Request headers are not accepted from callers; configure a 'profile' on the server.",
        )
    if str(payload.get("kind") or "vector") != "vector":
        raise HTTPException(status_code=400, detail="Only vector (GeoJSON) URL sources are supported.")
    params = {k: payload[k] for k in ("url", "profile", "timeout") if payload.get(k) is not None}
    params["url"] = url.strip()
    result = _run_connector(request, "load_geojson_url", params)
    safe_url = result.metadata["source_ref"]["url"]
    from urllib.parse import urlparse as _urlparse

    raw_name = _urlparse(safe_url).path.split("/")[-1] or "remote_data.geojson"
    display_name = payload.get("display_name") or raw_name
    upload = _snapshot(
        request, result,
        filename=_safe_filename(raw_name, "remote_data.geojson"),
        display_name=str(display_name),
        project_id=payload.get("project_id"),
        context={"original_url": safe_url, "feature_count": result.metadata.get("feature_count", 0)},
    )
    return _json_safe({
        **upload,
        "source_type": "url",
        "feature_count": result.metadata.get("feature_count", 0),
        "original_url": safe_url,
    })
