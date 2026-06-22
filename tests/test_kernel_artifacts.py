from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geochat_kernel.models import ArtifactKind, GeoArtifact

from orchestrator.kernel_artifacts import (
    artifact_to_public_dict,
    artifacts_to_public_list,
    make_artifact,
    output_to_artifact,
)


def test_make_artifact_returns_kernel_geo_artifact() -> None:
    artifact = make_artifact(
        kind=ArtifactKind.FEATURES,
        title="Parks",
        payload={
            "format": "geojson",
            "data": {"type": "FeatureCollection", "features": []},
        },
        source_node="load_parks",
        primary=True,
        metadata={"language": "fa"},
    )

    assert isinstance(artifact, GeoArtifact)
    assert artifact.kind == "features"
    assert artifact.title == "Parks"
    assert artifact.ref_id == "load_parks"
    assert artifact.primary is True
    assert artifact.metadata["language"] == "fa"


def test_artifact_to_public_dict_keeps_kernel_kind_and_public_type() -> None:
    artifact = make_artifact(
        kind=ArtifactKind.FEATURES,
        payload={
            "format": "geojson",
            "data": {"type": "FeatureCollection", "features": []},
        },
        source_node="nearest_result",
    )

    public = artifact_to_public_dict(artifact)

    assert public["kind"] == "features"
    assert public["type"] == "vector_layer"
    assert public["source_node"] == "nearest_result"
    assert public["payload"]["format"] == "geojson"


def test_output_to_artifact_detects_geojson_feature_collection() -> None:
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

    artifact = output_to_artifact(
        geojson,
        source_node="query_parks",
        title="پارک‌ها",
        produced_by="query_database_postgis",
    )

    assert artifact.kind == "features"
    assert artifact.title == "پارک‌ها"
    assert artifact.ref_id == "query_parks"
    assert artifact.produced_by == "query_database_postgis"
    assert artifact.metadata["feature_count"] == 1
    assert artifact.payload["format"] == "geojson"


def test_output_to_artifact_detects_table_rows() -> None:
    rows = [
        {"name": "A", "distance_m": 120.5},
        {"name": "B", "distance_m": 250.0},
    ]

    artifact = output_to_artifact(rows, title="Distances")

    assert artifact.kind == "table"
    assert artifact.metadata["row_count"] == 2
    assert artifact.payload["rows"] == rows


def test_artifacts_to_public_list() -> None:
    artifacts = [
        make_artifact(kind=ArtifactKind.TABLE, payload={"rows": []}),
        make_artifact(kind=ArtifactKind.REPORT, payload={"summary": "ok"}),
    ]

    public = artifacts_to_public_list(artifacts)

    assert len(public) == 2
    assert public[0]["type"] == "table"
    assert public[1]["type"] == "report"


def test_output_to_artifact_detects_feature_dict_without_feature_collection_type() -> None:
    value = {
        "features": [
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"name": "A"},
            }
        ]
    }

    artifact = output_to_artifact(value, source_node="feature_dict")

    assert artifact.kind == "features"
    assert artifact.ref_id == "feature_dict"
    assert artifact.payload["format"] == "geojson"
    assert artifact.payload["data"]["type"] == "FeatureCollection"
    assert artifact.metadata["feature_count"] == 1


def test_output_to_artifact_detects_table_dict_with_rows() -> None:
    value = {
        "title": "Distances",
        "columns": ["name", "distance_m"],
        "rows": [
            {"name": "A", "distance_m": 10},
            {"name": "B", "distance_m": 20},
        ],
    }

    artifact = output_to_artifact(value, source_node="distance_table")

    assert artifact.kind == "table"
    assert artifact.title == "Distances"
    assert artifact.ref_id == "distance_table"
    assert artifact.payload["rows"] == value["rows"]
    assert artifact.payload["columns"] == value["columns"]
    assert artifact.metadata["row_count"] == 2


def test_output_to_artifact_detects_report_dict() -> None:
    value = {
        "title": "تحلیل پارک‌ها",
        "summary": "گزارش آماده است.",
        "sections": [
            {
                "title": "نتیجه",
                "content": "ok",
            }
        ],
    }

    artifact = output_to_artifact(value, source_node="report_node")

    assert artifact.kind == "report"
    assert artifact.title == "تحلیل پارک‌ها"
    assert artifact.ref_id == "report_node"
    assert artifact.payload["format"] == "json"
    assert artifact.payload["data"]["summary"] == "گزارش آماده است."


def test_output_to_artifact_detects_download_dict() -> None:
    value = {
        "name": "report.pdf",
        "pdf_path": "/tmp/report.pdf",
    }

    artifact = output_to_artifact(value, source_node="pdf_node")

    assert artifact.kind == "download"
    assert artifact.title == "report.pdf"
    assert artifact.payload["format"] == "pdf"
    assert artifact.payload["path"] == "/tmp/report.pdf"


def test_output_to_artifact_detects_raster_reference_dict() -> None:
    value = {
        "name": "slope",
        "raster_path": "/tmp/slope.tif",
    }

    artifact = output_to_artifact(value, source_node="raster_node")

    assert artifact.kind == "raster_ref"
    assert artifact.title == "slope"
    assert artifact.payload["format"] == "geotiff"
    assert artifact.payload["path"] == "/tmp/slope.tif"
