from types import SimpleNamespace

from smart_spatial_system.application.services.query_spec_enrichment import (
    enrich_query_database_params_from_inputs,
)


def test_enrich_query_database_params_injects_runtime_values() -> None:
    query_spec = SimpleNamespace(
        operations=[
            SimpleNamespace(
                op="query_database",
                params={
                    "host": "<provided-at-runtime>",
                    "schema": "public",
                    "table": "",
                },
            )
        ]
    )

    enrich_query_database_params_from_inputs(
        query_spec,
        {
            "host": "localhost",
            "database": "gis",
            "user": "postgres",
            "password": "secret",
            "schema": "ignored_public",
            "table": "places",
            "limit": 100,
            "output_srid": 4326,
        },
    )

    params = query_spec.operations[0].params

    assert params["host"] == "localhost"
    assert params["database"] == "gis"
    assert params["user"] == "postgres"
    assert params["password"] == "secret"
    assert params["schema"] == "public"
    assert params["table"] == "places"
    assert params["limit"] == 100
    assert params["output_srid"] == 4326


def test_enrich_query_database_params_preserves_existing_non_empty_values() -> None:
    query_spec = SimpleNamespace(
        operations=[
            SimpleNamespace(
                op="query_database",
                params={
                    "host": "explicit-host",
                    "database": "explicit-db",
                    "limit": 10,
                },
            )
        ]
    )

    enrich_query_database_params_from_inputs(
        query_spec,
        {
            "host": "runtime-host",
            "database": "runtime-db",
            "limit": 1000,
        },
    )

    params = query_spec.operations[0].params

    assert params["host"] == "explicit-host"
    assert params["database"] == "explicit-db"
    assert params["limit"] == 10


def test_enrich_query_database_params_defaults_geom_col_for_sql_mode() -> None:
    query_spec = SimpleNamespace(
        operations=[
            SimpleNamespace(
                op="query_database",
                params={
                    "sql": "SELECT id, way AS geom FROM places",
                },
            )
        ]
    )

    enrich_query_database_params_from_inputs(
        query_spec,
        {
            "database": "gis",
        },
    )

    params = query_spec.operations[0].params

    assert params["database"] == "gis"
    assert params["geom_col"] == "geom"


def test_enrich_query_database_params_ignores_non_query_database_ops() -> None:
    query_spec = SimpleNamespace(
        operations=[
            SimpleNamespace(
                op="ndvi",
                params={},
            )
        ]
    )

    enrich_query_database_params_from_inputs(
        query_spec,
        {
            "database": "gis",
        },
    )

    assert query_spec.operations[0].params == {}
