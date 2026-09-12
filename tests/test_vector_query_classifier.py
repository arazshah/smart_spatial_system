import pytest

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


# ------------------------------------------------------------------ #
# English/Persian parity
# ------------------------------------------------------------------ #

@pytest.mark.parametrize(
    "persian,english",
    [
        ("نمایش نقاط روی نقشه", "display the sites on the map"),
        ("نمایش لایه وکتور", "show the vector layer"),
        ("نقاط را نشان بده", "plot the points"),
        ("عوارض را روی نقشه نمایش بده", "render the features on the map"),
    ],
)
def test_display_queries_classify_the_same_in_both_languages(persian, english):
    """
    The two sides used to disagree: the Persian phrasing reached the
    vector-display handler while its literal English translation fell
    through to the legacy routing-aware planner, which then failed asking
    for raster capabilities the query never needed.
    """
    assert is_vector_display_query(persian) is True
    assert is_vector_display_query(english) is True


@pytest.mark.parametrize(
    "persian,english",
    [
        ("چند نقطه داخل فایل است؟", "how many points are in this file?"),
        ("تعداد عوارض لایه را بگو", "count the features in this layer"),
        ("خلاصه لایه وکتور", "summarize the vector layer"),
    ],
)
def test_summary_queries_classify_the_same_in_both_languages(persian, english):
    assert is_vector_summary_query(persian) is True
    assert is_vector_summary_query(english) is True


@pytest.mark.parametrize(
    "query",
    [
        "display the NDVI raster",
        "show the slope map",
        "calculate the burnt area from the satellite image",
    ],
)
def test_raster_display_queries_are_not_claimed_by_the_vector_classifier(query):
    """
    The widened English token list must not swallow raster work: these all
    carry a display token, so only the absence of a vector noun keeps them
    out of the vector handler.
    """
    assert is_vector_display_query(query) is False
