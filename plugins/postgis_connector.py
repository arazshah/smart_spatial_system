"""
postgis_connector.py

GeoChat SDK Plugin
==================

Plugin ID:
    postgis_connector

Purpose:
    Connect to PostgreSQL/PostGIS, fetch a spatial table/layer,
    convert rows to GeoJSON Features using PostGIS functions,
    and return a standard VectorOut object.

New config-aware behavior:
    The plugin can receive connection information directly as function parameters
    or load it from config/plugins/postgis_connector.yaml using a profile.

Usage:
    fetch_postgis_layer(profile="local", table="roads")

Config:
    config/plugins/postgis_connector.yaml
"""

from __future__ import annotations

import json
import re
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.exceptions import SDKDependencyError

from orchestrator.provider_error_mapping import make_provider_execution_error
from plugins._shared.plugin_config import get_profile_config, pick_first


PLUGIN_ID = "postgis_connector"

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


def _validate_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")

    value = value.strip()

    if not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"Unsafe SQL identifier for {field_name}: {value!r}. "
            "Only letters, numbers and underscores are allowed, and it must not start with a number."
        )

    return value


def _quote_identifier(value: str) -> str:
    return f'"{value}"'


def _validate_limit(limit: int) -> int:
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer.")

    if limit < 0:
        raise ValueError("limit must be greater than or equal to 0.")

    if limit > 100000:
        raise ValueError("limit is too large. Maximum allowed limit is 100000.")

    return limit


def _validate_output_srid(output_srid: int | None) -> int | None:
    if output_srid is None:
        return None

    if not isinstance(output_srid, int):
        raise ValueError("output_srid must be an integer or None.")

    if output_srid <= 0:
        raise ValueError("output_srid must be a positive integer.")

    return output_srid


def _validate_where_clause(where: str | None) -> str | None:
    if where is None:
        return None

    if not isinstance(where, str):
        raise ValueError("where must be a string or None.")

    cleaned = where.strip()
    if not cleaned:
        return None

    lowered = f" {cleaned.lower()} "

    for token in _FORBIDDEN_WHERE_TOKENS:
        if token in lowered:
            raise ValueError(f"Unsafe token found in where clause: {token.strip()}")

    return cleaned


def _build_conninfo(
    *,
    dsn: str | None = None,
    host: str | None = None,
    port: int = 5432,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
    connect_timeout: int = 10,
) -> str:
    """
    Build PostgreSQL connection info.

    Either provide dsn or host/database/user/password.
    """
    if dsn is not None and isinstance(dsn, str) and dsn.strip():
        return dsn.strip()

    if not host or not isinstance(host, str):
        raise ValueError("Either dsn or host must be provided.")

    if not database or not isinstance(database, str):
        raise ValueError("Either dsn or database must be provided.")

    if not user or not isinstance(user, str):
        raise ValueError("Either dsn or user must be provided.")

    if password is None or not isinstance(password, str):
        raise ValueError("Either dsn or password must be provided.")

    if not isinstance(port, int) or port <= 0:
        raise ValueError("port must be a positive integer.")

    if not isinstance(connect_timeout, int) or connect_timeout <= 0:
        raise ValueError("connect_timeout must be a positive integer.")

    return " ".join([
        f"host={host}",
        f"port={port}",
        f"dbname={database}",
        f"user={user}",
        f"password={password}",
        f"connect_timeout={connect_timeout}",
    ])



def _sql_text_literal(value: str) -> str:
    """Build a safe single-quoted SQL string literal for known-safe values."""
    if not isinstance(value, str):
        raise ValueError("SQL text literal must be a string.")
    return "'" + value.replace("'", "''") + "'"


