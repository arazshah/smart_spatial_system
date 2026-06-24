from __future__ import annotations

from pathlib import Path


def test_orchestrator_service_wires_upload_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "from orchestrator.upload_service import UploadService, UploadServiceError" in source
    assert "self.upload_service = UploadService(self.upload_storage)" in source


def test_orchestrator_service_does_not_call_upload_storage_operations_directly() -> None:
    from pathlib import Path

    service_source = Path("orchestrator/service.py").read_text(encoding="utf-8")
    data_source_source = Path(
        "smart_spatial_system/application/services/data_source_service.py"
    ).read_text(encoding="utf-8")

    forbidden_calls = [
        "self.upload_storage.save_upload(",
        "self.upload_storage.list_uploads(",
        "self.upload_storage.read_metadata(",
        "self.upload_storage.get_file_path(",
        "self.upload_storage.get_media_type(",
        "self.upload_storage.save_external_source(",
        "self.upload_storage.delete_upload(",
        "self.upload_storage.update_metadata(",
        "self.upload_storage.read_json_content(",
    ]

    combined_source = service_source + "\n" + data_source_source

    for call in forbidden_calls:
        assert call not in combined_source

    orchestrator_expected_calls = [
        "self.upload_service.save_upload(",
        "self.upload_service.list_uploads(",
        "self.upload_service.read_metadata(",
        "self.upload_service.get_file_path(",
        "self.upload_service.get_media_type(",
    ]

    for call in orchestrator_expected_calls:
        assert call in service_source

    data_source_expected_calls = [
        "self.upload_service.save_external_source(",
        "self.upload_service.delete_upload(",
        "self.upload_service.update_metadata(",
        "self.upload_service.read_json_content(",
    ]

    for call in data_source_expected_calls:
        assert call in data_source_source

