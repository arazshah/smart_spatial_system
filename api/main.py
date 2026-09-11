"""
FastAPI API for Smart Spatial System.

This module owns the HTTP application factory, CORS setup, service wiring,
and API router registration.

Run:
    uvicorn api.main:app --reload
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import os
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import require_api_key
from api.routers.data_source_connectors import router as data_source_connectors_router
from api.routers.data_sources import router as data_sources_router
from api.routers.plugins_settings import router as plugins_settings_router
from api.routers.projects import router as projects_router
from api.routers.query_planner import router as query_planner_router
from api.routers.requests_outputs import router as requests_outputs_router
from api.routers.system import router as system_router
from api.routers.uploads import router as uploads_router
from api.routers.weights import router as weights_router
from orchestrator.service import OrchestratorService, OrchestratorServiceConfig


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

    # When set, every route except "/" and "/health" requires the matching
    # X-API-Key header (see api/auth.py). Defaults to the SMART_SPATIAL_API_KEY
    # env var; leave both unset to keep the API open (local dev, or a
    # deployment that has not opted in yet).
    api_key: str | None = field(default_factory=lambda: os.environ.get("SMART_SPATIAL_API_KEY") or None)


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
    app.state.api_key = final_api_config.api_key

    protected = [Depends(require_api_key)]

    # "/" and "/health" stay open for monitoring/load-balancer liveness checks.
    app.include_router(system_router)
    app.include_router(projects_router, dependencies=protected)
    app.include_router(uploads_router, dependencies=protected)
    app.include_router(data_sources_router, dependencies=protected)
    app.include_router(data_source_connectors_router, dependencies=protected)
    app.include_router(plugins_settings_router, dependencies=protected)
    app.include_router(requests_outputs_router, dependencies=protected)
    app.include_router(weights_router, dependencies=protected)
    app.include_router(query_planner_router, dependencies=protected)

    return app


app = create_app()
