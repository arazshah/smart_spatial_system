from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from orchestrator.map_layers import MapLayerBuilder
from orchestrator.response_builder import SimpleResponseBuilder
from smart_spatial_system.application.services.query_execution.real_estate_ranking_response import (
    build_real_estate_ranking_response,
)
from smart_spatial_system.application.services.vector_display_handler import (
    try_handle_vector_display_directly,
)

FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "name": "A",
                "score": 91,
            },
            "geometry": {
                "type": "Point",
                "coordinates": [51.4, 35.7],
            },
        }
    ],
}


def _json_safe(value: Any) -> Any:
    return value


class _FakeRouter:
    def resolve(self, capability_name: str) -> Any:
        if capability_name == "inspect_vector":
            return SimpleNamespace(
                plugin_id="vector_tools",
                output_kind="summary",
                callable=lambda *, vector: {
                    "summary": {
                        "feature_count": len(vector["features"]),
                        "geometry_counts": {
                            "Point": len(vector["features"]),
                        },
                        "property_keys": ["name", "score"],
                    }
                },
            )

        if capability_name == "display_vector_layer":
            return SimpleNamespace(
                plugin_id="vector_tools",
                output_kind="map_layer",
                callable=lambda *, vector, layer_id, name, visible: {
                    "message": "Vector layer is ready.",
                    "summary": {
                        "feature_count": len(vector["features"]),
                    },
                    "outputs": {
                        "vectors": [
                            {
                                "id": layer_id,
                                "name": name,
                                "format": "geojson",
                                "role": "map_layer",
                                "geojson": vector,
                                "summary": {
                                    "feature_count": len(vector["features"]),
                                },
                            }
                        ],
                        "rasters": [],
                        "tables": [],
                    },
                    "layers": [
                        {
                            "id": layer_id,
                            "name": name,
                            "type": "vector",
                            "format": "geojson",
                            "visible": visible,
                            "geojson": vector,
                            "summary": {
                                "feature_count": len(vector["features"]),
                            },
                        }
                    ],
                },
            )

        if capability_name == "summarize_vector_layer":
            return SimpleNamespace(
                plugin_id="vector_tools",
                output_kind="text",
                callable=lambda *, vector: {
                    "message": "Vector layer contains features.",
                    "summary": {
                        "feature_count": len(vector["features"]),
                    },
                },
            )

        raise KeyError(capability_name)


class _FakeVectorContext:
    def __init__(self) -> None:
        self.remembered: list[dict[str, Any]] = []

    def _is_real_estate_analysis_query(self, query: str) -> bool:
        return False

    def _build_enabled_router(self) -> _FakeRouter:
        return _FakeRouter()

    def _remember(self, *, request_id: str, record: dict[str, Any]) -> None:
        self.remembered.append(
            {
                "request_id": request_id,
                "record": record,
            }
        )


def test_vector_display_response_keeps_layers_and_outputs_vectors_in_sync() -> None:
    context = _FakeVectorContext()

    response = try_handle_vector_display_directly(
        context,
        query="show vector layer",
        inputs={},
        resolved_inputs={
            "geojson": FEATURE_COLLECTION,
        },
        final_request_id="req-map-contract-vector",
        final_metadata={},
        json_safe=_json_safe,
    )

    assert response is not None
    assert response["ok"] is True

    outputs = response["outputs"]
    layers = response["layers"]

    assert isinstance(outputs["vectors"], list)
    assert isinstance(layers, list)
    assert outputs["vectors"]
    assert layers

    vector = outputs["vectors"][0]
    layer = layers[0]

    assert vector["id"] == layer["id"] == "active_vector"
    assert vector["name"] == layer["name"] == "active_vector"
    assert vector["format"] == layer["format"] == "geojson"
    assert vector["role"] == "map_layer"

    assert layer["type"] == "vector"
    assert layer["visible"] is True

    assert vector["geojson"] == FEATURE_COLLECTION
    assert layer["geojson"] == FEATURE_COLLECTION
    assert layer["geojson"]["type"] == "FeatureCollection"
    assert len(layer["geojson"]["features"]) == 1

    assert outputs["documents"] == []
    assert outputs["reports"] == []
    assert outputs["files"] == []
    assert outputs["artifacts"] == []


