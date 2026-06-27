"""
PostGIS planning context helpers for query execution.

This module keeps PostGIS schema discovery, semantic planning context assembly,
and runtime connection input flattening out of QueryExecutionService.
"""

from __future__ import annotations

import importlib

import sys

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.planning.postgis_semantic_resolver import PostGISSchemaContext




_POSTGIS_SCHEMA_MODEL_NAMES = {
    "ColumnInfo",
    "PostGISSchemaContext",
    "PostGISTableInfo",
}


def _postgis_semantic_resolver_module():
    return importlib.import_module("orchestrator.planning.postgis_semantic_resolver")


def _semantic_planning_context_module():
    return importlib.import_module("orchestrator.planning.semantic_planning_context")


def _column_info_type():
    return getattr(_postgis_semantic_resolver_module(), "ColumnInfo")


def _postgis_table_info_type():
    return getattr(_postgis_semantic_resolver_module(), "PostGISTableInfo")


def _postgis_schema_context_type():
    return getattr(_postgis_semantic_resolver_module(), "PostGISSchemaContext")


def _discover_postgis_schema(connection_config):
    return getattr(_postgis_semantic_resolver_module(), "discover_postgis_schema")(
        connection_config
    )


def _build_semantic_planning_context(*args, **kwargs):
    return getattr(
        _semantic_planning_context_module(),
        "build_semantic_planning_context",
    )(*args, **kwargs)


