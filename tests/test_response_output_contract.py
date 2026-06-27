from __future__ import annotations

from typing import Any

from orchestrator.production_response import ProductionResponseBuilder
from smart_spatial_system.application.services.planning_response_adapter import (
    planning_outputs_to_response_payload,
)
from smart_spatial_system.application.services.query_execution.real_estate_ranking_response import (
    build_real_estate_ranking_response,
)


class _PlanningResult:
    output_nodes: dict[str, Any] = {}


def test_planning_outputs_include_standard_success_buckets() -> None:
    _, outputs, _ = planning_outputs_to_response_payload(_PlanningResult())

    assert set(
        [
            "vectors",
            "rasters",
            "tables",
            "documents",
            "reports",
            "files",
            "artifacts",
        ]
    ).issubset(outputs.keys())

    assert outputs["vectors"] == []
    assert outputs["rasters"] == []
    assert outputs["tables"] == []
    assert outputs["documents"] == []
    assert outputs["reports"] == []
    assert outputs["files"] == []
    assert outputs["artifacts"] == []


def test_production_response_dict_exposes_frontend_contract_fields() -> None:
    response = ProductionResponseBuilder().build_dict(
        run_result={
            "response": {
                "status": "success",
                "request_id": "req-1",
                "answer": "done",
                "map": {
                    "layers": [
                        {
                            "id": "layer-1",
                            "type": "vector",
                        }
                    ]
                },
                "documents": [
                    {
                        "id": "doc-1",
                    }
                ],
                "artifacts": [
                    {
                        "id": "artifact-1",
                    }
                ],
                "trace": [
                    {
                        "step": "inspect",
                        "status": "success",
                    }
                ],
            },
            "outputs": {
                "vectors": [],
                "rasters": [],
                "tables": [],
            },
        },
    )

    assert response["ok"] is True
    assert response["message"] == response["answer"]
    assert response["layers"] == [{"id": "layer-1", "type": "vector"}]
    assert response["documents"] == [{"id": "doc-1"}]
    assert response["artifacts"] == [{"id": "artifact-1"}]
    assert response["trace"] == [{"step": "inspect", "status": "success"}]
    assert response["steps"] == response["trace"]


def test_real_estate_ranking_response_uses_standard_output_contract() -> None:
    ranked_geojson = {
        "type": "FeatureCollection",
        "features": [],
    }
    documents = [
        {
            "id": "doc-1",
            "format": "pdf",
        }
    ]
    report = {
        "title": "گزارش",
        "summary": {},
    }

    response = build_real_estate_ranking_response(
        query="رتبه‌بندی املاک",
        rid="req-1",
        message="done",
        features=[],
        ranked_features=[],
        ranked_geojson=ranked_geojson,
        rejected_rows=[],
        table_rows=[],
        summary={},
        report=report,
        documents=documents,
        document_warnings=[],
        render_pdf_trace_step={
            "order": 5,
            "node_id": "node_005_render_pdf",
            "capability_name": "render_pdf",
            "plugin_id": "real_estate_ranking_bridge",
            "output_kind": "document",
            "status": "success",
        },
        spatial_enrichment_summary={},
        llm_intent=None,
        build_analysis_inspector=lambda **kwargs: kwargs,
        llm_planning_enabled=lambda: False,
    )

    assert response["ok"] is True
    assert response["status"] == "succeeded"
    assert response["documents"] == documents
    assert response["trace"]
    assert response["report"] == report
    assert response["files"] == []
    assert response["artifacts"] == []

    outputs = response["outputs"]
    assert set(
        [
            "vectors",
            "rasters",
            "tables",
            "documents",
            "reports",
            "files",
            "artifacts",
        ]
    ).issubset(outputs.keys())
    assert outputs["documents"] == documents
    assert outputs["files"] == []
    assert outputs["artifacts"] == []
