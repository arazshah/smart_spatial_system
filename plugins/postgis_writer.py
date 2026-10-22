"""
postgis_writer.py

GeoChat SDK Plugin
==================

Plugin ID:
    postgis_writer

Capability:
    - write_postgis_layer:
        Save features into a PostGIS table (geometry + JSONB properties) so results can be used by
        other systems or reloaded later.

This is the only plugin that WRITES to a database, so it is locked down by default:

    * ``allow_writes`` must be set to true in config/plugins/postgis_writer.yaml (default false);
    * the connection comes only from a server-side ``profile`` of that same config (give it a
      dedicated, least-privilege role); there is no dsn/host/password parameter at all;
    * only schemas listed in ``allowed_write_schemas`` can be written, and table names must start
      with ``table_prefix`` when one is configured;
    * identifiers are restricted to ``[a-z_][a-z0-9_]{0,62}`` (so they can be safely quoted) and
      all values are bound parameters;
    * it never drops tables: ``create`` refuses an existing table, ``append`` and ``replace``
      (TRUNCATE + insert) only touch tables this plugin created (marked by a table comment);
    * everything runs in one transaction; row count is capped.

Table layout:
    id bigserial primary key, properties jsonb, geom geometry(Geometry, <srid>) + a GiST index.

Not verified against a live PostGIS in this repository's test-suite (the tests drive a recording
fake connection and check the SQL and parameters); try it on a scratch database first.
"""

from __future__ import annotations

import json
import re
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.exceptions import SDKDependencyError
from geochat_sdk.plugin import auto_collect

from orchestrator.provider_error_mapping import redact_provider_error_message
from plugins._shared.plugin_config import get_profile_config
from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    load_config,
    to_int,
    utc_now_iso,
    validate_choice,
)

PLUGIN_ID = "postgis_writer"

MARKER = "smart_spatial_system:managed"
DEFAULT_MAX_ROWS = 100_000
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise ValueError(f"{name} must match [a-z_][a-z0-9_]{{0,62}} (lowercase letters, digits, underscore).")
    return value


def _connect(conninfo: str):
    """Open a psycopg connection (separate so tests can substitute a fake)."""
    try:
        import psycopg
    except ImportError as exc:
        raise SDKDependencyError(
            "write_postgis_layer requires psycopg: pip install 'psycopg[binary]' (extra: postgis)"
        ) from exc
    return psycopg.connect(conninfo)


def _conninfo(profile: dict[str, Any]) -> str:
    try:
        from psycopg.conninfo import make_conninfo
    except ImportError as exc:
        raise SDKDependencyError("write_postgis_layer requires psycopg: pip install 'psycopg[binary]'") from exc
    parts = {
        "host": profile.get("host"), "port": profile.get("port"), "dbname": profile.get("database"),
        "user": profile.get("user"), "password": profile.get("password"),
        "connect_timeout": profile.get("connect_timeout", 10),
    }
    if not parts["host"] or not parts["dbname"] or not parts["user"]:
        raise ValueError("The writer profile needs host, database and user.")
    return make_conninfo(**{k: v for k, v in parts.items() if v not in (None, "")})


