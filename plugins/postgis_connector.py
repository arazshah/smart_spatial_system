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
            f"ELSE ST_AsGeoJSON(ST_Transform({geom_sql}, %s))::jsonb END"
        )
        params.append(output_srid)
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
        'properties', to_jsonb(t) - %s
    ) AS feature
FROM {schema_sql}.{table_sql} AS t
""".strip()

    params.append(geom_col)

    if where:
        sql += f"\nWHERE {where}"

    sql += "\nLIMIT %s"
    params.append(limit)

    return sql, params


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


def _normalize_feature(value: Any, row_index: int) -> dict[str, Any]:
    if isinstance(value, bytes):
        value = value.decode("utf-8")

    if isinstance(value, str):
        try:
            value = json.loads(value)
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


def _execute_postgis_query(conninfo: str, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    try:
        import psycopg
    except ImportError as exc:
        raise SDKDependencyError(
            "The postgis_connector plugin requires 'psycopg'. "
            "Install it with: pip install psycopg[binary]"
        ) from exc

    features: list[dict[str, Any]] = []

    try:
        with psycopg.connect(conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        for idx, row in enumerate(rows):
            features.append(_row_to_feature(row, idx))

        return features

    except Exception as exc:
        raise RuntimeError(f"Failed to execute PostGIS query. Error: {exc}") from exc


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
    # Auto-detect geometry column if not explicitly provided
    # ------------------------------------------------------------------
    if final_geom_col is None:
        detected = _auto_detect_geom_column(conninfo, final_schema, table)
        if detected:
            final_geom_col = detected

    if final_geom_col is None:
        raise ValueError(
            f"Could not auto-detect geometry column for "
            f'"{final_schema}"."{table}". '
            "Please specify geom_col explicitly."
        )

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
