"""
Regression tests for PostGIS planning helper ownership.

orchestrator.service keeps backward-compatible imports for older callers/tests,
but the actual implementation must live in:
smart_spatial_system.application.services.query_execution.postgis_planning_context
"""

from __future__ import annotations

from pathlib import Path

import orchestrator.service as service_module
from smart_spatial_system.application.services.query_execution import (
    postgis_planning_context as postgis_context,
)


SERVICE_PATH = Path("orchestrator/service.py")


def test_orchestrator_service_reexports_postgis_helpers_from_query_execution_module() -> None:
    assert (
        service_module._build_query_spec_runtime_inputs
        is postgis_context._build_query_spec_runtime_inputs
    )
    assert (
        service_module._extract_semantic_planning_context_from_sources
        is postgis_context._extract_semantic_planning_context_from_sources
    )
    assert (
        service_module._normalize_postgis_connection_config
        is postgis_context._normalize_postgis_connection_config
    )
    assert (
        service_module._coerce_postgis_schema_context
        is postgis_context._coerce_postgis_schema_context
    )


def test_orchestrator_service_does_not_own_duplicate_postgis_helper_implementations() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")

    duplicate_function_defs = [
        "def _first_mapping_value(",
        "def _coerce_postgis_schema_context(",
        "def _looks_like_postgis_connection(",
        "def _normalize_postgis_connection_config(",
        "def _discover_postgis_schema_context_from_connection_config(",
        "def _build_query_spec_runtime_inputs(",
        "def _extract_semantic_planning_context_from_sources(",
    ]

    for function_def in duplicate_function_defs:
        assert function_def not in source

    assert (
        "from smart_spatial_system.application.services.query_execution.postgis_planning_context import"
        in source
    )
