"""
postgis_connector.py

GeoChat SDK Plugin
==================

Plugin ID:
    postgis_connector

Purpose:
    Connect to a PostgreSQL/PostGIS database, fetch a spatial table/layer,
    convert rows to GeoJSON Features using PostGIS functions, and return
    a standard VectorOut object.

Design:
    - psycopg is imported lazily only during execution.
    - No database dependency is imported during plugin discovery.
    - SQL identifiers are strictly validated to reduce injection risk.
    - WHERE clause is optional but defensively checked.
    - Output is always VectorOut, so SDK converts it to ExecutionArtifact(kind="features").
"""

from __future__ import annotations

import json
import re
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut
from geochat_sdk.exceptions import SDKDependencyError


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
    """
    Validate an SQL identifier such as schema, table or geometry column name.

    This function only allows simple PostgreSQL identifiers:
        - starts with a letter or underscore
        - then letters, numbers or underscores

    Args:
        value:
            Identifier value.
        field_name:
            Human-readable field name used in error messages.

    Returns:
        The validated identifier.

    Raises:
        ValueError:
            If identifier is empty or unsafe.
    """
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
    """
    Quote a validated PostgreSQL identifier.
    """
    return f'"{value}"'


def _validate_limit(limit: int) -> int:
    """
    Validate LIMIT value.
    """
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer.")

    if limit < 0:
        raise ValueError("limit must be greater than or equal to 0.")

    if limit > 100000:
        raise ValueError("limit is too large. Maximum allowed limit is 100000.")

    return limit


def _validate_output_srid(output_srid: int | None) -> int | None:
    """
    Validate optional output SRID.
    """
    if output_srid is None:
        return None

    if not isinstance(output_srid, int):
        raise ValueError("output_srid must be an integer or None.")

    if output_srid <= 0:
        raise ValueError("output_srid must be a positive integer.")

    return output_srid


def _validate_where_clause(where: str | None) -> str | None:
    """
    Validate a simple WHERE clause.

    The clause is still raw SQL, because real GIS filtering often requires
    database-specific expressions. However, this function rejects obvious
    dangerous tokens and statement separators.

    Examples accepted:
        population > 1000
        name = 'Tehran'
        ST_Intersects(geom, ST_MakeEnvelope(...))

    Args:
        where:
            Optional WHERE expression without the 'WHERE' keyword.

    Returns:
        Cleaned where clause or None.

    Raises:
        ValueError:
            If the clause contains unsafe tokens.
    """
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

    Either provide:
        - dsn
    or provide:
        - host, database, user, password

    Args:
        dsn:
            Full PostgreSQL connection string.
        host:
            Database host.
        port:
            Database port.
        database:
            Database name.
        user:
            Username.
        password:
            Password.
        connect_timeout:
            Connection timeout in seconds.

    Returns:
        PostgreSQL connection string.

    Raises:
        ValueError:
            If required connection information is missing.
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

    parts = [
        f"host={host}",
        f"port={port}",
        f"dbname={database}",
        f"user={user}",
        f"password={password}",
        f"connect_timeout={connect_timeout}",
    ]

    return " ".join(parts)


