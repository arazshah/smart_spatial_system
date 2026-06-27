"""
Regression tests for PostGIS planning context extraction.

The QueryExecutionService should orchestrate query execution, while PostGIS
schema/context helpers live in query_execution.postgis_planning_context.
"""

from __future__ import annotations

from pathlib import Path

from smart_spatial_system.application.services.query_execution.postgis_planning_context import (
    _build_query_spec_runtime_inputs,
    _extract_semantic_planning_context_from_sources,
)


QUERY_EXECUTION_SERVICE_PATH = Path(
    "smart_spatial_system/application/services/query_execution_service.py"
)


def test_query_execution_service_delegates_postgis_planning_context_helpers() -> None:
    source = QUERY_EXECUTION_SERVICE_PATH.read_text(encoding="utf-8")

    assert (
        "from smart_spatial_system.application.services.query_execution."
        "postgis_planning_context import"
    ) in source

    assert "_build_query_spec_runtime_inputs" in source
    assert "_extract_semantic_planning_context_from_sources" in source

    assert "import psycopg2" not in source
    assert "def _discover_postgis_schema_context_from_connection_config" not in source
    assert "def _normalize_postgis_connection_config" not in source
    assert "def _coerce_postgis_schema_context" not in source


def test_build_query_spec_runtime_inputs_flattens_postgis_connection() -> None:
    runtime_inputs, found = _build_query_spec_runtime_inputs(
        resolved_inputs={
            "layer": "parcels",
        },
        user_context={
            "postgis_connection": {
                "source_type": "postgis",
                "host": "localhost",
                "port": "5432",
                "database": "gis",
                "user": "postgres",
                "password": "secret",
                "connect_timeout": "7",
                "schemas": [
                    "public",
                    "osm",
                ],
            },
        },
        metadata=None,
    )

    assert found is True
    assert runtime_inputs["layer"] == "parcels"
    assert runtime_inputs["host"] == "localhost"
    assert runtime_inputs["port"] == 5432
    assert runtime_inputs["database"] == "gis"
    assert runtime_inputs["user"] == "postgres"
    assert runtime_inputs["password"] == "secret"
    assert runtime_inputs["connect_timeout"] == 7
    assert runtime_inputs["schemas"] == ["public", "osm"]
    assert runtime_inputs["postgis_connection"]["database"] == "gis"
    assert runtime_inputs["database_connection"]["database"] == "gis"


def test_extract_semantic_planning_context_forwards_existing_context() -> None:
    existing_context = {
        "tables": [
            {
                "schema": "public",
                "table": "parcels",
                "geom_col": "geom",
            }
        ],
        "concepts": {
            "parcel": "public.parcels",
        },
    }

    context, error = _extract_semantic_planning_context_from_sources(
        query="پارسل‌های نزدیک مدرسه را پیدا کن",
        resolved_inputs={},
        user_context={
            "semantic_planning_context": existing_context,
        },
        metadata={},
    )

    assert error is None
    assert context == existing_context


def test_build_query_spec_runtime_inputs_without_connection_is_noop() -> None:
    runtime_inputs, found = _build_query_spec_runtime_inputs(
        resolved_inputs={
            "table": "parcels",
        },
        user_context={},
        metadata={},
    )

    assert found is False
    assert runtime_inputs == {
        "table": "parcels",
    }