@capability(
    name="write_postgis_layer",
    keywords=[
        "save to postgis", "write to database", "store layer in postgis", "export to postgis",
        "persist results database",
        "ذخیره در postgis", "نوشتن در پایگاه داده", "ذخیره لایه در دیتابیس", "ذخیره نتایج در پایگاه داده",
    ],
    description=(
        "Write features into a PostGIS table (geometry + JSONB properties). Disabled unless the "
        "deployer enables writes; server-side profile only; never drops tables."
    ),
    required_inputs=["features", "table"],
    optional_inputs=["profile", "schema", "mode", "srid", "metadata"],
    output_kind="json",
    permissions=[],
    metadata={
        "category": "output",
        "data_type": "vector",
        "operation": "write_postgis_layer",
        "returns": "dict",
        "artifact_kind": "json",
        "access_scope": "database_write",
        "config_aware": True,
        "routable": True,
    },
)
def write_postgis_layer(
    features: Any,
    table: str,
    profile: str | None = None,
    schema: str | None = None,
    mode: str = "create",
    srid: int = 4326,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Write a layer to PostGIS.

    Args:
        features: Features to store (GeoJSON-like; geometry may be null).
        table: Table name (lowercase identifier; must start with the configured table_prefix).
        profile: Writer profile name from config/plugins/postgis_writer.yaml (default: default_profile).
        schema: Target schema; must be in allowed_write_schemas (default: the first allowed schema).
        mode: ``create`` (table must not exist), ``append`` or ``replace`` (TRUNCATE then insert);
            the last two only for tables created by this plugin.
        srid: EPSG code of the coordinates (not transformed); append must match the table's SRID.
        metadata: Extra information echoed in the result.

    Returns:
        dict ``{"type": "table_written", "schema", "table", "rows_written", "mode", "srid"}``.
    """
    config = load_config(PLUGIN_ID)
    if config.get("allow_writes") is not True:
        raise ValueError(
            "Writing to PostGIS is disabled. Set allow_writes: true (and allowed_write_schemas, a "
            "profile) in config/plugins/postgis_writer.yaml to enable it."
        )
    allowed_schemas = [str(s) for s in (config.get("allowed_write_schemas") or [])]
    if not allowed_schemas:
        raise ValueError("allowed_write_schemas in the postgis_writer config is empty; no schema may be written.")
    chosen_schema = _identifier(schema if schema is not None else allowed_schemas[0], "schema")
    if chosen_schema not in allowed_schemas:
        raise ValueError(f"Schema '{chosen_schema}' is not in allowed_write_schemas.")
    name = _identifier(table, "table")
    prefix = str(config.get("table_prefix") or "")
    if prefix and not name.startswith(prefix):
        raise ValueError(f"Table names must start with '{prefix}'.")
    write_mode = validate_choice(mode, {"create", "append", "replace"}, "mode")
    srid_value = to_int(srid, "srid")
    if not 1 <= srid_value <= 999999:
        raise ValueError("srid must be a positive EPSG code.")

    input_features, source_info = extract_features(features)
    check_limit(len(input_features), to_int(config.get("max_rows", DEFAULT_MAX_ROWS), "max_rows"), "row count")
    rows = []
    for feature in input_features:
        try:
            properties = json.dumps(feature.get("properties") or {}, ensure_ascii=False, default=str)
            geometry = json.dumps(feature["geometry"]) if feature.get("geometry") else None
        except (TypeError, ValueError) as exc:
            raise ValueError(f"A feature cannot be serialised: {exc}") from None
        rows.append((properties, geometry, srid_value))

    profile_config = get_profile_config(PLUGIN_ID, profile, required=True)
    qualified = f'"{chosen_schema}"."{name}"'
    try:
        connection = _connect(_conninfo(profile_config))
        try:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass(%s)::text", (qualified,))
                exists = cursor.fetchone()[0] is not None
                if write_mode == "create":
                    if exists:
                        raise ValueError(f"Table {chosen_schema}.{name} already exists (mode=create never overwrites).")
                    cursor.execute(
                        f"CREATE TABLE {qualified} (id bigserial PRIMARY KEY, "
                        f"properties jsonb NOT NULL DEFAULT '{{}}'::jsonb, geom geometry(Geometry, {srid_value}))"
                    )
                    cursor.execute(f"COMMENT ON TABLE {qualified} IS '{MARKER}'")
                    cursor.execute(f'CREATE INDEX ON {qualified} USING GIST (geom)')
                else:
                    if not exists:
                        raise ValueError(f"Table {chosen_schema}.{name} does not exist; use mode=create first.")
                    cursor.execute("SELECT obj_description(%s::regclass, 'pg_class')", (qualified,))
                    if cursor.fetchone()[0] != MARKER:
                        raise ValueError(
                            f"Table {chosen_schema}.{name} was not created by this plugin; refusing to modify it."
                        )
                    cursor.execute(
                        "SELECT srid FROM geometry_columns WHERE f_table_schema = %s AND f_table_name = %s "
                        "AND f_geometry_column = 'geom'", (chosen_schema, name))
                    found = cursor.fetchone()
                    if found is None or int(found[0]) != srid_value:
                        raise ValueError(f"The table's SRID ({found[0] if found else 'unknown'}) differs from srid={srid_value}.")
                    if write_mode == "replace":
                        cursor.execute(f"TRUNCATE {qualified}")
                if rows:
                    cursor.executemany(
                        f"INSERT INTO {qualified} (properties, geom) "
                        "VALUES (%s::jsonb, ST_SetSRID(ST_GeomFromGeoJSON(%s), %s))",
                        rows,
                    )
        finally:
            connection.close()
    except ValueError:
        raise
    except SDKDependencyError:
        raise
    except Exception as exc:
        raise RuntimeError(
            redact_provider_error_message(f"PostGIS write failed: {type(exc).__name__}: {exc}")[:300]
        ) from None

    return {
        "type": "table_written",
        "schema": chosen_schema,
        "table": name,
        "rows_written": len(rows),
        "mode": write_mode,
        "srid": srid_value,
        "metadata": {"source": PLUGIN_ID, "operation": "write_postgis_layer", "created_at": utc_now_iso(),
                     **source_info, **(metadata or {})},
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="PostGIS Writer",
    description="Writes features to a PostGIS table (disabled by default; server profile only).",
    author="GeoChat Platform Team",
    permissions=[],
)
