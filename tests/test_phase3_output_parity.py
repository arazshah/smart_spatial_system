from __future__ import annotations

from orchestrator.planning.output_parity import (
    compare_output_node_parity,
    summarize_output_value,
)
from orchestrator.planning.runner import make_static_planning_runner
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec

import plugins.postgis_connector as postgis_connector


def test_output_parity_summarizes_vector_like_outputs_without_datasource_dependency() -> None:
    value = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [1.0, 2.0],
                },
                "properties": {
                    "id": 1,
                    "name": "A",
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [],
                },
                "properties": {
                    "id": 2,
                    "score": 0.9,
                },
            },
        ],
    }

    summary = summarize_output_value(value)

    assert summary["shape"] == "vector_features"
    assert summary["feature_count"] == 2
    assert summary["geometry_types"] == ["Point", "Polygon"]
    assert summary["property_keys"] == ["id", "name", "score"]
    assert summary["top_level_type"] == "FeatureCollection"


def test_output_node_parity_detects_missing_extra_and_vector_mismatches() -> None:
    dag_outputs = {
        "places": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [1.0, 2.0],
                    },
                    "properties": {
                        "id": 1,
                    },
                }
            ],
        },
        "report": {
            "status": "ok",
        },
    }

    kernel_outputs = {
        "places": {
            "type": "FeatureCollection",
            "features": [],
        },
        "extra": {
            "status": "extra",
        },
    }

    parity = compare_output_node_parity(dag_outputs, kernel_outputs)

    assert parity["compatible"] is False
    assert parity["status"] == "mismatch"
    assert parity["missing_from_kernel"] == ["report"]
    assert parity["extra_in_kernel"] == ["extra"]
    assert parity["nodes"]["places"]["compatible"] is False
    assert "feature_count" in parity["nodes"]["places"]["mismatches"]


def test_provider_database_queryspec_has_dag_kernel_vector_parity(
    monkeypatch,
) -> None:
    """
    Phase 3 / Step 9.

    This uses a PostGIS provider capability only as one plugin-backed provider.
    The parity utility itself remains source-agnostic and output-shape based.
    """

    def fake_fetch_postgis_sql_layer(**kwargs):
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [10.0, 20.0],
                    },
                    "properties": {
                        "id": 1,
                        "label": "A",
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [11.0, 21.0],
                    },
                    "properties": {
                        "id": 2,
                        "label": "B",
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
        raw_query="Load a provider-backed vector layer",
        goal="load_provider_backed_vector_layer",
        entities=[
            EntitySpec(
                ref="source",
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
                    "table": "sample_layer",
                    "columns": ["id", "label"],
                    "geom_col": "geom",
                    "geom_alias": "geom",
                    "where": None,
                    "limit": 2,
                    "output_srid": 4326,
                    "host": "localhost",
                    "port": 5432,
                    "database": "sample_database",
                    "user": "sample_user",
                    "password": "sample_password",
                    "connect_timeout": 3,
                },
                output="loaded_layer",
            )
        ],
        outputs=[
            OutputSpec(
                kind="vector",
                source="loaded_layer",
            )
        ],
    )

    result = runner.run_with_kernel_execution(
        query_spec,
        initial_inputs={},
    )

    assert result.success is True
    assert result.kernel_execution is not None
    assert result.kernel_execution.success is True

    parity = compare_output_node_parity(
        result.output_nodes,
        result.kernel_execution.output_nodes,
    )

    assert parity["compatible"] is True
    assert parity["status"] == "compatible"
    assert parity["missing_from_kernel"] == []
    assert parity["extra_in_kernel"] == []
    assert parity["dag_output_node_ids"] == ["loaded_layer"]
    assert parity["kernel_output_node_ids"] == ["loaded_layer"]

    node_parity = parity["nodes"]["loaded_layer"]

    assert node_parity["compatible"] is True
    assert node_parity["exact_equal"] is True
    assert node_parity["mismatches"] == []

    assert node_parity["dag_summary"]["shape"] == "vector_features"
    assert node_parity["kernel_summary"]["shape"] == "vector_features"
    assert node_parity["dag_summary"]["feature_count"] == 2
    assert node_parity["kernel_summary"]["feature_count"] == 2
    assert node_parity["dag_summary"]["geometry_types"] == ["Point"]
    assert node_parity["kernel_summary"]["geometry_types"] == ["Point"]
    assert node_parity["dag_summary"]["property_keys"] == ["id", "label"]
    assert node_parity["kernel_summary"]["property_keys"] == ["id", "label"]
