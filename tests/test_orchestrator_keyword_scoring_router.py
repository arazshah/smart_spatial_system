"""
Tests for keyword-scoring capability router.

Run:
    pytest tests/test_orchestrator_keyword_scoring_router.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.capability_registry import CapabilityRegistry  # noqa: E402
from orchestrator.capability_scoring import KeywordScoringCapabilityRouter  # noqa: E402


SAFE_MODULES = [
    "plugins.spectral_indices",
    "plugins.raster_threshold",
    "plugins.raster_to_vector",
]


NDVI_QUERY = (
    "از تصویر ماهواره‌ای NDVI بگیر و مناطقی که NDVI آنها بیشتر از 0.3 است "
    "را به پلیگون تبدیل کن"
)


def _make_router() -> KeywordScoringCapabilityRouter:
    registry = CapabilityRegistry.from_plugin_modules(SAFE_MODULES)
    return KeywordScoringCapabilityRouter(registry=registry)


def test_keyword_scoring_router_registers_capabilities() -> None:
    router = _make_router()

    assert router.registered_capability_names() == [
        "calculate_spectral_index",
        "raster_to_vector",
        "threshold_raster",
    ]


def test_keyword_scoring_scores_ndvi_query_relevant_capabilities() -> None:
    router = _make_router()

    candidates = router.score_query(NDVI_QUERY, min_score=0.1)

    names = [candidate.capability_name for candidate in candidates]

    assert "calculate_spectral_index" in names
    assert "threshold_raster" in names
    assert "raster_to_vector" in names

    by_name = {candidate.capability_name: candidate for candidate in candidates}

    assert by_name["calculate_spectral_index"].score > 0
    assert by_name["threshold_raster"].score > 0
    assert by_name["raster_to_vector"].score > 0

    assert "ndvi" in by_name["calculate_spectral_index"].matched_terms
    assert "بیشتر از" in by_name["threshold_raster"].matched_terms
    assert "پلیگون" in by_name["raster_to_vector"].matched_terms


def test_keyword_scoring_select_relevant_returns_sorted_candidates() -> None:
    router = _make_router()

    candidates = router.select_relevant(NDVI_QUERY, min_score=0.2)

    assert len(candidates) >= 3

    scores = [candidate.score for candidate in candidates]
    assert scores == sorted(scores, reverse=True)

    assert all(candidate.score >= 0.2 for candidate in candidates)


def test_keyword_scoring_best_match_for_polygon_query() -> None:
    router = _make_router()

    best = router.best_match(
        "ماسک رستر را به پلیگون تبدیل کن",
        min_score=0.2,
    )

    assert best.capability_name == "raster_to_vector"
    assert best.plugin_id == "raster_to_vector"
    assert best.output_kind == "vector"
    assert "پلیگون" in best.matched_terms


def test_keyword_scoring_best_match_for_threshold_query() -> None:
    router = _make_router()

    best = router.best_match(
        "پیکسل‌هایی که مقدارشان بیشتر از 0.3 است را به صورت ماسک استخراج کن",
        min_score=0.2,
    )

    assert best.capability_name == "threshold_raster"
    assert best.plugin_id == "raster_threshold"
    assert best.output_kind == "raster"


def test_keyword_scoring_best_match_for_ndvi_query_with_raster_filter() -> None:
    router = _make_router()

    best = router.best_match(
        "شاخص NDVI را از تصویر ماهواره‌ای محاسبه کن",
        expected_output_kind="raster",
        min_score=0.2,
    )

    assert best.capability_name == "calculate_spectral_index"
    assert best.plugin_id == "spectral_indices"
    assert best.output_kind == "raster"


def test_keyword_scoring_output_kind_filter_vector() -> None:
    router = _make_router()

    candidates = router.score_query(
        NDVI_QUERY,
        expected_output_kind="vector",
        min_score=0.1,
    )

    assert [candidate.capability_name for candidate in candidates] == [
        "raster_to_vector",
    ]

    assert candidates[0].output_kind == "vector"


def test_keyword_scoring_rejects_empty_query() -> None:
    router = _make_router()

    with pytest.raises(ValueError, match="query"):
        router.score_query("")


def test_keyword_scoring_rejects_invalid_top_k() -> None:
    router = _make_router()

    with pytest.raises(ValueError, match="top_k"):
        router.score_query(NDVI_QUERY, top_k=0)


def test_keyword_scoring_no_match_raises_for_best_match() -> None:
    router = _make_router()

    with pytest.raises(ValueError, match="No capability"):
        router.best_match(
            "یک جمله نامرتبط بدون هیچ کلیدواژه مکانی",
            min_score=0.8,
        )