def _build_select_features_sql(
    *,
    schema: str,
    table: str,
    geom_col: str,
    where: str | None,
    limit: int,
    output_srid: int | None,
) -> tuple[str, list[Any]]:
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    geom_col = _validate_identifier(geom_col, "geom_col")
    limit = _validate_limit(limit)
    output_srid = _validate_output_srid(output_srid)
    where = _validate_where_clause(where)

    schema_sql = _quote_identifier(schema)
    table_sql = _quote_identifier(table)
    geom_sql = f't.{_quote_identifier(geom_col)}'

    params: list[Any] = []

    if output_srid is not None:
        geometry_expr = (
            f"CASE WHEN {geom_sql} IS NULL THEN NULL "
            f"ELSE ST_AsGeoJSON(ST_Transform({geom_sql}, {int(output_srid)}))::jsonb END"
        )
    else:
        geometry_expr = (
            f"CASE WHEN {geom_sql} IS NULL THEN NULL "
            f"ELSE ST_AsGeoJSON({geom_sql})::jsonb END"
        )

    sql = f"""
SELECT
    jsonb_build_object(
        'type', 'Feature',
        'geometry', {geometry_expr},
        'properties', to_jsonb(t) - {_sql_text_literal(geom_col)}
    )::text AS feature
FROM {schema_sql}.{table_sql} AS t
""".strip()

    if where:
        sql += f"\nWHERE {where}"

    sql += f"\nLIMIT {int(limit)}"

    return sql, []


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    if not geometry:
        return None

    coords = geometry.get("coordinates")
    if coords is None:
        return None

    xs: list[float] = []
    ys: list[float] = []

    def walk(obj: Any) -> None:
        if (
            isinstance(obj, (list, tuple))
            and len(obj) >= 2
            and _is_number(obj[0])
            and _is_number(obj[1])
        ):
            xs.append(float(obj[0]))
            ys.append(float(obj[1]))
            return

        if isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(coords)

    if not xs or not ys:
        return None

    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_bboxes(bboxes: list[list[float]]) -> dict[str, float] | None:
    valid = [b for b in bboxes if b and len(b) == 4]
    if not valid:
        return None

    return {
        "minx": min(b[0] for b in valid),
        "miny": min(b[1] for b in valid),
        "maxx": max(b[2] for b in valid),
        "maxy": max(b[3] for b in valid),
    }



def _safe_decode_database_bytes(value: object, *, label: str = "database value") -> object:
    """
    Decode byte-like database values without crashing on malformed sequences.

    Some drivers/adapters may return json/jsonb/geometry helper values as bytes
    or memoryview. For spatial pipelines, one malformed textual value must not
    abort the whole query. We prefer strict UTF-8, then fall back to replacement.
    """
    if isinstance(value, memoryview):
        value = value.tobytes()

    if isinstance(value, bytearray):
        value = bytes(value)

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace")

    return value


def _safe_json_loads_database_value(value: object, *, label: str = "database json value") -> object:
    """
    Parse JSON-like values returned by PostGIS/psycopg safely.

    Accepted:
      - dict/list: returned as-is
      - str: json.loads
      - bytes/memoryview: safe decode then json.loads
    """
    import json

    value = _safe_decode_database_bytes(value, label=label)

    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception as exc:
            preview = value[:300]
            raise ValueError(f"Could not parse {label} as JSON. Preview={preview!r}. Error: {exc}") from exc

    return value

def _normalize_feature(value: Any, row_index: int) -> dict[str, Any]:
    if isinstance(value, bytes):
        value = _safe_decode_database_bytes(value, label="GeoJSON geometry")

    if isinstance(value, str):
        try:
            value = _safe_json_loads_database_value(value, label="GeoJSON geometry")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Database row {row_index} does not contain valid JSON.") from exc

    if not isinstance(value, dict):
        raise ValueError(f"Database row {row_index} feature must be a JSON object.")

    if value.get("type") != "Feature":
        raise ValueError(f"Database row {row_index} is not a GeoJSON Feature.")

    properties = value.get("properties")
    if properties is None:
        properties = {}

    if not isinstance(properties, dict):
        raise ValueError(f"Database row {row_index} properties must be an object or null.")

    if "geometry" not in value:
        value["geometry"] = None

    return {
        "type": "Feature",
        "geometry": value.get("geometry"),
        "properties": properties,
    }


def _row_to_feature(row: Any, row_index: int) -> dict[str, Any]:
    if isinstance(row, dict):
        if "feature" not in row:
            raise ValueError(f"Database row {row_index} does not contain 'feature' column.")
        return _normalize_feature(row["feature"], row_index)

    if isinstance(row, (tuple, list)):
        if not row:
            raise ValueError(f"Database row {row_index} is empty.")
        return _normalize_feature(row[0], row_index)

    raise ValueError(f"Unsupported database row type at index {row_index}: {type(row).__name__}")


