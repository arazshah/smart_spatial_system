"""
QuerySpec enrichment helpers.

These helpers prepare generated QuerySpecs with safe runtime values before
deterministic DAG planning/execution.
"""

from __future__ import annotations

from typing import Any


QUERY_DATABASE_RUNTIME_KEYS = (
    "host",
    "port",
    "database",
    "user",
    "password",
    "connect_timeout",
    "profile",
    "dsn",
    "schema",
    "table",
    "geom_col",
    "limit",
    "output_srid",
)


def enrich_query_database_params_from_inputs(
    query_spec: Any,
    resolved_inputs: dict[str, Any],
) -> None:
    """
    Inject runtime database connection parameters into query_database ops.

    The LLM should describe *what* to query, not invent secrets or runtime
    connection details. This function copies safe runtime inputs into the
    executable QuerySpec before DAG planning.
    """
    if not resolved_inputs:
        return

    operations = getattr(query_spec, "operations", None) or []

    for operation in operations:
        if getattr(operation, "op", None) != "query_database":
            continue

        params = getattr(operation, "params", None)

        if not isinstance(params, dict):
            continue

        for key in QUERY_DATABASE_RUNTIME_KEYS:
            value = resolved_inputs.get(key)

            if value is None:
                continue

            if key in params and params.get(key) not in (None, "", "<provided-at-runtime>"):
                continue

            params[key] = value

        # SQL mode normally exposes geometry as "AS geom".
        # If the LLM generated SQL and no geom_col is present, use "geom".
        if isinstance(params.get("sql"), str) and params.get("sql", "").strip():
            params.setdefault("geom_col", "geom")
