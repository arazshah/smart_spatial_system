"""
Tests for postgis_connector plugin.

Run:
    pytest tests/test_postgis_connector.py -v
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.postgis_connector import (  # noqa: E402
    PLUGIN,
    PLUGIN_ID,
    _build_conninfo,
    _build_select_features_sql,
    _geometry_bbox,
    _merge_bboxes,
    _row_to_feature,
    _validate_identifier,
    _validate_limit,
    _validate_output_srid,
    _validate_where_clause,
    fetch_postgis_layer,
)


def test_plugin_manifest_basic_fields() -> None:
    """
    PLUGIN must expose a valid manifest.
    """
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "postgis_connector"
    assert PLUGIN.manifest.version == "1.2.0"
    assert PLUGIN.manifest.name == "PostGIS Connector"
    assert "database" in PLUGIN.manifest.permissions


def test_validate_identifier_accepts_safe_names() -> None:
    """
    Safe SQL identifiers should be accepted.
    """
    assert _validate_identifier("public", "schema") == "public"
    assert _validate_identifier("roads_2024", "table") == "roads_2024"
    assert _validate_identifier("_geom", "geom_col") == "_geom"


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "123roads",
        "roads-table",
        "roads.table",
        "roads;DROP",
        "roads name",
        "roads/*x*/",
    ],
)
def test_validate_identifier_rejects_unsafe_names(value: str) -> None:
    """
    Unsafe SQL identifiers must be rejected.
    """
    with pytest.raises(ValueError):
        _validate_identifier(value, "table")


def test_validate_limit_accepts_valid_limit() -> None:
    """
    Valid limit should be accepted.
    """
    assert _validate_limit(0) == 0
    assert _validate_limit(1000) == 1000


@pytest.mark.parametrize("value", [-1, 100001, "10"])
def test_validate_limit_rejects_invalid_limit(value) -> None:
    """
    Invalid limit should be rejected.
    """
    with pytest.raises(ValueError):
        _validate_limit(value)


def test_validate_output_srid_accepts_none_and_positive_int() -> None:
    """
    output_srid can be None or positive integer.
    """
    assert _validate_output_srid(None) is None
    assert _validate_output_srid(4326) == 4326


@pytest.mark.parametrize("value", [0, -4326, "4326"])
def test_validate_output_srid_rejects_invalid(value) -> None:
    """
    Invalid SRID values should be rejected.
    """
    with pytest.raises(ValueError):
        _validate_output_srid(value)


def test_validate_where_clause_accepts_simple_clause() -> None:
    """
    Simple WHERE clause should be accepted.
    """
    assert _validate_where_clause("population > 1000") == "population > 1000"
    assert _validate_where_clause(" name = 'Tehran' ") == "name = 'Tehran'"
    assert _validate_where_clause(None) is None
    assert _validate_where_clause("") is None


@pytest.mark.parametrize(
    "where",
    [
        "1=1; DROP TABLE roads",
        "name = 'x' -- comment",
        "DROP TABLE roads",
        "DELETE FROM roads",
        "UPDATE roads SET x=1",
        "/* hidden */ 1=1",
    ],
)
def test_validate_where_clause_rejects_unsafe_clause(where: str) -> None:
    """
    Unsafe WHERE clauses must be rejected.
    """
    with pytest.raises(ValueError):
        _validate_where_clause(where)


def test_build_conninfo_from_dsn() -> None:
    """
    DSN should be returned as-is when provided.
    """
    dsn = "postgresql://user:pass@localhost:5432/mydb"
    assert _build_conninfo(dsn=dsn) == dsn


def test_build_conninfo_from_parts() -> None:
    """
    Connection info should be created from individual parts.
    """
    conninfo = _build_conninfo(
        host="localhost",
        port=5432,
        database="gis",
        user="postgres",
        password="secret",
        connect_timeout=5,
    )

    assert "host=localhost" in conninfo
    assert "port=5432" in conninfo
    assert "dbname=gis" in conninfo
    assert "user=postgres" in conninfo
    assert "password=secret" in conninfo
    assert "connect_timeout=5" in conninfo


def test_build_conninfo_rejects_missing_parts() -> None:
    """
    If DSN is not provided, required parts must exist.
    """
    with pytest.raises(ValueError, match="host"):
        _build_conninfo(database="gis", user="postgres", password="secret")


def test_build_select_features_sql_without_transform() -> None:
    """
    SQL without output_srid should not contain ST_Transform.
    """
    sql, params = _build_select_features_sql(
        schema="public",
        table="roads",
        geom_col="geom",
        where=None,
        limit=10,
        output_srid=None,
    )

    assert 'FROM "public"."roads" AS t' in sql
    assert "ST_AsGeoJSON(t.\"geom\")" in sql
    assert "ST_Transform" not in sql
    assert "LIMIT 10" in sql
    assert params == []


def test_build_select_features_sql_with_transform_and_where() -> None:
    """
    SQL with output_srid should contain ST_Transform and WHERE.
    """
    sql, params = _build_select_features_sql(
        schema="gis",
        table="buildings",
        geom_col="geometry",
        where="height > 10",
        limit=25,
        output_srid=4326,
    )

    assert 'FROM "gis"."buildings" AS t' in sql
    assert 'ST_Transform(t."geometry", 4326)' in sql
    assert "WHERE height > 10" in sql
    assert params == []


def test_geometry_bbox_point() -> None:
    """
    Point geometry bbox should be calculated correctly.
    """
    geometry = {
        "type": "Point",
        "coordinates": [51.4, 35.7],
    }

    assert _geometry_bbox(geometry) == [51.4, 35.7, 51.4, 35.7]


def test_geometry_bbox_polygon() -> None:
    """
    Polygon geometry bbox should be calculated correctly.
    """
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [51.0, 35.0],
                [52.0, 35.0],
                [52.0, 36.0],
                [51.0, 36.0],
                [51.0, 35.0],
            ]
        ],
    }

    assert _geometry_bbox(geometry) == [51.0, 35.0, 52.0, 36.0]


def test_merge_bboxes() -> None:
    """
    Multiple bboxes should be merged into one bbox dict.
    """
    merged = _merge_bboxes([
        [51.4, 35.7, 51.4, 35.7],
        [51.0, 35.0, 52.0, 36.0],
    ])

    assert merged == {
        "minx": 51.0,
        "miny": 35.0,
        "maxx": 52.0,
        "maxy": 36.0,
    }


def test_row_to_feature_from_dict() -> None:
    """
    Dict row should be converted to GeoJSON Feature.
    """
    row = {
        "feature": {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [1, 2],
            },
            "properties": {
                "id": 1,
            },
        }
    }

    feature = _row_to_feature(row, 0)

    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["properties"]["id"] == 1


def test_row_to_feature_from_tuple_json_string() -> None:
    """
    Tuple row with JSON string should be converted to GeoJSON Feature.
    """
    feature_json = json.dumps({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [1, 2],
        },
        "properties": {
            "name": "A",
        },
    })

    feature = _row_to_feature((feature_json,), 0)

    assert feature["type"] == "Feature"
    assert feature["properties"]["name"] == "A"


def test_row_to_feature_rejects_invalid_feature() -> None:
    """
    Non-feature JSON should be rejected.
    """
    with pytest.raises(ValueError):
        _row_to_feature({"feature": {"type": "Point"}}, 0)


class FakeCursor:
    """
    Fake psycopg cursor for successful query tests.
    """

    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = None
        self.executed_params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed_sql = sql
        self.executed_params = params

    def fetchall(self):
        return self.rows


class FakeConnection:
    """
    Fake psycopg connection.
    """

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.rows)


def install_fake_psycopg(monkeypatch, rows):
    """
    Install a fake psycopg module into sys.modules.
    """
    fake_module = types.ModuleType("psycopg")

    def connect(conninfo):
        return FakeConnection(rows)

    fake_module.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)


def test_fetch_postgis_layer_success_with_fake_psycopg(monkeypatch) -> None:
    """
    fetch_postgis_layer should return VectorOut using mocked psycopg.
    """
    rows = [
        {
            "feature": {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.4, 35.7],
                },
                "properties": {
                    "id": 1,
                    "name": "A",
                },
            }
        },
        {
            "feature": {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [51.0, 35.0],
                            [52.0, 35.0],
                            [52.0, 36.0],
                            [51.0, 36.0],
                            [51.0, 35.0],
                        ]
                    ],
                },
                "properties": {
                    "id": 2,
                    "name": "B",
                },
            }
        },
    ]

    install_fake_psycopg(monkeypatch, rows)

    result = fetch_postgis_layer(
        dsn="postgresql://user:pass@localhost:5432/gis",
        schema="public",
        table="parcels",
        geom_col="geom",
        where="id > 0",
        limit=100,
        output_srid=4326,
    )

    assert result is not None
    assert len(result.features) == 2
    assert result.features[0]["properties"]["name"] == "A"

    md = result.metadata
    assert md["source"] == "postgis"
    assert md["loader"] == "postgis_connector"
    assert md["schema"] == "public"
    assert md["table"] == "parcels"
    assert md["layer"] == "public.parcels"
    assert md["geom_col"] == "geom"
    assert md["feature_count"] == 2
    assert md["where_applied"] is True
    assert md["limit"] == 100
    assert md["output_srid"] == 4326
    assert md["crs"] == "EPSG:4326"
    assert md["geometry_types"]["Point"] == 1
    assert md["geometry_types"]["Polygon"] == 1
    assert md["bounds"]["minx"] == pytest.approx(51.0)
    assert md["bounds"]["maxy"] == pytest.approx(36.0)


def test_fetch_postgis_layer_success_with_connection_parts(monkeypatch) -> None:
    """
    fetch_postgis_layer should accept host/database/user/password when dsn is absent.
    """
    rows = [
        (
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"id": 1},
            },
        )
    ]

    install_fake_psycopg(monkeypatch, rows)

    result = fetch_postgis_layer(
        host="localhost",
        port=5432,
        database="gis",
        user="postgres",
        password="secret",
        table="roads",
        limit=1,
    )

    assert len(result.features) == 1
    assert result.metadata["table"] == "roads"
    assert result.metadata["connection"]["host"] == "localhost"
    assert result.metadata["connection"]["database"] == "gis"


def test_fetch_postgis_layer_rejects_unsafe_table() -> None:
    """
    Unsafe table identifier must be rejected before database connection.
    """
    with pytest.raises(ValueError):
        fetch_postgis_layer(
            dsn="postgresql://x",
            table="roads;DROP",
        )


def test_fetch_postgis_layer_rejects_missing_connection(monkeypatch, tmp_path: Path) -> None:
    """
    Missing connection info must raise ValueError when no config profile is available.

    Since postgis_connector is now config-aware, the normal project config may provide
    a default_profile. This test isolates config lookup into an empty temporary
    config directory.
    """
    empty_config_dir = tmp_path / "empty_config" / "plugins"
    empty_config_dir.mkdir(parents=True)

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(empty_config_dir))
    monkeypatch.delenv("POSTGIS_PASSWORD", raising=False)

    with pytest.raises(ValueError):
        fetch_postgis_layer(table="roads")


def test_vectorout_to_artifact(monkeypatch) -> None:
    """
    VectorOut returned by plugin must be convertible to ExecutionArtifact.
    """
    rows = [
        {
            "feature": {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [1, 2],
                },
                "properties": {
                    "id": 1,
                },
            }
        }
    ]

    install_fake_psycopg(monkeypatch, rows)

    result = fetch_postgis_layer(
        dsn="postgresql://user:pass@localhost:5432/gis",
        table="points",
        limit=1,
    )

    artifact = result.to_artifact(produced_by="test_postgis_connector")

    assert artifact.kind == "features"
    assert artifact.produced_by == "test_postgis_connector"
    assert "features" in artifact.payload
    assert len(artifact.payload["features"]) == 1
    assert artifact.payload["source"] == "postgis"
    assert artifact.payload["loader"] == "postgis_connector"
    assert artifact.payload["feature_count"] == 1


def test_capability_registered_inside_plugin() -> None:
    """
    auto_collect should collect decorated capabilities into SDKPlugin.
    """
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "fetch_postgis_layer" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    """
    Capability descriptor generated by SDK registration should contain expected fields.
    """
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "fetch_postgis_layer")

    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "fetch_postgis_layer"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.kind == "capability"
    assert descriptor.output_kind == "vector"
    assert "table" in descriptor.required_inputs
    assert "dsn" in descriptor.optional_inputs
    assert "schema" in descriptor.optional_inputs
    assert "geom_col" in descriptor.optional_inputs
    assert "where" in descriptor.optional_inputs
    assert "limit" in descriptor.optional_inputs
    assert "database" in descriptor.requires_permissions
    assert descriptor.metadata["routable"] is True
    assert descriptor.metadata["category"] == "data_io"
    assert descriptor.metadata["artifact_kind"] == "features"
    assert descriptor.metadata["access_scope"] == "read_database"


def test_fetch_postgis_layer_with_config_profile(monkeypatch, tmp_path: Path) -> None:
    """
    fetch_postgis_layer should load connection settings from config profile.
    """
    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    config_file = config_dir / "postgis_connector.yaml"
    config_file.write_text(
        """
