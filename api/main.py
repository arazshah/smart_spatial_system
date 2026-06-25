"""
FastAPI MVP API for Smart Spatial System.

This API is the operational HTTP boundary for frontend usage.

MVP endpoints:
    GET  /health
    POST /query
    POST /feedback
    GET  /requests
    GET  /requests/{request_id}
    GET  /weights
    POST /weights/save
    POST /weights/reload
    POST /weights/proposals/apply

Run:
    uvicorn api.main:app --reload
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from orchestrator.plugin_config_store import (
    PluginConfigStoreError,
    read_plugin_config,
    write_plugin_config,
)
from orchestrator.service import (
    OrchestratorService,
    OrchestratorServiceConfig,
    OrchestratorServiceError,
)
from api.support import (
    http_error_detail as _http_error_detail,
    json_safe as _json_safe,
    service as _service,
)
from api.routers.system import router as system_router
from api.routers.projects import router as projects_router
from api.routers.uploads import router as uploads_router
from api.routers.data_sources import router as data_sources_router
from api.routers.data_source_connectors import router as data_source_connectors_router
from api.routers.plugins_settings import router as plugins_settings_router
from api.routers.requests_outputs import router as requests_outputs_router
from api.routers.weights import router as weights_router
from api.routers.query_planner import router as query_planner_router


@dataclass(frozen=True)
class APIConfig:
    """
    FastAPI application config.

    For frontend development, default CORS allows localhost React/Vite ports.
    In production, restrict allowed_origins.
    """

    title: str = "Smart Spatial System API"
    version: str = "0.1.0"
    description: str = "MVP API for natural geospatial query execution."

    allow_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    allow_credentials: bool = True
    allow_methods: tuple[str, ...] = ("*",)
    allow_headers: tuple[str, ...] = ("*",)


def create_app(
    *,
    service: OrchestratorService | None = None,
    service_config: OrchestratorServiceConfig | None = None,
    api_config: APIConfig | None = None,
) -> FastAPI:
    """
    Create FastAPI app.

    Tests can inject a service with tmp_path weights.
    Production/dev can use default config.
    """
    final_api_config = api_config or APIConfig()

    app = FastAPI(
        title=final_api_config.title,
        version=final_api_config.version,
        description=final_api_config.description,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(final_api_config.allow_origins),
        allow_credentials=final_api_config.allow_credentials,
        allow_methods=list(final_api_config.allow_methods),
        allow_headers=list(final_api_config.allow_headers),
    )

    app.state.service = service or OrchestratorService(
        service_config or OrchestratorServiceConfig()
    )

    app.include_router(system_router)
    app.include_router(projects_router)
    app.include_router(uploads_router)
    app.include_router(data_sources_router)
    app.include_router(data_source_connectors_router)
    app.include_router(plugins_settings_router)
    app.include_router(requests_outputs_router)
    app.include_router(weights_router)
    app.include_router(query_planner_router)













































    # ── Data Source Manager: External Sources ─────────────────────




    return app








app = create_app()
