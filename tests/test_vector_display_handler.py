from types import SimpleNamespace

from smart_spatial_system.application.services.vector_display_handler import (
    try_handle_vector_display_directly,
)


def _json_safe(value):
    return value


FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "A"},
            "geometry": {
                "type": "Point",
                "coordinates": [51.4, 35.7],
            },
        }
    ],
}


class FakeRouter:
    def resolve(self, capability_name: str):
        if capability_name == "inspect_vector":
            return SimpleNamespace(
                plugin_id="vector_tools",
                output_kind="summary",
                callable=lambda *, vector: {
                    "summary": {
                        "feature_count": 1,
                        "geometry_counts": {"Point": 1},
                        "property_keys": ["name"],
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
                        "feature_count": 1,
                        "geometry_counts": {"Point": 1},
                        "property_keys": ["name"],
                    },
                    "outputs": {
                        "vectors": [
                            {
                                "id": layer_id,
                                "name": name,
                                "format": "geojson",
                                "role": "map_layer",
                                "geojson": vector,
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
                        }
                    ],
                },
            )

        if capability_name == "summarize_vector_layer":
            return SimpleNamespace(
                plugin_id="vector_tools",
                output_kind="text",
                callable=lambda *, vector: {
                    "message": "Vector layer contains 1 features.",
                    "summary": {
                        "feature_count": 1,
                        "geometry_counts": {"Point": 1},
                        "property_keys": ["name"],
                    },
                },
            )

        raise KeyError(capability_name)


class FakeContext:
    def __init__(self) -> None:
        self.remembered = []

    def _is_real_estate_analysis_query(self, query: str) -> bool:
        return False

    def _build_enabled_router(self):
        return FakeRouter()

    def _remember(self, *, request_id, record):
        self.remembered.append(
            {
                "request_id": request_id,
                "record": record,
            }
        )


def test_try_handle_vector_display_directly_returns_none_for_non_vector_query() -> None:
    context = FakeContext()

    result = try_handle_vector_display_directly(
        context,
        query="ndvi raster analysis",
        inputs={"geojson": FEATURE_COLLECTION},
        resolved_inputs={"geojson": FEATURE_COLLECTION},
        final_request_id="req-1",
        final_metadata={},
        json_safe=_json_safe,
    )

    assert result is None
    assert context.remembered == []


def test_try_handle_vector_display_directly_builds_display_response() -> None:
    context = FakeContext()

    result = try_handle_vector_display_directly(
        context,
        query="show vector layer",
        inputs={},
        resolved_inputs={"geojson": FEATURE_COLLECTION},
        final_request_id="req-display-1",
        final_metadata={"source": "test"},
        band_map={"red": 1},
        user_context={"user": "demo"},
        json_safe=_json_safe,
    )

    assert result is not None
    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["request_id"] == "req-display-1"
    assert result["result"]["type"] == "vector_display"
    assert result["summary"]["feature_count"] == 1
    assert result["outputs"]["vectors"][0]["id"] == "active_vector"
    assert result["layers"][0]["id"] == "active_vector"
    assert result["metadata"]["execution_mode"] == "capability_bridge"
    assert result["metadata"]["legacy_handler_name"] == "vector_display"

    assert len(context.remembered) == 1
    assert context.remembered[0]["request_id"] == "req-display-1"
    assert context.remembered[0]["record"]["production_response"]["result"]["type"] == (
        "vector_display"
    )


def test_try_handle_vector_display_directly_builds_summary_response() -> None:
    context = FakeContext()

    result = try_handle_vector_display_directly(
        context,
        query="summarize vector layer",
        inputs={},
        resolved_inputs={"geojson": FEATURE_COLLECTION},
        final_request_id="req-summary-1",
        final_metadata={},
        json_safe=_json_safe,
    )

    assert result is not None
    assert result["ok"] is True
    assert result["result"]["type"] == "vector_summary"
    assert result["result"]["feature_count"] == 1
    assert result["summary"]["geometry_counts"] == {"Point": 1}
    assert result["outputs"]["vectors"][0]["geojson"] == FEATURE_COLLECTION
    assert result["layers"][0]["geojson"] == FEATURE_COLLECTION
    assert result["metadata"]["legacy_handler_name"] == "vector_summary"


def test_try_handle_vector_display_directly_does_not_swallow_real_estate_ranking() -> None:
    class RealEstateContext(FakeContext):
        def _is_real_estate_analysis_query(self, query: str) -> bool:
            return True

    context = RealEstateContext()

    result = try_handle_vector_display_directly(
        context,
        query="گزارش املاک را به صورت جدول بده",
        inputs={"geojson": FEATURE_COLLECTION},
        resolved_inputs={"geojson": FEATURE_COLLECTION},
        final_request_id="req-real-estate-1",
        final_metadata={},
        json_safe=_json_safe,
    )

    assert result is None
    assert context.remembered == []
