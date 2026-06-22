from __future__ import annotations

from types import SimpleNamespace

from orchestrator.service import OrchestratorService


def test_planning_outputs_include_normalized_artifacts() -> None:
    service = OrchestratorService.__new__(OrchestratorService)

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"name": "پارک ملت"},
            }
        ],
    }

    rows = [
        {"name": "A", "distance_m": 120.5},
        {"name": "B", "distance_m": 250.0},
    ]

    planning_result = SimpleNamespace(
        output_nodes={
            "parks_layer": geojson,
            "distance_table": rows,
            "summary_payload": {"summary": "ok"},
        }
    )

    layers, outputs, primary_report = service._planning_outputs_to_response_payload(
        planning_result
    )

    assert primary_report is not None
    assert len(layers) == 1

    assert "artifacts" in outputs
    assert len(outputs["artifacts"]) == 3

    artifact_by_source = {
        artifact["source_node"]: artifact
        for artifact in outputs["artifacts"]
    }

    parks_artifact = artifact_by_source["parks_layer"]
    assert parks_artifact["kind"] == "features"
    assert parks_artifact["type"] == "vector_layer"
    assert parks_artifact["payload"]["format"] == "geojson"
    assert parks_artifact["metadata"]["feature_count"] == 1

    table_artifact = artifact_by_source["distance_table"]
    assert table_artifact["kind"] == "table"
    assert table_artifact["type"] == "table"
    assert table_artifact["payload"]["rows"] == rows
    assert table_artifact["metadata"]["row_count"] == 2

    payload_artifact = artifact_by_source["summary_payload"]
    assert payload_artifact["kind"] == "scalar"
    assert payload_artifact["type"] == "json"
    assert payload_artifact["payload"]["data"]["summary"] == "ok"
