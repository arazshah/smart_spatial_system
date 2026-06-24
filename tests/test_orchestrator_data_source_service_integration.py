from __future__ import annotations

from pathlib import Path


def test_orchestrator_service_wires_data_source_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "from orchestrator.data_source_service import DataSourceService, DataSourceServiceError" in source
    assert "self.data_source_service = DataSourceService(" in source


def test_orchestrator_data_source_methods_delegate_to_data_source_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    expected_calls = [
        "self.data_source_service.register_csv_table_source(",
        "self.data_source_service.register_wms_source(",
        "self.data_source_service.list_project_data_sources(",
        "self.data_source_service.get_data_source(",
        "self.data_source_service.delete_data_source(",
        "self.data_source_service.update_data_source(",
        "self.data_source_service.preview_data_source(",
        "self.data_source_service._normalize_data_source_metadata(",
        "self.data_source_service._build_json_preview(",
    ]

    for call in expected_calls:
        assert call in source
