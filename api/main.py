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

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from orchestrator.service import (
    OrchestratorService,
    OrchestratorServiceConfig,
    OrchestratorServiceError,
)


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

    @app.get("/")
    def root() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "Smart Spatial System API",
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        svc = _service(request)
        return _json_safe(svc.get_health())

    @app.post("/uploads/raster")
    async def upload_raster(
        request: Request,
        file: UploadFile = File(...),
        kind: str = Form("raster"),
    ) -> dict[str, Any]:
        """
        Upload a raster file.

        MVP:
            - JSON raster files can be used directly through raster_ref.
            - GeoTIFF files are stored for future rasterio/local_raster_loader integration.
        """
        svc = _service(request)

        content = await file.read()

        try:
            payload = svc.save_upload(
                filename=file.filename or "upload.bin",
                content=content,
                content_type=file.content_type,
                kind=kind,
                user_context={
                    "source": "api_upload",
                },
            )
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

        return _json_safe(payload)

    @app.get("/uploads")
    def list_uploads(
        request: Request,
    ) -> list[dict[str, Any]]:
        svc = _service(request)
        return _json_safe(svc.list_uploads())

    @app.get("/uploads/{upload_id}")
    def get_upload_metadata(
        request: Request,
        upload_id: str,
    ) -> dict[str, Any]:
        svc = _service(request)

        try:
            return _json_safe(svc.get_upload_metadata(upload_id))
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

    @app.get("/uploads/{upload_id}/file")
    def download_upload_file(
        request: Request,
        upload_id: str,
    ) -> FileResponse:
        svc = _service(request)

        try:
            file_path = svc.get_upload_file_path(upload_id)
            media_type = svc.get_upload_file_media_type(upload_id)
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=file_path.name,
        )

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
                    detail=str(exc),
                ) from exc

            raise HTTPException(
                status_code=400,
                detail=str(exc),
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
                    detail=str(exc),
                ) from exc

            raise HTTPException(
                status_code=400,
                detail=str(exc),
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
                detail=str(exc),
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
                    detail=str(exc),
                ) from exc

            raise HTTPException(
                status_code=400,
                detail=str(exc),
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
                detail=str(exc),
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
                detail=str(exc),
            ) from exc

        return FileResponse(
            path=file_path,
            media_type=svc.get_output_file_media_type(filename),
            filename=filename,
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
                detail=str(exc),
            ) from exc

    @app.post("/weights/reload")
    def reload_weights(request: Request) -> dict[str, Any]:
        svc = _service(request)

        try:
            return _json_safe(svc.reload_weights())
        except OrchestratorServiceError as exc:
            raise HTTPException(
                status_code=500,
                detail=str(exc),
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
                detail=str(exc),
            ) from exc

        return _json_safe(payload)

    return app


def _service(request: Request) -> OrchestratorService:
    return request.app.state.service


def _json_safe(value: Any) -> Any:
    """
    Convert objects/dataclasses/internal runtime objects to JSON-safe data.

    This is intentionally defensive because request history can contain:
        - dataclasses
        - plugin result objects
        - plan objects
        - dict-like evidence
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass

    if is_dataclass(value):
        try:
            return _json_safe(asdict(value))
        except Exception:
            pass

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict) and payload:
        return _json_safe(payload)

    return repr(value)


app = create_app()
