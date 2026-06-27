from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

from orchestrator.output_storage import OutputStorage, OutputStorageConfig
from smart_spatial_system.application.services.query_execution.real_estate_analysis_inspector import (
    build_real_estate_analysis_inspector,
)
from smart_spatial_system.application.services.query_execution.real_estate_document_renderer import (
    try_render_real_estate_ranking_document,
)


class _FakePdfOut:
    def __init__(self, *, file_path: str, pdf_bytes: bytes, meta: dict[str, Any]) -> None:
        self.success = True
        self.file_path = file_path
        self.pdf_bytes = pdf_bytes
        self.meta = meta
        self.html = ""
        self.errors: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "file_path": self.file_path,
            "meta": self.meta,
            "errors": self.errors,
        }


def test_real_estate_pdf_document_renderer_exposes_public_document_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    fake_pdf_module = types.ModuleType("plugins.pdf_renderer")

    def fake_render_pdf(
        payload: dict[str, Any],
        *,
        output_path: str,
        save_to_disk: bool,
        metadata: dict[str, Any],
    ) -> _FakePdfOut:
        assert payload["meta"]["format"] == "pdf"
        assert save_to_disk is True

        pdf_bytes = b"%PDF-1.4\n% contract test\n%%EOF\n"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(pdf_bytes)

        return _FakePdfOut(
            file_path=output_path,
            pdf_bytes=pdf_bytes,
            meta=metadata,
        )

    fake_pdf_module.render_pdf = fake_render_pdf
    monkeypatch.setitem(sys.modules, "plugins.pdf_renderer", fake_pdf_module)

    documents, warnings, trace_step = try_render_real_estate_ranking_document(
        report={
            "title": "گزارش رتبه‌بندی املاک",
            "notes": [],
        },
        table_rows=[],
        ranked_geojson={
            "type": "FeatureCollection",
            "features": [],
        },
        summary={},
        request_id="req-doc-contract-001",
        build_pdf_report_payload=lambda **kwargs: {
            "meta": {
                "format": "pdf",
                "title": kwargs["report"]["title"],
            },
            "summary": kwargs["summary"],
            "table": {
                "rows": kwargs["table_rows"],
            },
        },
    )

    assert warnings == []
    assert trace_step["status"] == "success"
    assert trace_step["artifact_id"] == "real_estate_ranking_pdf"

    assert len(documents) == 1
    document = documents[0]

    assert document["id"] == "real_estate_ranking_pdf"
    assert document["name"] == "real_estate_ranking_report.pdf"
    assert document["filename"] == "real_estate_ranking_req-doc-contract-001.pdf"
    assert document["format"] == "pdf"
    assert document["role"] == "downloadable_report"
    assert document["mime_type"] == "application/pdf"

    assert document["path"] == document["file_path"]
    assert document["path"].endswith("real_estate_ranking_req-doc-contract-001.pdf")

    assert document["download_url"] == (
        "/requests/req-doc-contract-001/documents/"
        "real_estate_ranking_req-doc-contract-001.pdf"
    )
    assert document["preview_url"] == document["download_url"]
    assert document["size_bytes"] > 0
    assert document["meta"]["request_id"] == "req-doc-contract-001"
    assert document["meta"]["report_id"] == "real_estate_ranking_report"


