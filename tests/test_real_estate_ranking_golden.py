"""
Golden test for the real-estate ranking direct-handler pipeline
(REFACTOR_PLAN.md Phase 5, step 1 -- see
docs/PHASE5_REAL_ESTATE_PLUGIN_PLAN.md).

Captures try_handle_real_estate_ranking_directly's full output,
field-by-field, for a realistic property FeatureCollection, before any
code moves into plugin capabilities. This is the regression net for every
step after it in the Phase 5 migration: ranked order, scores, rejection
reasons, table rows, and report/document metadata must not change (except
where a later step documents an intentional, deliberate diff).

Run:
    pytest tests/test_real_estate_ranking_golden.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_spatial_system.application.services.query_execution.real_estate_ranking_direct_handler import (  # noqa: E402
    try_handle_real_estate_ranking_directly,
)

REAL_ESTATE_QUERY = (
    "ملک‌هایی را پیدا کن که کمتر از ۵۰۰ متر به مترو یا مرکز خرید نزدیک باشند، "
    "نزدیک خیابان اصلی باشند، ریسک سیل و زلزله و آتش‌سوزی پایین داشته باشند، "
    "به هر ملک امتیاز بده و گزارش رتبه‌بندی تولید کن"
)


def _sample_properties() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "p1",
                    "name": "آپارتمان مرکز",
                    "price": 12000000000,
                    "kind": "apartment",
                    "distance_to_metro_m": 420,
                    "distance_to_mall_m": 620,
                    "distance_to_main_road_m": 90,
                    "flood_risk": "low",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                    "in_allowed_zone": True,
                },
                "geometry": {"type": "Point", "coordinates": [51.389, 35.689]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "p2",
                    "name": "ویلای لوکس",
                    "price": 18000000000,
                    "kind": "villa",
                    "distance_to_metro_m": 700,
                    "distance_to_mall_m": 310,
                    "distance_to_main_road_m": 60,
                    "flood_risk": "low",
                    "earthquake_risk": "low",
                    "fire_risk": "low",
                    "in_allowed_zone": True,
                },
                "geometry": {"type": "Point", "coordinates": [51.395, 35.692]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "p3",
                    "name": "زمین حاشیه‌ای",
                    "price": 7500000000,
                    "kind": "land",
                    "distance_to_metro_m": 1200,
                    "distance_to_mall_m": 950,
                    "distance_to_main_road_m": 220,
                    "flood_risk": "high",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                    "in_allowed_zone": False,
                },
                "geometry": {"type": "Point", "coordinates": [51.401, 35.684]},
            },
        ],
    }


def _handle(**overrides: Any) -> dict[str, Any] | None:
    kwargs = {
        "query": REAL_ESTATE_QUERY,
        "inputs": {"properties": _sample_properties()},
        "request_id": "req-golden-real-estate-1",
        "llm_intent": None,
        "llm_planning_enabled": lambda: False,
    }
    kwargs.update(overrides)
    return try_handle_real_estate_ranking_directly(**kwargs)


def test_golden_ranking_order_and_scores() -> None:
    response = _handle()

    assert response is not None
    ranking = response["result"]["ranking"]

    assert [row["id"] for row in ranking] == ["p2", "p1"]
    assert [row["rank"] for row in ranking] == [1, 2]
    assert ranking[0]["name"] == "ویلای لوکس"
    assert ranking[0]["score"] == 91.0
    assert ranking[1]["name"] == "آپارتمان مرکز"
    assert ranking[1]["score"] == 75.0
    assert ranking[0]["score"] > ranking[1]["score"]


def test_golden_rejection_reasons() -> None:
    response = _handle()

    assert response is not None
    rejected = response["result"]["rejected"]

    assert len(rejected) == 1
    assert rejected[0]["id"] == "p3"
    assert rejected[0]["name"] == "زمین حاشیه‌ای"
    assert rejected[0]["reasons"] == [
        "farther_than_500m_from_metro_or_mall",
        "far_from_main_road",
        "high_flood_risk",
        "outside_allowed_construction_zone",
    ]


def test_golden_summary() -> None:
    response = _handle()

    assert response is not None
    summary = response["summary"]

    assert summary["candidate_count"] == 3
    assert summary["eligible_count"] == 2
    assert summary["rejected_count"] == 1
    assert summary["top_property"] == "ویلای لوکس"
    assert summary["top_score"] == 91.0
    assert summary["criteria"] == {
        "max_distance_to_metro_or_mall_m": 500,
        "max_distance_to_main_road_m": 150,
        "excluded_risk_level": "high",
        "medium_risk_policy": "allowed_with_score_penalty",
        "requires_allowed_construction_zone": True,
    }
    assert "spatial_enrichment" not in summary


def test_golden_table_rows() -> None:
    response = _handle()

    assert response is not None
    table_by_id = {
        row["id"]: row for row in response["outputs"]["tables"][0]["rows"]
    }

    assert set(table_by_id.keys()) == {"p1", "p2"}
    assert table_by_id["p2"]["rank"] == 1
    assert table_by_id["p2"]["kind"] == "villa"
    assert table_by_id["p2"]["price"] == 18000000000
    assert table_by_id["p2"]["best_poi_distance_m"] == 310
    assert table_by_id["p2"]["in_allowed_zone"] is True
    assert table_by_id["p1"]["rank"] == 2
    assert table_by_id["p1"]["best_poi_distance_m"] == 420

    rejected_rows = response["outputs"]["tables"][1]["rows"]
    assert len(rejected_rows) == 1
    assert rejected_rows[0]["id"] == "p3"


def test_golden_report_and_message() -> None:
    response = _handle()

    assert response is not None
    report = response["report"]

    assert report["title"] == "Property Ranking and Investment Analysis Report"
    assert report["language"] == "en"
    assert len(report["ranking"]) == 2
    assert len(report["rejected"]) == 1
    assert len(report["notes"]) == 3

    assert response["answer"] == (
        "Property ranking complete. Of 3 properties, 2 were eligible."
        " Top choice: ویلای لوکس with a score of 91.0."
    )


def test_golden_top_level_envelope_and_metadata() -> None:
    response = _handle()

    assert response is not None
    assert response["ok"] is True
    assert response["status"] == "success"
    assert response["request_id"] == "req-golden-real-estate-1"
    assert response["result"]["type"] == "real_estate_ranking"
    assert response["metadata"]["execution_mode"] == "real_estate_ranking_bridge"
    assert response["metadata"]["capabilities"] == [
        "filter_features",
        "score_features",
        "rank_features",
        "build_report",
        "render_pdf",
    ]
    assert response["audit_record"]["status"] == "success"
    assert response["audit_record"]["execution_mode"] == "real_estate_ranking_bridge"


def test_golden_trace_capability_sequence() -> None:
    response = _handle()

    assert response is not None
    trace_capabilities = [step["capability_name"] for step in response["trace"]]

    assert trace_capabilities == [
        "filter_features",
        "score_features",
        "rank_features",
        "build_report",
        "render_pdf",
    ]
    assert all(step["status"] == "success" for step in response["trace"][:4])


def test_golden_layers_and_vector_output() -> None:
    response = _handle()

    assert response is not None
    assert response["layers"][0]["id"] == "ranked_properties"
    assert response["layers"][0]["type"] == "vector"
    assert len(response["layers"][0]["geojson"]["features"]) == 2

    vector_output = response["outputs"]["vectors"][0]
    assert vector_output["id"] == "ranked_properties"
    assert vector_output["role"] == "map_layer"
    assert len(vector_output["geojson"]["features"]) == 2


def test_golden_without_inputs_returns_none() -> None:
    response = _handle(inputs={})

    assert response is None


def test_golden_non_real_estate_query_returns_none() -> None:
    response = _handle(query="ndvi raster analysis", inputs={})

    assert response is None
