"""
Strict QuerySpec contract validation.

The LLM is allowed to describe intent only inside canonical schemas.
It must not invent executable plugin parameters.

Contract V1 focuses on query_database/PostGIS:
    - no raw SQL from LLM
    - no ad-hoc select string
    - no geometry expression in columns
    - deterministic compiler builds SQL later
"""

from __future__ import annotations

import re
from typing import Any

from orchestrator.planning.spec import QuerySpec


class QuerySpecContractError(ValueError):
    """Raised when LLM-generated QuerySpec violates the execution contract."""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_FORBIDDEN_WHERE_TOKENS = [
    ";",
    "--",
    "/*",
    "*/",
    " drop ",
    " delete ",
    " update ",
    " insert ",
    " alter ",
    " truncate ",
    " create ",
    " grant ",
    " revoke ",
    " copy ",
    " vacuum ",
    " execute ",
    " call ",
]

QUERY_DATABASE_ALLOWED_PARAMS = {
    # Canonical planning params
    "source_type",
    "mode",
    "schema",
    "table",
    "columns",
    "geom_col",
    "geom_alias",
    "where",
    "limit",
    "output_srid",

    # Runtime/execution params injected by service, not invented by LLM
    "profile",
    "dsn",
    "host",
    "port",
    "database",
    "user",
    "password",
    "connect_timeout",
}

QUERY_DATABASE_FORBIDDEN_PARAMS = {
    "sql",
    "query",
    "select",
    "fields",
    "projection",
    "select_columns",
    "geometry",
    "geom",
}


def _validate_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuerySpecContractError(f"{label} must be a non-empty string.")

    cleaned = value.strip()

    if not _IDENTIFIER_RE.match(cleaned):
        raise QuerySpecContractError(
            f"{label} must be a safe SQL identifier, got {value!r}. "
            "Only letters, numbers and underscores are allowed, and it must not start with a number."
        )

    return cleaned


def _validate_optional_identifier(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _validate_identifier(value, label)


def _validate_columns(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise QuerySpecContractError(
            "query_database.params.columns must be a list of property column names."
        )

    columns: list[str] = []

    for index, item in enumerate(value):
        col = _validate_identifier(item, f"query_database.params.columns[{index}]")

        lowered = col.lower()
        if " as " in lowered or "(" in lowered or ")" in lowered or "." in lowered:
            raise QuerySpecContractError(
                "query_database.params.columns must contain only raw property column names. "
                "Do not put geometry aliases or SQL expressions in columns. "
                "Use geom_col and geom_alias for geometry."
            )

        columns.append(col)

    return columns


def _validate_where(value: Any) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise QuerySpecContractError("query_database.params.where must be a string or null.")

    cleaned = value.strip()
    if not cleaned:
        return None

    lowered = f" {cleaned.lower()} "
    for token in _FORBIDDEN_WHERE_TOKENS:
        if token in lowered:
            raise QuerySpecContractError(
                f"Unsafe token found in query_database.params.where: {token.strip()}"
            )

    return cleaned


def _validate_limit(value: Any) -> int | None:
    if value is None:
        return None

    if not isinstance(value, int) or isinstance(value, bool):
        raise QuerySpecContractError("query_database.params.limit must be an integer.")

    if value < 0:
        raise QuerySpecContractError("query_database.params.limit must be >= 0.")

    if value > 100000:
        raise QuerySpecContractError("query_database.params.limit is too large. Maximum is 100000.")

    return value


def _validate_output_srid(value: Any) -> int | None:
    if value is None:
        return None

    if not isinstance(value, int) or isinstance(value, bool):
        raise QuerySpecContractError("query_database.params.output_srid must be an integer.")

    if value <= 0:
        raise QuerySpecContractError("query_database.params.output_srid must be positive.")

    return value


def _validate_query_database_params(params: dict[str, Any], *, operation_index: int) -> None:
    unknown = sorted(set(params) - QUERY_DATABASE_ALLOWED_PARAMS)
    forbidden = sorted(set(params) & QUERY_DATABASE_FORBIDDEN_PARAMS)

    if forbidden:
        raise QuerySpecContractError(
            f"operations[{operation_index}].params contains forbidden query_database keys: {forbidden}. "
            "Use the canonical schema: source_type, mode, schema, table, columns, geom_col, "
            "geom_alias, where, limit, output_srid. Do not use sql/select/fields/projection."
        )

    if unknown:
        raise QuerySpecContractError(
            f"operations[{operation_index}].params contains unsupported query_database keys: {unknown}. "
            f"Allowed keys: {sorted(QUERY_DATABASE_ALLOWED_PARAMS)}"
        )

    source_type = params.get("source_type", "postgis")
    if source_type != "postgis":
        raise QuerySpecContractError(
            f"operations[{operation_index}].params.source_type must be 'postgis'."
        )

    mode = params.get("mode", "select_table")
    if mode not in {"select_table", "table_layer"}:
        raise QuerySpecContractError(
            f"operations[{operation_index}].params.mode must be 'select_table' or 'table_layer'."
        )

    _validate_optional_identifier(params.get("schema"), f"operations[{operation_index}].params.schema")
    _validate_identifier(params.get("table"), f"operations[{operation_index}].params.table")

    if mode == "select_table":
        _validate_columns(params.get("columns"))
        _validate_identifier(params.get("geom_col"), f"operations[{operation_index}].params.geom_col")
        _validate_identifier(
            params.get("geom_alias") or "geom",
            f"operations[{operation_index}].params.geom_alias",
        )

    if mode == "table_layer":
        # Table layer mode may rely on plugin auto-detection/fallback for geom_col.
        _validate_optional_identifier(
            params.get("geom_col"),
            f"operations[{operation_index}].params.geom_col",
        )

    _validate_where(params.get("where"))
    _validate_limit(params.get("limit"))
    _validate_output_srid(params.get("output_srid"))


def validate_query_spec_contract(query_spec: QuerySpec) -> None:
    """
    Validate QuerySpec against strict execution contracts.

    This must run after LLM generation and runtime input enrichment, but before
    planner/DAG execution.
    """
    for index, operation in enumerate(query_spec.operations):
        if operation.op != "query_database":
            continue

        if not isinstance(operation.params, dict):
            raise QuerySpecContractError(f"operations[{index}].params must be an object.")

        _validate_query_database_params(operation.params, operation_index=index)