def _build_select_features_sql(
    *,
    schema: str,
    table: str,
    geom_col: str,
    where: str | None,
    limit: int,
    output_srid: int | None,
) -> tuple[str, list[Any]]:
    """
    Build a safe SQL query that returns one JSONB GeoJSON Feature per row.

    The query has this logical form:

        SELECT jsonb_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(...geom...)::jsonb,
            'properties', to_jsonb(t) - 'geom'
        ) AS feature
        FROM "schema"."table" AS t
        WHERE ...
        LIMIT %s

    Args:
        schema:
            PostgreSQL schema name.
        table:
            Table name.
        geom_col:
            Geometry column.
        where:
            Optional WHERE clause without 'WHERE'.
        limit:
            Maximum number of rows.
        output_srid:
            Optional target SRID. If None, no ST_Transform is applied.

    Returns:
        Tuple of SQL string and parameter list.
    """
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
    """
    Return True if value is int/float but not bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """
    Calculate bbox from GeoJSON geometry.

    Returns:
        [minx, miny, maxx, maxy] or None.
    """
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
    """
    Merge multiple bbox arrays into one bbox dict.
    """
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
    """
    Normalize a database-returned value into a GeoJSON Feature dict.

    psycopg may return JSONB as:
        - dict
        - str
        - bytes
    """
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
    """
    Extract the 'feature' column from a DB row.

    Supports:
        - dict rows: {"feature": ...}
        - tuple/list rows: (feature,)
    """
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
    """
    Execute SQL using psycopg and return GeoJSON features.

    psycopg is imported lazily to avoid startup failure when the dependency is
    not installed.
    """
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

    except SDKDependencyError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to execute PostGIS query. Error: {exc}") from exc


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
) -> dict[str, Any]:
    """
    Build JSON-friendly metadata for fetched layer.
    """
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
        "connection": {
            "host": host,
            "database": database,
        },
    }


@capability(
    name="fetch_postgis_layer",
    keywords=[
        # English keywords
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

        # Persian keywords
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
        "Connect to a PostgreSQL/PostGIS database, fetch a spatial table/layer, "
        "and return GeoJSON-like features as VectorOut."
    ),
    required_inputs=["table"],
    optional_inputs=[
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
        "routable": True,
    },
)
def fetch_postgis_layer(
    table: str,
    dsn: str | None = None,
    schema: str = "public",
    geom_col: str = "geom",
    where: str | None = None,
    limit: int = 1000,
    output_srid: int | None = None,
    host: str | None = None,
    port: int = 5432,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
    connect_timeout: int = 10,
) -> VectorOut:
    """
    Fetch a spatial layer from PostGIS and return it as VectorOut.

    Args:
        table:
            Table name. Required.
        dsn:
            Optional PostgreSQL DSN/connection string.
        schema:
            PostgreSQL schema name. Default: public.
        geom_col:
            Geometry column name. Default: geom.
        where:
            Optional SQL WHERE expression without the WHERE keyword.
        limit:
            Maximum number of features to fetch.
        output_srid:
            Optional SRID for ST_Transform. If None, no transform is applied.
        host:
            PostgreSQL host, used if dsn is not provided.
        port:
            PostgreSQL port.
        database:
            Database name, used if dsn is not provided.
        user:
            Database username, used if dsn is not provided.
        password:
            Database password, used if dsn is not provided.
        connect_timeout:
            Connection timeout in seconds.

    Returns:
        VectorOut:
            GeoJSON Features and metadata.

    Raises:
        ValueError:
            Invalid input, unsafe SQL identifier or unsafe WHERE clause.
        SDKDependencyError:
            psycopg is not installed.
        RuntimeError:
            Connection/query failed.
    """
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    geom_col = _validate_identifier(geom_col, "geom_col")
    limit = _validate_limit(limit)
    output_srid = _validate_output_srid(output_srid)
    where = _validate_where_clause(where)

    conninfo = _build_conninfo(
        dsn=dsn,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        connect_timeout=connect_timeout,
    )

    sql, params = _build_select_features_sql(
        schema=schema,
        table=table,
        geom_col=geom_col,
        where=where,
        limit=limit,
        output_srid=output_srid,
    )

    features = _execute_postgis_query(
        conninfo=conninfo,
        sql=sql,
        params=params,
    )

    metadata = _build_metadata(
        features=features,
        schema=schema,
        table=table,
        geom_col=geom_col,
        where=where,
        limit=limit,
        output_srid=output_srid,
        host=host,
        database=database,
    )

    return VectorOut(
        features=features,
        metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="PostGIS Connector",
    description=(
        "Connects to PostgreSQL/PostGIS databases and fetches spatial layers "
        "as GeoJSON features for the GeoChat spatial pipeline."
    ),
    author="GeoChat Platform Team",
    permissions=["database"],
)
