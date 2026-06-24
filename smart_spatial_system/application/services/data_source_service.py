from __future__ import annotations

from typing import Any

from orchestrator.project_service import ProjectServiceError
from orchestrator.upload_service import UploadServiceError
from orchestrator.upload_storage import UploadStorageError


class DataSourceServiceError(RuntimeError):
    """Raised when a data source service operation fails."""


class DataSourceService:
    """Application service boundary for project data source operations."""

    def __init__(self, project_service: Any, upload_service: Any) -> None:
        if project_service is None:
            raise DataSourceServiceError("ProjectService dependency is required.")
        if upload_service is None:
            raise DataSourceServiceError("UploadService dependency is required.")
        self.project_service = project_service
        self.upload_service = upload_service

    def register_csv_table_source(
            self,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            """
            Register a CSV/Table source in the project data catalog.

            MVP behavior:
            - Stores metadata only.
            - If x/y columns are provided, frontend/next phases can treat it as a point layer.
            """
            if not isinstance(payload, dict):
                raise DataSourceServiceError("CSV/Table payload must be an object.")

            project_id = str(payload.get("project_id") or "").strip() or None
            display_name = str(
                payload.get("display_name")
                or payload.get("name")
                or payload.get("table_name")
                or payload.get("url")
                or "CSV/Table Source"
            ).strip()

            source_url = str(payload.get("url") or "").strip()
            table_name = str(payload.get("table_name") or "").strip()

            if not source_url and not table_name:
                raise DataSourceServiceError("CSV/Table source requires url or table_name.")

            normalized_payload = {
                "display_name": display_name,
                "description": payload.get("description") or "",
                "tags": payload.get("tags") or [],
                "url": source_url or None,
                "table_name": table_name or None,
                "delimiter": payload.get("delimiter") or ",",
                "encoding": payload.get("encoding") or "utf-8",
                "has_header": bool(payload.get("has_header", True)),
                "x_column": payload.get("x_column") or payload.get("longitude_column") or None,
                "y_column": payload.get("y_column") or payload.get("latitude_column") or None,
                "crs": payload.get("crs") or "EPSG:4326",
                "geometry_mode": payload.get("geometry_mode") or "xy",
                "source_kind": "csv_table",
            }

            try:
                metadata = self.upload_service.save_external_source(
                    source_type="csv_table",
                    kind="table",
                    display_name=display_name,
                    payload=normalized_payload,
                    project_id=project_id,
                )

                if project_id:
                    self.project_service.attach_upload(project_id, metadata["upload_id"])

                return self._normalize_data_source_metadata(
                    metadata,
                    project_id=project_id,
                )
            except (UploadStorageError, ProjectServiceError, UploadServiceError) as exc:
                raise DataSourceServiceError(str(exc)) from exc

    def register_wms_source(
            self,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            """
            Register a WMS source in the project data catalog.

            MVP behavior:
            - Stores WMS connection metadata.
            - Map rendering can later read connection.base_url/layer_name/options.
            """
            if not isinstance(payload, dict):
                raise DataSourceServiceError("WMS payload must be an object.")

            project_id = str(payload.get("project_id") or "").strip() or None
            base_url = str(payload.get("base_url") or payload.get("url") or "").strip()
            layer_name = str(payload.get("layer_name") or payload.get("layers") or "").strip()

            if not base_url:
                raise DataSourceServiceError("WMS base_url is required.")

            if not layer_name:
                raise DataSourceServiceError("WMS layer_name is required.")

            display_name = str(
                payload.get("display_name")
                or payload.get("name")
                or layer_name
                or "WMS Source"
            ).strip()

            normalized_payload = {
                "display_name": display_name,
                "description": payload.get("description") or "",
                "tags": payload.get("tags") or [],
                "base_url": base_url,
                "url": base_url,
                "layer_name": layer_name,
                "layers": layer_name,
                "version": payload.get("version") or "1.3.0",
                "format": payload.get("format") or "image/png",
                "transparent": bool(payload.get("transparent", True)),
                "crs": payload.get("crs") or "EPSG:3857",
                "attribution": payload.get("attribution") or "",
                "opacity": payload.get("opacity", 0.85),
                "source_kind": "wms",
            }

            try:
                metadata = self.upload_service.save_external_source(
                    source_type="wms",
                    kind="online",
                    display_name=display_name,
                    payload=normalized_payload,
                    project_id=project_id,
                )

                if project_id:
                    self.project_service.attach_upload(project_id, metadata["upload_id"])

                return self._normalize_data_source_metadata(
                    metadata,
                    project_id=project_id,
                )
            except (UploadStorageError, ProjectServiceError, UploadServiceError) as exc:
                raise DataSourceServiceError(str(exc)) from exc

    def list_project_data_sources(
            self,
            project_id: str,
        ) -> list[dict[str, Any]]:
            try:
                project = self.project_service.get_project(project_id)
            except ProjectServiceError as exc:
                raise DataSourceServiceError(str(exc)) from exc

            upload_ids = project.get("uploads")
            if not isinstance(upload_ids, list):
                upload_ids = []

            items: list[dict[str, Any]] = []

            for upload_id in upload_ids:
                try:
                    metadata = self.upload_service.read_metadata(str(upload_id))
                except UploadStorageError:
                    continue

                items.append(
                    self._normalize_data_source_metadata(
                        metadata,
                        project_id=project_id,
                    )
                )

            items.sort(
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            return items

    def get_data_source(
            self,
            upload_id: str,
        ) -> dict[str, Any]:
            try:
                metadata = self.upload_service.read_metadata(upload_id)
            except (UploadStorageError, UploadServiceError) as exc:
                raise DataSourceServiceError(str(exc)) from exc

            project_id = None
            for project in self.project_service.list_projects():
                uploads = project.get("uploads")
                if isinstance(uploads, list) and upload_id in uploads:
                    project_id = project.get("project_id")
                    break

            return self._normalize_data_source_metadata(
                metadata,
                project_id=project_id,
            )

    def delete_data_source(
            self,
            upload_id: str,
        ) -> dict[str, Any]:
            try:
                metadata = self.upload_service.read_metadata(upload_id)
            except (UploadStorageError, UploadServiceError) as exc:
                raise DataSourceServiceError(str(exc)) from exc

            attached_projects: list[str] = []

            for project in self.project_service.list_projects():
                project_id = project.get("project_id")
                uploads = project.get("uploads")

                if (
                    project_id
                    and isinstance(uploads, list)
                    and upload_id in uploads
                ):
                    try:
                        self.project_service.detach_upload(project_id, upload_id)
                        attached_projects.append(str(project_id))
                    except ProjectServiceError as exc:
                        raise DataSourceServiceError(str(exc)) from exc

            try:
                self.upload_service.delete_upload(upload_id)
            except (UploadStorageError, UploadServiceError) as exc:
                raise DataSourceServiceError(str(exc)) from exc

            payload = self._normalize_data_source_metadata(
                metadata,
                project_id=attached_projects[0] if attached_projects else None,
            )
            payload["deleted"] = True
            payload["detached_from_projects"] = attached_projects
            return payload

    def _normalize_data_source_metadata(
            self,
            metadata: dict[str, Any],
            *,
            project_id: str | None = None,
        ) -> dict[str, Any]:
            upload_id = str(metadata.get("upload_id") or "")

            display_name = (
                metadata.get("display_name")
                or metadata.get("original_filename")
                or metadata.get("filename")
                or upload_id
            )

            return {
                "data_source_id": upload_id,
                "upload_id": upload_id,
                "project_id": project_id,
                "name": display_name,
                "display_name": metadata.get("display_name"),
                "description": metadata.get("description") or "",
                "tags": metadata.get("tags") or [],
                "filename": metadata.get("filename"),
                "original_filename": metadata.get("original_filename"),
                "kind": metadata.get("kind") or "unknown",
                "extension": metadata.get("extension"),
                "media_type": metadata.get("content_type")
                or "application/octet-stream",
                "size_bytes": metadata.get("size_bytes") or 0,
                "created_at": metadata.get("stored_at"),
                "stored_at": metadata.get("stored_at"),
                "updated_at": metadata.get("updated_at"),
                "parsed_json_available": bool(
                    metadata.get("parsed_json_available")
                ),
                "content_type": metadata.get("content_type"),
                "sha256": metadata.get("sha256"),
                "source_type": metadata.get("source_type"),
                "external": bool(metadata.get("external")),
                "status": metadata.get("status") or "ready",
                "connection": metadata.get("connection") or {},
                "crs": (metadata.get("connection") or {}).get("crs"),
                "url": (metadata.get("connection") or {}).get("url")
                or (metadata.get("connection") or {}).get("base_url"),
                "base_url": (metadata.get("connection") or {}).get("base_url"),
                "layer_name": (metadata.get("connection") or {}).get("layer_name"),
                "table_name": (metadata.get("connection") or {}).get("table_name"),
                "x_column": (metadata.get("connection") or {}).get("x_column"),
                "y_column": (metadata.get("connection") or {}).get("y_column"),
            }

    def update_data_source(
            self,
            upload_id: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            if not isinstance(payload, dict):
                raise DataSourceServiceError("Update payload must be an object.")

            patch = {
                "display_name": payload.get("name"),
                "description": payload.get("description"),
                "tags": payload.get("tags"),
            }

            try:
                metadata = self.upload_service.update_metadata(upload_id, patch)
            except (UploadStorageError, UploadServiceError) as exc:
                raise DataSourceServiceError(str(exc)) from exc

            project_id = None
            for project in self.project_service.list_projects():
                uploads = project.get("uploads")
                if isinstance(uploads, list) and upload_id in uploads:
                    project_id = project.get("project_id")
                    break

            return self._normalize_data_source_metadata(
                metadata,
                project_id=project_id,
            )

    def preview_data_source(
            self,
            upload_id: str,
        ) -> dict[str, Any]:
            try:
                metadata = self.upload_service.read_metadata(upload_id)
            except (UploadStorageError, UploadServiceError) as exc:
                raise DataSourceServiceError(str(exc)) from exc

            project_id = None
            for project in self.project_service.list_projects():
                uploads = project.get("uploads")
                if isinstance(uploads, list) and upload_id in uploads:
                    project_id = project.get("project_id")
                    break

            base = self._normalize_data_source_metadata(
                metadata,
                project_id=project_id,
            )

            if metadata.get("external"):
                connection = metadata.get("connection") or {}
                base["preview"] = {
                    "type": metadata.get("source_type") or "external",
                    "message": "External data source metadata preview.",
                    "connection": connection,
                    "fields": sorted(connection.keys()) if isinstance(connection, dict) else [],
                }
                return base

            if metadata.get("parsed_json_available"):
                try:
                    content = self.upload_service.read_json_content(upload_id)
                    base["preview"] = self._build_json_preview(content)
                    return base
                except UploadStorageError:
                    pass

            base["preview"] = {
                "type": "file",
                "message": "Preview is not available for this file type yet.",
            }
            return base

    def _build_json_preview(
            self,
            payload: Any,
        ) -> dict[str, Any]:
            if isinstance(payload, dict):
                payload_type = str(payload.get("type") or "")

                if payload_type == "FeatureCollection":
                    features = payload.get("features")
                    if not isinstance(features, list):
                        features = []

                    geometry_types = sorted({
                        str(feature.get("geometry", {}).get("type"))
                        for feature in features
                        if isinstance(feature, dict)
                        and isinstance(feature.get("geometry"), dict)
                        and feature.get("geometry", {}).get("type")
                    })

                    sample_limit = 100

                    return {
                        "type": "geojson_summary",
                        "geojson_type": "FeatureCollection",
                        "feature_count": len(features),
                        "geometry_types": geometry_types,
                        "keys": sorted(payload.keys()),
                        "sample_limit": sample_limit,
                        "sample_geojson": {
                            "type": "FeatureCollection",
                            "features": features[:sample_limit],
                        },
                    }

                if payload_type == "Feature":
                    geometry_type = None
                    geometry = payload.get("geometry")
                    if isinstance(geometry, dict):
                        geometry_type = geometry.get("type")

                    return {
                        "type": "geojson_summary",
                        "geojson_type": "Feature",
                        "feature_count": 1,
                        "geometry_types": [geometry_type] if geometry_type else [],
                        "keys": sorted(payload.keys()),
                        "sample_geojson": {
                            "type": "FeatureCollection",
                            "features": [payload],
                        },
                    }

                if payload_type in {
                    "Point",
                    "MultiPoint",
                    "LineString",
                    "MultiLineString",
                    "Polygon",
                    "MultiPolygon",
                    "GeometryCollection",
                }:
                    return {
                        "type": "geojson_summary",
                        "geojson_type": payload_type,
                        "feature_count": 1,
                        "geometry_types": [payload_type],
                        "keys": sorted(payload.keys()),
                        "sample_geojson": {
                            "type": "FeatureCollection",
                            "features": [
                                {
                                    "type": "Feature",
                                    "properties": {},
                                    "geometry": payload,
                                }
                            ],
                        },
                    }

                return {
                    "type": "json_summary",
                    "keys": sorted(payload.keys())[:30],
                    "key_count": len(payload.keys()),
                }

            if isinstance(payload, list):
                return {
                    "type": "json_list",
                    "length": len(payload),
                }

            return {
                "type": "json_scalar",
                "value_type": type(payload).__name__,
            }
