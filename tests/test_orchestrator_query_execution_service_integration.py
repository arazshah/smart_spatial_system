from __future__ import annotations

from pathlib import Path


def test_orchestrator_service_wires_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "from orchestrator.query_execution_service import "
        "QueryExecutionService, QueryExecutionServiceError"
    ) in source
    assert "self.query_execution_service = QueryExecutionService(self)" in source


def test_orchestrator_handle_query_delegates_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "return self.query_execution_service.handle_query(" in source


def test_orchestrator_query_planning_handler_delegates_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._try_handle_query_with_planning("
        in source
    )


def test_query_execution_service_contains_query_entrypoints() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def handle_query(" in source
    assert "def _try_handle_query_with_planning(" in source
    assert "def __getattr__(self, name: str)" in source


def test_query_execution_service_contains_pure_planning_helpers() -> None:
    source = Path(
        "smart_spatial_system/application/services/query_execution_service.py"
    ).read_text(encoding="utf-8")

    assert "def _planning_trace_to_steps(" in source
    assert "def _planning_outputs_to_response_payload(" in source
    assert "def _enrich_query_database_params_from_inputs(" in source


def test_orchestrator_pure_planning_helpers_delegate_to_query_execution_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert (
        "return self.query_execution_service._planning_trace_to_steps("
        in source
    )
    assert (
        "return self.query_execution_service._planning_outputs_to_response_payload("
        in source
    )
    assert (
        "return self.query_execution_service._enrich_query_database_params_from_inputs("
        in source
    )
