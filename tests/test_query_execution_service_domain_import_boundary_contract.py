"""
Domain import boundary regression tests for QueryExecutionService.

QueryExecutionService is generic orchestration/application plumbing. It may
preserve compatibility methods temporarily, but it must not import domain-
specific real_estate query-execution modules at top-level.
"""

from __future__ import annotations

import ast
from pathlib import Path


SERVICE_PATH = Path("smart_spatial_system/application/services/query_execution_service.py")


def test_query_execution_service_does_not_top_level_import_real_estate_query_modules() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SERVICE_PATH))

    forbidden_prefix = (
        "smart_spatial_system.application.services.query_execution.real_estate"
    )

    offenders: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(forbidden_prefix):
                offenders.append(module)

    assert offenders == []


def test_query_execution_service_uses_lazy_domain_callable_boundary() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert "def _query_execution_domain_callable(" in source
    assert "importlib.import_module(" in source