def test_real_estate_response_keeps_ranked_layer_and_vector_contract_in_sync() -> None:
    response = build_real_estate_ranking_response(
        query="رتبه‌بندی املاک",
        rid="req-map-contract-real-estate",
        message="done",
        features=FEATURE_COLLECTION["features"],
        ranked_features=FEATURE_COLLECTION["features"],
        ranked_geojson=FEATURE_COLLECTION,
        rejected_rows=[],
        table_rows=[
            {
                "rank": 1,
                "name": "A",
                "score": 91,
            }
        ],
        summary={
            "eligible_count": 1,
            "top_property": "A",
            "top_score": 91,
        },
        report={
            "title": "گزارش رتبه‌بندی املاک",
            "summary": {},
        },
        documents=[],
        document_warnings=[],
        render_pdf_trace_step={
            "order": 5,
            "node_id": "node_005_render_pdf",
            "capability_name": "render_pdf",
            "plugin_id": "pdf_renderer",
            "output_kind": "document",
            "status": "skipped",
        },
        spatial_enrichment_summary={},
        llm_intent=None,
        build_analysis_inspector=lambda **kwargs: kwargs,
        llm_planning_enabled=lambda: False,
    )

    assert response["ok"] is True

    outputs = response["outputs"]
    assert outputs["vectors"]
    assert response["layers"]

    vector = outputs["vectors"][0]
    layer = response["layers"][0]

    assert vector["id"] == layer["id"] == "ranked_properties"
    assert vector["name"] == "ranked_properties"
    assert layer["name"] == "املاک رتبه‌بندی‌شده"
    assert vector["format"] == layer["format"] == "geojson"
    assert vector["role"] == "map_layer"

    assert layer["type"] == "vector"
    assert layer["visible"] is True
    assert vector["geojson"] == FEATURE_COLLECTION
    assert layer["geojson"] == FEATURE_COLLECTION
    assert len(layer["geojson"]["features"]) == 1

    assert outputs["documents"] == []
    assert outputs["files"] == []
    assert outputs["artifacts"] == []


def test_map_layer_builder_exposes_frontend_leaflet_ready_contract() -> None:
    builder = MapLayerBuilder()

    payload = builder.build_for_request_record(
        {
            "request_id": "req-map-contract-builder",
            "production_response": {
                "layers": [
                    {
                        "id": "ranked_properties",
                        "name": "ranked_properties",
                        "type": "vector",
                        "format": "geojson",
                        "visible": True,
                        "geojson": FEATURE_COLLECTION,
                    }
                ],
                "outputs": {
                    "vectors": [
                        {
                            "id": "ranked_properties",
                            "name": "ranked_properties",
                            "format": "geojson",
                            "role": "map_layer",
                            "geojson": FEATURE_COLLECTION,
                        }
                    ]
                },
            },
        }
    )

    assert payload["request_id"] == "req-map-contract-builder"
    assert payload["layer_count"] >= 1
    assert isinstance(payload["warnings"], list)
    assert payload["layers"]

    layer = payload["layers"][0]

    assert layer["name"] == "ranked_properties"
    assert layer["kind"] == "vector"
    assert layer["crs"] == "EPSG:4326"
    assert layer["source_crs"] == "EPSG:4326"
    assert layer["feature_count"] == 1
    assert layer["geojson"]["type"] == "FeatureCollection"
    assert len(layer["geojson"]["features"]) == 1
    assert layer["source"] == "raw_record"


def test_legacy_simple_response_builder_keeps_map_layers_contract() -> None:
    response = SimpleResponseBuilder().build(
        {
            "outputs": {
                "vegetation_polygons": {
                    "type": "FeatureCollection",
                    "features": FEATURE_COLLECTION["features"],
                    "metadata": {
                        "selected_pixel_count": 1,
                    },
                }
            },
            "trace": [
                {
                    "step": "polygonize",
                    "status": "success",
                }
            ],
            "intent": SimpleNamespace(
                intent_name="extract_vegetation_polygons_from_ndvi_threshold",
                threshold_value=0.3,
            ),
        }
    )

    assert response["status"] == "success"
    assert response["map"]["layers"]

    layer = response["map"]["layers"][0]

    assert layer["id"] == "vegetation_polygons"
    assert layer["title"] == "Vegetation polygons from NDVI threshold"
    assert layer["type"] == "vector"
    assert layer["source_kind"] == "inline_geojson"
    assert layer["feature_count"] == 1
    assert layer["data"]["type"] == "FeatureCollection"
    assert len(layer["data"]["features"]) == 1

    viewport = response["map"]["viewport"]
    assert viewport["strategy"] == "fit_layers"
    assert viewport["layer_ids"] == ["vegetation_polygons"]

    assert response["metadata"]["final_output"] == "vegetation_polygons"
    assert response["metadata"]["feature_count"] == 1
