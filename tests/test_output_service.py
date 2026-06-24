from __future__ import annotations

import pytest

from orchestrator.output_service import OutputService, OutputServiceError
from orchestrator.output_storage import OutputStorageError
from smart_spatial_system.application.services.output_service import (
    OutputService as NewOutputService,
)


class FakeOutputStorage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_manifest(self, request_id):
        self.calls.append("read_manifest")
        return {"request_id": request_id, "files": []}

    def list_files(self, request_id):
        self.calls.append("list_files")
        return [{"filename": "result.geojson"}]

    def get_file_path(self, request_id, filename):
        self.calls.append("get_file_path")
        return f"/tmp/{request_id}/{filename}"

    def get_media_type(self, filename):
        self.calls.append("get_media_type")
        return "application/geo+json"

    def save_request_record(self, *args, **kwargs):
        self.calls.append("save_request_record")
        return {"request_id": "req-1", "files": []}


class FailingOutputStorage:
    def read_manifest(self, request_id):
        raise OutputStorageError("manifest failed")


def test_orchestrator_output_service_wrapper_points_to_new_layout() -> None:
    assert OutputService is NewOutputService


def test_output_service_requires_storage_dependency() -> None:
    with pytest.raises(OutputServiceError):
        OutputService(None)


def test_output_service_delegates_operations() -> None:
    storage = FakeOutputStorage()
    service = OutputService(storage)  # type: ignore[arg-type]

    assert service.read_manifest("req-1") == {"request_id": "req-1", "files": []}
    assert service.list_files("req-1") == [{"filename": "result.geojson"}]
    assert service.get_file_path("req-1", "result.geojson") == "/tmp/req-1/result.geojson"
    assert service.get_media_type("result.geojson") == "application/geo+json"
    assert service.save_request_record(request_id="req-1") == {
        "request_id": "req-1",
        "files": [],
    }

    assert storage.calls == [
        "read_manifest",
        "list_files",
        "get_file_path",
        "get_media_type",
        "save_request_record",
    ]


def test_output_service_converts_storage_error_to_service_error() -> None:
    service = OutputService(FailingOutputStorage())  # type: ignore[arg-type]

    with pytest.raises(OutputServiceError) as exc_info:
        service.read_manifest("missing")

    assert "manifest failed" in str(exc_info.value)
