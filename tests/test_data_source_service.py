from __future__ import annotations

import pytest

from orchestrator.data_source_service import DataSourceService, DataSourceServiceError
from smart_spatial_system.application.services.data_source_service import (
    DataSourceService as NewDataSourceService,
)


class FakeProjectService:
    def __init__(self) -> None:
        self.projects = {
            "proj-1": {
                "project_id": "proj-1",
                "uploads": ["up-1"],
            }
        }
        self.attached: list[tuple[str, str]] = []
        self.detached: list[tuple[str, str]] = []

    def attach_upload(self, project_id: str, upload_id: str) -> dict:
        self.attached.append((project_id, upload_id))
        self.projects.setdefault(project_id, {"project_id": project_id, "uploads": []})
        self.projects[project_id]["uploads"].append(upload_id)
        return self.projects[project_id]

    def detach_upload(self, project_id: str, upload_id: str) -> dict:
        self.detached.append((project_id, upload_id))
        uploads = self.projects[project_id].setdefault("uploads", [])
        if upload_id in uploads:
            uploads.remove(upload_id)
        return self.projects[project_id]

    def get_project(self, project_id: str) -> dict:
        return self.projects[project_id]

    def list_projects(self) -> list[dict]:
        return list(self.projects.values())


class FakeUploadService:
    def __init__(self) -> None:
        self.metadata = {
            "up-1": {
                "upload_id": "up-1",
                "filename": "sample.geojson",
                "original_filename": "sample.geojson",
                "kind": "vector",
                "content_type": "application/geo+json",
                "stored_at": "2026-01-01T00:00:00Z",
                "parsed_json_available": True,
            }
        }
        self.deleted: list[str] = []

    def save_external_source(self, *, source_type, kind, display_name, payload, project_id):
        upload_id = f"{source_type}-1"
        item = {
            "upload_id": upload_id,
            "display_name": display_name,
            "kind": kind,
            "source_type": source_type,
            "external": True,
            "connection": payload,
            "stored_at": "2026-01-01T00:00:00Z",
        }
        self.metadata[upload_id] = item
        return item

    def read_metadata(self, upload_id: str) -> dict:
        return self.metadata[upload_id]

    def update_metadata(self, upload_id: str, patch: dict) -> dict:
        self.metadata[upload_id].update({k: v for k, v in patch.items() if v is not None})
        return self.metadata[upload_id]

    def delete_upload(self, upload_id: str) -> None:
        self.deleted.append(upload_id)
        self.metadata.pop(upload_id, None)

    def read_json_content(self, upload_id: str) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "A"},
                    "geometry": {"type": "Point", "coordinates": [1, 2]},
                }
            ],
        }


def test_orchestrator_data_source_service_wrapper_points_to_new_layout() -> None:
    assert DataSourceService is NewDataSourceService


def test_data_source_service_requires_dependencies() -> None:
    with pytest.raises(DataSourceServiceError):
        DataSourceService(None, FakeUploadService())

    with pytest.raises(DataSourceServiceError):
        DataSourceService(FakeProjectService(), None)


def test_register_csv_table_source_attaches_to_project() -> None:
    project_service = FakeProjectService()
    upload_service = FakeUploadService()
    service = DataSourceService(project_service, upload_service)

    result = service.register_csv_table_source(
        {
            "project_id": "proj-1",
            "url": "file:///tmp/table.csv",
            "name": "Table",
            "x_column": "lon",
            "y_column": "lat",
        }
    )

    assert result["source_type"] == "csv_table"
    assert result["project_id"] == "proj-1"
    assert result["name"] == "Table"
    assert project_service.attached == [("proj-1", "csv_table-1")]


def test_list_project_data_sources_returns_normalized_items() -> None:
    service = DataSourceService(FakeProjectService(), FakeUploadService())

    result = service.list_project_data_sources("proj-1")

    assert len(result) == 1
    assert result[0]["data_source_id"] == "up-1"
    assert result[0]["project_id"] == "proj-1"
    assert result[0]["kind"] == "vector"


def test_preview_geojson_data_source() -> None:
    service = DataSourceService(FakeProjectService(), FakeUploadService())

    result = service.preview_data_source("up-1")

    assert result["data_source_id"] == "up-1"
    assert result["preview"]["type"] == "geojson_summary"
    assert result["preview"]["feature_count"] == 1


def test_delete_data_source_detaches_and_deletes_upload() -> None:
    project_service = FakeProjectService()
    upload_service = FakeUploadService()
    service = DataSourceService(project_service, upload_service)

    result = service.delete_data_source("up-1")

    assert result["deleted"] is True
    assert result["detached_from_projects"] == ["proj-1"]
    assert project_service.detached == [("proj-1", "up-1")]
    assert upload_service.deleted == ["up-1"]