def test_real_estate_analysis_inspector_normalizes_document_contract() -> None:
    document = {
        "id": "real_estate_ranking_pdf",
        "name": "real_estate_ranking_report.pdf",
        "filename": "real_estate_ranking_req-1.pdf",
        "format": "pdf",
        "role": "downloadable_report",
        "mime_type": "application/pdf",
        "path": "artifacts/reports/real_estate_ranking_req-1.pdf",
        "file_path": "artifacts/reports/real_estate_ranking_req-1.pdf",
        "download_url": "/requests/req-1/documents/real_estate_ranking_req-1.pdf",
        "preview_url": "/requests/req-1/documents/real_estate_ranking_req-1.pdf",
        "size_bytes": 123,
    }

    inspector = build_real_estate_analysis_inspector(
        title="گزارش رتبه‌بندی املاک",
        status="succeeded",
        summary={
            "eligible_count": 1,
        },
        outputs={
            "vectors": [],
            "rasters": [],
            "tables": [],
            "reports": [
                {
                    "id": "real_estate_ranking_report",
                    "name": "real_estate_ranking_report",
                    "format": "json",
                    "role": "analysis_report",
                    "data": {
                        "title": "گزارش رتبه‌بندی املاک",
                        "summary": {},
                    },
                }
            ],
            "documents": [document],
            "files": [],
            "artifacts": [],
        },
        layers=[],
        trace=[],
        documents=[document],
        warnings=[],
    )

    assert inspector["documents"]
    normalized_document = inspector["documents"][0]

    assert normalized_document["id"] == "real_estate_ranking_pdf"
    assert normalized_document["type"] == "document"
    assert normalized_document["name"] == "real_estate_ranking_report.pdf"
    assert normalized_document["role"] == "downloadable_report"
    assert normalized_document["format"] == "pdf"
    assert normalized_document["mime_type"] == "application/pdf"
    assert normalized_document["path"] == document["download_url"]
    assert normalized_document["file_path"] == document["file_path"]
    assert normalized_document["download_url"] == document["download_url"]
    assert normalized_document["preview_url"] == document["preview_url"]
    assert normalized_document["size_bytes"] == 123
    assert normalized_document["source"] == "outputs.documents"

    output_documents = [
        item
        for item in inspector["outputs"]
        if item.get("type") == "document"
    ]
    assert output_documents
    assert output_documents[0]["download_url"] == document["download_url"]

    output_reports = [
        item
        for item in inspector["outputs"]
        if item.get("type") == "report"
    ]
    assert output_reports
    assert output_reports[0]["id"] == "real_estate_ranking_report"
    assert output_reports[0]["format"] == "json"
    assert output_reports[0]["role"] == "analysis_report"


def test_output_storage_manifest_files_expose_public_file_contract(
    tmp_path: Path,
) -> None:
    storage = OutputStorage(
        OutputStorageConfig(
            root_dir=tmp_path / "outputs",
        )
    )

    manifest = storage.save_request_record(
        {
            "request_id": "req-output-contract-001",
            "query": "test query",
            "production_response": {
                "status": "success",
                "request_id": "req-output-contract-001",
                "query_hash": "hash-1",
                "outputs": {
                    "summary": {
                        "feature_count": 1,
                    }
                },
            },
            "audit_record": {
                "status": "success",
                "outputs_summary": {
                    "feature_count": 1,
                },
            },
            "run_result": {
                "response": {
                    "status": "success",
                },
            },
        },
        map_layers_payload={
            "layers": [
                {
                    "name": "contract_layer",
                    "type": "vector",
                    "feature_count": 1,
                    "geojson": {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": None,
                                "properties": {
                                    "name": "A",
                                },
                            }
                        ],
                    },
                }
            ]
        },
    )

    assert manifest["schema_version"] == "1.0.0"
    assert manifest["request_id"] == "req-output-contract-001"
    assert manifest["files"]

    filenames = {
        item["filename"]
        for item in manifest["files"]
    }

    assert "manifest.json" in filenames
    assert "production_response.json" in filenames
    assert "audit_record.json" in filenames
    assert "outputs_summary.json" in filenames
    assert "request_metadata.json" in filenames
    assert "map_layers.json" in filenames
    assert "contract_layer.geojson" in filenames
    assert "run_result_light.json" in filenames

    for file_info in manifest["files"]:
        assert file_info["filename"]
        assert file_info["kind"]
        assert file_info["path"]
        assert file_info["size_bytes"] >= 0
        assert file_info["media_type"]

    geojson_file = next(
        item
        for item in manifest["files"]
        if item["filename"] == "contract_layer.geojson"
    )

    assert geojson_file["kind"] == "geojson"
    assert geojson_file["media_type"] == "application/geo+json"
    assert geojson_file["layer_name"] == "contract_layer"
    assert geojson_file["feature_count"] == 1
