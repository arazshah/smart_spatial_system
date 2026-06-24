from __future__ import annotations

import pytest

from orchestrator.upload_service import UploadService, UploadServiceError
from orchestrator.upload_storage import UploadStorageError
from smart_spatial_system.application.services.upload_service import (
    UploadService as NewUploadService,
)


class FakeUploadStorage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def save_upload(self, *args, **kwargs):
        self.calls.append("save_upload")
        return {"upload_id": "up-1"}

    def list_uploads(self):
        self.calls.append("list_uploads")
        return [{"upload_id": "up-1"}]

    def read_metadata(self, upload_id):
        self.calls.append("read_metadata")
        return {"upload_id": upload_id}

    def get_file_path(self, upload_id):
        self.calls.append("get_file_path")
        return f"/tmp/{upload_id}.geojson"

    def get_media_type(self, upload_id):
        self.calls.append("get_media_type")
        return "application/geo+json"

    def save_external_source(self, *args, **kwargs):
        self.calls.append("save_external_source")
        return {"upload_id": "external-1"}

    def delete_upload(self, upload_id):
        self.calls.append("delete_upload")
        return None

    def update_metadata(self, upload_id, patch):
        self.calls.append("update_metadata")
        return {"upload_id": upload_id, **patch}

    def read_json_content(self, upload_id):
        self.calls.append("read_json_content")
        return {"ok": True}


class FailingUploadStorage:
    def read_metadata(self, upload_id):
        raise UploadStorageError("metadata failed")


def test_orchestrator_upload_service_wrapper_points_to_new_layout() -> None:
    assert UploadService is NewUploadService


def test_upload_service_requires_storage_dependency() -> None:
    with pytest.raises(UploadServiceError):
        UploadService(None)


def test_upload_service_delegates_operations() -> None:
    storage = FakeUploadStorage()
    service = UploadService(storage)  # type: ignore[arg-type]

    assert service.save_upload(filename="a.geojson") == {"upload_id": "up-1"}
    assert service.list_uploads() == [{"upload_id": "up-1"}]
    assert service.read_metadata("up-1") == {"upload_id": "up-1"}
    assert service.get_file_path("up-1") == "/tmp/up-1.geojson"
    assert service.get_media_type("up-1") == "application/geo+json"
    assert service.save_external_source(source_type="wms") == {"upload_id": "external-1"}
    assert service.update_metadata("up-1", {"name": "Layer"}) == {
        "upload_id": "up-1",
        "name": "Layer",
    }
    assert service.read_json_content("up-1") == {"ok": True}
    assert service.delete_upload("up-1") is None

    assert storage.calls == [
        "save_upload",
        "list_uploads",
        "read_metadata",
        "get_file_path",
        "get_media_type",
        "save_external_source",
        "update_metadata",
        "read_json_content",
        "delete_upload",
    ]


def test_upload_service_converts_storage_error_to_service_error() -> None:
    service = UploadService(FailingUploadStorage())  # type: ignore[arg-type]

    with pytest.raises(UploadServiceError) as exc_info:
        service.read_metadata("missing")

    assert "metadata failed" in str(exc_info.value)