default_profile: local
profiles:
  local:
    host: localhost
    port: 5432
    database: gis
    user: postgres
    password_env: TEST_POSTGIS_PASSWORD
    default_schema: public
    default_geom_col: geom
    default_limit: 10
    output_srid: 4326
    connect_timeout: 5
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("TEST_POSTGIS_PASSWORD", "secret")

    rows = [
        {
            "feature": {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.4, 35.7],
                },
                "properties": {
                    "id": 1,
                    "name": "A",
                },
            }
        }
    ]

    install_fake_psycopg(monkeypatch, rows)

    result = fetch_postgis_layer(
        profile="local",
        table="roads",
    )

    assert len(result.features) == 1
    assert result.metadata["profile"] == "local"
    assert result.metadata["schema"] == "public"
    assert result.metadata["table"] == "roads"
    assert result.metadata["geom_col"] == "geom"
    assert result.metadata["limit"] == 10
    assert result.metadata["output_srid"] == 4326
    assert result.metadata["crs"] == "EPSG:4326"
    assert result.metadata["connection"]["host"] == "localhost"
    assert result.metadata["connection"]["database"] == "gis"


def test_execute_postgis_query_failure_has_provider_structured_error(monkeypatch) -> None:
    import sys
    import types

    import pytest

    import plugins.postgis_connector as postgis_connector
    from orchestrator.provider_error_mapping import ProviderExecutionError

    fake_psycopg = types.ModuleType("psycopg")

    def fake_connect(conninfo):
        raise RuntimeError("connection refused password=must-not-leak")

    fake_psycopg.connect = fake_connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    with pytest.raises(ValueError) as exc_info:
        postgis_connector._execute_postgis_query(
            conninfo="host=localhost password=must-not-leak",
            sql="SELECT 1",
            params=[],
        )

    exc = exc_info.value

    assert isinstance(exc, ProviderExecutionError)
    assert hasattr(exc, "structured_error")
    assert exc.structured_error["code"] == "provider.connection_failed"
    assert exc.structured_error["category"] == "provider_error"
    assert exc.structured_error["source"] == "postgis_connector"
    assert exc.structured_error["retryable"] is True
    assert exc.structured_error["details"]["provider"] == "postgis"
    assert exc.structured_error["details"]["operation"] == "execute_query"
    assert exc.structured_error["details"]["driver"] == "psycopg"
    assert "must-not-leak" not in exc.structured_error["message"]