def _escape_literal_percent_for_pyformat(sql: str) -> str:
    """
    Escape literal percent signs for psycopg/psycopg2 pyformat execution.

    psycopg uses %s placeholders. Therefore literal SQL patterns such as:
        ILIKE '%مترو%'
        ILIKE '%metro%'
    must be sent to cursor.execute(sql, params) as:
        ILIKE '%%مترو%%'
        ILIKE '%%metro%%'

    Keep real %s placeholders intact.
    Keep already-escaped %% intact.
    Escape every other %.
    """
    if "%" not in sql:
        return sql

    out: list[str] = []
    i = 0

    while i < len(sql):
        ch = sql[i]

        if ch != "%":
            out.append(ch)
            i += 1
            continue

        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        # Real positional placeholder.
        if nxt == "s":
            out.append("%s")
            i += 2
            continue

        # Already escaped literal percent.
        if nxt == "%":
            out.append("%%")
            i += 2
            continue

        # Any other percent is a literal percent and must be escaped for
        # pyformat parsing. This includes Persian/UTF-8 sequences after %.
        out.append("%%")
        i += 1

    return "".join(out)


def _execute_postgis_query(
    *,
    conninfo: str,
    sql: str,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Execute a compiled read-only PostGIS query and convert feature rows.

    SQL is built deterministically with all safe values inlined, so normally no
    bound parameters are required. This avoids pyformat placeholder conflicts
    with literal LIKE patterns such as '%مترو%' or '%subway%'.

    Primary driver is psycopg v3, with a psycopg2 fallback for rare driver-level
    decode edge cases on multilingual OSM data.
    """
    bound_params = list(params or [])

    def rows_to_features(rows: list[Any]) -> list[dict[str, Any]]:
        return [_row_to_feature(row, row_index) for row_index, row in enumerate(rows)]

    def is_retryable_driver_error(exc: BaseException) -> bool:
        if isinstance(exc, UnicodeDecodeError):
            return True
        message = str(exc).lower()
        return (
            "unicode" in message
            or "utf-8" in message
            or "codec can't decode" in message
            or "decode byte" in message
        )

    def run_with_psycopg() -> list[dict[str, Any]]:
        try:
            import psycopg  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "The postgis_connector plugin requires 'psycopg'. "
                "Install it with: pip install psycopg[binary]"
            ) from exc

        with psycopg.connect(conninfo) as conn:
            try:
                conn.execute("SET client_encoding TO 'UTF8'")
            except Exception:
                pass

            with conn.cursor() as cur:
                if bound_params:
                    cur.execute(sql, bound_params)
                else:
                    cur.execute(sql)
                rows = cur.fetchall()

        return rows_to_features(rows)

    def run_with_psycopg2() -> list[dict[str, Any]]:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(conninfo)
        try:
            try:
                conn.set_client_encoding("UTF8")
            except Exception:
                pass

            with conn.cursor() as cur:
                if bound_params:
                    cur.execute(sql, bound_params)
                else:
                    cur.execute(sql)
                rows = cur.fetchall()

            return rows_to_features(rows)
        finally:
            conn.close()

    primary_error: BaseException | None = None

    try:
        return run_with_psycopg()
    except Exception as exc:
        primary_error = exc
        if not is_retryable_driver_error(exc):
            message = f"Failed to execute PostGIS query. Error: {exc}"
            raise make_provider_execution_error(
                exc,
                provider="postgis",
                operation="execute_query",
                source="postgis_connector",
                message=message,
                details={
                    "driver": "psycopg",
                    "sql_preview": sql[:300],
                    "param_count": len(bound_params),
                },
            ) from exc

    try:
        return run_with_psycopg2()
    except Exception as fallback_exc:
        message = (
            "Failed to execute PostGIS query. "
            f"Primary psycopg error: {primary_error}. "
            f"psycopg2 fallback error: {fallback_exc}"
        )
        raise make_provider_execution_error(
            fallback_exc,
            provider="postgis",
            operation="execute_query",
            source="postgis_connector",
            message=message,
            details={
                "driver": "psycopg2",
                "primary_driver_error_type": (
                    type(primary_error).__name__ if primary_error is not None else None
                ),
                "sql_preview": sql[:300],
                "param_count": len(bound_params),
            },
        ) from fallback_exc


def _auto_detect_geom_column(
    conninfo: str,
    schema: str,
    table: str,
) -> str | None:
    """
    Query geometry_columns to auto-detect the geometry column name.

    Returns None if detection fails (e.g. psycopg not installed,
    table not registered in geometry_columns, or connection error).
    """
    try:
        import psycopg
    except ImportError:
        return None

    sql = """
        SELECT f_geometry_column
        FROM geometry_columns
        WHERE f_table_schema = %s
          AND f_table_name = %s
        LIMIT 1
    """

    try:
        with psycopg.connect(conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (schema, table))
                row = cur.fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass

    return None


def _build_metadata(
    *,
    features: list[dict[str, Any]],
    schema: str,
    table: str,
    geom_col: str,
    where: str | None,
    limit: int,
    output_srid: int | None,
    host: str | None,
    database: str | None,
    profile: str | None,
) -> dict[str, Any]:
    geometry_types: dict[str, int] = {}
    bboxes: list[list[float]] = []

    for feature in features:
        geometry = feature.get("geometry")

        if isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "Unknown")
            bbox = _geometry_bbox(geometry)
            if bbox is not None:
                bboxes.append(bbox)
        elif geometry is None:
            gtype = "Null"
        else:
            gtype = "Invalid"

        geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

    return {
        "source": "postgis",
        "loader": PLUGIN_ID,
        "format": "geojson_features",
        "schema": schema,
        "table": table,
        "layer": f"{schema}.{table}",
        "geom_col": geom_col,
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": _merge_bboxes(bboxes),
        "where_applied": bool(where),
        "limit": limit,
        "output_srid": output_srid,
        "crs": f"EPSG:{output_srid}" if output_srid else None,
        "profile": profile,
        "connection": {
            "host": host,
            "database": database,
        },
    }


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(value)


@capability(
    name="fetch_postgis_layer",
    keywords=[
        "postgis",
        "postgres",
        "postgresql",
        "database",
        "spatial database",
        "postgis layer",
        "fetch layer",
        "load postgis",
        "read postgis",
        "database layer",
        "sql layer",
        "db layer",
        "gis database",
        "پست‌جیس",
        "پست جیس",
        "پستگیس",
        "پست گریس",
        "پستگرس",
        "دیتابیس مکانی",
        "پایگاه داده مکانی",
        "لایه دیتابیس",
        "لایه پایگاه داده",
        "خواندن از دیتابیس",
        "واکشی لایه",
        "اتصال به دیتابیس",
        "اتصال به پایگاه داده",
    ],
    description=(
        "Connect to PostgreSQL/PostGIS, fetch a spatial table/layer, "
        "and return GeoJSON-like features as VectorOut."
    ),
    required_inputs=["table"],
    optional_inputs=[
        "profile",
        "dsn",
        "schema",
        "geom_col",
        "where",
        "limit",
        "output_srid",
        "host",
        "port",
        "database",
        "user",
        "password",
        "connect_timeout",
    ],
    output_kind="vector",
    permissions=["database"],
    metadata={
        "category": "data_io",
        "data_type": "vector",
        "source_type": "postgis",
        "source_priority": 2,
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "read_database",
        "config_aware": True,
        "supports_profiles": True,
        "routable": True,
    },
)
def fetch_postgis_layer(
    table: str,
    profile: str | None = None,
    dsn: str | None = None,
    schema: str | None = None,
    geom_col: str | None = None,
    where: str | None = None,
    limit: int | None = None,
    output_srid: int | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
    connect_timeout: int | None = None,
) -> VectorOut:
    """
    Fetch a spatial layer from PostGIS and return it as VectorOut.

    Connection can be provided in two ways:

    1. Direct parameters:
        fetch_postgis_layer(
            host="localhost",
            database="gis",
            user="postgres",
            password="secret",
            table="roads",
        )

    2. Config profile:
        fetch_postgis_layer(
            profile="local",
            table="roads",
        )

    Direct function parameters always override config values.

    Geometry column is auto-detected from geometry_columns unless explicitly provided
    via geom_col parameter or config profile.
    """
    # If direct connection parameters are provided and no explicit profile is requested,
    # do not load profile config. This prevents unrelated config/env problems from
    # breaking direct calls such as tests or one-off executions.
    #
    # Example:
    #   fetch_postgis_layer(dsn="postgresql://...", table="roads")
    #
    # In this case, password_env from config must not be resolved.
    direct_connection_provided = any(
        value is not None
        for value in (
            dsn,
            host,
            port,
            database,
            user,
            password,
            connect_timeout,
        )
    )

    if profile is None and direct_connection_provided:
        profile_config = {}
    else:
        profile_config = get_profile_config(
            plugin_id=PLUGIN_ID,
            profile=profile,
            required=False,
        )

    final_schema = pick_first(
        schema,
        profile_config.get("default_schema"),
        profile_config.get("schema"),
        default="public",
    )

    final_geom_col = pick_first(
        geom_col,
        profile_config.get("default_geom_col"),
        profile_config.get("geom_col"),
        default=None,
    )

    final_limit = pick_first(
        limit,
        profile_config.get("default_limit"),
        profile_config.get("limit"),
        default=10000,
    )

    final_output_srid = pick_first(
        output_srid,
        profile_config.get("output_srid"),
        default=None,
    )

    final_dsn = pick_first(dsn, profile_config.get("dsn"), default=None)
    final_host = pick_first(host, profile_config.get("host"), default=None)
    final_port = pick_first(port, profile_config.get("port"), default=5432)
    final_database = pick_first(database, profile_config.get("database"), default=None)
    final_user = pick_first(user, profile_config.get("user"), default=None)
    final_password = pick_first(password, profile_config.get("password"), default=None)
    final_connect_timeout = pick_first(
        connect_timeout,
        profile_config.get("connect_timeout"),
        default=10,
    )

    final_schema = _validate_identifier(str(final_schema), "schema")
    table = _validate_identifier(table, "table")
    final_limit = _validate_limit(_to_int_or_none(final_limit))
    final_output_srid = _validate_output_srid(_to_int_or_none(final_output_srid))
    where = _validate_where_clause(where)
    final_port = _to_int_or_none(final_port)
    final_connect_timeout = _to_int_or_none(final_connect_timeout)

    conninfo = _build_conninfo(
        dsn=final_dsn,
        host=final_host,
        port=final_port,
        database=final_database,
        user=final_user,
        password=final_password,
        connect_timeout=final_connect_timeout,
    )

    # ------------------------------------------------------------------
    # Auto-detect geometry column if not explicitly provided.
    #
    # If geometry_columns is unavailable or does not contain the layer,
    # fall back to the common "geom" column. This keeps direct calls and
    # tests usable while still allowing callers to override geom_col.
    # ------------------------------------------------------------------
    if final_geom_col is None:
        detected = _auto_detect_geom_column(conninfo, final_schema, table)
        if detected:
            final_geom_col = detected
        else:
            final_geom_col = "geom"

    final_geom_col = _validate_identifier(str(final_geom_col), "geom_col")
    # ------------------------------------------------------------------

    sql, params = _build_select_features_sql(
        schema=final_schema,
        table=table,
        geom_col=final_geom_col,
        where=where,
        limit=final_limit,
        output_srid=final_output_srid,
    )

    features = _execute_postgis_query(
        conninfo=conninfo,
        sql=sql,
        params=params,
    )

    metadata = _build_metadata(
        features=features,
        schema=final_schema,
        table=table,
        geom_col=final_geom_col,
        where=where,
        limit=final_limit,
        output_srid=final_output_srid,
        host=final_host,
        database=final_database,
        profile=profile,
    )

    return VectorOut(
        features=features,
        metadata=metadata,
    )



_READONLY_SQL_FORBIDDEN_TOKENS = [
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
    " merge ",
]


def _validate_readonly_select_sql(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql must be a non-empty string.")

    cleaned = sql.strip()

    # Accept SELECT/WITH even when followed by newline/tab/space.
    # Examples:
    #   SELECT ...
    #   SELECT\n...
    #   WITH ...
    #   WITH\n...
    first_word = cleaned.split(None, 1)[0].lower()
    if first_word not in {"select", "with"}:
        raise ValueError("Only read-only SELECT/WITH SQL statements are allowed.")

    # Normalize whitespace for forbidden token checks.
    lowered = " " + " ".join(cleaned.lower().split()) + " "

    for token in _READONLY_SQL_FORBIDDEN_TOKENS:
        if token in lowered:
            raise ValueError(f"Unsafe token found in SQL query: {token.strip()}")

    return cleaned

def _build_select_from_sql_query(
    *,
    sql: str,
    geom_col: str,
    limit: int,
    output_srid: int | None,
) -> tuple[str, list[Any]]:
    sql = _validate_readonly_select_sql(sql)
    geom_col = _validate_identifier(geom_col, "geom_col")
    limit = _validate_limit(limit)
    output_srid = _validate_output_srid(output_srid)

    geom_sql = f'q.{_quote_identifier(geom_col)}'
    params: list[Any] = []

    if output_srid is not None:
        geometry_expr = (
            f"CASE WHEN {geom_sql} IS NULL THEN NULL "
            f"ELSE ST_AsGeoJSON(ST_Transform({geom_sql}, {int(output_srid)}))::jsonb END"
        )
    else:
        geometry_expr = (
            f"CASE WHEN {geom_sql} IS NULL THEN NULL "
            f"ELSE ST_AsGeoJSON({geom_sql})::jsonb END"
        )

    wrapped_sql = f"""
SELECT
    jsonb_build_object(
        'type', 'Feature',
        'geometry', {geometry_expr},
        'properties', to_jsonb(q) - {_sql_text_literal(geom_col)}
    )::text AS feature
FROM (
{sql}
) AS q
WHERE {geom_sql} IS NOT NULL
LIMIT {int(limit)}
""".strip()

    return wrapped_sql, []


@capability(
    name="fetch_postgis_sql_layer",
    keywords=[
        "postgis sql",
        "postgres sql",
        "spatial sql",
        "execute spatial query",
        "run postgis query",
        "read sql layer",
        "sql geojson",
        "query postgis",
        "تحلیل postgis",
        "کوئری postgis",
        "کوئری پست‌جیس",
        "کوئری پست جیس",
        "کوئری مکانی",
        "تحلیل مکانی دیتابیس",
        "اجرای sql مکانی",
    ],
    description=(
        "Execute a read-only SELECT/WITH SQL query against PostgreSQL/PostGIS "
        "and return the result rows as a GeoJSON-like VectorOut. The SQL must expose "
        "a geometry column alias, usually AS geom."
    ),
    required_inputs=["sql"],
    optional_inputs=[
        "profile",
        "dsn",
        "geom_col",
        "limit",
        "output_srid",
        "host",
        "port",
        "database",
        "user",
        "password",
        "connect_timeout",
    ],
    output_kind="vector",
    permissions=["database"],
    metadata={
        "category": "data_io",
        "data_type": "vector",
        "source_type": "postgis",
        "source_priority": 1,
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "read_database",
        "config_aware": True,
        "supports_profiles": True,
        "routable": True,
        "sql_capable": True,
    },
)
def fetch_postgis_sql_layer(
    sql: str,
    profile: str | None = None,
    dsn: str | None = None,
    geom_col: str | None = None,
    limit: int | None = None,
    output_srid: int | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
    connect_timeout: int | None = None,
) -> VectorOut:
    """
    Execute a safe read-only PostGIS SQL query and return it as VectorOut.

    The SQL must expose a geometry column with an alias matching geom_col.

    Example:
        SELECT osm_id, name, way AS geom
        FROM osm_tehran_parks
        WHERE area_m2 > 50000
    """
    direct_connection_provided = any(
        value is not None
        for value in (
            dsn,
            host,
            port,
            database,
            user,
            password,
            connect_timeout,
        )
    )

    if profile is None and direct_connection_provided:
        profile_config = {}
    else:
        profile_config = get_profile_config(
            plugin_id=PLUGIN_ID,
            profile=profile,
            required=False,
        )

    final_geom_col = pick_first(
        geom_col,
        profile_config.get("sql_geom_col"),
        default="geom",
    )

    final_limit = pick_first(
        limit,
        profile_config.get("default_limit"),
        profile_config.get("limit"),
        default=1000,
    )

    final_output_srid = pick_first(
        output_srid,
        profile_config.get("output_srid"),
        default=None,
    )

    final_dsn = pick_first(dsn, profile_config.get("dsn"), default=None)
    final_host = pick_first(host, profile_config.get("host"), default=None)
    final_port = pick_first(port, profile_config.get("port"), default=5432)
    final_database = pick_first(database, profile_config.get("database"), default=None)
    final_user = pick_first(user, profile_config.get("user"), default=None)
    final_password = pick_first(password, profile_config.get("password"), default=None)
    final_connect_timeout = pick_first(
        connect_timeout,
        profile_config.get("connect_timeout"),
        default=10,
    )

    final_geom_col = _validate_identifier(str(final_geom_col), "geom_col")
    final_limit = _validate_limit(_to_int_or_none(final_limit))
    final_output_srid = _validate_output_srid(_to_int_or_none(final_output_srid))
    final_port = _to_int_or_none(final_port)
    final_connect_timeout = _to_int_or_none(final_connect_timeout)

    conninfo = _build_conninfo(
        dsn=final_dsn,
        host=final_host,
        port=final_port,
        database=final_database,
        user=final_user,
        password=final_password,
        connect_timeout=final_connect_timeout,
    )

    wrapped_sql, params = _build_select_from_sql_query(
        sql=sql,
        geom_col=final_geom_col,
        limit=final_limit,
        output_srid=final_output_srid,
    )

    features = _execute_postgis_query(
        conninfo=conninfo,
        sql=wrapped_sql,
        params=params,
    )

    metadata = _build_metadata(
        features=features,
        schema="public",
        table="__sql_query__",
        geom_col=final_geom_col,
        where=None,
        limit=final_limit,
        output_srid=final_output_srid,
        host=final_host,
        database=final_database,
        profile=profile,
    )
    metadata["sql_query"] = True

    return VectorOut(
        features=features,
        metadata=metadata,
    )





_SELECT_CLAUSE_FORBIDDEN_TOKENS = [
    ";",
    "--",
    "/*",
    "*/",
    " from ",
    " where ",
    " group ",
    " order ",
    " limit ",
    " union ",
    " join ",
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


def _validate_select_clause(select: str | None) -> str | None:
    """
    Validate a SELECT-list fragment such as:
        osm_id, name, way AS geom

    This is intentionally stricter than full SQL validation because it is only
    used to build:
        SELECT <select> FROM "schema"."table" ...
    """
    if select is None:
        return None

    if not isinstance(select, str):
        raise ValueError("select must be a string or None.")

    cleaned = select.strip()
    if not cleaned:
        return None

    lowered = f" {cleaned.lower()} "

    for token in _SELECT_CLAUSE_FORBIDDEN_TOKENS:
        if token in lowered:
            raise ValueError(f"Unsafe token found in select clause: {token.strip()}")

    return cleaned


def _build_safe_sql_from_select_parts(
    *,
    select: str,
    schema: str | None,
    table: str,
    where: str | None,
    limit: int | None,
) -> str:
    final_schema = _validate_identifier(str(schema or "public"), "schema")
    final_table = _validate_identifier(table, "table")
    final_select = _validate_select_clause(select)

    if not final_select:
        raise ValueError("select must be provided when building SQL from select parts.")

    final_where = _validate_where_clause(where)
    final_limit = _validate_limit(_to_int_or_none(limit if limit is not None else 1000))

    sql = (
        f"SELECT {final_select}\n"
        f"FROM {_quote_identifier(final_schema)}.{_quote_identifier(final_table)}"
    )

    if final_where:
        sql += f"\nWHERE {final_where}"

    sql += f"\nLIMIT {final_limit}"

    # Reuse the full read-only SQL validator as a second safety gate.
    return _validate_readonly_select_sql(sql)



@capability(
    name="query_database_postgis",
    keywords=[
        "query database",
        "postgis query database",
        "database query",
        "spatial database query",
        "query_database",
        "کوئری دیتابیس",
        "کوئری پایگاه داده",
        "کوئری postgis",
        "پرس‌وجوی postgis",
    ],
    description=(
        "Canonical adapter for logical query_database operations against PostGIS. "
        "LLM must provide a strict schema; this adapter compiles it deterministically "
        "to safe read-only SQL or table fetch."
    ),
    required_inputs=["table"],
    optional_inputs=[
        "source_type",
        "mode",
        "columns",
        "profile",
        "dsn",
        "schema",
        "geom_col",
        "geom_alias",
        "where",
        "limit",
        "output_srid",
        "host",
        "port",
        "database",
        "user",
        "password",
        "connect_timeout",
    ],
    output_kind="vector",
    permissions=["database"],
    metadata={
        "category": "data_io",
        "data_type": "vector",
        "source_type": "postgis",
        "source_priority": 0,
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "read_database",
        "config_aware": True,
        "supports_profiles": True,
        "routable": True,
        "sql_capable": False,
        "canonical_contract": "query_database.postgis.v1",
        "adapter_for": ["fetch_postgis_layer", "fetch_postgis_sql_layer"],
    },
)
def query_database_postgis(
    table: str,
    source_type: str | None = "postgis",
    mode: str | None = "select_table",
    columns: list[str] | None = None,
    profile: str | None = None,
    dsn: str | None = None,
    schema: str | None = None,
    geom_col: str | None = None,
    geom_alias: str | None = "geom",
    where: str | None = None,
    limit: int | None = None,
    output_srid: int | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
    connect_timeout: int | None = None,
) -> VectorOut:
    """
    Execute canonical query_database/PostGIS V1.

    Canonical select_table params:
      {
        "source_type": "postgis",
        "mode": "select_table",
        "schema": "public",
        "table": "osm_tehran_parks",
        "columns": ["osm_id", "name"],
        "geom_col": "way",
        "geom_alias": "geom",
        "where": "way IS NOT NULL",
        "limit": 10,
        "output_srid": 4326
      }

    Important:
      - LLM must not provide raw SQL.
      - LLM must not put "way AS geom" inside columns.
      - SQL is built deterministically here.
    """
    if source_type not in (None, "postgis"):
        raise ValueError("query_database_postgis only supports source_type='postgis'.")

    final_mode = mode or "select_table"
    final_schema = _validate_identifier(str(schema or "public"), "schema")
    final_table = _validate_identifier(table, "table")
    final_limit = _validate_limit(_to_int_or_none(limit if limit is not None else 1000))
    final_output_srid = _validate_output_srid(_to_int_or_none(output_srid))
    final_where = _validate_where_clause(where)

    if final_mode == "select_table":
        if not isinstance(columns, list):
            raise ValueError(
                "query_database_postgis select_table mode requires columns as a list of property column names."
            )

        safe_columns: list[str] = []
        for index, item in enumerate(columns):
            if not isinstance(item, str):
                raise ValueError(f"columns[{index}] must be a string.")

            cleaned = item.strip()

            if not cleaned:
                raise ValueError(f"columns[{index}] must be non-empty.")

            # columns are property identifiers only, not SQL expressions.
            safe_columns.append(_validate_identifier(cleaned, f"columns[{index}]"))

        if not geom_col:
            raise ValueError("query_database_postgis select_table mode requires geom_col.")

        final_geom_col = _validate_identifier(str(geom_col), "geom_col")
        final_geom_alias = _validate_identifier(str(geom_alias or "geom"), "geom_alias")

        select_parts = [_quote_identifier(col) for col in safe_columns]
        select_parts.append(
            f"{_quote_identifier(final_geom_col)} AS {_quote_identifier(final_geom_alias)}"
        )

        sql = (
            f"SELECT {', '.join(select_parts)} "
            f"FROM {_quote_identifier(final_schema)}.{_quote_identifier(final_table)}"
        )

        if final_where:
            sql += f" WHERE {final_where}"

        sql += f" LIMIT {final_limit}"

        sql = _validate_readonly_select_sql(sql)

        return fetch_postgis_sql_layer(
            sql=sql,
            profile=profile,
            dsn=dsn,
            geom_col=final_geom_alias,
            limit=final_limit,
            output_srid=final_output_srid,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            connect_timeout=connect_timeout,
        )

    if final_mode == "table_layer":
        final_geom_col = None
        if geom_col is not None:
            final_geom_col = _validate_identifier(str(geom_col), "geom_col")

        return fetch_postgis_layer(
            table=final_table,
            profile=profile,
            dsn=dsn,
            schema=final_schema,
            geom_col=final_geom_col,
            where=final_where,
            limit=final_limit,
            output_srid=final_output_srid,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            connect_timeout=connect_timeout,
        )

    raise ValueError("query_database_postgis mode must be 'select_table' or 'table_layer'.")


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.2.0",
    name="PostGIS Connector",
    description=(
        "Connects to PostgreSQL/PostGIS databases and fetches spatial layers "
        "as GeoJSON features for the GeoChat spatial pipeline. "
        "Supports config profiles and auto-detection of geometry columns."
    ),
    author="GeoChat Platform Team",
    permissions=["database"],
)
