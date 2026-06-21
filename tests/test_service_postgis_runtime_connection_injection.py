from orchestrator.service import (
    OrchestratorService,
    _build_query_spec_runtime_inputs,
)
from orchestrator.planning.spec import OperationSpec, QuerySpec


def test_build_query_spec_runtime_inputs_flattens_postgis_connection_from_user_context():
    runtime_inputs, injected = _build_query_spec_runtime_inputs(
        resolved_inputs={},
        user_context={
            "postgis_connection": {
                "source_type": "postgis",
                "host": "localhost",
                "port": "5433",
                "database": "osm_tehran",
                "user": "postgres",
                "password": "secret",
                "schemas": ["public"],
                "connect_timeout": 5,
            }
        },
        metadata={},
    )

    assert injected is True
    assert runtime_inputs["host"] == "localhost"
    assert runtime_inputs["port"] == 5433
    assert runtime_inputs["database"] == "osm_tehran"
    assert runtime_inputs["user"] == "postgres"
    assert runtime_inputs["password"] == "secret"
    assert runtime_inputs["connect_timeout"] == 5
    assert runtime_inputs["postgis_connection"]["password"] == "secret"
    assert runtime_inputs["database_connection"]["database"] == "osm_tehran"


def test_query_database_enrichment_receives_flattened_postgis_connection_params():
    svc = OrchestratorService()

    spec = QuerySpec(
        raw_query="پارک‌ها را از PostGIS بگیر",
        goal="load parks",
        entities=[],
        operations=[
            OperationSpec(
                op="query_database",
                inputs={},
                params={
                    "source_type": "postgis",
                    "mode": "select_table",
                    "schema": "public",
                    "table": "planet_osm_point",
                    "columns": ["osm_id", "name"],
                    "geom_col": "way",
                    "geom_alias": "geom",
                    "where": '"way" IS NOT NULL',
                    "limit": 10,
                    "output_srid": 3857,
                },
                output="parks",
            )
        ],
        outputs=[],
        metadata={},
    )

    runtime_inputs, injected = _build_query_spec_runtime_inputs(
        resolved_inputs={},
        user_context={
            "postgis_connection": {
                "source_type": "postgis",
                "host": "localhost",
                "port": 5433,
                "database": "osm_tehran",
                "user": "postgres",
                "password": "secret",
                "connect_timeout": 5,
            }
        },
        metadata={},
    )

    assert injected is True

    svc._enrich_query_database_params_from_inputs(
        spec,
        runtime_inputs,
    )

    params = spec.operations[0].params

    assert params["host"] == "localhost"
    assert params["port"] == 5433
    assert params["database"] == "osm_tehran"
    assert params["user"] == "postgres"
    assert params["password"] == "secret"
    assert params["connect_timeout"] == 5