def __getattr__(name: str):
    if name in _POSTGIS_SCHEMA_MODEL_NAMES:
        return getattr(_postgis_semantic_resolver_module(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _first_mapping_value(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return value
    return None


def _coerce_postgis_schema_context(value: Any) -> PostGISSchemaContext | None:
    """
    Convert a JSON-friendly schema context into PostGISSchemaContext.

    Accepted shape:
      {
        "tables": [
          {
            "schema": "public",
            "table": "planet_osm_point",
            "geom_col": "way",
            "geometry_type": "POINT",
            "srid": 3857,
            "estimated_rows": 1000,
            "columns": [
              {"name": "osm_id", "data_type": "bigint", "udt_name": "int8"},
              ...
            ]
          }
        ]
      }

    If value is already PostGISSchemaContext, it is returned as-is.
    """
    from orchestrator.planning.postgis_semantic_resolver import (
        ColumnInfo,
        PostGISSchemaContext,
        PostGISTableInfo,
    )

    if isinstance(value, _postgis_schema_context_type()):
        return value

    if not isinstance(value, dict):
        return None

    raw_tables = value.get("tables")
    if not isinstance(raw_tables, list):
        return None

    tables: list[PostGISTableInfo] = []

    for raw_table in raw_tables:
        if not isinstance(raw_table, dict):
            continue

        schema = str(raw_table.get("schema") or "").strip()
        table = str(raw_table.get("table") or "").strip()
        geom_col = str(raw_table.get("geom_col") or raw_table.get("geometry_column") or "").strip()

        if not schema or not table or not geom_col:
            continue

        raw_columns = raw_table.get("columns") or []
        columns: list[ColumnInfo] = []

        if isinstance(raw_columns, list):
            for raw_col in raw_columns:
                if isinstance(raw_col, dict):
                    name = str(raw_col.get("name") or "").strip()
                    if not name:
                        continue
                    columns.append(
                        _column_info_type()(
                            name=name,
                            data_type=str(raw_col.get("data_type") or ""),
                            udt_name=str(raw_col.get("udt_name") or ""),
                        )
                    )
                elif isinstance(raw_col, str) and raw_col.strip():
                    columns.append(_column_info_type()(name=raw_col.strip()))

        srid_value = raw_table.get("srid")
        try:
            srid = int(srid_value) if srid_value is not None else None
        except Exception:
            srid = None

        estimated_rows_value = raw_table.get("estimated_rows")
        try:
            estimated_rows = (
                int(estimated_rows_value)
                if estimated_rows_value is not None
                else None
            )
        except Exception:
            estimated_rows = None

        tables.append(
            _postgis_table_info_type()(
                schema=schema,
                table=table,
                geom_col=geom_col,
                geometry_type=str(raw_table.get("geometry_type") or ""),
                srid=srid,
                columns=tuple(columns),
                estimated_rows=estimated_rows,
            )
        )

    if not tables:
        return None

    return _postgis_schema_context_type()(tables=tuple(tables))


_POSTGIS_CONNECTION_KEYS = (
    "postgis_connection",
    "postgis",
    "database_connection",
    "db_connection",
    "connection",
)


def _looks_like_postgis_connection(value: Any) -> bool:
    if not isinstance(value, dict):
        return False

    lowered = {
        str(k).lower(): v
        for k, v in value.items()
    }

    for key in ("source_type", "type", "driver", "dialect", "engine"):
        raw = lowered.get(key)
        if raw is not None and str(raw).strip().lower() in {
            "postgis",
            "postgres",
            "postgresql",
        }:
            return True

    if lowered.get("dsn"):
        dsn = str(lowered.get("dsn") or "").lower()
        if "postgres" in dsn or "postgis" in dsn or "dbname=" in dsn:
            return True

    has_database = any(k in lowered for k in ("database", "dbname", "db_name"))
    has_host = "host" in lowered or "hostname" in lowered
    has_user = "user" in lowered or "username" in lowered

    # Host+database is usually enough for explicit postgis_connection containers.
    return bool(has_database and (has_host or has_user))


def _normalize_postgis_connection_config(value: Any) -> dict[str, Any] | None:
    """
    Normalize a PostGIS/PostgreSQL connection config.

    Returns a safe internal dict with only connection-related fields:
      dsn, host, port, database, user, password, connect_timeout, schemas

    It intentionally ignores arbitrary extra fields.
    """
    if not _looks_like_postgis_connection(value):
        return None

    raw = dict(value)

    def pick(*names: str) -> Any:
        for name in names:
            if name in raw and raw.get(name) not in (None, ""):
                return raw.get(name)
        return None

    config: dict[str, Any] = {}

    dsn = pick("dsn", "url", "uri")
    if dsn:
        config["dsn"] = str(dsn)

    host = pick("host", "hostname")
    if host:
        config["host"] = str(host)

    port = pick("port")
    if port not in (None, ""):
        try:
            config["port"] = int(port)
        except Exception:
            config["port"] = str(port)

    database = pick("database", "dbname", "db_name")
    if database:
        config["database"] = str(database)

    user = pick("user", "username")
    if user:
        config["user"] = str(user)

    password = pick("password", "pass")
    if password:
        config["password"] = str(password)

    timeout = pick("connect_timeout", "timeout")
    if timeout not in (None, ""):
        try:
            config["connect_timeout"] = int(timeout)
        except Exception:
            config["connect_timeout"] = timeout

    schemas = pick("schemas", "schema")
    if schemas:
        if isinstance(schemas, str):
            config["schemas"] = [schemas]
        elif isinstance(schemas, (list, tuple, set)):
            config["schemas"] = [str(s) for s in schemas if str(s).strip()]

    if not config.get("dsn") and not config.get("database"):
        return None

    return config


def _extract_postgis_connection_config_from_sources(
    *,
    resolved_inputs: dict[str, Any] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Find PostGIS connection configuration in known request/context locations.

    Priority:
      user_context → metadata → resolved_inputs
    """
    sources = (
        user_context or {},
        metadata or {},
        resolved_inputs or {},
    )

    # First check known container keys.
    for source in sources:
        if not isinstance(source, dict):
            continue

        for key in _POSTGIS_CONNECTION_KEYS:
            candidate = source.get(key)
            normalized = _normalize_postgis_connection_config(candidate)
            if normalized is not None:
                return normalized

    # Then allow the source object itself to be a connection config.
    for source in sources:
        normalized = _normalize_postgis_connection_config(source)
        if normalized is not None:
            return normalized

    return None


def _discover_postgis_schema_context_from_connection_config(
    connection_config: dict[str, Any],
) -> PostGISSchemaContext:
    """
    Open a psycopg2 connection and discover PostGIS schema.

    The caller receives exceptions; higher-level planning should handle them
    non-fatally.
    """
    from orchestrator.planning.postgis_semantic_resolver import (
        discover_postgis_schema,
    )

    import psycopg2

    schemas = connection_config.get("schemas") or None

    connect_timeout = connection_config.get("connect_timeout", 5)

    if connection_config.get("dsn"):
        conn = psycopg2.connect(
            connection_config["dsn"],
            connect_timeout=connect_timeout,
        )
    else:
        kwargs: dict[str, Any] = {
            "host": connection_config.get("host"),
            "port": connection_config.get("port"),
            "dbname": connection_config.get("database"),
            "user": connection_config.get("user"),
            "password": connection_config.get("password"),
            "connect_timeout": connect_timeout,
        }
        kwargs = {k: v for k, v in kwargs.items() if v not in (None, "")}
        conn = psycopg2.connect(**kwargs)

    try:
        return _discover_postgis_schema(
            conn,
            schemas=schemas,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _build_query_spec_runtime_inputs(
    *,
    resolved_inputs: dict[str, Any] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """
    Build runtime inputs used to enrich executable QuerySpec operation params.

    Important:
    LLM/semantic planning creates safe logical query_database params:
      schema/table/columns/geom_col/where

    Runtime execution also needs connection params:
      host/port/database/user/password/connect_timeout/dsn/profile

    UI often sends those params nested under:
      user_context.postgis_connection
      user_context.postgis
      metadata.postgis_connection
      resolved_inputs.postgis_connection

    `_enrich_query_database_params_from_inputs` expects top-level keys, so this
    helper flattens the normalized connection config into runtime_inputs.

    Password is included only in runtime inputs for execution; response metadata
    must continue using redaction helpers.
    """
    runtime_inputs: dict[str, Any] = dict(resolved_inputs or {})

    connection_config = _extract_postgis_connection_config_from_sources(
        resolved_inputs=resolved_inputs,
        user_context=user_context,
        metadata=metadata,
    )

    if connection_config is None:
        return runtime_inputs, False

    runtime_inputs.update(connection_config)
    runtime_inputs.setdefault("postgis_connection", connection_config)
    runtime_inputs.setdefault("database_connection", connection_config)

    return runtime_inputs, True



def _resolve_postgis_schema_discoverer():
    """
    Resolve the PostGIS schema discoverer.

    The implementation lives in this module, but older tests/callers may
    monkeypatch orchestrator.service._discover_postgis_schema_context_from_connection_config.
    Honor that monkeypatch without moving the implementation back to
    orchestrator.service.
    """
    service_module = sys.modules.get("orchestrator.service")
    service_discoverer = getattr(
        service_module,
        "_discover_postgis_schema_context_from_connection_config",
        None,
    )

    if (
        callable(service_discoverer)
        and service_discoverer is not _discover_postgis_schema_context_from_connection_config
    ):
        return service_discoverer

    return _discover_postgis_schema_context_from_connection_config


def _extract_semantic_planning_context_from_sources(
    *,
    query: str,
    resolved_inputs: dict[str, Any] | None,
    user_context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Build or forward semantic_planning_context for LLM planning.

    Priority:
    1. Existing semantic_planning_context from user_context/metadata/resolved_inputs.
    2. Build from postgis_schema_context if provided.

    This function is intentionally non-fatal: errors are returned as strings and
    must not break the existing planning path.
    """
    try:
        user_context = user_context or {}
        metadata = metadata or {}
        resolved_inputs = resolved_inputs or {}

        existing = _first_mapping_value(
            user_context.get("semantic_planning_context"),
            metadata.get("semantic_planning_context"),
            resolved_inputs.get("semantic_planning_context"),
        )
        if existing is not None:
            return existing, None

        schema_source = _first_mapping_value(
            user_context.get("postgis_schema_context"),
            user_context.get("postgis_schema"),
            metadata.get("postgis_schema_context"),
            metadata.get("postgis_schema"),
            resolved_inputs.get("postgis_schema_context"),
            resolved_inputs.get("postgis_schema"),
        )

        schema_context = _coerce_postgis_schema_context(schema_source)

        if schema_context is None:
            connection_config = _extract_postgis_connection_config_from_sources(
                resolved_inputs=resolved_inputs,
                user_context=user_context,
                metadata=metadata,
            )

            if connection_config is None:
                return None, None

            schema_context = _resolve_postgis_schema_discoverer()(
                connection_config
            )

        explicit_concepts = (
            user_context.get("semantic_concepts")
            or metadata.get("semantic_concepts")
            or resolved_inputs.get("semantic_concepts")
            or None
        )

        from orchestrator.planning.semantic_planning_context import (
            build_semantic_planning_context,
        )

        context = _build_semantic_planning_context(
            query,
            schema_context,
            explicit_concepts=explicit_concepts,
        )

        return context.to_dict(), None

    except Exception as exc:
        return None, str(exc)
