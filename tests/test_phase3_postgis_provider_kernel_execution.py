from __future__ import annotations

from orchestrator.planning.runner import make_static_planning_runner
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec

import plugins.postgis_connector as postgis_connector


def test_query_database_provider_queryspec_executes_through_kernel_runtime(
    monkeypatch,
) -> None:
    """
    Phase 3 / Step 8 provider integration test.

    Core principle:
    The kernel execution path must remain source-agnostic. This test uses the
    PostGIS provider only as one concrete plugin capability behind the logical
    query_database operation.

    It intentionally does not connect to a real database and does not depend on
    OSM, Tehran, a UI, a language, or a specific deployment.
    """

    calls: list[dict] = []

    def fake_fetch_postgis_sql_layer(**kwargs):
        calls.append(dict(kwargs))

        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [51.0, 35.0],
                    },
                    "properties": {
                        "id": 1,
                        "name": "Sample A",
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [52.0, 36.0],
                    },
                    "properties": {
                        "id": 2,
                        "name": "Sample B",
                    },
                },
            ],
        }

    monkeypatch.setattr(
        postgis_connector,
        "fetch_postgis_sql_layer",
        fake_fetch_postgis_sql_layer,
    )

    runner = make_static_planning_runner(
        {
            "query_database_postgis": postgis_connector.query_database_postgis,
        }
    )

    query_spec = QuerySpec(
        raw_query="Load a sample provider-backed spatial layer",
        goal="load_provider_backed_vector_layer",
        entities=[
            EntitySpec(
                ref="sample_places_source",
                kind="database",
            )
        ],
        operations=[
            OperationSpec(
                op="query_database",
                inputs={},
                params={
                    "source_type": "postgis",
                    "mode": "select_table",
                    "schema": "public",
                    "table": "sample_places",
                    "columns": ["id", "name"],
                    "geom_col": "geom",
                    "geom_alias": "geom",
                    "where": "geom IS NOT NULL",
                    "limit": 2,
                    "output_srid": 4326,
                    "host": "localhost",
                    "port": 5432,
                    "database": "sample_database",
                    "user": "sample_user",
                    "password": "sample_password",
                    "connect_timeout": 3,
                },
                output="places",
            )
        ],
        outputs=[
            OutputSpec(
                kind="vector",
                source="places",
            )
        ],
    )

    result = runner.run_with_kernel_execution(
        query_spec,
        initial_inputs={},
    )

    assert result.success is True
    assert result.error is None

    assert result.kernel_plan is not None
    assert result.kernel_execution is not None
    assert result.kernel_execution.success is True
    assert result.kernel_execution.error is None

    # run_with_kernel_execution intentionally executes both:
    # 1. current DAG path
    # 2. experimental kernel path
    assert len(calls) == 2

    for call in calls:
        assert call["geom_col"] == "geom"
        assert call["limit"] == 2
        assert call["output_srid"] == 4326
        assert call["host"] == "localhost"
        assert call["database"] == "sample_database"
        assert call["user"] == "sample_user"

        sql = call["sql"]

        assert "SELECT" in sql
        assert '"id"' in sql
        assert '"name"' in sql
        assert '"geom" AS "geom"' in sql
        assert 'FROM "public"."sample_places"' in sql
        assert "geom IS NOT NULL" in sql
        assert "LIMIT 2" in sql

        # Safety check: the provider adapter must compile a read-only query.
        assert "DROP" not in sql.upper()
        assert "DELETE" not in sql.upper()
        assert "UPDATE" not in sql.upper()
        assert "INSERT" not in sql.upper()

    dag_output = result.output_nodes["places"]
    kernel_output = result.kernel_execution.output_nodes["places"]

    assert dag_output == kernel_output
    assert kernel_output["type"] == "FeatureCollection"
    assert len(kernel_output["features"]) == 2

    kernel_artifact = result.kernel_execution.output_artifacts["places"]

    assert kernel_artifact.step_id == "places"
    assert kernel_artifact.metadata["capability_name"] == "query_database_postgis"
    assert kernel_artifact.metadata["source"] == "smart_spatial_system.kernel_execution_bridge"
    assert kernel_artifact.has_live is True
