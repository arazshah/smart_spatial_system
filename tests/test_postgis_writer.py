"""postgis_writer: lock-down defaults, identifier safety, modes (against a recording fake connection)."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

import plugins.postgis_writer as module
from plugins.postgis_writer import MARKER, write_postgis_layer

FEATURES = [
    {"type": "Feature", "properties": {"name": "تهران", "n": 1},
     "geometry": {"type": "Point", "coordinates": [51.4, 35.7]}},
    {"type": "Feature", "properties": {"name": "none"}, "geometry": None},
]
ENABLED = {"allow_writes": True, "allowed_write_schemas": ["analysis_out"], "table_prefix": "ssys_",
           "max_rows": 100}


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.db.statements.append((sql, params))
        self._row = self.db.answer(sql, params)

    def executemany(self, sql, rows):
        self.db.statements.append((sql, list(rows)))

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, exists=False, comment=MARKER, srid=4326):
        self.statements = []
        self.exists, self.comment, self.srid = exists, comment, srid
        self.closed = False
        self.rolled_back = False

    def answer(self, sql, params):
        if "to_regclass" in sql:
            return ("x",) if self.exists else (None,)
        if "obj_description" in sql:
            return (self.comment,)
        if "geometry_columns" in sql:
            return (self.srid,)
        return None

    @contextmanager
    def transaction(self):
        try:
            yield
        except BaseException:
            self.rolled_back = True
            raise

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def db(monkeypatch):
    config = dict(ENABLED)
    holder = {"conn": FakeConnection()}
    monkeypatch.setattr(module, "load_config", lambda _id: config)
    monkeypatch.setattr(module, "get_profile_config", lambda plugin_id, profile, required=False: {
        "host": "db", "database": "gis", "user": "w", "password": "secret"})
    monkeypatch.setattr(module, "_conninfo", lambda profile: "host=db dbname=gis")
    monkeypatch.setattr(module, "_connect", lambda conninfo: holder["conn"])
    holder["config"] = config
    return holder


def sql_text(conn):
    return "\n".join(s for s, _ in conn.statements)


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {})
    with pytest.raises(ValueError, match="disabled"):
        write_postgis_layer(FEATURES, "ssys_roads")
    monkeypatch.setattr(module, "load_config", lambda _id: {"allow_writes": True})
    with pytest.raises(ValueError, match="allowed_write_schemas"):
        write_postgis_layer(FEATURES, "ssys_roads")
    monkeypatch.setattr(module, "load_config", lambda _id: {"allow_writes": "yes", "allowed_write_schemas": ["s"]})
    with pytest.raises(ValueError, match="disabled"):  # only a real boolean true enables writes
        write_postgis_layer(FEATURES, "ssys_roads")


def test_create_builds_table_comment_index_and_binds_values(db):
    result = write_postgis_layer(FEATURES, "ssys_roads")
    conn = db["conn"]
    text = sql_text(conn)
    assert 'CREATE TABLE "analysis_out"."ssys_roads"' in text and "geometry(Geometry, 4326)" in text
    assert f"COMMENT ON TABLE \"analysis_out\".\"ssys_roads\" IS '{MARKER}'" in text
    assert "USING GIST (geom)" in text and "DROP" not in text.upper()
    insert_sql, rows = conn.statements[-1]
    assert "ST_SetSRID(ST_GeomFromGeoJSON(%s), %s)" in insert_sql and "%s::jsonb" in insert_sql
    assert json.loads(rows[0][0]) == {"name": "تهران", "n": 1} and json.loads(rows[0][1])["type"] == "Point"
    assert rows[1][1] is None and rows[0][2] == 4326
    assert conn.closed and result["rows_written"] == 2 and result["mode"] == "create"
    assert result["schema"] == "analysis_out" and result["table"] == "ssys_roads"


def test_values_are_never_interpolated_into_sql(db):
    nasty = [{"type": "Feature", "properties": {"x": "'); DROP TABLE users; --"},
              "geometry": {"type": "Point", "coordinates": [0, 0]}}]
    write_postgis_layer(nasty, "ssys_roads")
    assert "DROP TABLE users" not in sql_text(db["conn"])
    assert "DROP TABLE users" in db["conn"].statements[-1][1][0][0]  # only inside a bound parameter


def test_create_refuses_existing_tables_and_rolls_back(db):
    db["conn"] = FakeConnection(exists=True)
    with pytest.raises(ValueError, match="already exists"):
        write_postgis_layer(FEATURES, "ssys_roads")
    assert "INSERT" not in sql_text(db["conn"]) and db["conn"].rolled_back and db["conn"].closed


def test_append_and_replace_only_touch_tables_this_plugin_created(db):
    db["conn"] = FakeConnection(exists=True)
    write_postgis_layer(FEATURES, "ssys_roads", mode="append")
    assert "TRUNCATE" not in sql_text(db["conn"]) and "INSERT" in sql_text(db["conn"])
    db["conn"] = FakeConnection(exists=True)
    write_postgis_layer(FEATURES, "ssys_roads", mode="replace")
    text = sql_text(db["conn"])
    assert text.index("TRUNCATE") < text.index("INSERT") and "DROP" not in text.upper()
    db["conn"] = FakeConnection(exists=True, comment="someone else's table")
    with pytest.raises(ValueError, match="not created by this plugin"):
        write_postgis_layer(FEATURES, "ssys_roads", mode="replace")
    assert "TRUNCATE" not in sql_text(db["conn"])
    db["conn"] = FakeConnection(exists=False)
    with pytest.raises(ValueError, match="does not exist"):
        write_postgis_layer(FEATURES, "ssys_roads", mode="append")
    db["conn"] = FakeConnection(exists=True, srid=32639)
    with pytest.raises(ValueError, match="SRID"):
        write_postgis_layer(FEATURES, "ssys_roads", mode="append")


@pytest.mark.parametrize("kwargs", [
    {"table": "Ssys_Roads"}, {"table": 'ssys_x"; drop table y; --'}, {"table": "ssys_a b"},
    {"table": "ssys_" + "a" * 70}, {"table": "roads"},  # prefix
    {"schema": "public"}, {"schema": 'analysis_out"."x'}, {"mode": "drop"}, {"srid": 0}, {"srid": "abc"},
])
def test_identifier_prefix_schema_and_mode_validation(db, kwargs):
    with pytest.raises(ValueError):
        write_postgis_layer(FEATURES, **{"table": "ssys_roads", **kwargs})
    assert db["conn"].statements == []  # nothing reached the database


def test_row_limit_and_unserialisable_features(db):
    db["config"]["max_rows"] = 1
    with pytest.raises(ValueError, match="row count"):
        write_postgis_layer(FEATURES, "ssys_roads")
    db["config"]["max_rows"] = 100
    bad = [{"type": "Feature", "properties": {"x": {1, 2}}, "geometry": None}]
    write_postgis_layer(bad, "ssys_roads")  # sets are stringified, not fatal


def test_connection_errors_are_redacted(db, monkeypatch):
    def broken(conninfo):
        raise ConnectionError("could not connect: postgresql://admin:s3cret@10.0.0.5/db password=hunter2")

    monkeypatch.setattr(module, "_connect", broken)
    with pytest.raises(RuntimeError) as err:
        write_postgis_layer(FEATURES, "ssys_roads")
    assert "s3cret" not in str(err.value) and "hunter2" not in str(err.value)


def test_there_is_no_way_to_pass_connection_details():
    with pytest.raises(TypeError):
        write_postgis_layer(FEATURES, "ssys_roads", dsn="postgresql://u:p@evil/db")
    with pytest.raises(TypeError):
        write_postgis_layer(FEATURES, "ssys_roads", host="evil")


def test_conninfo_quotes_special_characters():
    pytest.importorskip("psycopg")
    info = module._conninfo({"host": "db", "database": "gis", "user": "u", "password": "p w'd\\x host=evil"})
    assert "host=evil" not in info.replace("'p w\\'d\\\\x host=evil'", "")  # only inside the quoted password
    with pytest.raises(ValueError, match="host, database and user"):
        module._conninfo({"database": "gis"})
