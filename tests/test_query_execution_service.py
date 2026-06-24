from __future__ import annotations

import pytest

from orchestrator.query_execution_service import (
    QueryExecutionService,
    QueryExecutionServiceError,
)
from smart_spatial_system.application.services.query_execution_service import (
    QueryExecutionService as NewQueryExecutionService,
)


class FakeContext:
    def __init__(self) -> None:
        self.config = {"name": "fake-config"}

    def helper(self) -> str:
        return "ok"


def test_orchestrator_query_execution_service_wrapper_points_to_new_layout() -> None:
    assert QueryExecutionService is NewQueryExecutionService


def test_query_execution_service_requires_context_dependency() -> None:
    with pytest.raises(QueryExecutionServiceError):
        QueryExecutionService(None)


def test_query_execution_service_delegates_missing_attributes_to_context() -> None:
    context = FakeContext()
    service = QueryExecutionService(context)

    assert service.config == {"name": "fake-config"}
    assert service.helper() == "ok"
