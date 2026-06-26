from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_query_spec_planning_context(
    *,
    query: str,
    resolved_inputs: dict[str, Any],
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    project_id: str | None,
    response_language: Any,
    extract_semantic_planning_context: Callable[..., tuple[Any, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    semantic_planning_context, semantic_planning_context_error = (
        extract_semantic_planning_context(
            query=query,
            resolved_inputs=resolved_inputs,
            user_context=user_context,
            metadata=metadata,
        )
    )

    planning_context: dict[str, Any] = {
        "available_inputs": sorted((resolved_inputs or {}).keys()),
        "response_language": response_language,
        "project_id": project_id,
        "query_spec_contracts": {
            "query_database": {
                "contract": "query_database.postgis.v1",
                "required_format": {
                    "source_type": "postgis",
                    "mode": "select_table",
                    "schema": "public",
                    "table": "table_name_without_schema",
                    "columns": ["property_column_1", "property_column_2"],
                    "geom_col": "real_geometry_column",
                    "geom_alias": "geom",
                    "where": "optional safe where clause",
                    "limit": 1000,
                    "output_srid": 4326,
                },
                "rules": [
                    "Do not use sql.",
                    "Do not use select.",
                    "Do not use fields.",
                    "Do not use projection.",
                    "Do not invent parameter names.",
                    "columns must contain only property column names.",
                    "Do not put geometry expressions like 'way AS geom' in columns.",
                    "Use geom_col for the real geometry column and geom_alias for the output geometry alias.",
                ],
                "valid_example": {
                    "op": "query_database",
                    "inputs": {},
                    "params": {
                        "source_type": "postgis",
                        "mode": "select_table",
                        "schema": "public",
                        "table": "osm_tehran_parks",
                        "columns": ["osm_id", "name"],
                        "geom_col": "way",
                        "geom_alias": "geom",
                        "where": "way IS NOT NULL",
                        "limit": 10,
                        "output_srid": 4326,
                    },
                    "output": "parks_layer",
                },
            }
        },
    }

    metadata_updates: dict[str, Any] = {}

    if semantic_planning_context is not None:
        planning_context["semantic_planning_context"] = semantic_planning_context
        metadata_updates["semantic_planning_context_attached"] = True

    if semantic_planning_context_error:
        metadata_updates["semantic_planning_context_error"] = semantic_planning_context_error

    return planning_context, metadata_updates
