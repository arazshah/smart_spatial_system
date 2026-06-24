from __future__ import annotations

from pathlib import Path


def test_orchestrator_service_wires_output_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "from orchestrator.output_service import OutputService, OutputServiceError" in source
    assert "self.output_service = OutputService(self.output_storage)" in source


def test_orchestrator_service_does_not_call_output_storage_operations_directly() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    forbidden_calls = [
        "self.output_storage.read_manifest(",
        "self.output_storage.list_files(",
        "self.output_storage.get_file_path(",
        "self.output_storage.get_media_type(",
        "self.output_storage.save_request_record(",
    ]

    for call in forbidden_calls:
        assert call not in source

    expected_calls = [
        "self.output_service.read_manifest(",
        "self.output_service.list_files(",
        "self.output_service.get_file_path(",
        "self.output_service.get_media_type(",
        "self.output_service.save_request_record(",
    ]

    for call in expected_calls:
        assert call in source
