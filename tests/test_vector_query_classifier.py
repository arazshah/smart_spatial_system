from smart_spatial_system.application.services.vector_query_classifier import (
    is_vector_display_query,
    is_vector_summary_query,
)


def test_is_vector_display_query_matches_text_tokens() -> None:
    assert is_vector_display_query("نقاط را روی نقشه نمایش بده") is True
    assert is_vector_display_query("show vector layer") is True
    assert is_vector_display_query("draw geojson features") is True


def test_is_vector_display_query_matches_intent_contract() -> None:
    assert is_vector_display_query(
        "anything",
        {
            "intent_name": "vector_display",
            "required_inputs": {
                "vector": True,
                "raster": False,
            },
            "output_expectation": {
                "map_layer": True,
            },
        },
    ) is True


def test_is_vector_display_query_matches_preferred_vector_capability() -> None:
    assert is_vector_display_query(
        "anything",
        {
            "intent_name": "unknown",
            "required_inputs": {
                "vector": True,
                "raster": False,
            },
            "output_expectation": {
                "map_layer": True,
            },
            "preferred_capabilities": [
                "filter_features",
            ],
        },
    ) is True


def test_is_vector_display_query_rejects_raster_or_plain_text_queries() -> None:
    assert is_vector_display_query("ndvi raster analysis") is False
    assert is_vector_display_query(
        "anything",
        {
            "intent_name": "vector_display",
            "required_inputs": {
                "vector": True,
                "raster": True,
            },
            "output_expectation": {
                "map_layer": True,
            },
        },
    ) is False


def test_is_vector_summary_query_matches_text_tokens() -> None:
    assert is_vector_summary_query("تعداد عارضه‌های فایل را بگو") is True
    assert is_vector_summary_query("چند نقطه داخل geojson است؟") is True
    assert is_vector_summary_query("summarize vector layer") is True


def test_is_vector_summary_query_matches_intent_name() -> None:
    assert is_vector_summary_query(
        "anything",
        {
            "intent_name": "vector_summary",
            "required_inputs": {
                "vector": True,
                "raster": False,
            },
        },
    ) is True


def test_is_vector_summary_query_matches_text_output_contract() -> None:
    assert is_vector_summary_query(
        "anything",
        {
            "intent_name": "unknown",
            "required_inputs": {
                "vector": True,
                "raster": False,
            },
            "output_expectation": {
                "text": True,
                "map_layer": False,
            },
        },
    ) is True


def test_is_vector_summary_query_rejects_display_and_raster_queries() -> None:
    assert is_vector_summary_query("show vector layer") is False
    assert is_vector_summary_query("ndvi raster statistics") is False
    assert is_vector_summary_query(
        "anything",
        {
            "intent_name": "vector_statistics",
            "required_inputs": {
                "vector": True,
                "raster": True,
            },
        },
    ) is False
