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















    @app.get("/plugins")
    def list_plugins(
        request: Request,
    ) -> list[dict[str, Any]]:
        svc = _service(request)
        return _json_safe(svc.list_plugins())

    @app.get("/plugins/{plugin_id}")
    def get_plugin(
        request: Request,
        plugin_id: str,
    ) -> dict[str, Any]:
        svc = _service(request)

        try:
            return _json_safe(svc.get_plugin(plugin_id))
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=404,
                detail=_http_error_detail(exc),
            ) from exc


    @app.patch("/plugins/{plugin_id}")
    def patch_plugin(
        request: Request,
        plugin_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        svc = _service(request)

        try:
            return _json_safe(
                svc.update_plugin_state(
                    plugin_id,
                    enabled=payload.get("enabled"),
                )
            )
        except OrchestratorServiceError as exc:
            message = str(exc)
            raise HTTPException(
                status_code=404 if "Unknown plugin:" in message else 400,
                detail=message,
            ) from exc


    @app.get("/plugins/{plugin_id}/config")
    def get_plugin_config(
        request: Request,
        plugin_id: str,
    ) -> dict[str, Any]:
        svc = _service(request)

        # Ensure the plugin actually exists before exposing config.
        try:
            svc.get_plugin(plugin_id)
        except OrchestratorServiceError as exc:
            raise HTTPException(status_code=404, detail=_http_error_detail(exc)) from exc

        try:
            return _json_safe(read_plugin_config(plugin_id))
        except PluginConfigStoreError as exc:
            raise HTTPException(status_code=400, detail=_http_error_detail(exc)) from exc

    @app.put("/plugins/{plugin_id}/config")
    def put_plugin_config(
        request: Request,
        plugin_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        svc = _service(request)

        try:
            svc.get_plugin(plugin_id)
        except OrchestratorServiceError as exc:
            raise HTTPException(status_code=404, detail=_http_error_detail(exc)) from exc

        raw_yaml = payload.get("raw_yaml")
        parsed = payload.get("parsed")

        try:
            result = write_plugin_config(
                plugin_id,
                raw_yaml=raw_yaml,
                parsed=parsed,
            )
        except PluginConfigStoreError as exc:
            raise HTTPException(status_code=400, detail=_http_error_detail(exc)) from exc

        return _json_safe(result)

    @app.get("/settings/runtime")
    def get_runtime_settings(
        request: Request,
    ) -> dict[str, Any]:
        """
        Return non-sensitive runtime settings.

        This endpoint intentionally never returns secrets/API keys.
        """
        svc = _service(request)
        return _json_safe(svc.get_runtime_settings())

    @app.post("/settings/llm/smoke-test")
    def llm_smoke_test(
        request: Request,
    ) -> dict[str, Any]:
        """
        Verify backend-to-LLM connectivity.

        This endpoint never returns secrets.
        """
        svc = _service(request)

        try:
            return _json_safe(svc.run_llm_smoke_test())
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=502,
                detail=_http_error_detail(exc),
            ) from exc

    @app.post("/planner/intent")
    def plan_intent(
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Plan natural geospatial query intent using LLM.

        Does not execute plugins.
        """
        svc = _service(request)

        query = payload.get("query")

        try:
            return _json_safe(svc.plan_intent_with_llm(query))
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=400,
                detail=_http_error_detail(exc),
            ) from exc






    @app.post("/query")
    def query_endpoint(
        request: Request,
        body: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        """
        Execute natural geospatial query.

        Expected body:
            {
                "query": "...",
                "inputs": {...},
                "band_map": {...},
                "request_id": "optional",
                "user_context": {...},
                "metadata": {...},
                "min_score": 0.01
            }
        """
        svc = _service(request)

        query_text = body.get("query")
        inputs = body.get("inputs")

        if not isinstance(query_text, str) or not query_text.strip():
            raise HTTPException(
                status_code=400,
                detail="'query' must be a non-empty string.",
            )

        if not isinstance(inputs, dict):
            raise HTTPException(
                status_code=400,
                detail="'inputs' must be an object.",
            )

        band_map = body.get("band_map") or {}

        if not isinstance(band_map, dict):
            raise HTTPException(
                status_code=400,
                detail="'band_map' must be an object when provided.",
            )

        user_context = body.get("user_context") or {}

        if not isinstance(user_context, dict):
            raise HTTPException(
                status_code=400,
                detail="'user_context' must be an object when provided.",
            )

        metadata = body.get("metadata") or {}

        if not isinstance(metadata, dict):
            raise HTTPException(
                status_code=400,
                detail="'metadata' must be an object when provided.",
            )

        min_score = body.get("min_score")

        if min_score is not None and not isinstance(min_score, (int, float)):
            raise HTTPException(
                status_code=400,
                detail="'min_score' must be numeric when provided.",
            )

        response = svc.handle_query(
            query=query_text,
            inputs=inputs,
            band_map={
                str(key): int(value)
                for key, value in band_map.items()
            },
            request_id=body.get("request_id"),
            user_context=user_context,
            metadata=metadata,
            min_score=float(min_score) if min_score is not None else None,
            project_id=str(body.get("project_id") or "").strip() or None,
        )

        return _json_safe(response)

    @app.post("/feedback")
    def feedback_endpoint(
        request: Request,
        body: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        """
        Submit user feedback for a previous request.

        Expected body:
            {
                "request_id": "...",
                "rating": "correct|incorrect|partial",
                "issue_types": ["route_error"],
                "expected_capability": "threshold_raster",
                "expected_plugin_id": "raster_threshold",
                "comment": "...",
                "user_context": {...}
            }
        """
        svc = _service(request)

        request_id = body.get("request_id")
        rating = body.get("rating")

        if not isinstance(request_id, str) or not request_id.strip():
            raise HTTPException(
                status_code=400,
                detail="'request_id' must be a non-empty string.",
            )

        if not isinstance(rating, str) or not rating.strip():
            raise HTTPException(
                status_code=400,
                detail="'rating' must be a non-empty string.",
            )

        issue_types = body.get("issue_types")

        if issue_types is not None and not isinstance(issue_types, list):
            raise HTTPException(
                status_code=400,
                detail="'issue_types' must be a list when provided.",
            )

        user_context = body.get("user_context")

        if user_context is not None and not isinstance(user_context, dict):
            raise HTTPException(
                status_code=400,
                detail="'user_context' must be an object when provided.",
            )

        try:
            payload = svc.submit_feedback(
                request_id=request_id,
                rating=rating,
                issue_types=issue_types,
                expected_capability=body.get("expected_capability"),
                expected_plugin_id=body.get("expected_plugin_id"),
                comment=body.get("comment"),
                user_context=user_context,
            )
        except OrchestratorServiceError as exc:
            if "Unknown request_id" in str(exc):
                raise HTTPException(
                    status_code=404,
                    detail=_http_error_detail(exc),
                ) from exc

            raise HTTPException(
                status_code=400,
                detail=_http_error_detail(exc),
            ) from exc

        return _json_safe(payload)

    @app.get("/requests")
    def list_requests(request: Request) -> list[dict[str, Any]]:
        svc = _service(request)
        return _json_safe(svc.list_requests())

    @app.get("/requests/{request_id}")
    def get_request(
        request: Request,
        request_id: str,
    ) -> dict[str, Any]:
        svc = _service(request)
        record = svc.get_request(request_id)

        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown request_id: {request_id}",
            )

        return _json_safe(record)

    @app.get("/requests/{request_id}/map-layers")
    def get_request_map_layers(
        request: Request,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Return Leaflet-ready map layers for a previous request.
        """
        svc = _service(request)

        try:
            return _json_safe(svc.get_map_layers(request_id))
        except OrchestratorServiceError as exc:
            if "Unknown request_id" in str(exc):
                raise HTTPException(
                    status_code=404,
                    detail=_http_error_detail(exc),
                ) from exc

            raise HTTPException(
                status_code=400,
                detail=_http_error_detail(exc),
            ) from exc

    @app.get("/requests/{request_id}/outputs")
    def get_request_outputs(
        request: Request,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Return persisted output manifest for a request.
        """
        svc = _service(request)

        try:
            return _json_safe(svc.get_output_manifest(request_id))
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=404,
                detail=_http_error_detail(exc),
            ) from exc

    @app.post("/requests/{request_id}/outputs/save")
    def save_request_outputs(
        request: Request,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Persist outputs for a request again.
        """
        svc = _service(request)

        try:
            return _json_safe(svc.save_request_outputs(request_id))
        except OrchestratorServiceError as exc:
            if "Unknown request_id" in str(exc):
                raise HTTPException(
                    status_code=404,
                    detail=_http_error_detail(exc),
                ) from exc

            raise HTTPException(
                status_code=400,
                detail=_http_error_detail(exc),
            ) from exc

    @app.get("/requests/{request_id}/outputs/files")
    def list_request_output_files(
        request: Request,
        request_id: str,
    ) -> list[dict[str, Any]]:
        """
        List persisted output files for a request.
        """
        svc = _service(request)

        try:
            return _json_safe(svc.list_output_files(request_id))
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=404,
                detail=_http_error_detail(exc),
            ) from exc

    @app.get("/requests/{request_id}/outputs/files/{filename}")
    def download_request_output_file(
        request: Request,
        request_id: str,
        filename: str,
    ) -> FileResponse:
        """
        Download one persisted output file.
        """
        svc = _service(request)

        try:
            file_path = svc.get_output_file_path(
                request_id,
                filename,
            )
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=404,
                detail=_http_error_detail(exc),
            ) from exc

        return FileResponse(
            path=file_path,
            media_type=svc.get_output_file_media_type(filename),
            filename=filename,
        )

    @app.get("/requests/{request_id}/documents/{filename}")
    def download_request_document(
        request: Request,
        request_id: str,
        filename: str,
    ) -> FileResponse:
        """
        Download a generated document for a request.

        Security policy:
        - only serves files from artifacts/reports
        - blocks path traversal
        - currently allows the real-estate ranking PDF generated for the same request_id
        """
        safe_filename = Path(filename).name
        if safe_filename != filename:
            raise HTTPException(
                status_code=404,
                detail="Unknown document file.",
            )

        expected_filename = f"real_estate_ranking_{request_id}.pdf"
        if safe_filename != expected_filename:
            raise HTTPException(
                status_code=404,
                detail="Unknown document file.",
            )

        reports_dir = Path("artifacts") / "reports"
        file_path = reports_dir / safe_filename

        try:
            resolved_reports_dir = reports_dir.resolve()
            resolved_file_path = file_path.resolve()
        except OSError as exc:
            raise HTTPException(
                status_code=404,
                detail="Unknown document file.",
            ) from exc

        if (
            resolved_reports_dir not in resolved_file_path.parents
            or not resolved_file_path.is_file()
        ):
            raise HTTPException(
                status_code=404,
                detail="Unknown document file.",
            )

        return FileResponse(
            path=resolved_file_path,
            media_type="application/pdf",
            filename=safe_filename,
        )

    @app.get("/weights")
    def get_weights(request: Request) -> dict[str, Any]:
        svc = _service(request)
        return _json_safe(svc.get_weights())

    @app.post("/weights/save")
    def save_weights(request: Request) -> dict[str, Any]:
        svc = _service(request)

        try:
            return _json_safe(svc.save_weights())
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=500,
                detail=_http_error_detail(exc),
            ) from exc

    @app.post("/weights/reload")
    def reload_weights(request: Request) -> dict[str, Any]:
        svc = _service(request)

        try:
            return _json_safe(svc.reload_weights())
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=500,
                detail=_http_error_detail(exc),
            ) from exc

    @app.post("/weights/proposals/apply")
    def apply_weight_proposal(
        request: Request,
        body: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        """
        Approve and apply a weight proposal.

        Expected body:
            {
                "proposal": {...},
                "save": true
            }
        """
        svc = _service(request)

        proposal = body.get("proposal")

        if not isinstance(proposal, dict):
            raise HTTPException(
                status_code=400,
                detail="'proposal' must be an object.",
            )

        save = body.get("save", True)

        if not isinstance(save, bool):
            raise HTTPException(
                status_code=400,
                detail="'save' must be boolean when provided.",
            )

        try:
            payload = svc.approve_and_apply_proposal(
                proposal,
                save=save,
            )
        except (OrchestratorServiceError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=_http_error_detail(exc),
            ) from exc

        return _json_safe(payload)


    # ── Data Source Manager: External Sources ─────────────────────




    return app








app = create_app()
